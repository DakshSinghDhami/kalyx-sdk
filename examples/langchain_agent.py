"""LangChain retriever example (requires the [langchain] extra)."""

import sys

from kalyx_sdk import KalyxClient
from kalyx_sdk.langchain import KalyxRetriever


def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "how does escrow settlement work?"
    with KalyxClient(max_price_lamports=100_000) as client:
        retriever = KalyxRetriever(client=client)
        docs = retriever.invoke(query)
        if not docs:
            print("No relevant context found (nothing paid).")
            return 0
        for doc in docs:
            print(doc.page_content)
            print(f"\ncitation: {doc.metadata.get('citation')}")
            print(f"escrow:   {doc.metadata.get('escrow_address')}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
