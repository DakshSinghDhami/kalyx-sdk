"""KALYX SDK — synchronous client.

Configuration resolution and URL validation live here; the full client class
is built up feature by feature. Env fallbacks (``KALYX_GATEWAY_URL``,
``KALYX_KEYPAIR_PATH``, ``KALYX_RPC_URL``) are read but never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from ._version import __version__
from .chain import KnowledgeNodeState
from .errors import ConfigError, VerificationFailed
from .escrow import DEFAULT_PROGRAM_ID, derive_node_pda
from .models import Challenge
from .wallet import KeypairSource, Wallet, load_wallet

#: Default public gateway (Solana devnet deployment).
DEFAULT_GATEWAY_URL = "https://kalyxprotocol.xyz"
#: Default RPC endpoint (Solana devnet).
DEFAULT_RPC_URL = "https://api.devnet.solana.com"

_ENV_GATEWAY_URL = "KALYX_GATEWAY_URL"
_ENV_KEYPAIR_PATH = "KALYX_KEYPAIR_PATH"
_ENV_RPC_URL = "KALYX_RPC_URL"

# S104: "0.0.0.0" here is a *dialing* allowlist for local dev gateways, not a bind.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104


def _is_loopback(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


def validate_gateway_url(url: str) -> str:
    """Validate a gateway base URL.

    TLS is mandatory except for loopback addresses: passing an ``http://``
    URL whose host is ``localhost``/``127.0.0.1``/``::1`` is the explicit
    opt-in for local development. Anything else raises :class:`ConfigError`.
    Returns the URL without a trailing slash.
    """
    if not isinstance(url, str) or not url.strip():
        raise ConfigError("gateway_url must be a non-empty string")
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(
            f"gateway_url must use https (got scheme {parsed.scheme!r}); "
            "http is only allowed for localhost"
        )
    if not parsed.hostname:
        raise ConfigError("gateway_url has no host")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ConfigError(
            "gateway_url must use https; plain http is only allowed for "
            "localhost (loopback) gateways"
        )
    return url


def validate_rpc_url(url: str) -> str:
    """Validate a Solana RPC URL (http/https/ws-less, any host)."""
    if not isinstance(url, str) or not url.strip():
        raise ConfigError("rpc_url must be a non-empty string")
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError("rpc_url must use http or https")
    if not parsed.hostname:
        raise ConfigError("rpc_url has no host")
    return url


def default_user_agent() -> str:
    """The SDK's identifying User-Agent (honest protocol citizenship)."""
    return f"kalyx-sdk/{__version__} (+https://kalyxprotocol.xyz)"


@dataclass(frozen=True)
class ClientConfig:
    """Resolved, validated client configuration.

    Built by :meth:`ClientConfig.resolve`; never contains the keypair secret,
    only the loaded :class:`~kalyx_sdk.wallet.Wallet` wrapper.
    """

    gateway_url: str
    rpc_url: str
    wallet: Wallet | None
    max_price_lamports: int | None
    session_budget_lamports: int | None
    timeout: float
    user_agent: str

    @classmethod
    def resolve(
        cls,
        *,
        gateway_url: str | None = None,
        keypair: KeypairSource | None = None,
        rpc_url: str | None = None,
        max_price_lamports: int | None = None,
        session_budget_lamports: int | None = None,
        timeout: float = 30.0,
        user_agent: str | None = None,
    ) -> ClientConfig:
        """Resolve constructor args against environment and defaults.

        Env fallbacks: ``KALYX_GATEWAY_URL``, ``KALYX_KEYPAIR_PATH``,
        ``KALYX_RPC_URL``. Values are never logged.
        """
        gw = gateway_url or os.environ.get(_ENV_GATEWAY_URL) or DEFAULT_GATEWAY_URL
        rpc = rpc_url or os.environ.get(_ENV_RPC_URL) or DEFAULT_RPC_URL
        kp_source = keypair
        if kp_source is None:
            env_path = os.environ.get(_ENV_KEYPAIR_PATH)
            if env_path:
                kp_source = env_path
        wallet = load_wallet(kp_source) if kp_source is not None else None
        if max_price_lamports is not None and max_price_lamports < 0:
            raise ConfigError("max_price_lamports must be >= 0")
        if session_budget_lamports is not None and session_budget_lamports < 0:
            raise ConfigError("session_budget_lamports must be >= 0")
        if timeout <= 0:
            raise ConfigError("timeout must be positive")
        return cls(
            gateway_url=validate_gateway_url(gw),
            rpc_url=validate_rpc_url(rpc),
            wallet=wallet,
            max_price_lamports=max_price_lamports,
            session_budget_lamports=session_budget_lamports,
            timeout=float(timeout),
            user_agent=user_agent or default_user_agent(),
        )


