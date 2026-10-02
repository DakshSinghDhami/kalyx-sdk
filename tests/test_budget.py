"""Budget tracker unit tests: caps, exhaustion, and spend accounting."""

import pytest

from kalyx_sdk.budget import BudgetTracker
from kalyx_sdk.errors import BudgetExhausted, ConfigError, PriceExceedsBudget


def test_unlimited_budget_never_exhausts():
    t = BudgetTracker(budget_lamports=None)
    assert t.remaining_lamports is None
    t.check_spend(10**12)
    t.record(10**12)
    assert t.spent == 10**12


def test_budget_exhaustion_blocks_and_reports():
    t = BudgetTracker(budget_lamports=100)
    t.record(60)
    assert t.remaining_lamports == 40
    with pytest.raises(BudgetExhausted) as ei:
        t.check_spend(41)
    assert ei.value.spent_lamports == 60
    assert ei.value.budget_lamports == 100
    # Exact fit still allowed.
    t.check_spend(40)


def test_price_cap():
    t = BudgetTracker()
    t.check_price(100, cap=100)  # equal is fine
    with pytest.raises(PriceExceedsBudget):
        t.check_price(101, cap=100)
    t.check_price(10**9, cap=None)  # no cap


def test_negative_values_rejected():
    with pytest.raises(ConfigError):
        BudgetTracker(budget_lamports=-1)
    with pytest.raises(ConfigError):
        BudgetTracker(budget_lamports=10, spent=-1)
    with pytest.raises(ConfigError):
        BudgetTracker().record(-5)


def test_zero_budget_blocks_first_payment():
    t = BudgetTracker(budget_lamports=0)
    with pytest.raises(BudgetExhausted):
        t.check_spend(1)
    t.check_spend(0)  # zero-price is within a zero budget
