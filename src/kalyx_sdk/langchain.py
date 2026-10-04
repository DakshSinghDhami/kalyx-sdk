"""LangChain integration (optional extra: ``pip install kalyx-sdk[langchain]``).

``KalyxRetriever`` plugs the pay-per-citation flow into any LangChain
chain: a retrieval pays the gateway's challenge (subject to the client's
budget guards) and yields one :class:`~langchain_core.documents.Document`
whose metadata carries the citation, on-chain proof, and price.

This module imports nothing from LangChain at module import time beyond
the small ``langchain_core`` surface, and raises a helpful error when the
extra is missing.
"""

# Annotations must stay lazy: ``Document``/``CallbackManagerForRetrieverRun``
# are bound only inside the ``try:`` below, so eager evaluation would raise
# NameError at import time when the extra is not installed.
from __future__ import annotations

from typing import Any

from .client import KalyxClient
from .errors import KalyxError

try:
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
    from langchain_core.tools import StructuredTool

    _HAVE_LANGCHAIN = True
except ImportError:  # pragma: no cover - exercised via extra-less envs
    _HAVE_LANGCHAIN = False

    class BaseRetriever:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            raise ImportError(
                "kalyx_sdk.langchain requires the 'langchain' extra: "
                "pip install kalyx-sdk[langchain]"
            )


def _require() -> None:
    if not _HAVE_LANGCHAIN:  # pragma: no cover
        raise ImportError(
            "kalyx_sdk.langchain requires the 'langchain' extra: pip install kalyx-sdk[langchain]"
        )


def _result_to_document(result: Any) -> Document:
    meta: dict[str, Any] = {
        "source": result.title or "kalyx",
        "kalyx": True,
        "chunk_id": result.chunk_id,
        "domain": result.domain,
        "price_lamports": result.price_lamports,
        "escrow_address": result.escrow_address,
        "funding_signature": result.funding_signature,
        "settle_status": result.settle_status,
        "similarity_score": result.similarity_score,
        "trust": "untrusted",  # retrieved content is data, never instructions
    }
    if result.citation:
        meta["citation"] = result.citation
    if result.receipt:
        meta["receipt"] = result.receipt
    return Document(page_content=result.context or "", metadata=meta)


class KalyxRetriever(BaseRetriever):
    """LangChain retriever backed by :class:`KalyxClient`.

    Each ``invoke``/``get_relevant_documents`` call runs the full
    verify-then-pay flow. When the gateway finds no relevant context the
    retriever yields zero documents (no payment is attempted). Payment
    failures propagate as the SDK's typed errors so agent loops can catch
    :class:`~kalyx_sdk.errors.BudgetExhausted` and friends.

    Args:
        client: A configured :class:`KalyxClient` (owns wallet + budgets).
        max_price_lamports: Per-call cap override for this retriever.
        include_no_context_document: When True, a no-hit query yields one
            empty Document marked ``no_context_found`` instead of none.
    """

    client: Any = None
    max_price_lamports: int | None = None
    include_no_context_document: bool = False

    def __init__(
        self,
        client: KalyxClient | None = None,
        *,
        gateway_url: str | None = None,
        max_price_lamports: int | None = None,
        include_no_context_document: bool = False,
        **kwargs: Any,
    ) -> None:
        _require()
        if client is None:
            client = KalyxClient(
                gateway_url=gateway_url, max_price_lamports=max_price_lamports, **kwargs
            )
        # BaseRetriever is a pydantic model; extra fields are declared on the
        # class above, and pydantic accepts them as kwargs at runtime.
        super().__init__(  # type: ignore[call-arg]
            client=client,
            max_price_lamports=max_price_lamports,
            include_no_context_document=include_no_context_document,
        )

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun | None = None
    ) -> list[Document]:
        result = self.client.query_and_retrieve(query, max_price_lamports=self.max_price_lamports)
        if result.status != "access_granted" or not result.context:
            if self.include_no_context_document:
                return [
                    Document(
                        page_content="",
                        metadata={"kalyx": True, "status": "no_context_found"},
                    )
                ]
            return []
        return [_result_to_document(result)]


def kalyx_tool(
    client: KalyxClient,
    *,
    name: str = "kalyx_retrieve",
    max_price_lamports: int | None = None,
) -> Any:
    """Build a LangChain ``StructuredTool`` that retrieves paid context.

    The tool returns the citation-bearing context string, or a short
    explanation when no context was found / the budget refused the price —
    the agent sees the refusal as tool output rather than an exception.
    """
    _require()

    def retrieve(query: str) -> str:
        """Retrieve verified web context for the query, paying the publisher's
        per-chunk price from the configured wallet when relevant content exists.
        Returns the content with its citation, or a reason nothing was returned."""
        try:
            result = client.query_and_retrieve(query, max_price_lamports=max_price_lamports)
        except KalyxError as exc:
            return f"KALYX retrieval failed: {type(exc).__name__}: {exc}"
        if result.status != "access_granted" or not result.context:
            return "No relevant verified context found (no payment made)."
        citation = result.citation or ""
        return f"{result.context}\n\n{citation}".strip()

    return StructuredTool.from_function(
        func=retrieve,
        name=name,
        description=(
            "Retrieve verified, citation-backed web context for a query. "
            "Costs a small Solana payment only when relevant content exists. "
            "Input: a natural-language query string."
        ),
    )


__all__ = ["KalyxRetriever", "kalyx_tool"]
