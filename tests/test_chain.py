"""Hermetic tests for chain.py: RPC parsing, cluster guard, confirmation polling."""

import base64

import httpx
import pytest
import respx

from kalyx_sdk.chain import (
    ESCROW_DISCRIMINATOR,
    GENESIS_DEVNET,
    GENESIS_MAINNET,
    RpcClient,
    assert_cluster_allowed,
    balance,
    classify_cluster,
    decode_escrow,
    escrow_state,
    latest_blockhash,
    minimum_rent,
    send_transaction,
    signature_status,
    wait_for_confirmation,
)
from kalyx_sdk.errors import ChainError, ClusterMismatchError, GatewayUnavailable

RPC = "https://rpc.test"


def rpc_client():
    return RpcClient(httpx.Client(), RPC)


def jsonrpc_ok(result):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


@respx.mock
def test_balance():
    respx.post(RPC).mock(return_value=jsonrpc_ok({"value": 12345}))
    assert balance(rpc_client(), "addr") == 12345


@respx.mock
def test_rpc_error_becomes_chain_error():
    respx.post(RPC).mock(return_value=httpx.Response(200, json={"error": {"message": "boom"}}))
    with pytest.raises(ChainError, match="boom"):
        balance(rpc_client(), "addr")


@respx.mock
def test_rpc_http_500_is_retryable():
    respx.post(RPC).mock(return_value=httpx.Response(500, text="oops"))
    with pytest.raises(ChainError) as ei:
        balance(rpc_client(), "addr")
    assert ei.value.retryable


@respx.mock
def test_rpc_http_400_not_retryable():
    respx.post(RPC).mock(return_value=httpx.Response(400, text="bad"))
    with pytest.raises(ChainError) as ei:
        balance(rpc_client(), "addr")
    assert not ei.value.retryable


@respx.mock
def test_rpc_transport_error_is_retryable():
    respx.post(RPC).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ChainError) as ei:
        balance(rpc_client(), "addr")
    assert ei.value.retryable


@respx.mock
def test_rpc_non_json():
    respx.post(RPC).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(ChainError):
        balance(rpc_client(), "addr")


@respx.mock
def test_latest_blockhash():
    respx.post(RPC).mock(return_value=jsonrpc_ok({"value": {"blockhash": "bh123"}}))
    assert latest_blockhash(rpc_client()) == "bh123"


@respx.mock
def test_minimum_rent():
    respx.post(RPC).mock(return_value=jsonrpc_ok(1_795_680))
    assert minimum_rent(rpc_client(), 130) == 1_795_680


def test_classify_cluster():
    assert classify_cluster(GENESIS_MAINNET) == "mainnet"
    assert classify_cluster(GENESIS_DEVNET) == "devnet"
    assert classify_cluster(None, "http://127.0.0.1:8899") == "localnet"
    assert classify_cluster(None, "https://api.devnet.solana.com") == "unknown"
    assert classify_cluster("SomeOtherHash") == "unknown"


def test_mainnet_refused():
    with pytest.raises(ClusterMismatchError):
        assert_cluster_allowed(GENESIS_MAINNET)


def test_unknown_cluster_refused():
    with pytest.raises(ClusterMismatchError):
        assert_cluster_allowed("SomeUnknownHash", "https://rpc.example")


def test_devnet_and_localnet_allowed():
    assert assert_cluster_allowed(GENESIS_DEVNET) == "devnet"
    assert assert_cluster_allowed(None, "http://localhost:8899") == "localnet"


def _escrow_account_b64() -> str:
    raw = bytearray()
    raw += ESCROW_DISCRIMINATOR
    raw += b"\x01" * 32  # consumer
    raw += b"\x02" * 32  # node
    raw += (20_000).to_bytes(8, "little")
    raw += b"\x03" * 32  # query_hash
    raw += bytes([1])  # Funded
    raw += (1_700_000_000).to_bytes(8, "little", signed=True)
    raw += (123_456).to_bytes(8, "little")
    raw += bytes([254])
    return base64.b64encode(bytes(raw)).decode()


