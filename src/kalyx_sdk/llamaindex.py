"""LlamaIndex integration (optional extra: ``pip install kalyx-sdk[llamaindex]``).

``KalyxLlamaIndexRetriever`` plugs the pay-per-citation flow into
LlamaIndex query engines; ``kalyx_query_tool``/``KalyxToolSpec`` expose it
to LlamaIndex agents. Retrieved text is data, never instructions — nodes
carry ``trust=untrusted`` metadata.
"""

from typing import Any

from .client import KalyxClient
from .errors import KalyxError

try:
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.schema import NodeWithScore, TextNode
    from llama_index.core.tools import FunctionTool
    from llama_index.core.tools.tool_spec.base import BaseToolSpec

    _HAVE_LLAMAINDEX = True
except ImportError:  # pragma: no cover - exercised via extra-less envs
    _HAVE_LLAMAINDEX = False

    class BaseRetriever:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **k: Any) -> None:
            raise ImportError(
                "kalyx_sdk.llamaindex requires the 'llamaindex' extra: "
                "pip install kalyx-sdk[llamaindex]"
            )

    class BaseToolSpec:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **k: Any) -> None:
            raise ImportError(
                "kalyx_sdk.llamaindex requires the 'llamaindex' extra: "
                "pip install kalyx-sdk[llamaindex]"
            )


def _require() -> None:
    if not _HAVE_LLAMAINDEX:  # pragma: no cover
        raise ImportError(
            "kalyx_sdk.llamaindex requires the 'llamaindex' extra: "
            "pip install kalyx-sdk[llamaindex]"
        )


def _result_to_node(result: Any) -> Any:
    meta: dict[str, Any] = {
        "kalyx": True,
        "chunk_id": result.chunk_id,
        "domain": result.domain,
        "price_lamports": result.price_lamports,
        "escrow_address": result.escrow_address,
        "funding_signature": result.funding_signature,
        "settle_status": result.settle_status,
        "trust": "untrusted",
    }
    if result.citation:
        meta["citation"] = result.citation
    node = TextNode(text=result.context or "", metadata=meta)
    return NodeWithScore(
        node=node, score=result.similarity_score if result.similarity_score is not None else 1.0
    )


class KalyxLlamaIndexRetriever(BaseRetriever):
    """LlamaIndex retriever backed by :class:`KalyxClient`.

    A retrieval that finds no relevant context returns an empty list (no
    payment attempted). Payment failures propagate as typed SDK errors.
    """

    def __init__(
        self,
        client: KalyxClient | None = None,
        *,
        gateway_url: str | None = None,
        max_price_lamports: int | None = None,
        **kwargs: Any,
    ) -> None:
        _require()
        if client is None:
            client = KalyxClient(
                gateway_url=gateway_url, max_price_lamports=max_price_lamports, **kwargs
            )
        super().__init__()
        self._client = client
        self._max_price = max_price_lamports

    def _retrieve(self, query_bundle: Any) -> list[Any]:
        result = self._client.query_and_retrieve(
            query_bundle.query_str, max_price_lamports=self._max_price
        )
        if result.status != "access_granted" or not result.context:
            return []
        return [_result_to_node(result)]


class KalyxToolSpec(BaseToolSpec):
    """Tool spec exposing paid retrieval to LlamaIndex agents."""

    # Base class declares an instance variable; match its (invariant) type.
    spec_functions: list[str | tuple[str, str]] = ["retrieve"]  # noqa: RUF012

    def __init__(self, client: KalyxClient, *, max_price_lamports: int | None = None) -> None:
        _require()
        super().__init__()
        self._client = client
        self._max_price = max_price_lamports

    def retrieve(self, query: str) -> str:
        """Retrieve verified, citation-backed web context for a query.

        Pays a small Solana transaction only when relevant content exists.
        Returns the content with its citation, or the reason nothing came back.
        """
        try:
            result = self._client.query_and_retrieve(query, max_price_lamports=self._max_price)
        except KalyxError as exc:
            return f"KALYX retrieval failed: {type(exc).__name__}: {exc}"
        if result.status != "access_granted" or not result.context:
            return "No relevant verified context found (no payment made)."
        return f"{result.context}\n\n{result.citation or ''}".strip()


def kalyx_query_tool(
    client: KalyxClient,
    *,
    name: str = "kalyx_retrieve",
    max_price_lamports: int | None = None,
) -> Any:
    """Build a LlamaIndex ``FunctionTool`` that retrieves paid context."""
    _require()
    spec = KalyxToolSpec(client, max_price_lamports=max_price_lamports)
    return FunctionTool.from_defaults(
        fn=spec.retrieve,
        name=name,
        description=(
            "Retrieve verified, citation-backed web context for a query. "
            "Costs a small Solana payment only when relevant content exists."
        ),
    )


__all__ = ["KalyxLlamaIndexRetriever", "KalyxToolSpec", "kalyx_query_tool"]
