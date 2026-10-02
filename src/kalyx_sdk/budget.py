"""Budget tracking: per-call price caps and session spend limits.

Extracted from the client so the accounting logic is unit-testable on its
own and reusable by the sync and async clients.
"""

from dataclasses import dataclass

from .errors import BudgetExhausted, ConfigError, PriceExceedsBudget


@dataclass
class BudgetTracker:
    """Tracks session spend against an optional total budget.

    ``budget_lamports=None`` means unlimited session spend. ``spent`` only
    grows when :meth:`record` is called (i.e. after a payment actually
    succeeded) — refusals never consume budget.
    """

    budget_lamports: int | None = None
    spent: int = 0

    def __post_init__(self) -> None:
        if self.budget_lamports is not None and self.budget_lamports < 0:
            raise ConfigError("session budget must be >= 0")
        if self.spent < 0:
            raise ConfigError("spent must be >= 0")

    @property
    def remaining_lamports(self) -> int | None:
        """Lamports still spendable, or None when uncapped."""
        if self.budget_lamports is None:
            return None
        return max(0, self.budget_lamports - self.spent)

    def check_price(self, price_lamports: int, cap: int | None) -> None:
        """Enforce the per-call cap. Raises :class:`PriceExceedsBudget`."""
        if cap is not None and price_lamports > cap:
            raise PriceExceedsBudget(
                f"price {price_lamports} lamports exceeds the per-call cap of {cap} lamports",
            )

    def check_spend(self, price_lamports: int) -> None:
        """Enforce the session budget. Raises :class:`BudgetExhausted`."""
        if self.budget_lamports is not None and self.spent + price_lamports > self.budget_lamports:
            raise BudgetExhausted(
                f"paying {price_lamports} lamports would exceed the session "
                f"budget ({self.spent} + {price_lamports} > "
                f"{self.budget_lamports})",
                spent_lamports=self.spent,
                budget_lamports=self.budget_lamports,
            )

    def record(self, price_lamports: int) -> None:
        """Record a payment that actually happened."""
        if price_lamports < 0:
            raise ConfigError("cannot record a negative spend")
        self.spent += price_lamports
