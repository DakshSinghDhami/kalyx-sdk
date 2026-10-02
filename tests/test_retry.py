"""Retry policy tests: backoff, Retry-After honoring, and client integration."""

import time

import httpx
import pytest
import respx
from solders.keypair import Keypair

from kalyx_sdk import KalyxClient
from kalyx_sdk.errors import ChainError, GatewayUnavailable, RateLimited, ReplayRejected
from kalyx_sdk.retry import RetryPolicy, is_retryable, run_with_retries

from .helpers import (
    GATEWAY,
    RPC_URL,
    FakeRpc,
    challenge_body,
    config_body,
    no_context_body,
)

FAST = RetryPolicy(attempts=3, base_delay=0.0, max_delay=0.0, jitter=False)


def test_policy_delays_exponential_capped():
    p = RetryPolicy(attempts=5, base_delay=1.0, backoff=2.0, max_delay=4.0, jitter=False)
    assert p.delays() == [1.0, 2.0, 4.0, 4.0]


def test_retry_after_overrides_backoff():
    p = RetryPolicy(attempts=2, base_delay=100.0, max_delay=30.0, jitter=False)
    exc = RateLimited("slow down", retry_after=10.0)
    assert p.delay_for(0, exc) == 10.0
    # clamped to max_delay
    exc2 = RateLimited("slow down", retry_after=999.0)
    assert p.delay_for(0, exc2) == 30.0


def test_jitter_bounds():
    p = RetryPolicy(attempts=2, base_delay=10.0, max_delay=10.0, jitter=True)
    for _ in range(20):
        assert 5.0 <= p.delay_for(0) <= 10.0


def test_is_retryable_classification():
    assert is_retryable(GatewayUnavailable("x"))
    assert is_retryable(RateLimited("x"))
    assert is_retryable(ChainError("x", retryable=True))
    assert not is_retryable(ChainError("x", retryable=False))
    assert not is_retryable(ReplayRejected("x"))
    assert not is_retryable(ValueError("x"))


def test_run_with_retries_eventually_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise GatewayUnavailable("down")
        return "ok"

    assert run_with_retries(flaky, FAST) == "ok"
    assert len(calls) == 3


def test_run_with_retries_gives_up():
    def always_down():
        raise RateLimited("nope", retry_after=0)

    start = time.monotonic()
    with pytest.raises(RateLimited):
        run_with_retries(always_down, FAST)
    assert time.monotonic() - start < 1.0  # no real sleeping with zero delays


def test_run_with_retries_does_not_retry_permanent():
    calls = []

    def permanent():
        calls.append(1)
        raise ReplayRejected("used")

    with pytest.raises(ReplayRejected):
        run_with_retries(permanent, FAST)
    assert len(calls) == 1


# --- client integration ------------------------------------------------------


@respx.mock
def test_probe_retries_transient_503_then_succeeds():
    payer = Keypair.from_seed(bytes([9]) * 32)
    route = respx.post(f"{GATEWAY}/v1/query")
    route.side_effect = [
        httpx.Response(503, json={"error": "booting", "reason": "warming"}),
        httpx.Response(200, json=no_context_body()),
    ]
    client = KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL, keypair=payer, retry_policy=FAST)
    assert client.probe("q").status == "no_context_found"
    assert route.call_count == 2


@respx.mock
def test_probe_429_honors_retry_after():
    payer = Keypair.from_seed(bytes([9]) * 32)
    route = respx.post(f"{GATEWAY}/v1/query")
    route.side_effect = [
        httpx.Response(429, json={"error": "slow"}, headers={"Retry-After": "0"}),
        httpx.Response(200, json=no_context_body()),
    ]
    client = KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL, keypair=payer, retry_policy=FAST)
    assert client.probe("q").status == "no_context_found"
    assert route.call_count == 2


@respx.mock
def test_probe_gives_up_after_policy_exhausted():
    payer = Keypair.from_seed(bytes([9]) * 32)
    route = respx.post(f"{GATEWAY}/v1/query").respond(
        503, json={"error": "down", "reason": "offline"}
    )
    client = KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL, keypair=payer, retry_policy=FAST)
    with pytest.raises(GatewayUnavailable) as ei:
        client.probe("q")
    assert ei.value.reason == "offline"
    assert route.call_count == 3


@respx.mock
def test_payment_never_retried_after_send():
    """A transient retrieve failure retries, but a failed broadcast does not."""
    payer = Keypair.from_seed(bytes([9]) * 32)
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json={})  # malformed

    def failing_send(params):
        raise RuntimeError("blockhash not found")

    rpc = FakeRpc(sendTransaction=failing_send)
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL, keypair=payer, retry_policy=FAST)
    with pytest.raises(ChainError):
        client.query_and_retrieve("settlement")
    assert rpc.methods().count("sendTransaction") == 1  # never retried


@respx.mock
def test_retrieve_transient_error_is_retried():
    payer = Keypair.from_seed(bytes([9]) * 32)
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    from .helpers import retrieve_ok_body

    retrieve = respx.post(f"{GATEWAY}/v1/retrieve")
    retrieve.side_effect = [
        httpx.Response(503, json={"error": "verify busy", "reason": "verify_busy"}),
        httpx.Response(200, json=retrieve_ok_body()),
    ]
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL, keypair=payer, retry_policy=FAST)
    result = client.query_and_retrieve("settlement")
    assert result.status == "access_granted"
    assert retrieve.call_count == 2
    assert rpc.methods().count("sendTransaction") == 1  # paid exactly once
