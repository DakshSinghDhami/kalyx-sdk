"""KALYX SDK error hierarchy.

Every error raised by the SDK inherits from :class:`KalyxError`, carries a
human-readable message, and — when the gateway sent a request id header —
carries it as ``request_id``. No error message ever contains key material,
file paths supplied by the caller, or environment variable values.
"""

from __future__ import annotations

from typing import Any


class KalyxError(Exception):
    """Base class for all KALYX SDK errors.

    Attributes:
        request_id: The gateway's request id for the failed call, when the
            gateway sent one (``x-request-id`` / ``x-kalyx-request-id``).
            ``None`` for locally-raised errors.
    """

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        self.request_id = request_id
        super().__init__(message)


class ConfigError(KalyxError):
    """Invalid local configuration: bad URL scheme, unreadable keypair, bad env."""


class ClusterMismatchError(KalyxError):
    """The configured RPC is not a permitted cluster (mainnet or unknown).

    The SDK checks the RPC's genesis hash before any money path and fails
    closed: mainnet is always refused and there is no override switch.
    """


class NoContextFound(KalyxError):
    """Raised by strict flows when the gateway finds no relevant context.

    The probe itself is free; this error is only raised by APIs documented as
    strict. ``query_and_retrieve`` reports it as a result status instead.
    """


class PaymentRequired(KalyxError):
    """A payment is required but cannot or will not be made.

    Carries the raw challenge payload (already validated as far as possible)
    so callers can inspect or retry with a higher cap.
    """

    def __init__(
        self,
        message: str,
        *,
        challenge: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        self.challenge = challenge
        super().__init__(message, request_id=request_id)


class PriceExceedsBudget(PaymentRequired):
    """The challenge price is above the per-call price cap."""


class BudgetExhausted(KalyxError):
    """Paying this challenge would exceed the session budget."""

    def __init__(
        self,
        message: str,
        *,
        spent_lamports: int = 0,
        budget_lamports: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.spent_lamports = spent_lamports
        self.budget_lamports = budget_lamports
        super().__init__(message, request_id=request_id)


class InsufficientFunds(KalyxError):
    """The payer keypair cannot cover price + rent + transaction fees.

    Raised before any transaction is signed or broadcast.

    Attributes:
        required_lamports: Total lamports the payment would need.
        available_lamports: Lamports the payer currently holds, when known.
    """

    def __init__(
        self,
        message: str,
        *,
        required_lamports: int | None = None,
        available_lamports: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.required_lamports = required_lamports
        self.available_lamports = available_lamports
        super().__init__(message, request_id=request_id)


class ReplayRejected(KalyxError):
    """HTTP 409 — the payment credential was already consumed."""


class VerificationFailed(KalyxError):
    """Payment or content verification failed.

    Used for gateway 403s (payment could not be verified), for served content
    whose hash does not match the committed chunk id, and for challenges that
    fail client-side validation (wrong program id, tampered price, bad PDA).
    The ``reason`` is always safe to show to a user.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.reason = reason
        super().__init__(message, request_id=request_id)


class RateLimited(KalyxError):
    """HTTP 429 — the gateway rate limit was hit.

    Attributes:
        retry_after: Seconds to wait before retrying, parsed from the
            ``Retry-After`` header (delta-seconds or HTTP-date) or the JSON
            body. ``None`` when the gateway did not say.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        request_id: str | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message, request_id=request_id)


class GatewayUnavailable(KalyxError):
    """HTTP 503 or the gateway is unreachable / returned malformed data.

    Attributes:
        reason: Machine-readable reason from the gateway when present
            (e.g. ``wallet_unfunded``), else ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.reason = reason
        super().__init__(message, request_id=request_id)


class DisabledError(KalyxError):
    """HTTP 403 — the requested feature is disabled on this gateway.

    Distinct from :class:`VerificationFailed`: the gateway answered with a
    machine-readable ``reason`` such as ``demo_disabled`` or
    ``cluster_refused``.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.reason = reason
        super().__init__(message, request_id=request_id)


class ChainError(KalyxError):
    """A Solana RPC call, broadcast, or confirmation failed.

    Attributes:
        signature: The transaction signature involved, when known.
        retryable: True for transient failures (transport errors, timeouts,
            HTTP 5xx/429) where a later retry may succeed. The SDK still never
            retries a payment broadcast automatically; the flag exists so
            callers can make their own policy decisions.
    """

    def __init__(
        self,
        message: str,
        *,
        signature: str | None = None,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        self.signature = signature
        self.retryable = retryable
        super().__init__(message, request_id=request_id)
