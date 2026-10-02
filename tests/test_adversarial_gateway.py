"""Adversarial gateway scenarios over a real loopback socket server.

Proves the SDK never loses funds and never trusts bad data when the gateway
misbehaves. Every scenario asserts *no payment was broadcast* unless the
scenario is explicitly a post-payment one, and that raised errors are typed
(:class:`KalyxError` subclasses) — never bare exceptions.
"""

import pytest
from solders.keypair import Keypair

from kalyx_sdk import KalyxClient
from kalyx_sdk.errors import (
    ChainError,
    GatewayUnavailable,
    KalyxError,
    ReplayRejected,
    VerificationFailed,
)
from kalyx_sdk.retry import RetryPolicy

from .evil_gateway import (
    INJECTION,
    INJECTION_CHUNK_ID,
    OTHER_PROGRAM,
    RANDOM_PDA,
    EvilGateway,
    Scenario,
)
from .helpers import AUTHOR_ADDR, PRICE

FAST = RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0, jitter=False)


def make_client(stack: EvilGateway, **kw) -> KalyxClient:
    kw.setdefault("gateway_url", stack.gateway_url)
    kw.setdefault("rpc_url", stack.rpc_url)
    kw.setdefault("keypair", Keypair.from_seed(bytes([9]) * 32))
    kw.setdefault("timeout", 1.0)
    kw.setdefault("confirm_timeout", 2.0)
    kw.setdefault("retry_policy", FAST)
    return KalyxClient(**kw)


# --- tampered challenges (must fail BEFORE any payment) -------------------------


def test_price_raised_in_escrow_params_rejected_pre_payment():
    sc = Scenario()
    sc.query_body = {
        "chunk_id": "0123456789abcdef",
        "price_lamports": PRICE,
        "publisher_wallet": AUTHOR_ADDR,
        "program_id": "x",
        "escrow_params": {"price_lamports": PRICE * 3},  # inconsistent raise
    }
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(GatewayUnavailable):
            client.probe("settlement")
        assert stack.send_count == 0


def test_wrong_program_id_rejected_pre_payment():
    sc = Scenario(program_id=OTHER_PROGRAM)
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(VerificationFailed) as ei:
            client.query_and_retrieve("settlement")
        assert ei.value.reason == "program_id_mismatch"
        assert stack.send_count == 0


def test_node_pda_not_deriving_from_content_hash_rejected():
    sc = Scenario(node_address=RANDOM_PDA)
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(VerificationFailed) as ei:
            client.query_and_retrieve("settlement")
        assert ei.value.reason == "pda_mismatch"
        assert stack.send_count == 0


def test_onchain_node_price_higher_than_challenge_rejected():
    sc = Scenario(node_price=PRICE * 2)  # gateway underquotes the on-chain price
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(VerificationFailed) as ei:
            client.query_and_retrieve("settlement")
        assert ei.value.reason == "onchain_price_higher"
        assert stack.send_count == 0


def test_onchain_content_hash_differs_from_challenge_rejected():
    sc = Scenario(node_hash="ab" * 32)  # chain commits to different content
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(VerificationFailed) as ei:
            client.query_and_retrieve("settlement")
        assert ei.value.reason == "content_hash_mismatch"
        assert stack.send_count == 0


def test_zero_price_challenge_rejected():
    sc = Scenario(query_price=0)
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(GatewayUnavailable):  # malformed challenge
            client.query_and_retrieve("settlement")
        assert stack.send_count == 0


# --- prompt injection in content is inert data -----------------------------------


def test_prompt_injection_content_returned_as_inert_data():
    sc = Scenario(challenge_chunk_id=INJECTION_CHUNK_ID)
    with EvilGateway(sc) as stack, make_client(stack) as client:
        result = client.query_and_retrieve("settlement")
        assert result.status == "access_granted"
        assert result.context == INJECTION  # verbatim, never parsed/executed
        assert result.content_safety.get("prompt_injection_suspected") is True
        assert result.content_safety.get("trust") == "untrusted"
        assert stack.send_count == 1


# --- post-payment failures: no double payment -------------------------------------


def test_retrieve_replay_after_funding_no_second_payment():
    sc = Scenario(retrieve_mode="replay")
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(ReplayRejected):
            client.query_and_retrieve("settlement")
        assert stack.send_count == 1


# --- transport hostility -----------------------------------------------------------


def test_gateway_hang_times_out_typed():
    sc = Scenario(query_mode="hang")
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(GatewayUnavailable):
            client.probe("settlement")


def test_gateway_drops_mid_body_on_probe():
    sc = Scenario(query_mode="drop")
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(GatewayUnavailable):
            client.probe("settlement")


def test_gateway_truncates_chunked_body_on_probe():
    sc = Scenario(query_mode="chunked_drop")
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(GatewayUnavailable):
            client.probe("settlement")


def test_retrieve_hang_after_payment_no_double_pay():
    sc = Scenario(retrieve_mode="hang")
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(GatewayUnavailable):
            client.query_and_retrieve("settlement")
        assert stack.send_count == 1  # paid once; retrieve retried, never re-paid
        assert stack.state.retrieve_attempts == 2  # FAST policy: 2 attempts


def test_retrieve_drop_after_payment_no_double_pay():
    sc = Scenario(retrieve_mode="drop")
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(GatewayUnavailable):
            client.query_and_retrieve("settlement")
        assert stack.send_count == 1


def test_redirect_to_http_not_followed():
    sc = Scenario(query_mode="redirect_http")
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(KalyxError):
            client.probe("settlement")
        assert stack.state.query_attempts == 1


def test_redirect_to_other_host_not_followed():
    sc = Scenario(query_mode="redirect_host")
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(KalyxError):
            client.probe("settlement")
        assert stack.state.query_attempts == 1


def test_huge_json_body_does_not_hang_or_crash():
    sc = Scenario(query_mode="huge")  # 8 MiB no_context payload
    with EvilGateway(sc) as stack, make_client(stack) as client:
        result = client.probe("settlement")
        assert result.status == "no_context_found"


def test_unconfirmed_tx_means_no_retrieve_call():
    """If the RPC never confirms (blockhash expired behind our back), the SDK
    raises ChainError with the signature and never asks the gateway to settle
    an unconfirmed payment."""
    sc = Scenario()
    sc.confirm_status = None  # getSignatureStatuses -> never confirmed
    with EvilGateway(sc) as stack, make_client(stack) as client:
        with pytest.raises(ChainError) as ei:
            client.query_and_retrieve("settlement")
        assert ei.value.signature == "funding-sig-1"
        assert stack.send_count == 1
        assert stack.state.retrieve_attempts == 0