# --- challenge verification (verify before paying) ----------------------------

_HEX64 = frozenset("0123456789abcdefABCDEF")


def verify_challenge_offchain(
    challenge: Challenge, *, expected_program_id: str | None = None
) -> None:
    """Validate a 402 challenge without touching the network.

    Checks, in order (any failure raises :class:`VerificationFailed` *before*
    any money moves):

    * ``program_id`` matches the gateway-reported id (or the vendored IDL's
      address when the config could not be fetched);
    * the challenge names an on-chain ``node_address`` (a node that is not
      registered on-chain cannot be paid);
    * ``content_hash`` is 32 bytes of hex;
    * the node PDA re-derives from ``(author, content_hash)`` — the author is
      ``author_wallet`` when the gateway reports it, else
      ``publisher_wallet`` (see BLOCKERS: the challenge does not carry the
      author explicitly).
    """
    expected = expected_program_id or DEFAULT_PROGRAM_ID
    if challenge.program_id != expected:
        raise VerificationFailed(
            "challenge program_id does not match the configured program",
            reason="program_id_mismatch",
        )
    if not challenge.node_address:
        raise VerificationFailed(
            "challenge names no on-chain node (node_address empty); "
            "the node is not registered, so it cannot be paid",
            reason="node_not_registered",
        )
    ch = challenge.content_hash
    if len(ch) != 64 or any(c not in _HEX64 for c in ch):
        raise VerificationFailed(
            "challenge content_hash is not 32 bytes of hex", reason="bad_content_hash"
        )
    author = challenge.author_wallet or challenge.publisher_wallet
    try:
        derived, _bump = derive_node_pda(author, bytes.fromhex(ch), expected)
    except Exception as exc:  # solders ValueError for bad base58 / off-curve
        raise VerificationFailed(
            "challenge wallets/hash cannot form a node PDA", reason="bad_pda_inputs"
        ) from exc
    if str(derived) != challenge.node_address:
        raise VerificationFailed(
            "challenge node_address does not match the re-derived node PDA",
            reason="pda_mismatch",
        )


def verify_node_preflight(challenge: Challenge, node: KnowledgeNodeState | None) -> None:
    """Validate the on-chain node account against the challenge.

    Raises :class:`VerificationFailed` when the node is missing, inactive,
    priced above the challenge, or bound to different content. Pure function:
    the account read itself lives in :mod:`kalyx_sdk.chain`.
    """
    if node is None:
        raise VerificationFailed(
            "knowledge node account does not exist on-chain", reason="node_not_registered"
        )
    if not node.is_active:
        raise VerificationFailed("knowledge node is inactive on-chain", reason="node_inactive")
    if node.content_hash.hex() != challenge.content_hash.lower():
        raise VerificationFailed(
            "on-chain node content hash differs from the challenge",
            reason="content_hash_mismatch",
        )
    if node.price_lamports > challenge.price_lamports:
        raise VerificationFailed(
            "on-chain node price exceeds the challenge price; funding at the "
            "challenge price would fail on-chain",
            reason="onchain_price_higher",
        )


# --- HTTP plumbing -------------------------------------------------------------

import hashlib  # noqa: E402
import time  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402

