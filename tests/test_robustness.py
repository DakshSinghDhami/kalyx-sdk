"""Client robustness: confirm-before-retrieve ordering, commitment flapping,
unicode/oversized queries, HTTP-date Retry-After, and oversized but valid
payloads. All hermetic (respx fakes)."""

import json
import time

import httpx
import pytest
import respx
from solders.keypair import Keypair

from kalyx_sdk import KalyxClient
from kalyx_sdk.chain import GENESIS_MAINNET, assert_cluster_allowed
from kalyx_sdk.client import parse_retry_after
from kalyx_sdk.errors import (
    ChainError,
    ClusterMismatchError,
    GatewayUnavailable,
)
from kalyx_sdk.retry import RetryPolicy

from .helpers import (
    GATEWAY,
    RPC_URL,
    FakeRpc,
    challenge_body,
    config_body,
    no_context_body,
    retrieve_ok_body,
)

FAST = RetryPolicy(attempts=2, base_delay=0.0, max_delay=0.0, jitter=False)


@pytest.fixture
def payer():
    return Keypair.from_seed(bytes([9]) * 32)


def make_client(payer, **kw):
    kw.setdefault("gateway_url", GATEWAY)
    kw.setdefault("rpc_url", RPC_URL)
    kw.setdefault("keypair", payer)
    kw.setdefault("retry_policy", FAST)
    return KalyxClient(**kw)


def _wire_defaults():
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())


# --- confirm-before-retrieve ordering ------------------------------------------


@respx.mock
def test_retrieve_happens_only_after_confirmation(payer):
    """The funding tx must reach 'confirmed' before /v1/retrieve is called."""
    _wire_defaults()
    events: list[str] = []

    def flap(params):
        # first poll: still processed; second poll: confirmed
        events.append("poll")
        if events.count("poll") == 1:
            return {"value": [{"confirmationStatus": "processed", "err": None}]}
        return {"value": [{"confirmationStatus": "confirmed", "err": None}]}

    def on_retrieve(request):
        events.append("retrieve")
        return httpx.Response(200, json=retrieve_ok_body())

    respx.post(f"{GATEWAY}/v1/retrieve").mock(side_effect=on_retrieve)
    rpc = FakeRpc(getSignatureStatuses=flap)
    respx.post(RPC_URL).mock(side_effect=rpc.handler)

    client = make_client(payer)
    result = client.query_and_retrieve("settlement")

    assert result.status == "access_granted"
    assert events.count("poll") >= 2  # polled through 'processed'
    first_confirmed_poll = events.index("poll", events.index("poll") + 1)
    assert events.index("retrieve") > first_confirmed_poll


@respx.mock
def test_confirmation_timeout_no_retrieve_no_second_payment(payer):
    """An unconfirmable (e.g. expired-blockhash) tx: ChainError carries the
    signature, retrieve is never called, and the payment is not retried."""
    _wire_defaults()
    retrieve = respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc(getSignatureStatuses=lambda p: {"value": [None]})
    respx.post(RPC_URL).mock(side_effect=rpc.handler)

    client = make_client(payer, confirm_timeout=0.2)
    with pytest.raises(ChainError) as ei:
        client.query_and_retrieve("settlement")
    assert ei.value.signature == "funding-sig-111"
    assert retrieve.call_count == 0
    assert rpc.methods().count("sendTransaction") == 1  # never double-paid


@respx.mock
def test_commitment_flapping_is_tolerated(payer):
    """processed -> None -> transient transport error -> finalized succeeds."""
    _wire_defaults()
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    polls = {"n": 0}
    statuses = [
        {"confirmationStatus": "processed", "err": None},
        None,  # RPC lost sight of the tx
        "TRANSPORT",  # connection-level failure (retryable)
        {"confirmationStatus": "finalized", "err": None},
    ]

    def rpc_side_effect(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        if payload["method"] == "getSignatureStatuses":
            outcome = statuses[min(polls["n"], len(statuses) - 1)]
            polls["n"] += 1
            if outcome == "TRANSPORT":
                raise httpx.ConnectError("rpc flaked")
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": [outcome]}}
            )
        return rpc.handler(request)

    respx.post(RPC_URL).mock(side_effect=rpc_side_effect)
    client = make_client(payer)
    assert client.query_and_retrieve("settlement").status == "access_granted"
    assert polls["n"] == 4


# --- malformed / hostile transport payloads ------------------------------------


