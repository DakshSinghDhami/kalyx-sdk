"""LlamaIndex integration tests (llama-index-core extra installed in dev)."""

import pytest
import respx
from solders.keypair import Keypair

from .helpers import (
    CONTENT,
    GATEWAY,
    RPC_URL,
    FakeRpc,
    challenge_body,
    config_body,
    no_context_body,
    retrieve_ok_body,
)

li = pytest.importorskip("llama_index.core", reason="llamaindex extra not installed")

from llama_index.core.schema import QueryBundle  # noqa: E402

from kalyx_sdk import KalyxClient  # noqa: E402
from kalyx_sdk.llamaindex import KalyxLlamaIndexRetriever, kalyx_query_tool  # noqa: E402
from kalyx_sdk.retry import RetryPolicy  # noqa: E402

FAST = RetryPolicy(attempts=1, base_delay=0.0, max_delay=0.0, jitter=False)
PAYER = Keypair.from_seed(bytes([9]) * 32)


def make_client(**kw):
    kw.setdefault("retry_policy", FAST)
    return KalyxClient(gateway_url=GATEWAY, rpc_url=RPC_URL, keypair=PAYER, **kw)


def mock_happy():
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    respx.post(f"{GATEWAY}/v1/retrieve").respond(200, json=retrieve_ok_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    return rpc


@respx.mock
def test_retriever_returns_node_with_metadata():
    mock_happy()
    retriever = KalyxLlamaIndexRetriever(client=make_client())
    nodes = retriever.retrieve(QueryBundle(query_str="settlement"))
    assert len(nodes) == 1
    node = nodes[0]
    assert node.node.text == CONTENT
    assert node.node.metadata["trust"] == "untrusted"
    assert node.node.metadata["escrow_address"]
    assert "citation" in node.node.metadata
    assert node.score == pytest.approx(0.71)


@respx.mock
def test_retriever_no_context_returns_empty():
    respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    retriever = KalyxLlamaIndexRetriever(client=make_client())
    assert retriever.retrieve(QueryBundle(query_str="irrelevant")) == []


@respx.mock
def test_query_tool_returns_content_with_citation():
    mock_happy()
    tool = kalyx_query_tool(make_client())
    out = tool.call(query="settlement")
    text = out.content if hasattr(out, "content") else str(out)
    assert CONTENT in text
    assert "Source:" in text


@respx.mock
def test_query_tool_error_becomes_output():
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    tool = kalyx_query_tool(make_client(session_budget_lamports=0))
    out = tool.call(query="settlement")
    text = out.content if hasattr(out, "content") else str(out)
    assert "BudgetExhausted" in text