def test_decode_escrow_layout():
    raw = base64.b64decode(_escrow_account_b64())
    st = decode_escrow(raw, address="escrowaddr")
    assert st.amount_lamports == 20_000
    assert st.status == "Funded"
    assert st.created_at == 1_700_000_000
    assert st.created_at_slot == 123_456
    assert st.bump == 254
    assert st.address == "escrowaddr"
    assert len(st.consumer) >= 32  # base58 of 32 bytes
    assert st.query_hash == b"\x03" * 32


def test_decode_escrow_rejects_short():
    with pytest.raises(ValueError):
        decode_escrow(b"\x00" * 10)


def test_decode_escrow_rejects_wrong_discriminator():
    raw = bytearray(base64.b64decode(_escrow_account_b64()))
    raw[0] ^= 0xFF
    with pytest.raises(ValueError):
        decode_escrow(bytes(raw))


@respx.mock
def test_escrow_state_reads_account():
    respx.post(RPC).mock(
        return_value=jsonrpc_ok(
            {
                "value": {
                    "owner": "GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2",
                    "data": [_escrow_account_b64(), "base64"],
                }
            }
        )
    )
    st = escrow_state(
        rpc_client(), "escrowaddr", program_id="GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2"
    )
    assert st is not None and st.funded


@respx.mock
def test_escrow_state_missing_returns_none():
    respx.post(RPC).mock(return_value=jsonrpc_ok({"value": None}))
    assert (
        escrow_state(rpc_client(), "x", program_id="GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2")
        is None
    )


@respx.mock
def test_escrow_state_wrong_owner_raises():
    respx.post(RPC).mock(
        return_value=jsonrpc_ok({"value": {"owner": "Other", "data": ["", "base64"]}})
    )
    with pytest.raises(GatewayUnavailable):
        escrow_state(rpc_client(), "x", program_id="GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2")


@respx.mock
def test_send_transaction():
    respx.post(RPC).mock(return_value=jsonrpc_ok("sig123"))
    assert send_transaction(rpc_client(), "dHg=") == "sig123"


@respx.mock
def test_wait_for_confirmation_success():
    respx.post(RPC).mock(
        return_value=jsonrpc_ok({"value": [{"confirmationStatus": "confirmed", "err": None}]})
    )
    wait_for_confirmation(rpc_client(), "sig", timeout=5, interval=0.01)


@respx.mock
def test_wait_for_confirmation_onchain_failure():
    respx.post(RPC).mock(
        return_value=jsonrpc_ok({"value": [{"confirmationStatus": None, "err": {"ix": 1}}]})
    )
    with pytest.raises(ChainError) as ei:
        wait_for_confirmation(rpc_client(), "sig", timeout=5, interval=0.01)
    assert ei.value.signature == "sig"


@respx.mock
def test_wait_for_confirmation_timeout():
    respx.post(RPC).mock(return_value=jsonrpc_ok({"value": [None]}))
    with pytest.raises(ChainError, match="not confirmed"):
        wait_for_confirmation(rpc_client(), "sig", timeout=0.05, interval=0.01)


@respx.mock
def test_wait_for_confirmation_tolerates_transient_then_success():
    route = respx.post(RPC)
    route.side_effect = [
        httpx.Response(500, text="flaky"),
        jsonrpc_ok({"value": [{"confirmationStatus": "finalized", "err": None}]}),
    ]
    wait_for_confirmation(rpc_client(), "sig", timeout=5, interval=0.01)


@respx.mock
def test_wait_for_confirmation_non_retryable_raises():
    respx.post(RPC).mock(return_value=httpx.Response(200, json={"error": {"message": "hard fail"}}))
    with pytest.raises(ChainError, match="hard fail"):
        wait_for_confirmation(rpc_client(), "sig", timeout=5, interval=0.01)


@respx.mock
def test_signature_status_none_when_unseen():
    respx.post(RPC).mock(return_value=jsonrpc_ok({"value": [None]}))
    assert signature_status(rpc_client(), "sig") is None
