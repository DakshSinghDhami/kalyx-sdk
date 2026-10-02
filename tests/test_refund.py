"""Consumer self-refund tests: refund_expired_escrow guards + happy path."""

import pytest
import respx
from solders.keypair import Keypair

from kalyx_sdk import KalyxClient
from kalyx_sdk.chain import current_slot
from kalyx_sdk.errors import PaymentRequired, VerificationFailed
from kalyx_sdk.escrow import DEFAULT_PROGRAM_ID, derive_escrow_pda, query_hash
from kalyx_sdk.retry import RetryPolicy

from .helpers import (
    GATEWAY,
    PRICE,
    RPC_URL,
    FakeRpc,
    config_body,
    escrow_account_b64,
)

FAST = RetryPolicy(attempts=1, base_delay=0.0, max_delay=0.0, jitter=False)
PAYER = Keypair.from_seed(bytes([9]) * 32)
PAYER_ADDR = str(PAYER.pubkey())
QUERY = "how are escrows settled"
QHASH = query_hash(QUERY)
ESCROW_ADDR = str(derive_escrow_pda(PAYER_ADDR, QHASH, DEFAULT_PROGRAM_ID)[0])

CURRENT_SLOT = 100_000
# config refund_timeout_slots = 4500 (from config_body()); slot 1000 is long past.
OLD_SLOT = 1000
RECENT_SLOT = CURRENT_SLOT - 10  # not yet expired


def client(**kw):
    kw.setdefault("retry_policy", FAST)
    return KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL, keypair=PAYER, **kw)


def rpc_with_escrow(**account_kw):
    rpc = FakeRpc(getSlot=lambda p: CURRENT_SLOT)
    rpc.set_account(
        ESCROW_ADDR,
        {
            "owner": DEFAULT_PROGRAM_ID,
            "data": [escrow_account_b64(consumer=PAYER_ADDR, qhash=QHASH, **account_kw), "base64"],
        },
    )
    return rpc


@respx.mock
def test_refund_happy_path():
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = rpc_with_escrow(created_at_slot=OLD_SLOT)
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    result = client().refund_expired_escrow(ESCROW_ADDR)
    assert result.escrow_address == ESCROW_ADDR
    assert result.refunded_lamports == PRICE
    assert result.signature == "funding-sig-111"  # FakeRpc send stub
    assert "sendTransaction" in rpc.methods()
    assert rpc.methods().index("sendTransaction") < rpc.methods().index("getSignatureStatuses")


@respx.mock
def test_refund_requires_keypair():
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    c = KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL, retry_policy=FAST)
    with pytest.raises(PaymentRequired):
        c.refund_expired_escrow(ESCROW_ADDR)


@respx.mock
def test_refund_missing_escrow():
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc(getSlot=lambda p: CURRENT_SLOT)  # no escrow account set
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    with pytest.raises(VerificationFailed) as ei:
        client().refund_expired_escrow(ESCROW_ADDR)
    assert ei.value.reason == "escrow_not_found"


@respx.mock
def test_refund_rejects_foreign_escrow():
    other = str(Keypair.from_seed(bytes([8]) * 32).pubkey())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc(getSlot=lambda p: CURRENT_SLOT)
    # Foreign escrow: its address need not match the derived PDA for this check;
    # ownership is rejected before the PDA check runs.
    foreign_addr = ESCROW_ADDR
    rpc.set_account(
        foreign_addr,
        {
            "owner": DEFAULT_PROGRAM_ID,
            "data": [
                escrow_account_b64(consumer=other, qhash=QHASH, created_at_slot=OLD_SLOT),
                "base64",
            ],
        },
    )
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    with pytest.raises(VerificationFailed) as ei:
        client().refund_expired_escrow(foreign_addr)
    assert ei.value.reason == "not_consumer"
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_refund_rejects_settled_escrow():
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = rpc_with_escrow(status="Settled", created_at_slot=OLD_SLOT)
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    with pytest.raises(VerificationFailed) as ei:
        client().refund_expired_escrow(ESCROW_ADDR)
    assert ei.value.reason == "escrow_not_refundable"
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_refund_rejects_unexpired_escrow():
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = rpc_with_escrow(created_at_slot=RECENT_SLOT)
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    with pytest.raises(VerificationFailed) as ei:
        client().refund_expired_escrow(ESCROW_ADDR)
    assert ei.value.reason == "escrow_not_expired"
    assert "sendTransaction" not in rpc.methods()


@respx.mock
def test_refund_created_status_allowed():
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = rpc_with_escrow(status="Created", created_at_slot=OLD_SLOT)
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    result = client().refund_expired_escrow(ESCROW_ADDR)
    assert result.refunded_lamports == PRICE


def test_current_slot_helper():
    import httpx

    class _Rpc:
        def call(self, method, params=None):
            assert method == "getSlot"
            assert params == [{"commitment": "confirmed"}]
            return 123

    assert current_slot(_Rpc()) == 123
    _ = httpx  # silence unused import when refactored