from . import chain as _chain  # noqa: E402
from .budget import BudgetTracker  # noqa: E402
from .errors import (  # noqa: E402
    ChainError,
    ClusterMismatchError,
    DisabledError,
    GatewayUnavailable,
    InsufficientFunds,
    KalyxError,
    PaymentRequired,
    PriceExceedsBudget,
    RateLimited,
    ReplayRejected,
)
from .escrow import build_create_and_fund_tx, build_refund_tx, query_hash  # noqa: E402
from .models import (  # noqa: E402
    NodeDetail,
    NodePage,
    ProbeResult,
    ProtocolConfig,
    RefundResult,
    RetrievalResult,
)
from .retry import RetryPolicy, run_with_retries  # noqa: E402

#: Lamports paid in fees for a one-signature transaction.
TX_FEE_LAMPORTS = 5_000

_CONFIG_TTL_SECONDS = 60.0
_DISABLED_REASONS = {"demo_disabled", "cluster_refused"}
#: Gateway statuses worth retrying (transient by definition).
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def _request_id(headers: httpx.Headers) -> str | None:
    rid = headers.get("x-request-id") or headers.get("x-kalyx-request-id")
    return rid if isinstance(rid, str) else None


def parse_retry_after(headers: httpx.Headers, body: dict[str, Any] | None = None) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or HTTP-date) into seconds.

    Falls back to a numeric ``retry_after`` field in the JSON body. Returns
    None when nothing parseable is present.
    """
    raw = headers.get("retry-after")
    if raw:
        raw = raw.strip()
        if raw.isdigit():
            return float(int(raw))
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(raw)
            return float(max(0.0, dt.timestamp() - time.time()))
        except (TypeError, ValueError):
            pass
    if body is not None:
        val = body.get("retry_after")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def _error_message(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, str) and err:
            return err
    return "unexpected gateway response"


def _map_error_status(status: int, body: Any, headers: httpx.Headers) -> KalyxError:
    """Map a gateway error status to the typed error hierarchy."""
    rid = _request_id(headers)
    msg = _error_message(body)
    reason = body.get("reason") if isinstance(body, dict) else None
    if status == 409:
        return ReplayRejected(msg, request_id=rid)
    if status == 429:
        return RateLimited(
            msg,
            retry_after=parse_retry_after(headers, body if isinstance(body, dict) else None),
            request_id=rid,
        )
    if status == 503:
        return GatewayUnavailable(msg, reason=reason, request_id=rid)
    if status == 403:
        if reason in _DISABLED_REASONS:
            return DisabledError(msg, reason=reason, request_id=rid)
        return VerificationFailed(msg, reason=reason, request_id=rid)
    if status == 404:
        return VerificationFailed(msg, reason="not_found", request_id=rid)
    if status == 400:
        return KalyxError(f"gateway rejected the request: {msg}", request_id=rid)
    if status == 401:
        return DisabledError(msg, reason=reason or "unauthorized", request_id=rid)
    if status >= 500:
        return GatewayUnavailable(msg, reason=reason, request_id=rid)
    return KalyxError(f"gateway error HTTP {status}: {msg}", request_id=rid)


def verify_content_binding(chunk_id: str, content: str) -> None:
    """Verify served content against its committed chunk id.

    The index commits ``chunk_id == sha256(content).hexdigest()[:16]``; a
    gateway (or middlebox) serving different bytes fails here, after payment
    but before the caller trusts the body.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    if digest != chunk_id.lower():
        raise VerificationFailed(
            "served content does not match the committed chunk id",
            reason="content_hash_mismatch",
        )


