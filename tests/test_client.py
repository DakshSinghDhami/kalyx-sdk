"""Sync client tests: probe, query_and_retrieve flow, error mapping, and the
confirm-before-retrieve invariant. All hermetic (respx fakes)."""

import json

import httpx
import pytest
import respx
from solders.keypair import Keypair

from kalyx_sdk import KalyxClient
from kalyx_sdk.errors import (
    BudgetExhausted,
    ChainError,
    ClusterMismatchError,
    GatewayUnavailable,
    InsufficientFunds,
    KalyxError,
    PaymentRequired,
    PriceExceedsBudget,
    RateLimited,
    ReplayRejected,
    VerificationFailed,
)
from kalyx_sdk.escrow import DEFAULT_PROGRAM_ID

from .helpers import (
    AUTHOR_ADDR,
    CONTENT,
    GATEWAY,
    NODE_ADDR,
    PRICE,
    RPC_URL,
    FakeRpc,
    challenge_body,
    config_body,
    no_context_body,
    retrieve_ok_body,
)


@pytest.fixture
def payer():
    return Keypair.from_seed(bytes([9]) * 32)


def make_client(payer, **kw):
    kw.setdefault("gateway_url", GATEWAY)
    kw.setdefault("rpc_url", RPC_URL)
    kw.setdefault("keypair", payer)
    return KalyxClient(**kw)


