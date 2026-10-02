"""Node catalog endpoint tests (sync + async)."""

import pytest
import respx
from solders.keypair import Keypair

from kalyx_sdk import KalyxClient
from kalyx_sdk.aclient import AsyncKalyxClient
from kalyx_sdk.errors import GatewayUnavailable, VerificationFailed
from kalyx_sdk.retry import RetryPolicy

from .helpers import GATEWAY, RPC_URL

FAST = RetryPolicy(attempts=1, base_delay=0.0, max_delay=0.0, jitter=False)

NODE_ID = "ab" * 32

SUMMARY = {
    "node_id": NODE_ID,
    "content_hash": NODE_ID,
    "citation_hash": "c" * 64,
    "title": "Escrow design notes",
    "domain": "example.com",
    "category": "docs",
    "publisher_wallet": "Pub111111111111111111111111111111111111",
    "author_wallet": "Auth11111111111111111111111111111111111",
    "price_lamports": 20_000,
    "chunk_count": 3,
    "chunk_ids": ["a" * 16, "b" * 16, "c" * 16],
    "free_preview": "Escrows lock lamports until settlement.",
    "published_at": 1_759_000_000,
    "license": "CC-BY-4.0",
    "tags": ["solana", "escrow"],
    "registered": True,
}

PAGE = {"items": [SUMMARY], "total": 1, "next_cursor": None}
DETAIL = {
    **SUMMARY,
    "verification_report": {
        "recomputed_at": 1_759_000_100,
        "chunk_ids_match": True,
        "chunks_checked": 3,
    },
}


def client():
    return KalyxClient(
        gateway_url=GATEWAY,
        rpc_url=RPC_URL,
        keypair=Keypair.from_seed(bytes([9]) * 32),
        retry_policy=FAST,
    )


@respx.mock
def test_list_nodes_page():
    route = respx.get(f"{GATEWAY}/v1/nodes").respond(200, json=PAGE)
    page = client().list_nodes(q="escrow", license="CC-BY-4.0", limit=10)
    assert page.total == 1
    assert page.next_cursor is None
    item = page.items[0]
    assert item.node_id == NODE_ID
    assert item.price_lamports == 20_000
    assert item.tags == ("solana", "escrow")
    assert item.registered is True
    qs = dict(route.calls.last.request.url.params)
    assert qs == {"q": "escrow", "license": "CC-BY-4.0", "limit": "10"}


@respx.mock
def test_list_nodes_pagination_cursor():
    respx.get(f"{GATEWAY}/v1/nodes").respond(
        200, json={"items": [SUMMARY], "total": 2, "next_cursor": 1}
    )
    page = client().list_nodes(cursor=0)
    assert page.next_cursor == 1


@respx.mock
def test_node_detail_with_report():
    respx.get(f"{GATEWAY}/v1/nodes/{NODE_ID}").respond(200, json=DETAIL)
    detail = client().node_detail(NODE_ID)
    assert detail.summary.title == "Escrow design notes"
    assert detail.verification_report["chunk_ids_match"] is True


@respx.mock
def test_node_detail_not_found():
    respx.get(f"{GATEWAY}/v1/nodes/nope").respond(404, json={"error": "node not found"})
    with pytest.raises(VerificationFailed) as ei:
        client().node_detail("nope")
    assert ei.value.reason == "not_found"


@respx.mock
def test_node_detail_empty_id():
    with pytest.raises(ValueError):
        client().node_detail("  ")


@respx.mock
def test_list_nodes_malformed():
    respx.get(f"{GATEWAY}/v1/nodes").respond(200, json={"items": "nope"})
    with pytest.raises(GatewayUnavailable):
        client().list_nodes()


@pytest.mark.asyncio
@respx.mock
async def test_async_list_nodes_and_detail():
    respx.get(f"{GATEWAY}/v1/nodes").respond(200, json=PAGE)
    respx.get(f"{GATEWAY}/v1/nodes/{NODE_ID}").respond(200, json=DETAIL)
    async with AsyncKalyxClient(
        gateway_url=GATEWAY,
        rpc_url=RPC_URL,
        keypair=Keypair.from_seed(bytes([9]) * 32),
        retry_policy=FAST,
    ) as c:
        page = await c.list_nodes()
        assert page.items[0].license == "CC-BY-4.0"
        detail = await c.node_detail(NODE_ID)
        assert detail.verification_report["chunks_checked"] == 3
