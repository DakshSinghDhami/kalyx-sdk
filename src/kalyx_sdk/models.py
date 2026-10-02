"""Typed models for the KALYX SDK.

Plain dataclasses with tolerant-but-checked parsing: every ``from_dict``
validates the fields it needs and raises :class:`~kalyx_sdk.errors.GatewayUnavailable`
when the gateway payload is malformed, so a misbehaving server never surfaces
as a bare ``KeyError``/``TypeError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import GatewayUnavailable

_RETRIEVE_OK = "access_granted"
_NO_CONTEXT = "no_context_found"


def _malformed(what: str) -> GatewayUnavailable:
    return GatewayUnavailable(f"gateway returned malformed {what}")


def _req_str(d: dict[str, Any], key: str, what: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v:
        raise _malformed(f"{what} (missing/invalid '{key}')")
    return v


def _str_or(d: dict[str, Any], key: str, default: str) -> str:
    v = d.get(key)
    return v if isinstance(v, str) else default


def _opt_str(d: dict[str, Any], key: str) -> str | None:
    v = d.get(key)
    return v if isinstance(v, str) and v else None


def _req_int(d: dict[str, Any], key: str, what: str) -> int:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise _malformed(f"{what} (missing/invalid '{key}')")
    return v


def _opt_float(d: dict[str, Any], key: str) -> float | None:
    v = d.get(key)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


@dataclass(frozen=True)
class ProtocolConfig:
    """Deployment truth reported by ``GET /v1/config``.

    All gateway-reported numeric fields are informational; the SDK re-derives
    everything security-relevant (program id is cross-checked against the
    challenge and the vendored IDL, prices against budget caps).
    """

    cluster: str | None = None
    genesis_hash: str | None = None
    cluster_verified: bool | None = None
    rpc_label: str | None = None
    program_id: str | None = None
    program_deployed: bool | None = None
    mode: str | None = None
    relevance_threshold: float | None = None
    default_price_lamports: int | None = None
    protocol_fee_bps: int | None = None
    refund_timeout_slots: int | None = None
    dataset_version: str | None = None
    node_count: int | None = None
    settlement_pubkey: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProtocolConfig:
        """Parse a ``/v1/config`` payload; unknown fields are kept in ``raw``."""
        if not isinstance(d, dict):
            raise _malformed("config")
        return cls(
            cluster=_opt_str(d, "cluster"),
            genesis_hash=_opt_str(d, "genesis_hash"),
            cluster_verified=d.get("cluster_verified")
            if isinstance(d.get("cluster_verified"), bool)
            else None,
            rpc_label=_opt_str(d, "rpc_label"),
            program_id=_opt_str(d, "program_id"),
            program_deployed=d.get("program_deployed")
            if isinstance(d.get("program_deployed"), bool)
            else None,
            mode=_opt_str(d, "mode"),
            relevance_threshold=_opt_float(d, "relevance_threshold"),
            default_price_lamports=d.get("default_price_lamports")
            if isinstance(d.get("default_price_lamports"), int)
            else None,
            protocol_fee_bps=d.get("protocol_fee_bps")
            if isinstance(d.get("protocol_fee_bps"), int)
            else None,
            refund_timeout_slots=d.get("refund_timeout_slots")
            if isinstance(d.get("refund_timeout_slots"), int)
            else None,
            dataset_version=_opt_str(d, "dataset_version"),
            node_count=d.get("node_count") if isinstance(d.get("node_count"), int) else None,
            settlement_pubkey=_opt_str(d, "settlement_pubkey"),
            raw=dict(d),
        )


@dataclass(frozen=True)
class Challenge:
    """A validated HTTP 402 payment challenge from ``POST /v1/query``.

    Attributes:
        chunk_id: Content hash (first 16 hex chars of the SHA-256) of the
            paywalled chunk; also the integrity commitment checked after
            retrieval.
        price_lamports: Price in lamports.
        publisher_wallet: Base58 payout wallet reported by the gateway.
        author_wallet: Node author when the gateway reports it (used for PDA
            re-derivation); falls back to ``publisher_wallet`` semantics.
        program_id: Escrow program id from the challenge body.
        node_address: On-chain knowledge-node PDA (``""`` when the node is not
            registered on-chain; such challenges are not payable).
        content_hash: 32-byte hex content hash committed on-chain.
        similarity_score / threshold: Proof-of-Relevance scores.
        free_preview: Publisher free preview (untrusted text).
    """

    chunk_id: str
    price_lamports: int
    publisher_wallet: str
    program_id: str
    node_address: str
    content_hash: str
    title: str = ""
    domain: str = ""
    author_wallet: str | None = None
    similarity_score: float | None = None
    threshold: float | None = None
    free_preview: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def payable_onchain(self) -> bool:
        """Whether the challenge names an on-chain node PDA to pay into."""
        return bool(self.node_address)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Challenge:
        """Parse and shape-check a 402 challenge body."""
        if not isinstance(d, dict):
            raise _malformed("challenge")
        chunk_id = _req_str(d, "chunk_id", "challenge")
        price = _req_int(d, "price_lamports", "challenge")
        if price <= 0:
            raise _malformed("challenge (non-positive price)")
        publisher = _req_str(d, "publisher_wallet", "challenge")
        program_id = _req_str(d, "program_id", "challenge")
        por = d.get("proof_of_relevance")
        if por is not None and not isinstance(por, dict):
            raise _malformed("challenge ('proof_of_relevance' not an object)")
        escrow = d.get("escrow_params")
        if escrow is not None and not isinstance(escrow, dict):
            raise _malformed("challenge ('escrow_params' not an object)")
        escrow = escrow or {}
        ep_program = escrow.get("program_id")
        if isinstance(ep_program, str) and ep_program and ep_program != program_id:
            raise _malformed("challenge (program_id mismatch in escrow_params)")
        ep_price = escrow.get("price_lamports")
        if isinstance(ep_price, int) and not isinstance(ep_price, bool) and ep_price != price:
            raise _malformed("challenge (price mismatch in escrow_params)")
        node_address = escrow.get("node_address")
        if node_address is None:
            node_address = d.get("node_address")  # node-content challenge shape
        content_hash = escrow.get("content_hash") or d.get("content_hash")
        return cls(
            chunk_id=chunk_id,
            price_lamports=price,
            publisher_wallet=publisher,
            program_id=program_id,
            node_address=node_address if isinstance(node_address, str) else "",
            content_hash=content_hash if isinstance(content_hash, str) else "",
            title=_str_or(d, "title", ""),
            domain=_str_or(d, "domain", ""),
            author_wallet=_opt_str(d, "author_wallet"),
            similarity_score=_opt_float(por or {}, "similarity_score"),
            threshold=_opt_float(por or {}, "confidence_threshold"),
            free_preview=_str_or(por or {}, "free_preview", ""),
            raw=dict(d),
        )


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a relevance probe. Never involves a payment.

    ``status`` is ``"no_context_found"`` (free) or ``"payment_required"``
    (``challenge`` is set).
    """

    status: str
    similarity_score: float | None = None
    threshold: float | None = None
    challenge: Challenge | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def payment_required(self) -> bool:
        """True when the gateway answered with a 402 challenge."""
        return self.challenge is not None


