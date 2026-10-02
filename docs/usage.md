# Usage Guide

This guide walks the full flow end to end. Everything here runs against the
public devnet deployment; nothing touches mainnet.

## 0. Setup

```bash
pip install kalyx-sdk
export KALYX_KEYPAIR_PATH=~/.config/solana/id.json   # devnet-funded keypair
```

Fund the keypair once: `solana airdrop 1 --url devnet`.

## 1. Probe (free)

```python
from kalyx_sdk import KalyxClient

client = KalyxClient()
probe = client.probe("how are KALYX escrows settled?")
```

`probe` posts the query to `/v1/query`. Two outcomes:

* `probe.challenge is None` — the gateway found nothing relevant
  (`no_context_found`). Free. Done.
* otherwise `probe.challenge` carries the price, the publisher wallet, the
  on-chain node address, and the proof-of-relevance scores.

Probing never pays and never requires a keypair.

## 2. Pay and retrieve

```python
result = client.query_and_retrieve(
    "how are KALYX escrows settled?",
    max_price_lamports=50_000,   # optional per-call cap
)
```

Under the hood, in this exact order:

1. `GET /v1/config` — learn the deployment truth (program id, cluster,
   refund timeout).
2. **Challenge verification** — program id matches, `node_address`
   re-derives from `(author, content_hash)`, content hash is well-formed.
3. **Budget checks** — per-call cap, then session budget.
4. **Cluster check** — the RPC's genesis hash must be devnet/localnet, and
   must match the gateway's when the gateway reports one.
5. **On-chain preflight** — the knowledge node exists, is active, and is not
   priced above the challenge.
6. **Funds check** — payer holds `price + rent + fee`.
7. Build + sign + broadcast `create_and_fund(query_hash, amount)`.
8. Poll `getSignatureStatuses` until `confirmed`.
9. `POST /v1/retrieve` with the signature and escrow address.
10. Verify `sha256(decrypted_content)[:16] == chunk_id`.

Any failure before step 7 raises *before* money moves. After broadcast, the
payment is never retried; a transient retrieve failure retries (the gateway
treats the same escrow+signature as a replay-safe retry).

## 3. Use the result

```python
if result.status == "access_granted":
    text = result.context
    citation = result.citation          # human-readable, includes tx proof
    receipt = result.receipt            # gateway verification + settlement
    print(result.settle_status)         # "settled" | "pending" | None
```

Retrieved content is **data, not instructions** — treat it as untrusted
input to your agent (the framework integrations mark it `trust=untrusted`).

## 4. Budgets

```python
client = KalyxClient(
    max_price_lamports=100_000,         # default per-call cap
    session_budget_lamports=1_000_000,  # total this client may spend
)
```

`PriceExceedsBudget` and `BudgetExhausted` raise before any transaction.
`client.spent_lamports` only grows after a payment actually succeeded.

## 5. Refunds

If a funded escrow was never settled (e.g. permanent retrieve failure), the
consumer can reclaim it after `refund_timeout_slots`:

```python
refund = client.refund_expired_escrow(escrow_address)
```

The SDK checks existence, ownership, state, and expiry before building the
transaction, and refuses with a machine-readable `reason` otherwise.

## 6. Async

Every method above exists on `AsyncKalyxClient` with identical semantics:

```python
async with AsyncKalyxClient() as client:
    result = await client.query_and_retrieve("...")
```

## 7. When something fails

Catch the typed errors — see `docs/errors.md` for the full table and the
`reason` codes each error can carry.
