"""Minimal autonomous-agent flow: probe for free, pay only when relevant.

Usage:
    python examples/basic_agent.py "your question"

Reads KALYX_GATEWAY_URL / KALYX_KEYPAIR_PATH / KALYX_RPC_URL from the
environment (see README). Devnet only — mainnet is always refused.
"""

import sys

from kalyx_sdk import KalyxClient
from kalyx_sdk.errors import (
    BudgetExhausted,
    InsufficientFunds,
    KalyxError,
    PaymentRequired,
    PriceExceedsBudget,
)


def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "how are KALYX escrows settled?"

    with KalyxClient(
        max_price_lamports=100_000,
        session_budget_lamports=500_000,
    ) as client:
        probe = client.probe(query)
        if probe.challenge is None:
            print("No relevant context found (free answer, nothing paid).")
            return 0

        ch = probe.challenge
        print(f"Relevant context: {ch.title!r} costs {ch.price_lamports} lamports")
        try:
            result = client.query_and_retrieve(query)
        except (PriceExceedsBudget, BudgetExhausted) as exc:
            print(f"Refused by budget: {exc}")
            return 2
        except InsufficientFunds as exc:
            print(
                f"Insufficient funds: need {exc.required_lamports}, have {exc.available_lamports}"
            )
            return 2
        except PaymentRequired as exc:
            print(f"Cannot pay: {exc}")
            return 2
        except KalyxError as exc:
            print(f"Retrieval failed: {type(exc).__name__}: {exc}")
            return 1

        print("\n--- context ---")
        print(result.context)
        print("\n--- citation ---")
        print(result.citation)
        print(f"\nspent this session: {client.spent_lamports} lamports")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
