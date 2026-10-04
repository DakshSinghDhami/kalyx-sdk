"""Docs must not drift from shipped facts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def test_readme_budget_exhausted_attributes_match_code() -> None:
    """The README error table must name the attributes BudgetExhausted
    actually carries (spent_lamports / budget_lamports)."""
    from kalyx_sdk.errors import BudgetExhausted

    exc = BudgetExhausted("x", spent_lamports=1, budget_lamports=2)
    assert exc.spent_lamports == 1 and exc.budget_lamports == 2

    table_line = next(
        line
        for line in README.read_text(encoding="utf-8").splitlines()
        if "BudgetExhausted" in line
    )
    assert "spent_lamports" in table_line and "budget_lamports" in table_line, (
        "README error table must name the real attributes "
        f"spent_lamports/budget_lamports; got: {table_line.strip()}"
    )
