"""Asynchronous KALYX client — mirrors :class:`kalyx_sdk.client.KalyxClient`.

Same verify-then-pay guarantees, same typed errors, same budget guards;
every network hop is awaited instead of blocking. Construct with
``AsyncKalyxClient(...)`` and use as an async context manager::

    async with AsyncKalyxClient(keypair="~/.config/solana/id.json") as client:
        result = await client.query_and_retrieve("how are escrows settled?")
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from . import chain as _chain
from .budget import BudgetTracker
from .client import (
    ClientConfig,
    KeypairSource,
    _map_error_status,
    _opt_float,
    _validate_query,
    verify_challenge_offchain,
    verify_content_binding,
    verify_node_preflight,
)
from .errors import (
    ChainError,
    ClusterMismatchError,
    GatewayUnavailable,
    InsufficientFunds,
    PaymentRequired,
    PriceExceedsBudget,
    VerificationFailed,
)
from .escrow import build_create_and_fund_tx, build_refund_tx, query_hash
from .models import (
    Challenge,
    NodeDetail,
    NodePage,
    ProbeResult,
    ProtocolConfig,
    RefundResult,
    RetrievalResult,
)
from .retry import RetryPolicy, arun_with_retries

_CONFIG_TTL_SECONDS = 60.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
TX_FEE_LAMPORTS = 5_000


class AsyncKalyxClient:
    """Asynchronous KALYX client. See :class:`KalyxClient` for semantics."""

    def __init__(
        self,
        gateway_url: str | None = None,
        *,
        keypair: KeypairSource | None = None,
        rpc_url: str | None = None,
        max_price_lamports: int | None = None,
        session_budget_lamports: int | None = None,
        timeout: float = 30.0,
        user_agent: str | None = None,
        confirm_timeout: float = 60.0,
        retry_policy: RetryPolicy | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = ClientConfig.resolve(
            gateway_url=gateway_url,
            keypair=keypair,
            rpc_url=rpc_url,
            max_price_lamports=max_price_lamports,
            session_budget_lamports=session_budget_lamports,
            timeout=timeout,
            user_agent=user_agent,
        )
        self._confirm_timeout = confirm_timeout
        self._retry = retry_policy or RetryPolicy()
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(
            headers={"User-Agent": self._cfg.user_agent},
            timeout=httpx.Timeout(timeout),
            verify=True,
            follow_redirects=False,
        )
        self._rpc = _chain.AsyncRpcClient(self._http, self._cfg.rpc_url)
        self._config_cache: tuple[float, ProtocolConfig] | None = None
        self._budget = BudgetTracker(budget_lamports=self._cfg.session_budget_lamports)
        self._cluster_checked = False

    # -- lifecycle ------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncKalyxClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    @property
    def address(self) -> str | None:
        return self._cfg.wallet.address if self._cfg.wallet else None

    @property
    def spent_lamports(self) -> int:
        return self._budget.spent

    @property
    def remaining_budget_lamports(self) -> int | None:
        return self._budget.remaining_lamports

    def __repr__(self) -> str:
        return f"AsyncKalyxClient(gateway_url={self._cfg.gateway_url!r}, address={self.address!r})"

    # -- HTTP helpers ---------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any, httpx.Headers]:
        url = f"{self._cfg.gateway_url}{path}"
        try:
            resp = await self._http.request(method, url, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise GatewayUnavailable(f"gateway unreachable: {type(exc).__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        return resp.status_code, body, resp.headers

    async def _request_retrying(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        ok_statuses: frozenset[int] = frozenset(),
    ) -> tuple[int, Any, httpx.Headers]:
        async def _do() -> tuple[int, Any, httpx.Headers]:
            status, body, headers = await self._request(
                method, path, json_body=json_body, params=params
            )
            if status in _RETRYABLE_STATUSES and status not in ok_statuses:
                raise _map_error_status(status, body, headers)
            return status, body, headers

        return await arun_with_retries(_do, self._retry)  # type: ignore[return-value]

    # -- public API -----------------------------------------------------

    async def config(self) -> ProtocolConfig:
        """Fetch ``GET /v1/config`` (60s client-side cache)."""
        now = time.monotonic()
        if self._config_cache and now - self._config_cache[0] < _CONFIG_TTL_SECONDS:
            return self._config_cache[1]
        status, body, headers = await self._request_retrying("GET", "/v1/config")
        if status != 200 or not isinstance(body, dict):
            raise _map_error_status(status, body, headers)
        cfg = ProtocolConfig.from_dict(body)
        self._config_cache = (now, cfg)
        return cfg

    async def probe(self, query: str) -> ProbeResult:
        """Probe relevance for a query. Free; never pays."""
        _validate_query(query)
        status, body, headers = await self._request_retrying(
            "POST", "/v1/query", json_body={"query": query}, ok_statuses=frozenset({402})
        )
        if status == 200 and isinstance(body, dict):
            if body.get("status") != "no_context_found":
                raise GatewayUnavailable("gateway returned malformed probe result")
            return ProbeResult(
                status="no_context_found",
                similarity_score=_opt_float(body, "similarity_score"),
                threshold=_opt_float(body, "threshold"),
                raw=dict(body),
            )
        if status == 402:
            if not isinstance(body, dict):
                raise GatewayUnavailable("gateway returned malformed challenge")
            challenge = Challenge.from_dict(body)
            return ProbeResult(
                status="payment_required",
                similarity_score=challenge.similarity_score,
                threshold=challenge.threshold,
                challenge=challenge,
                raw=dict(body),
            )
        raise _map_error_status(status, body, headers)

    async def list_nodes(
        self,
        *,
        q: str | None = None,
        license: str | None = None,
        limit: int | None = None,
        cursor: int | None = None,
    ) -> NodePage:
        """Browse the public node catalog (``GET /v1/nodes``). Free."""
        params: dict[str, Any] = {}
        if q:
            params["q"] = q
        if license:
            params["license"] = license
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        status, body, headers = await self._request_retrying("GET", "/v1/nodes", params=params)
        if status != 200 or not isinstance(body, dict):
            raise _map_error_status(status, body, headers)
        return NodePage.from_dict(body)

    async def node_detail(self, node_id: str) -> NodeDetail:
        """Fetch one node's metadata + live verification report. Free."""
        if not node_id or not node_id.strip():
            raise ValueError("node_id must be non-empty")
        status, body, headers = await self._request_retrying("GET", f"/v1/nodes/{node_id}")
        if status != 200 or not isinstance(body, dict):
            raise _map_error_status(status, body, headers)
        return NodeDetail.from_dict(body)

    async def query_and_retrieve(
        self, query: str, *, max_price_lamports: int | None = None
    ) -> RetrievalResult:
        """Full flow: probe → verify → pay → confirm → retrieve → verify.

        Identical guarantees and failure modes as the synchronous client;
        see :meth:`KalyxClient.query_and_retrieve`.
        """
        probe = await self.probe(query)
        if probe.challenge is None:
            return RetrievalResult(
                status="no_context_found",
                similarity_score=probe.similarity_score,
                raw=probe.raw,
            )
        challenge = probe.challenge
        cfg = await self.config()
        verify_challenge_offchain(challenge, expected_program_id=cfg.program_id)
        if cfg.program_deployed is False:
            raise VerificationFailed(
                "escrow program is not deployed on the gateway's cluster",
                reason="program_not_deployed",
            )

        cap = max_price_lamports
        if cap is None:
            cap = self._cfg.max_price_lamports
        self._check_affordable(challenge, cap)

        if self._cfg.wallet is None:
            raise PaymentRequired(
                "a keypair is required to pay the challenge", challenge=challenge.raw
            )

        await self._assert_cluster(cfg)

        node = await _chain.anode_state(
            self._rpc, challenge.node_address, program_id=challenge.program_id
        )
        verify_node_preflight(challenge, node)

        await self._assert_funds(challenge.price_lamports)

        qhash = query_hash(query)
        blockhash = await _chain.alatest_blockhash(self._rpc)
        payment = build_create_and_fund_tx(
            wallet=self._cfg.wallet,
            qhash=qhash,
            node_address=challenge.node_address,
            amount_lamports=challenge.price_lamports,
            recent_blockhash=blockhash,
            program_id=challenge.program_id,
        )
        try:
            signature = await _chain.asend_transaction(self._rpc, payment.transaction_b64)
        except ChainError as exc:
            raise ChainError(
                f"failed to broadcast funding transaction: {exc}",
                signature=payment.signature,
                retryable=exc.retryable,
            ) from exc
        await _chain.await_for_confirmation(self._rpc, signature, timeout=self._confirm_timeout)

        result = await arun_with_retries(  # type: ignore[assignment]
            lambda: self._retrieve(challenge, signature, payment.escrow_address),
            self._retry,
        )
        self._budget.record(challenge.price_lamports)
        return result

    async def refund_expired_escrow(self, escrow_address: str) -> RefundResult:
        """Async variant of :meth:`KalyxClient.refund_expired_escrow`."""
        if not escrow_address or not escrow_address.strip():
            raise ValueError("escrow_address must be non-empty")
        if self._cfg.wallet is None:
            raise PaymentRequired("a keypair is required to claim a refund")
        cfg = await self.config()
        await self._assert_cluster(cfg)
        state = await _chain.aescrow_state(self._rpc, escrow_address, program_id=cfg.program_id)
        if state is None:
            raise VerificationFailed(
                "escrow account does not exist on-chain", reason="escrow_not_found"
            )
        if state.consumer != self._cfg.wallet.address:
            raise VerificationFailed(
                "escrow belongs to a different consumer", reason="not_consumer"
            )
        if state.status not in ("Created", "Funded"):
            raise VerificationFailed(
                f"escrow is {state.status}; only Created/Funded escrows are refundable",
                reason="escrow_not_refundable",
            )
        slot = await _chain.acurrent_slot(self._rpc)
        timeout_slots = cfg.refund_timeout_slots or 0
        age = slot - state.created_at_slot
        if timeout_slots and age < timeout_slots:
            raise VerificationFailed(
                f"escrow not yet refundable: {age} of {timeout_slots} slots elapsed",
                reason="escrow_not_expired",
            )
        blockhash = await _chain.alatest_blockhash(self._rpc)
        payment = build_refund_tx(
            wallet=self._cfg.wallet,
            qhash=state.query_hash,
            recent_blockhash=blockhash,
            program_id=cfg.program_id,
        )
        if payment.escrow_address != escrow_address:
            raise VerificationFailed(
                "refund transaction targets a different escrow PDA",
                reason="escrow_pda_mismatch",
            )
        signature = await _chain.asend_transaction(self._rpc, payment.transaction_b64)
        await _chain.await_for_confirmation(self._rpc, signature, timeout=self._confirm_timeout)
        return RefundResult(
            escrow_address=escrow_address,
            refunded_lamports=state.amount_lamports,
            signature=signature,
        )

    # -- internals --------------------------------------------------------

    def _check_affordable(self, challenge: Challenge, cap: int | None) -> None:
        try:
            self._budget.check_price(challenge.price_lamports, cap)
        except PriceExceedsBudget as exc:
            exc.challenge = challenge.raw
            raise
        self._budget.check_spend(challenge.price_lamports)

    async def _assert_cluster(self, cfg: ProtocolConfig) -> None:
        if self._cluster_checked:
            return
        genesis = await _chain.agenesis_hash(self._rpc)
        _chain.assert_cluster_allowed(genesis, self._cfg.rpc_url)
        if cfg.cluster_verified and cfg.genesis_hash and genesis and cfg.genesis_hash != genesis:
            raise ClusterMismatchError(
                "gateway and client RPC are on different clusters; a payment would never verify"
            )
        self._cluster_checked = True

    async def _assert_funds(self, price: int) -> None:
        assert self._cfg.wallet is not None
        try:
            rent = await _chain.aminimum_rent(self._rpc, _chain.ESCROW_ACCOUNT_LEN)
        except ChainError:
            rent = 2_000_000
        required = price + rent + TX_FEE_LAMPORTS
        try:
            available = await _chain.abalance(self._rpc, self._cfg.wallet.address)
        except ChainError as exc:
            raise ChainError(f"could not read payer balance: {exc}") from exc
        if available < required:
            raise InsufficientFunds(
                f"payer holds {available} lamports but the payment needs "
                f"{required} (price {price} + rent {rent} + fee {TX_FEE_LAMPORTS})",
                required_lamports=required,
                available_lamports=available,
            )

    async def _retrieve(
        self, challenge: Challenge, signature: str, escrow_address: str
    ) -> RetrievalResult:
        status, body, headers = await self._request(
            "POST",
            "/v1/retrieve",
            json_body={
                "chunk_id": challenge.chunk_id,
                "tx_signature": signature,
                "escrow_address": escrow_address,
            },
        )
        if status != 200 or not isinstance(body, dict):
            raise _map_error_status(status, body, headers)
        if body.get("status") != "access_granted":
            raise GatewayUnavailable("gateway returned malformed retrieve result")
        content = body.get("decrypted_content")
        if not isinstance(content, str):
            raise GatewayUnavailable("gateway returned malformed retrieve content")
        verify_content_binding(challenge.chunk_id, content)
        settlement = body.get("settlement")
        settle_status = None
        if isinstance(settlement, dict):
            settled = settlement.get("settled")
            if settled is True:
                settle_status = "settled"
            elif settled is False:
                settle_status = "pending"
        verification = body.get("verification")
        safety = body.get("content_safety")
        return RetrievalResult(
            status="access_granted",
            context=content,
            citation=body.get("citation") if isinstance(body.get("citation"), str) else None,
            content_hash=challenge.content_hash or None,
            price_lamports=challenge.price_lamports,
            escrow_address=escrow_address,
            funding_signature=signature,
            settle_status=settle_status,
            receipt={
                "verification": verification if isinstance(verification, dict) else {},
                "settlement": settlement if isinstance(settlement, dict) else None,
            },
            chunk_id=challenge.chunk_id,
            title=body.get("title") if isinstance(body.get("title"), str) else challenge.title,
            domain=body.get("domain") if isinstance(body.get("domain"), str) else challenge.domain,
            similarity_score=challenge.similarity_score,
            content_safety=dict(safety) if isinstance(safety, dict) else {},
            raw=dict(body),
        )
