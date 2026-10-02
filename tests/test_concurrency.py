"""Concurrency guards: one client, one budget, parallel calls.

Two parallel ``query_and_retrieve`` calls sharing a client (and therefore one
``BudgetTracker``) must never *together* exceed the session budget: the
check-then-pay sequence has to be atomic with respect to the budget, or a
race window between the pre-payment check and the post-payment record lets
both calls through.
"""

import asyncio
import threading
import time

import httpx
import pytest
import respx
from solders.keypair import Keypair

from kalyx_sdk import KalyxClient
from kalyx_sdk.aclient import AsyncKalyxClient
from kalyx_sdk.errors import BudgetExhausted
from kalyx_sdk.retry import RetryPolicy

from .helpers import (
    GATEWAY,
    PRICE,
    RPC_URL,
    FakeRpc,
    challenge_body,
    config_body,
    retrieve_ok_body,
)

FAST = RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0, jitter=False)


def _slow_blockhash(delay):
    """getLatestBlockhash handler that widens the check->pay race window."""

    def handler(params):
        time.sleep(delay)
        return {"value": {"blockhash": "4uQeVj5tqViQh7yWWGStvkEG1Zmhx6uasJtWCJziofM"}}

    return handler


@respx.mock
def test_two_threads_never_exceed_session_budget():
    payer = Keypair.from_seed(bytes([9]) * 32)
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc(getLatestBlockhash=_slow_blockhash(0.05))
    respx.post(RPC_URL).mock(side_effect=rpc.handler)

    client = KalyxClient(
        gateway_url=GATEWAY,
        rpc_url=RPC_URL,
        keypair=payer,
        session_budget_lamports=PRICE,  # exactly one payment fits
        retry_policy=FAST,
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        barrier.wait(timeout=5)
        try:
            client.query_and_retrieve("settlement")
            outcomes.append("ok")
        except BudgetExhausted:
            outcomes.append("budget")
        except Exception as exc:
            outcomes.append(f"unexpected:{type(exc).__name__}:{exc}")

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(30)
    t2.join(30)

    assert sorted(outcomes) == ["budget", "ok"], outcomes
    assert client.spent_lamports == PRICE
    assert rpc.methods().count("sendTransaction") == 1


@respx.mock
def test_two_threads_double_budget_both_fit():
    payer = Keypair.from_seed(bytes([9]) * 32)
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc(getLatestBlockhash=_slow_blockhash(0.02))
    respx.post(RPC_URL).mock(side_effect=rpc.handler)

    client = KalyxClient(
        gateway_url=GATEWAY,
        rpc_url=RPC_URL,
        keypair=payer,
        session_budget_lamports=2 * PRICE,
        retry_policy=FAST,
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        barrier.wait(timeout=5)
        try:
            client.query_and_retrieve("settlement")
            outcomes.append("ok")
        except Exception as exc:
            outcomes.append(f"unexpected:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert outcomes == ["ok", "ok"], outcomes
    assert client.spent_lamports == 2 * PRICE
    assert rpc.methods().count("sendTransaction") == 2


# --- async variant ------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_async_two_tasks_never_exceed_session_budget():
    payer = Keypair.from_seed(bytes([9]) * 32)
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())

    rpc = FakeRpc()

    async def rpc_handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        payload = _json.loads(request.content.decode())
        if payload["method"] == "getLatestBlockhash":
            await asyncio.sleep(0.05)  # widen the check->pay window
        return rpc.handler(request)

    respx.post(RPC_URL).mock(side_effect=rpc_handler)

    client = AsyncKalyxClient(
        gateway_url=GATEWAY,
        rpc_url=RPC_URL,
        keypair=payer,
        session_budget_lamports=PRICE,
        retry_policy=FAST,
    )

    async def worker():
        try:
            await client.query_and_retrieve("settlement")
            return "ok"
        except BudgetExhausted:
            return "budget"
        except Exception as exc:
            return f"unexpected:{type(exc).__name__}:{exc}"

    results = await asyncio.gather(worker(), worker())
    assert sorted(results) == ["budget", "ok"], results
    assert client.spent_lamports == PRICE
    assert rpc.methods().count("sendTransaction") == 1
    await client.aclose()
