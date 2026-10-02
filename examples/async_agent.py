"""Async variant of the basic agent using AsyncKalyxClient."""

import asyncio
import sys

from kalyx_sdk.aclient import AsyncKalyxClient
from kalyx_sdk.errors import KalyxError


async def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "what is proof of relevance?"
    async with AsyncKalyxClient(max_price_lamports=100_000) as client:
        try:
            result = await client.query_and_retrieve(query)
        except KalyxError as exc:
            print(f"{type(exc).__name__}: {exc}")
            return 1
        if result.status != "access_granted":
            print("No relevant context found (free answer).")
            return 0
        print(result.context)
        print(f"\n{result.citation}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
