"""Docs must not drift from shipped facts.

Pinned regressions:
- The package is NOT on PyPI, so no doc may show a bare
  ``pip install kalyx-sdk`` (it would fail for users); installs go through
  the tag-pinned git URL.
- The README error table must name the attributes ``BudgetExhausted``
  actually carries (``spent_lamports`` / ``budget_lamports``), matching
  ``kalyx_sdk.errors``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
USAGE = ROOT / "docs" / "usage.md"

# A pip line that installs the published distribution by name (extras allowed),
# excluding local editable installs (`pip install -e ...`).
_BARE_PYPI_INSTALL = re.compile(r"pip install(?!.*\s-e\s)[^\n`]*\bkalyx-sdk\b")


def _offending_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if _BARE_PYPI_INSTALL.search(line) and "git+" not in line
    ]


def test_readme_has_no_bare_pypi_install() -> None:
    offenders = _offending_lines(README)
    assert not offenders, (
        "README shows PyPI install commands but kalyx-sdk is not on PyPI; "
        "use the tag-pinned git URL instead: " + "; ".join(offenders)
    )


def test_usage_guide_has_no_bare_pypi_install() -> None:
    offenders = _offending_lines(USAGE)
    assert not offenders, (
        "docs/usage.md shows a PyPI install command but kalyx-sdk is not on "
        "PyPI; use the tag-pinned git URL instead: " + "; ".join(offenders)
    )


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