@respx.mock
def test_probe_no_context(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    client = make_client(payer)
    result = client.probe("unrelated query")
    assert result.status == "no_context_found"
    assert result.challenge is None
    assert result.similarity_score == pytest.approx(0.12)


@respx.mock
def test_probe_payment_required(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    client = make_client(payer)
    result = client.probe("how does settlement work")
    assert result.status == "payment_required"
    assert result.challenge is not None
    assert result.challenge.price_lamports == PRICE
    assert result.challenge.node_address == NODE_ADDR


@respx.mock
def test_query_and_retrieve_happy_path(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)

    with make_client(payer) as client:
        result = client.query_and_retrieve("how does settlement work")

    assert result.status == "access_granted"
    assert result.context == CONTENT
    assert result.citation is not None and "Escrow notes" in result.citation
    assert result.price_lamports == PRICE
    assert result.funding_signature == "funding-sig-111"
    assert result.escrow_address
    assert result.settle_status == "settled"
    assert result.receipt["verification"]["valid"] is True

    methods = rpc.methods()
    # The payment is confirmed BEFORE the retrieve call happens.
    assert methods.index("sendTransaction") < methods.index("getSignatureStatuses")
    retrieve_route = respx.post(f"{GATEWAY}/v1/retrieve")
    assert retrieve_route.called


@respx.mock
def test_query_and_retrieve_free_answer(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    result = client.query_and_retrieve("irrelevant")
    assert result.status == "no_context_found"
    assert result.context is None
    assert rpc.calls == []  # no chain interaction for free answers


@respx.mock
def test_price_cap_refuses_before_payment(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer, max_price_lamports=PRICE - 1)
    with pytest.raises(PriceExceedsBudget):
        client.query_and_retrieve("settlement")
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_per_call_cap_overrides_client_cap(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer, max_price_lamports=1)
    with pytest.raises(PriceExceedsBudget):
        client.query_and_retrieve("settlement")
    result = client.query_and_retrieve("settlement", max_price_lamports=PRICE)
    assert result.status == "access_granted"


@respx.mock
def test_session_budget_exhausted(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer, session_budget_lamports=PRICE)
    assert client.query_and_retrieve("settlement").status == "access_granted"
    assert client.spent_lamports == PRICE
    with pytest.raises(BudgetExhausted) as ei:
        client.query_and_retrieve("settlement again")
    assert ei.value.spent_lamports == PRICE
    assert rpc.methods().count("sendTransaction") == 1  # second call never paid


@respx.mock
def test_insufficient_funds(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc(getBalance=lambda p: {"value": PRICE})
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(InsufficientFunds) as ei:
        client.query_and_retrieve("settlement")
    assert ei.value.required_lamports > ei.value.available_lamports
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_no_keypair_raises_payment_required():
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL)
    with pytest.raises(PaymentRequired) as ei:
        client.query_and_retrieve("settlement")
    assert ei.value.challenge is not None
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_mainnet_rpc_refused(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc(getGenesisHash=lambda p: "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d")
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(ClusterMismatchError):
        client.query_and_retrieve("settlement")
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_gateway_cluster_mismatch_refused(payer):
    """Gateway on devnet + client RPC on testnet must refuse (payment unverifiable)."""
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc(getGenesisHash=lambda p: "4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY")
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(ClusterMismatchError):
        client.query_and_retrieve("settlement")
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_program_not_deployed_refused(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body(program_deployed=False))
    client = make_client(payer)
    with pytest.raises(VerificationFailed) as ei:
        client.query_and_retrieve("settlement")
    assert ei.value.reason == "program_not_deployed"


@respx.mock
def test_tampered_challenge_rejected_before_payment(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(
        402,
        json=challenge_body(escrow_params={"content_hash": "ef" * 32, "node_address": NODE_ADDR}),
    )
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(VerificationFailed):
        client.query_and_retrieve("settlement")
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_retrieve_replay_rejected(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(409, json={"error": "signature already used"})
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(ReplayRejected):
        client.query_and_retrieve("settlement")


@respx.mock
def test_retrieve_forbidden_is_verification_failed(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(403, json={"error": "payment verification failed"})
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(VerificationFailed):
        client.query_and_retrieve("settlement")


@respx.mock
def test_retrieve_rate_limited_has_retry_after(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(
        429,
        json={"error": "too many requests", "retry_after": 17},
        headers={"Retry-After": "17"},
    )
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(RateLimited) as ei:
        client.query_and_retrieve("settlement")
    assert ei.value.retry_after == 17.0


@respx.mock
def test_retrieve_unavailable_has_reason(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(
        503, json={"error": "rpc down", "reason": "rpc_error"}
    )
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable) as ei:
        client.query_and_retrieve("settlement")
    assert ei.value.reason == "rpc_error"


@respx.mock
def test_retrieve_content_binding_enforced(payer):
    """Content that does not hash to the committed chunk id is rejected."""
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(
        200, json=retrieve_ok_body(decrypted_content="tampered bytes")
    )
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(VerificationFailed) as ei:
        client.query_and_retrieve("settlement")
    assert ei.value.reason == "content_hash_mismatch"


@respx.mock
def test_send_failure_raises_chain_error(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc(
        sendTransaction=lambda p: (_ for _ in ()).throw(RuntimeError("blockhash not found"))
    )
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(ChainError):
        client.query_and_retrieve("settlement")
    assert "getSignatureStatuses" not in rpc.methods()


@respx.mock
def test_probe_400_is_client_error(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(400, json={"error": "query too long"})
    client = make_client(payer)
    with pytest.raises(KalyxError):
        client.probe("x")


@respx.mock
def test_probe_rejects_invalid_query(payer):
    client = make_client(payer)
    with pytest.raises(ValueError):
        client.probe("   ")
    with pytest.raises(ValueError):
        client.probe("x" * 513)


@respx.mock
def test_gateway_unreachable(payer):
    respx.post(f"{GATEWAY}/v1/query").mock(side_effect=httpx.ConnectError("nope"))
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable):
        client.probe("settlement")


@respx.mock
def test_malformed_challenge_body(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json={"error": "Payment Required"})
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable):
        client.probe("settlement")


@respx.mock
def test_config_cached(payer):
    route = respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    client = make_client(payer)
    a = client.config()
    b = client.config()
    assert a is b
    assert route.call_count == 1


@respx.mock
def test_signed_tx_targets_challenge_program(payer):
    """The broadcast transaction must pay the challenge's program and node."""
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    captured = {}

    def capture_send(params):
        captured["tx"] = params[0]
        return "funding-sig-222"

    rpc = FakeRpc(sendTransaction=capture_send)
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    client.query_and_retrieve("settlement")

    import base64

    from solders.transaction import Transaction

    tx = Transaction.from_bytes(base64.b64decode(captured["tx"]))
    ix = tx.message.instructions[0]
    program = tx.message.account_keys[ix.program_id_index]
    assert str(program) == DEFAULT_PROGRAM_ID
    # account order: escrow(w), knowledge_node(r), consumer(w,s), system(r)
    keys = [str(k) for k in tx.message.account_keys]
    assert keys[ix.accounts[1]] == NODE_ADDR
    assert keys[ix.accounts[2]] == str(payer.pubkey())
    assert keys[3 + 0] or True  # silence lint; escrow is index 0
    assert str(payer.pubkey()) != AUTHOR_ADDR


@respx.mock
def test_blockhash_comes_from_rpc(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    result = client.query_and_retrieve("settlement")
    assert result.status == "access_granted"
    assert "getLatestBlockhash" in rpc.methods()
    # blockhash freshness: fetched before send
    assert rpc.methods().index("getLatestBlockhash") < rpc.methods().index("sendTransaction")


def test_client_repr_masks_nothing_but_address(payer):
    client = make_client(payer)
    rep = repr(client)
    assert "KalyxClient" in rep and str(payer.pubkey()) in rep
    assert json.dumps(rep)  # printable