@respx.mock
def test_probe_200_wrong_content_type_is_unavailable_not_crash(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(
        200,
        content=b"<html><body>proxy error</body></html>",
        headers={"Content-Type": "text/html"},
    )
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable):
        client.probe("settlement")


@respx.mock
def test_probe_200_truncated_json_is_unavailable(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(200, content=b'{"status": "no_cont')
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable):
        client.probe("settlement")


@respx.mock
def test_retrieve_200_non_json_after_payment(payer):
    """A paid retrieve answering junk must raise a typed error, not crash."""
    _wire_defaults()
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, content=b"\x89PNG\r\n")
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable):
        client.query_and_retrieve("settlement")


@respx.mock
def test_config_200_non_dict_is_unavailable(payer):
    respx.get(f"{GATEWAY}/v1/config").respond(200, content=b"[1,2,3]")
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable):
        client.config()


# --- redirects are never followed (no credential leak) -------------------------


@respx.mock
def test_probe_redirect_to_other_host_not_followed(payer):
    respx.post(f"{GATEWAY}/v1/query").respond(
        302, headers={"Location": "http://evil.invalid/v1/query"}
    )
    evil = respx.post("http://evil.invalid/v1/query").respond(200, json=no_context_body())
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable):
        client.probe("settlement")
    assert evil.call_count == 0


@respx.mock
def test_retrieve_redirect_after_payment_not_followed(payer):
    _wire_defaults()
    respx.post(f"{GATEWAY}/v1/retrieve").respond(
        302, headers={"Location": "http://evil.invalid/steal"}
    )
    evil = respx.post("http://evil.invalid/steal").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    client = make_client(payer)
    with pytest.raises(GatewayUnavailable):
        client.query_and_retrieve("settlement")
    assert evil.call_count == 0  # the payment credential never left the host


# --- oversized but valid payloads -------------------------------------------------


@respx.mock
def test_huge_node_page_parses(payer):
    items = [
        {
            "node_id": f"{i:064x}",
            "title": f"node {i} " + "x" * 100,
            "price_lamports": 20_000,
            "chunk_count": 1,
            "tags": ["a", "b"],
        }
        for i in range(5000)
    ]
    body = {"items": items, "total": 5000, "next_cursor": None}
    respx.get(f"{GATEWAY}/v1/nodes").respond(200, content=json.dumps(body).encode())
    client = make_client(payer)
    page = client.list_nodes()
    assert len(page.items) == 5000
    assert page.total == 5000


# --- query validation -----------------------------------------------------------


@respx.mock
def test_unicode_query_512_chars_passes(payer):
    route = respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    client = make_client(payer)
    query = "éscrow 決済 🤖 " * 40  # multibyte; 520 chars -> trim to exactly 512
    query = query[:512]
    result = client.probe(query)
    assert result.status == "no_context_found"
    sent = json.loads(route.calls[0].request.content.decode())
    assert sent["query"] == query


@respx.mock
def test_64kb_query_rejected_before_any_http(payer):
    route = respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    client = make_client(payer)
    with pytest.raises(ValueError):
        client.probe("x" * 65536)
    assert route.call_count == 0
    with pytest.raises(ValueError):
        client.query_and_retrieve("y" * 65536)
    assert route.call_count == 0


# --- Retry-After parsing --------------------------------------------------------


def test_retry_after_http_date_future_and_past():
    from email.utils import formatdate

    future = formatdate(time.time() + 30, usegmt=True)
    past = formatdate(time.time() - 3600, usegmt=True)  # clock-skewed/stale date
    h_future = httpx.Headers({"retry-after": future})
    h_past = httpx.Headers({"retry-after": past})
    assert parse_retry_after(h_future) is not None
    assert 0 < parse_retry_after(h_future) <= 30.0
    assert parse_retry_after(h_past) == 0.0  # clamped, never negative


def test_retry_after_numeric_body_fallback():
    assert parse_retry_after(httpx.Headers(), {"retry_after": 7}) == 7.0
    assert parse_retry_after(httpx.Headers(), {"retry_after": "7"}) is None
    assert parse_retry_after(httpx.Headers(), {"retry_after": True}) is None


# --- mainnet guard message -------------------------------------------------------


def test_mainnet_refusal_names_mainnet():
    with pytest.raises(ClusterMismatchError) as ei:
        assert_cluster_allowed(GENESIS_MAINNET, "https://api.mainnet-beta.solana.com")
    msg = str(ei.value).lower()
    assert "mainnet" in msg
    # The dedicated mainnet branch (not the generic fail-closed one):
    assert "devnet-only" in msg and "no mainnet mode" in msg