@dataclass(frozen=True)
class RetrievalResult:
    """Result of a full ``query_and_retrieve`` run.

    Attributes:
        status: ``"access_granted"`` or ``"no_context_found"``.
        context: The served content. **Untrusted publisher data** — treat as
            data, never as instructions (see ``content_safety``).
        citation: Human-readable citation string from the gateway.
        source_url / license: Publisher provenance when the gateway exposes
            it for this node (the retrieve payload does not carry it; enrich
            via :meth:`node` lookups). ``None`` when unknown.
        content_hash: 32-byte hex content hash committed on-chain (from the
            challenge), when known.
        price_lamports: Lamports actually paid (0 when free).
        escrow_address: The escrow PDA that was funded, when a payment ran.
        funding_signature: The funding transaction signature.
        settle_status: ``"settled"`` / ``"pending"`` / ``None`` per the
            gateway's settlement report.
        receipt: Machine receipt: verification + settlement dicts.
        content_safety: Gateway annotation of the untrusted content.
    """

    status: str
    context: str | None = None
    citation: str | None = None
    source_url: str | None = None
    license: str | None = None
    content_hash: str | None = None
    price_lamports: int = 0
    escrow_address: str | None = None
    funding_signature: str | None = None
    settle_status: str | None = None
    receipt: dict[str, Any] = field(default_factory=dict)
    chunk_id: str | None = None
    title: str | None = None
    domain: str | None = None
    similarity_score: float | None = None
    content_safety: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def paid(self) -> bool:
        """True when content was unlocked by a payment."""
        return self.status == _RETRIEVE_OK and self.price_lamports > 0


