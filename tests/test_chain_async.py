"""Async chain helper tests: AsyncRpcClient + the a* wrappers."""

import base64
import json

import httpx
import pytest
import respx

from kalyx_sdk.chain import (
    AsyncRpcClient,
    abalance,
    acurrent_slot,
    aescrow_state,
    agenesis_hash,
    aget_account_info,
    alatest_blockhash,
    aminimum_rent,
    anode_state,
    asend_transaction,
    asignature_status,
    await_for_confirmation,
)
from kalyx_sdk.errors import ChainError
from kalyx_sdk.escrow import DEFAULT_PROGRAM_ID

from .helpers import (
    BLOCKHASH,
    DEVNET_GENESIS,
    NODE_ADDR,
    RENT,
    RPC_URL,
    escrow_account_b64,
    node_account_b64,
)

pytestmark = pytest.mark.asyncio

ADDR = "11111111111111111111111111111112"


def rpc_handler(results: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        method = payload["method"]
        if method not in results:
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "no fake"}}
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": results[method]})

    return handler


def make_rpc():
    return AsyncRpcClient(httpx.AsyncClient(), RPC_URL)


@respx.mock
async def test_async_genesis_balance_blockhash():
    respx.post(RPC_URL).mock(
        side_effect=rpc_handler(
            {
                "getGenesisHash": DEVNET_GENESIS,
                "getBalance": {"value": 42},
                "getLatestBlockhash": {"value": {"blockhash": BLOCKHASH}},
                "getMinimumBalanceForRentExemption": RENT,
                "getSlot": 777,
            }
        )
    )
    rpc = make_rpc()
    assert await agenesis_hash(rpc) == DEVNET_GENESIS
    assert await abalance(rpc, ADDR) == 42
    assert await alatest_blockhash(rpc) == BLOCKHASH
    assert await aminimum_rent(rpc, 130) == RENT
    assert await acurrent_slot(rpc) == 777
    await rpc._http.aclose()


@respx.mock
async def test_async_account_and_decoders():
    respx.post(RPC_URL).mock(
        side_effect=rpc_handler(
            {
                "getAccountInfo": {
                    "value": {
                        "owner": DEFAULT_PROGRAM_ID,
                        "data": [node_account_b64(), "base64"],
                    }
                }
            }
        )
    )
    rpc = make_rpc()
    info = await aget_account_info(rpc, NODE_ADDR)
    assert info["owner"] == DEFAULT_PROGRAM_ID
    node = await anode_state(rpc, NODE_ADDR, program_id=DEFAULT_PROGRAM_ID)
    assert node is not None and node.is_active
    await rpc._http.aclose()


@respx.mock
async def test_async_escrow_state_roundtrip():
    from solders.keypair import Keypair

    consumer = str(Keypair.from_seed(bytes([9]) * 32).pubkey())
    data = escrow_account_b64(consumer=consumer, status="Funded")
    respx.post(RPC_URL).mock(
        side_effect=rpc_handler(
            {"getAccountInfo": {"value": {"owner": "p", "data": [data, "base64"]}}}
        )
    )
    rpc = make_rpc()
    state = await aescrow_state(rpc, "someaddr", program_id="p")
    assert state is not None
    assert state.status == "Funded"
    assert state.consumer == consumer
    assert state.amount_lamports == 20_000
    await rpc._http.aclose()


@respx.mock
async def test_async_send_and_confirm():
    respx.post(RPC_URL).mock(
        side_effect=rpc_handler(
            {
                "sendTransaction": "sig-abc",
                "getSignatureStatuses": {
                    "value": [{"confirmationStatus": "finalized", "err": None}]
                },
            }
        )
    )
    rpc = make_rpc()
    sig = await asend_transaction(rpc, base64.b64encode(b"tx").decode())
    assert sig == "sig-abc"
    st = await asignature_status(rpc, sig)
    assert st["confirmationStatus"] == "finalized"
    await await_for_confirmation(rpc, sig, timeout=5.0, interval=0.01)
    await rpc._http.aclose()


@respx.mock
async def test_async_confirmation_timeout():
    respx.post(RPC_URL).mock(side_effect=rpc_handler({"getSignatureStatuses": {"value": [None]}}))
    rpc = make_rpc()
    with pytest.raises(ChainError):
        await await_for_confirmation(rpc, "sig", timeout=0.05, interval=0.01)
    await rpc._http.aclose()


@respx.mock
async def test_async_rpc_error_raises_chain_error():
    respx.post(RPC_URL).mock(
        side_effect=rpc_handler({})  # no handler -> JSON-RPC error
    )
    rpc = make_rpc()
    with pytest.raises(ChainError):
        await abalance(rpc, ADDR)
    await rpc._http.aclose()


@respx.mock
async def test_async_malformed_results():
    respx.post(RPC_URL).mock(
        side_effect=rpc_handler(
            {
                "getBalance": {"value": "not-an-int"},
                "getSlot": "not-an-int",
                "getLatestBlockhash": {"value": {}},
            }
        )
    )
    rpc = make_rpc()
    with pytest.raises(ChainError):
        await abalance(rpc, ADDR)
    with pytest.raises(ChainError):
        await acurrent_slot(rpc)
    with pytest.raises(ChainError):
        await alatest_blockhash(rpc)
    await rpc._http.aclose()


@respx.mock
async def test_async_missing_escrow_returns_none():
    respx.post(RPC_URL).mock(side_effect=rpc_handler({"getAccountInfo": {"value": None}}))
    rpc = make_rpc()
    assert await aescrow_state(rpc, "x", program_id="p") is None
    assert await anode_state(rpc, "x", program_id="p") is None
    await rpc._http.aclose()
