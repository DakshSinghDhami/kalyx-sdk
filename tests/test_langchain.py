"""LangChain integration tests (langchain-core extra installed in dev)."""

import pytest
import respx
from solders.keypair import Keypair

from .helpers import (
    CONTENT,
    GATEWAY,
    PRICE,
    RPC_URL,
    FakeRpc,
    challenge_body,
    config_body,
    no_context_body,
    retrieve_ok_body,
)

lc = pytest.importorskip("langchain_core", reason="langchain extra not installed")

from langchain_core.documents import Document  # noqa: E402

from kalyx_sdk import KalyxClient  # noqa: E402
from kalyx_sdk.errors import BudgetExhausted  # noqa: E402
from kalyx_sdk.langchain import KalyxRetriever, kalyx_tool  # noqa: E402
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
def test_retriever_returns_document_with_citation():
    mock_happy()
    retriever = KalyxRetriever(client=make_client())
    docs = retriever.invoke("settlement")
    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, Document)
    assert doc.page_content == CONTENT
    assert doc.metadata["price_lamports"] == PRICE
    assert doc.metadata["escrow_address"]
    assert doc.metadata["trust"] == "untrusted"
    assert "citation" in doc.metadata


@respx.mock
def test_retriever_no_context_yields_nothing():
    respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    retriever = KalyxRetriever(client=make_client())
    assert retriever.invoke("irrelevant") == []


@respx.mock
def test_retriever_no_context_marker_document():
    respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    retriever = KalyxRetriever(client=make_client(), include_no_context_document=True)
    docs = retriever.invoke("irrelevant")
    assert len(docs) == 1
    assert docs[0].metadata["status"] == "no_context_found"
    assert docs[0].page_content == ""


@respx.mock
def test_retriever_propagates_budget_error():
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    retriever = KalyxRetriever(client=make_client(session_budget_lamports=0))
    with pytest.raises(BudgetExhausted):
        retriever.invoke("settlement")


@respx.mock
def test_tool_returns_content_with_citation():
    mock_happy()
    tool = kalyx_tool(make_client())
    out = tool.invoke({"query": "settlement"})
    assert CONTENT in out
    assert "Source:" in out


@respx.mock
def test_tool_no_context_is_polite_string():
    respx.post(f"{GATEWAY}/v1/query").respond(200, json=no_context_body())
    tool = kalyx_tool(make_client())
    out = tool.invoke({"query": "nothing"})
    assert "No relevant verified context" in out


@respx.mock
def test_tool_error_becomes_output_not_exception():
    respx.post(f"{GATEWAY}/v1/query").respond(402, json=challenge_body())
    respx.get(f"{GATEWAY}/v1/config").respond(200, json=config_body())
    rpc = FakeRpc()
    respx.post(RPC_URL).mock(side_effect=rpc.handler)
    tool = kalyx_tool(make_client(session_budget_lamports=0))
    out = tool.invoke({"query": "settlement"})
    assert "BudgetExhausted" in out