@dataclass(frozen=True)
class NodeSummary:
    """Public metadata + free preview for a knowledge node (no paid body)."""

    node_id: str
    title: str = ""
    domain: str | None = None
    category: str | None = None
    publisher_wallet: str | None = None
    author_wallet: str | None = None
    price_lamports: int = 0
    chunk_count: int = 0
    citation_hash: str | None = None
    content_hash: str | None = None
    free_preview: str | None = None
    source_url: str | None = None
    license: str | None = None
    tags: tuple[str, ...] = ()
    published_at: int | None = None
    registered: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NodeSummary:
        """Parse a node summary item from ``/v1/nodes`` or ``/v1/search``."""
        if not isinstance(d, dict):
            raise _malformed("node summary")
        node_id = _req_str(d, "node_id", "node summary")
        tags = d.get("tags")
        price = d.get("price_lamports")
        chunk_count = d.get("chunk_count")
        published = d.get("published_at")
        registered = d.get("registered")
        return cls(
            node_id=node_id,
            title=_str_or(d, "title", ""),
            domain=_opt_str(d, "domain"),
            category=_opt_str(d, "category"),
            publisher_wallet=_opt_str(d, "publisher_wallet"),
            author_wallet=_opt_str(d, "author_wallet"),
            price_lamports=price if isinstance(price, int) and not isinstance(price, bool) else 0,
            chunk_count=(
                chunk_count
                if isinstance(chunk_count, int) and not isinstance(chunk_count, bool)
                else 0
            ),
            citation_hash=_opt_str(d, "citation_hash"),
            content_hash=_opt_str(d, "content_hash"),
            free_preview=_opt_str(d, "free_preview"),
            source_url=_opt_str(d, "source_url"),
            license=_opt_str(d, "license"),
            tags=tuple(t for t in tags if isinstance(t, str)) if isinstance(tags, list) else (),
            published_at=(
                published
                if isinstance(published, int) and not isinstance(published, bool)
                else None
            ),
            registered=registered if isinstance(registered, bool) else None,
            raw=dict(d),
        )


@dataclass(frozen=True)
class NodePage:
    """One page of the ``/v1/nodes`` catalog."""

    items: tuple[NodeSummary, ...]
    total: int
    next_cursor: int | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NodePage:
        """Parse a ``/v1/nodes`` response."""
        if not isinstance(d, dict):
            raise _malformed("node page")
        items = d.get("items")
        if not isinstance(items, list):
            raise _malformed("node page (missing 'items')")
        total = d.get("total")
        cursor = d.get("next_cursor")
        return cls(
            items=tuple(NodeSummary.from_dict(i) for i in items),
            total=total if isinstance(total, int) and not isinstance(total, bool) else len(items),
            next_cursor=cursor
            if isinstance(cursor, int) and not isinstance(cursor, bool)
            else None,
            raw=dict(d),
        )


@dataclass(frozen=True)
class NodeDetail:
    """A single node summary plus the gateway's live verification report."""

    summary: NodeSummary
    verification_report: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NodeDetail:
        """Parse a ``/v1/nodes/{node_id}`` response."""
        if not isinstance(d, dict):
            raise _malformed("node detail")
        report = d.get("verification_report")
        return cls(
            summary=NodeSummary.from_dict(d),
            verification_report=dict(report) if isinstance(report, dict) else {},
            raw=dict(d),
        )


@dataclass(frozen=True)
class EscrowState:
    """Decoded on-chain escrow account.

    Layout (Anchor/Borsh, 130 bytes): discriminator(8) consumer(32) node(32)
    amount(u64) query_hash(32) status(u8) created_at(i64) created_at_slot(u64)
    bump(u8). See ``idl/kalyx.json`` and ``tests/test_escrow_layout.py``.
    """

    address: str
    consumer: str
    node: str
    amount_lamports: int
    query_hash: bytes
    status: str  # "Created" | "Funded" | "Settled" | "Disputed"
    created_at: int
    created_at_slot: int
    bump: int

    @property
    def funded(self) -> bool:
        """True when the escrow is in the on-chain ``Funded`` state."""
        return self.status == "Funded"


@dataclass(frozen=True)
class RefundResult:
    """Outcome of a successful ``refund_escrow`` call."""

    escrow_address: str
    refunded_lamports: int
    signature: str
    raw: dict[str, Any] = field(default_factory=dict)
