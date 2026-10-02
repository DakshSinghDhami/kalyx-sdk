"""Budget tracking: per-call price caps and session spend limits.

Extracted from the client so the accounting logic is unit-testable on its
own and reusable by the sync and async clients.

Concurrency: ``reserve``/``commit``/``release`` are guarded by a lock, so
parallel calls sharing one tracker cannot race the check-then-pay sequence
into overspending. Reservation accounting:

* ``reserve(price)`` — atomic check + hold, taken *before* signing. Raises
  :class:`~kalyx_sdk.errors.BudgetExhausted` when the hold would exceed the
  budget.
* ``commit(price)`` — the payment was broadcast; the hold becomes spend.
* ``release(price)`` — the payment provably never happened; the hold is
  voided.

All three methods are synchronous and await-free, which makes them safe for
both threads (sync client) and interleaved coroutines (async client).
"""

import threading
from dataclasses import dataclass, field

from .errors import BudgetExhausted, ConfigError, PriceExceedsBudget


@dataclass
class BudgetTracker:
    """Tracks session spend against an optional total budget.

    ``budget_lamports=None`` means unlimited session spend. ``spent`` grows
    when a payment is committed (broadcast) or directly recorded — refusals
    never consume budget. ``_reserved`` is spend that is held but not yet
    confirmed spent (payment in flight).
    """

    budget_lamports: int | None = None
    spent: int = 0
    _reserved: int = field(default=0, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.budget_lamports is not None and self.budget_lamports < 0:
            raise ConfigError("session budget must be >= 0")
        if self.spent < 0:
            raise ConfigError("spent must be >= 0")
        if self._reserved < 0:
            raise ConfigError("reserved must be >= 0")

    @property
    def remaining_lamports(self) -> int | None:
        """Lamports still spendable (budget minus spent and held), or None."""
        if self.budget_lamports is None:
            return None
        with self._lock:
            return max(0, self.budget_lamports - self.spent - self._reserved)

    def check_price(self, price_lamports: int, cap: int | None) -> None:
        """Enforce the per-call cap. Raises :class:`PriceExceedsBudget`."""
        if cap is not None and price_lamports > cap:
            raise PriceExceedsBudget(
                f"price {price_lamports} lamports exceeds the per-call cap of {cap} lamports",
            )

    def _over_budget(self, price_lamports: int) -> bool:
        return (
            self.budget_lamports is not None
            and self.spent + self._reserved + price_lamports > self.budget_lamports
        )

    def _budget_error(self, price_lamports: int) -> BudgetExhausted:
        held = f" + {self._reserved} held" if self._reserved else ""
        return BudgetExhausted(
            f"paying {price_lamports} lamports would exceed the session "
            f"budget ({self.spent}{held} + {price_lamports} > "
            f"{self.budget_lamports})",
            spent_lamports=self.spent,
            budget_lamports=self.budget_lamports,
        )

    def check_spend(self, price_lamports: int) -> None:
        """Enforce the session budget. Raises :class:`BudgetExhausted`.

        Pure check — use :meth:`reserve` when the check must be atomic with
        the hold (i.e. in payment paths).
        """
        with self._lock:
            if self._over_budget(price_lamports):
                raise self._budget_error(price_lamports)

    def reserve(self, price_lamports: int) -> None:
        """Atomically check the budget and hold ``price_lamports``.

        Pair every successful reservation with exactly one :meth:`commit`
        (payment broadcast) or :meth:`release` (payment never happened).
        """
        if price_lamports < 0:
            raise ConfigError("cannot reserve a negative spend")
        with self._lock:
            if self._over_budget(price_lamports):
                raise self._budget_error(price_lamports)
            self._reserved += price_lamports

    def commit(self, price_lamports: int) -> None:
        """Turn a reservation into spend (the payment was broadcast)."""
        if price_lamports < 0:
            raise ConfigError("cannot commit a negative spend")
        with self._lock:
            if price_lamports > self._reserved:
                raise ConfigError("commit without a matching reservation")
            self._reserved -= price_lamports
            self.spent += price_lamports

    def release(self, price_lamports: int) -> None:
        """Void a reservation (the payment provably never happened)."""
        if price_lamports < 0:
            raise ConfigError("cannot release a negative spend")
        with self._lock:
            if price_lamports > self._reserved:
                raise ConfigError("release without a matching reservation")
            self._reserved -= price_lamports

    def record(self, price_lamports: int) -> None:
        """Record a payment that actually happened (without a reservation)."""
        if price_lamports < 0:
            raise ConfigError("cannot record a negative spend")
        with self._lock:
            self.spent += price_lamports
