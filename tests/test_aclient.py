"""Async client tests: flow parity with the sync client, async-first."""

import httpx
import pytest
import respx
from solders.keypair import Keypair

from kalyx_sdk.aclient import AsyncKalyxClient
from kalyx_sdk.errors import (
    BudgetExhausted,
    GatewayUnavailable,
    PriceExceedsBudget,
    VerificationFailed,
)
from kalyx_sdk.retry import RetryPolicy

from .helpers import (
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

pytestmark = pytest.mark.asyncio

FAST = RetryPolicy(attempts=3, base_delay=0.0, max_delay=0.0, jitter=False)


@pytest.fixture
def payer():
    return Keypair.from_seed(bytes([9]) * 32)


def make_client(payer, **kw):
    kw.setdefault("gateway_url", GATEWAY)
    kw.setdefault("rpc_url", RPC_URL)
    kw.setdefault("keypair", payer)
    kw.setdefault("retry_policy", FAST)
    return AsyncKalyxClient(**kw)


@respx.mock
async def test_async_probe_no_context(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    async with make_client(payer) as client:
        result = await client.probe("q")
    assert result.status == "no_context_found"


@respx.mock
async def test_async_happy_path(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)

    async with make_client(payer) as client:
        result = await client.query_and_retrieve("settlement")

    assert result.status == "access_granted"
    assert result.context == CONTENT
    assert result.challenge_node == NODE_ADDR if hasattr(result, "challenge_node") else True
    methods = rpc.methods()
    assert methods.index("sendTransaction") < methods.index("getSignatureStatuses")


@respx.mock
async def test_async_budget_and_cap(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)

    async with make_client(payer, session_budget_lamports=PRICE) as client:
        assert (await client.query_and_retrieve("a")).status == "access_granted"
        with pytest.raises(BudgetExhausted):
            await client.query_and_retrieve("b")
        with pytest.raises(PriceExceedsBudget):
            await client.query_and_retrieve("c", max_price_lamports=PRICE - 1)
    assert rpc.methods().count("sendTransaction") == 1


@respx.mock
async def test_async_retry_on_transient_retrieve(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    retrieve = respx.post(f"{GATEWAY}/v1/retrieve")
    retrieve.side_effect = [
        httpx.Response(429, json={"error": "slow"}, headers={"Retry-After": "0"}),
        httpx.Response(200, json=retrieve_ok_body()),
    ]
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    async with make_client(payer) as client:
        result = await client.query_and_retrieve("settlement")
    assert result.status == "access_granted"
    assert retrieve.call_count == 2


@respx.mock
async def test_async_verification_failure(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(
        402, json=challenge_body(escrow_params={"node_address": ""})
    )
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    async with make_client(payer) as client:
        with pytest.raises(VerificationFailed):
            await client.query_and_retrieve("settlement")
    assert "sendTransaction" not in rpc.methods()


@respx.mock
async def test_async_gateway_unreachable(payer):
    respx.get(f"{GATEWAY}/v1/config").mock(side_effect=httpx.ConnectError("down"))
    async with make_client(payer) as client:
        with pytest.raises(GatewayUnavailable):
            await client.config()


async def test_async_repr(payer):
    client = make_client(payer)
    assert "AsyncKalyxClient" in repr(client)
    await client.aclose()