class KalyxClient:
    """Synchronous KALYX client.

    Args:
        gateway_url: Base URL of the KALYX gateway (https required unless
            loopback). Falls back to ``KALYX_GATEWAY_URL``, then the public
            deployment.
        keypair: Payer identity — path to a 64-byte JSON keypair file, raw
            64 bytes, ``solders.Keypair`` or :class:`Wallet`. Falls back to
            ``KALYX_KEYPAIR_PATH``. Without a keypair the client can probe and
            browse but never pays.
        rpc_url: Solana RPC endpoint; falls back to ``KALYX_RPC_URL``, then
            devnet. Must be devnet/localnet (checked by genesis hash before
            any payment).
        max_price_lamports: Default per-call price cap.
        session_budget_lamports: Total lamports this client may spend.
        timeout: HTTP timeout (seconds) for gateway and RPC calls.
        user_agent: Override the identifying User-Agent header.
        retry_policy: Backoff policy for transient failures on idempotent
            calls (probe, config, retrieve). Payments are never retried.
    """

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
        http_client: httpx.Client | None = None,
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
        self._http = http_client or httpx.Client(
            headers={"User-Agent": self._cfg.user_agent},
            timeout=httpx.Timeout(timeout),
            verify=True,
            follow_redirects=False,
        )
        self._rpc = _chain.RpcClient(self._http, self._cfg.rpc_url)
        self._config_cache: tuple[float, ProtocolConfig] | None = None
        self._budget = BudgetTracker(budget_lamports=self._cfg.session_budget_lamports)
        self._cluster_checked = False

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> KalyxClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def address(self) -> str | None:
        """Base58 payer address, or None when no keypair is configured."""
        return self._cfg.wallet.address if self._cfg.wallet else None

    @property
    def spent_lamports(self) -> int:
        """Lamports this client has paid so far (session spend)."""
        return self._budget.spent

    @property
    def remaining_budget_lamports(self) -> int | None:
        """Session lamports still spendable, or None when uncapped."""
        return self._budget.remaining_lamports

    def __repr__(self) -> str:
        return f"KalyxClient(gateway_url={self._cfg.gateway_url!r}, address={self.address!r})"

    # -- HTTP helpers ---------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any, httpx.Headers]:
        url = f"{self._cfg.gateway_url}{path}"
        try:
            resp = self._http.request(method, url, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise GatewayUnavailable(f"gateway unreachable: {type(exc).__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        return resp.status_code, body, resp.headers

    def _request_retrying(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        ok_statuses: frozenset[int] = frozenset(),
    ) -> tuple[int, Any, httpx.Headers]:
        """``_request`` with retries on transient statuses and network errors.

        Statuses in ``ok_statuses`` are returned even when non-2xx (402 for
        probe). Retryable statuses raise the mapped error so the policy can
        decide; on the final attempt the mapped error propagates.
        """

        def _do() -> tuple[int, Any, httpx.Headers]:
            status, body, headers = self._request(method, path, json_body=json_body, params=params)
            if status in _RETRYABLE_STATUSES and status not in ok_statuses:
                raise _map_error_status(status, body, headers)
            return status, body, headers

        return run_with_retries(_do, self._retry)

    # -- public API -----------------------------------------------------

    def config(self) -> ProtocolConfig:
        """Fetch ``GET /v1/config`` (60s client-side cache)."""
        now = time.monotonic()
        if self._config_cache and now - self._config_cache[0] < _CONFIG_TTL_SECONDS:
            return self._config_cache[1]
        status, body, headers = self._request_retrying("GET", "/v1/config")
        if status != 200 or not isinstance(body, dict):
            raise _map_error_status(status, body, headers)
        cfg = ProtocolConfig.from_dict(body)
        self._config_cache = (now, cfg)
        return cfg

    def probe(self, query: str) -> ProbeResult:
        """Probe relevance for a query. Free; never pays.

        Returns a :class:`ProbeResult` whose ``challenge`` is set when the
        gateway answered 402.
        """
        _validate_query(query)
        status, body, headers = self._request_retrying(
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

    def list_nodes(
        self,
        *,
        q: str | None = None,
        license: str | None = None,
        limit: int | None = None,
        cursor: int | None = None,
    ) -> NodePage:
        """Browse the public node catalog (``GET /v1/nodes``). Free.

        Returns one page; pass ``page.next_cursor`` as ``cursor`` to page on.
        """
        params: dict[str, Any] = {}
        if q:
            params["q"] = q
        if license:
            params["license"] = license
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        status, body, headers = self._request_retrying("GET", "/v1/nodes", params=params)
        if status != 200 or not isinstance(body, dict):
            raise _map_error_status(status, body, headers)
        return NodePage.from_dict(body)

    def node_detail(self, node_id: str) -> NodeDetail:
        """Fetch one node's metadata + live verification report. Free.

        Raises :class:`VerificationFailed` (``reason="not_found"``) for an
        unknown node id.
        """
        if not node_id or not node_id.strip():
            raise ValueError("node_id must be non-empty")
        status, body, headers = self._request_retrying("GET", f"/v1/nodes/{node_id}")
        if status != 200 or not isinstance(body, dict):
            raise _map_error_status(status, body, headers)
        return NodeDetail.from_dict(body)

    def query_and_retrieve(
        self, query: str, *, max_price_lamports: int | None = None
    ) -> RetrievalResult:
        """Full flow: probe → verify → pay → confirm → retrieve → verify content.

        Pays only when the gateway finds relevant context, the challenge
        verifies, and the price is within the per-call cap and session budget.
        Every failure raises *before* spending when raising after spending
        would strand funds; a payment that has been broadcast is never
        retried automatically.

        Raises:
            PriceExceedsBudget: price above the cap.
            BudgetExhausted: session budget would be exceeded.
            InsufficientFunds: payer cannot cover price + rent + fee.
            PaymentRequired: no keypair configured but payment is needed.
            VerificationFailed: challenge/content verification failed.
            ClusterMismatchError: RPC is mainnet/unknown.
            ChainError: broadcast or confirmation failed.
        """
        probe = self.probe(query)
        if probe.challenge is None:
            return RetrievalResult(
                status="no_context_found",
                similarity_score=probe.similarity_score,
                raw=probe.raw,
            )
        challenge = probe.challenge
        cfg = self.config()
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
                "a keypair is required to pay the challenge",
                challenge=challenge.raw,
            )

        self._assert_cluster(cfg)

        # On-chain preflight: node exists, active, priced no higher.
        node = _chain.node_state(self._rpc, challenge.node_address, program_id=challenge.program_id)
        verify_node_preflight(challenge, node)

        self._assert_funds(challenge.price_lamports)

        # Reserve the budget BEFORE signing; the hold is released only when
        # the payment provably never reached the wire. This makes the
        # check-then-pay sequence atomic across threads sharing this client.
        self._budget.reserve(challenge.price_lamports)
        try:
            qhash = query_hash(query)
            blockhash = _chain.latest_blockhash(self._rpc)
            payment = build_create_and_fund_tx(
                wallet=self._cfg.wallet,
                qhash=qhash,
                node_address=challenge.node_address,
                amount_lamports=challenge.price_lamports,
                recent_blockhash=blockhash,
                program_id=challenge.program_id,
            )
            try:
                signature = _chain.send_transaction(self._rpc, payment.transaction_b64)
            except ChainError as exc:
                raise ChainError(
                    f"failed to broadcast funding transaction: {exc}",
                    signature=payment.signature,
                    retryable=exc.retryable,
                ) from exc
        except BaseException:
            self._budget.release(challenge.price_lamports)
            raise
        # Broadcast accepted: the lamports are in flight regardless of what
        # happens next, so the reservation becomes spend now (a confirmation
        # timeout still leaves an on-chain-payment possibility).
        self._budget.commit(challenge.price_lamports)

        # Confirm BEFORE retrieving: the gateway's verifier reads at
        # confirmed commitment, so retrieving earlier would race it.
        _chain.wait_for_confirmation(self._rpc, signature, timeout=self._confirm_timeout)

        # Retrieve is idempotent for the same escrow + signature, so it is
        # safe to retry on transient failures.
        result = run_with_retries(
            lambda: self._retrieve(challenge, signature, payment.escrow_address),
            self._retry,
        )
        return result

    def refund_expired_escrow(self, escrow_address: str) -> RefundResult:
        """Reclaim a funded escrow after ``refund_timeout_slots`` elapsed.

        The on-chain program lets the consumer self-refund once the escrow
        has aged past the protocol's refund timeout (``refund_timeout_slots``
        from ``/v1/config``). This raises rather than attempting the
        transaction when the escrow is missing, not ours, already settled, or
        not yet expired.

        Raises:
            PaymentRequired: no keypair configured.
            VerificationFailed: escrow missing, foreign, settled/disputed,
                or not yet expired.
            ClusterMismatchError: RPC is mainnet/unknown.
            ChainError: broadcast or confirmation failed.
        """
        if not escrow_address or not escrow_address.strip():
            raise ValueError("escrow_address must be non-empty")
        if self._cfg.wallet is None:
            raise PaymentRequired("a keypair is required to claim a refund")
        cfg = self.config()
        self._assert_cluster(cfg)
        program_id = cfg.program_id or DEFAULT_PROGRAM_ID
        state = _chain.escrow_state(self._rpc, escrow_address, program_id=program_id)
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
        slot = _chain.current_slot(self._rpc)
        timeout_slots = cfg.refund_timeout_slots or 0
        age = slot - state.created_at_slot
        if timeout_slots and age < timeout_slots:
            raise VerificationFailed(
                f"escrow not yet refundable: {age} of {timeout_slots} slots elapsed",
                reason="escrow_not_expired",
            )
        blockhash = _chain.latest_blockhash(self._rpc)
        payment = build_refund_tx(
            wallet=self._cfg.wallet,
            qhash=state.query_hash,
            recent_blockhash=blockhash,
            program_id=program_id,
        )
        if payment.escrow_address != escrow_address:
            raise VerificationFailed(
                "refund transaction targets a different escrow PDA",
                reason="escrow_pda_mismatch",
            )
        signature = _chain.send_transaction(self._rpc, payment.transaction_b64)
        _chain.wait_for_confirmation(self._rpc, signature, timeout=self._confirm_timeout)
        return RefundResult(
            escrow_address=escrow_address,
            refunded_lamports=state.amount_lamports,
            signature=signature,
        )

    # -- internals --------------------------------------------------------

    def _check_affordable(self, challenge: Challenge, cap: int | None) -> None:
        """Per-call cap first, then the session budget — before any payment."""
        try:
            self._budget.check_price(challenge.price_lamports, cap)
        except PriceExceedsBudget as exc:
            exc.challenge = challenge.raw
            raise
        self._budget.check_spend(challenge.price_lamports)

    def _assert_cluster(self, cfg: ProtocolConfig) -> None:
        if self._cluster_checked:
            return
        genesis = _chain.genesis_hash(self._rpc)
        _chain.assert_cluster_allowed(genesis, self._cfg.rpc_url)
        if cfg.cluster_verified and cfg.genesis_hash and genesis and cfg.genesis_hash != genesis:
            raise ClusterMismatchError(
                "gateway and client RPC are on different clusters; a payment would never verify"
            )
        self._cluster_checked = True

    def _assert_funds(self, price: int) -> None:
        assert self._cfg.wallet is not None
        try:
            rent = _chain.minimum_rent(self._rpc, _chain.ESCROW_ACCOUNT_LEN)
        except ChainError:
            rent = 2_000_000  # conservative fallback when the RPC won't say
        required = price + rent + TX_FEE_LAMPORTS
        try:
            available = _chain.balance(self._rpc, self._cfg.wallet.address)
        except ChainError as exc:
            raise ChainError(f"could not read payer balance: {exc}") from exc
        if available < required:
            raise InsufficientFunds(
                f"payer holds {available} lamports but the payment needs "
                f"{required} (price {price} + rent {rent} + fee {TX_FEE_LAMPORTS})",
                required_lamports=required,
                available_lamports=available,
            )

    def _retrieve(
        self, challenge: Challenge, signature: str, escrow_address: str
    ) -> RetrievalResult:
        status, body, headers = self._request(
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


def _validate_query(query: str) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if len(query) > 512:
        raise ValueError("query exceeds the gateway's 512-character limit")


def _opt_float(d: dict[str, Any], key: str) -> float | None:
    v = d.get(key)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None
