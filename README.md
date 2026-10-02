# kalyx-sdk

Python SDK for the [KALYX protocol](https://kalyxprotocol.xyz): **pay-per-citation
context retrieval for autonomous AI agents** over HTTP 402 on Solana devnet.

A KALYX gateway answers a query with either free "no context found" or an
HTTP 402 challenge naming a price, a publisher, and an on-chain knowledge
node. This SDK verifies that challenge, pays it with a micro-transaction
on Solana, waits for confirmation, retrieves the content, and verifies the
served bytes against the committed content hash — all behind one call.

```python
from kalyx_sdk import KalyxClient

with KalyxClient(keypair="~/.config/solana/id.json",
                 max_price_lamports=100_000) as client:
    result = client.query_and_retrieve("how are KALYX escrows settled?")
    if result.status == "access_granted":
        print(result.context)
        print(result.citation)   # includes the on-chain funding signature
```

Nothing is paid unless the gateway actually finds relevant context and every
verification step passes. Free answers stay free.

## Install

```bash
pip install kalyx-sdk                     # core (httpx + solders)
pip install "kalyx-sdk[langchain]"        # + LangChain retriever/tool
pip install "kalyx-sdk[llamaindex]"       # + LlamaIndex retriever/tool
pip install "kalyx-sdk[dev]"              # tests, lint, typecheck
```

Requires Python ≥ 3.10.

## Quickstart

```python
from kalyx_sdk import KalyxClient

client = KalyxClient()  # env: KALYX_GATEWAY_URL / KALYX_KEYPAIR_PATH / KALYX_RPC_URL

# 1. Probe for free — never pays.
probe = client.probe("what is proof of relevance?")
if probe.challenge is None:
    print("no relevant context (free)")
else:
    print("would cost", probe.challenge.price_lamports, "lamports")

# 2. Pay + retrieve (raises typed errors on any verification failure).
result = client.query_and_retrieve(
    "what is proof of relevance?",
    max_price_lamports=50_000,     # per-call cap
)
```

Async-first agents use the identical async API:

```python
from kalyx_sdk.aclient import AsyncKalyxClient

async with AsyncKalyxClient(keypair="~/.config/solana/id.json") as client:
    result = await client.query_and_retrieve("...")
```

## What the SDK guarantees

* **Verify before paying.** The challenge's program id must match the
  gateway's `/v1/config` (or the vendored IDL), the node PDA must re-derive
  from `(author, content_hash)`, and the on-chain node must exist, be active,
  and not be priced above the challenge.
* **Cluster safety.** The client refuses mainnet (always) and any cluster it
  cannot identify by genesis hash. It also refuses when the gateway and your
  RPC endpoint are on *different* clusters — such a payment could never
  verify.
* **Confirm before retrieve.** The funding transaction must reach
  `confirmed` on-chain before the SDK calls `/v1/retrieve`.
* **Content integrity.** Served bytes must hash to the challenge's committed
  chunk id (`sha256(content)[:16]`), else `VerificationFailed`.
* **Budgets.** Optional per-call cap and session budget; refusals never
  consume budget and payments are never silently retried.
* **Idempotent retries.** Only probe/config/retrieve retry (429/5xx,
  honoring `Retry-After`). A broadcast payment is never re-broadcast.

## Error taxonomy

All SDK failures derive from `kalyx_sdk.errors.KalyxError`:

| Error | Meaning |
| --- | --- |
| `PaymentRequired` | a payment is needed but impossible/refused (carries the raw challenge) |
| `PriceExceedsBudget` | challenge price above the per-call cap |
| `BudgetExhausted` | session budget would be exceeded (carries spend/budget) |
| `InsufficientFunds` | payer cannot cover price + rent + fee (carries amounts) |
| `VerificationFailed` | challenge/content/cluster verification failed (machine `reason`) |
| `ClusterMismatchError` | mainnet, unknown, or gateway/client cluster mismatch |
| `RateLimited` | 429; carries `retry_after` seconds when the gateway says |
| `ReplayRejected` | 409; the signature was already used for a different escrow |
| `GatewayUnavailable` | 5xx/network; carries the machine `reason` when present |
| `DisabledError` | gateway feature disabled (`demo_disabled`, `cluster_refused`) |
| `ChainError` | RPC/broadcast/confirmation failure (`retryable` flag) |

## Budgets and safety rails

```python
client = KalyxClient(
    keypair="agent.json",
    max_price_lamports=100_000,        # per-call cap
    session_budget_lamports=1_000_000, # total this client may spend
)
client.spent_lamports              # running total
client.remaining_budget_lamports   # None when uncapped
```

The client also checks the payer holds `price + rent + fee` before building
any transaction (`InsufficientFunds`), so a failed payment never burns fees.

## Browsing the catalog (free)

```python
page = client.list_nodes(q="escrow", license="CC-BY-4.0", limit=10)
for node in page.items:
    print(node.title, node.price_lamports, node.registered)
detail = client.node_detail(page.items[0].node_id)
print(detail.verification_report)  # live recomputation by the gateway
```

## Refunding an expired escrow

If a payment succeeded but retrieval failed permanently, the consumer can
reclaim the escrow after the protocol's `refund_timeout_slots`:

```python
refund = client.refund_expired_escrow(escrow_address)
print(refund.refunded_lamports, refund.signature)
```

The SDK refuses to attempt the transaction when the escrow is missing,
foreign, already settled, or not yet expired.

## LangChain

```python
from kalyx_sdk.langchain import KalyxRetriever, kalyx_tool

retriever = KalyxRetriever(client=client)
docs = retriever.invoke("how are escrows settled?")
# docs[0].page_content  -> retrieved text
# docs[0].metadata      -> citation, escrow_address, funding_signature,
#                          trust="untrusted" (content is data, never instructions)

tool = kalyx_tool(client)   # StructuredTool for agents; errors become output
```

## LlamaIndex

```python
from kalyx_sdk.llamaindex import KalyxLlamaIndexRetriever, kalyx_query_tool

retriever = KalyxLlamaIndexRetriever(client=client)
nodes = retriever.retrieve("how are escrows settled?")

tool = kalyx_query_tool(client)  # FunctionTool for agents
```

## Configuration

| Parameter | Env var | Default |
| --- | --- | --- |
| `gateway_url` | `KALYX_GATEWAY_URL` | `https://kalyxprotocol.xyz` |
| `rpc_url` | `KALYX_RPC_URL` | `https://api.devnet.solana.com` |
| `keypair` | `KALYX_KEYPAIR_PATH` | — |
| `max_price_lamports` | — | uncapped |
| `session_budget_lamports` | — | uncapped |
| `timeout` | — | 30s |
| `confirm_timeout` | — | 60s |

Keypairs are standard 64-byte Solana JSON arrays. File permissions are
checked (warn, or hard-fail with `KALYX_STRICT_KEYPAIR_MODE=1`). The SDK
never logs key material, and `repr()` of a wallet shows only the address.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest tests/                    # 196 hermetic tests, no network
ruff check src tests && ruff format --check src tests
mypy src/kalyx_sdk
python -m build && twine check dist/*
```

Coverage is at 87% (bar: 85%). All tests are hermetic — `respx` fakes the
gateway and JSON-RPC; nothing touches the network or mainnet.

## Links

* Protocol site / live gateway: <https://kalyxprotocol.xyz>
* Program (devnet): `GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2`
* License: MIT
