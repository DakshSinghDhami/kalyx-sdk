"""Retry policy for transient gateway and RPC failures.

Only idempotent operations are retried: probing, config reads, and the
``/v1/retrieve`` call (the gateway treats the same escrow + signature as a
replay-safe retry of the original payment). A broadcast funding
transaction is *never* retried by this policy — callers wait for
confirmation instead.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from .errors import ChainError, GatewayUnavailable, KalyxError, RateLimited

T = TypeVar("T")

#: Error types considered transient and safe to retry for idempotent calls.
RETRYABLE_ERRORS = (GatewayUnavailable, RateLimited)


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with jitter.

    ``attempts`` counts the initial try, so ``attempts=3`` means up to two
    retries. A :class:`~kalyx_sdk.errors.RateLimited` error's
    ``retry_after`` overrides the computed delay (clamped to ``max_delay``).
    """

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    backoff: float = 2.0
    jitter: bool = True

    def delays(self, exc: KalyxError | None = None) -> list[float]:
        """The delay before each retry (len == attempts - 1)."""
        out: list[float] = []
        for i in range(max(0, self.attempts - 1)):
            delay = min(self.base_delay * (self.backoff**i), self.max_delay)
            out.append(delay)
        return out

    def delay_for(self, attempt_index: int, exc: KalyxError | None = None) -> float:
        delay = min(self.base_delay * (self.backoff**attempt_index), self.max_delay)
        if isinstance(exc, RateLimited) and exc.retry_after is not None:
            delay = min(max(exc.retry_after, 0.0), self.max_delay)
        if self.jitter and delay > 0:
            delay *= random.uniform(0.5, 1.0)  # noqa: S311 — not security-sensitive
        return delay


def is_retryable(exc: BaseException) -> bool:
    """True when the operation may be retried safely."""
    if isinstance(exc, RETRYABLE_ERRORS):
        return True
    return isinstance(exc, ChainError) and exc.retryable


def run_with_retries(fn: Callable[[], T], policy: RetryPolicy) -> T:
    """Run ``fn`` retrying transient failures per ``policy`` (sync)."""
    for attempt in range(policy.attempts):
        try:
            return fn()
        except KalyxError as exc:
            if attempt == policy.attempts - 1 or not is_retryable(exc):
                raise
            time.sleep(policy.delay_for(attempt, exc))
    raise AssertionError("unreachable")  # pragma: no cover


async def arun_with_retries(fn: Callable[[], Awaitable[T]], policy: RetryPolicy) -> T:
    """Async variant of :func:`run_with_retries`."""
    for attempt in range(policy.attempts):
        try:
            return await fn()
        except KalyxError as exc:
            if attempt == policy.attempts - 1 or not is_retryable(exc):
                raise
            await asyncio.sleep(policy.delay_for(attempt, exc))
    raise AssertionError("unreachable")  # pragma: no cover
