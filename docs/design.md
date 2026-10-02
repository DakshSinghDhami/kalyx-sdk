# KALYX SDK — Design

Client-side SDK for the KALYX protocol: an agent probes a gateway with a
natural-language query, receives an HTTP 402 payment challenge when relevant
publisher content exists, pays a micro-escrow on Solana devnet, and unlocks the
verified content with a citation receipt.

Scope: **consumer-side only**. The SDK never requires gateway or server
changes and exposes no publisher, admin, or settlement paths.

## The flow (what the wire actually looks like)

1. `POST /v1/query {"query": q}` →
   - `200 {"status": "no_context_found", ...}` — free, nothing charged, or
   - `402` challenge: `chunk_id`, `price_lamports`, `publisher_wallet`,
     `program_id`, `proof_of_relevance`, and `escrow_params`
     (`node_address`, `content_hash`, `query_hash_algorithm`,
     `escrow_pda_seeds`).
2. The SDK verifies the challenge (below), then builds, signs and broadcasts
   `create_and_fund(query_hash, amount)` to the escrow program. The escrow PDA
   is `["escrow", consumer, query_hash]`; `query_hash = sha256(query)` salted
   with a per-run nonce so repeated queries never collide on the PDA (the
   gateway verifies the on-chain hash, not the plaintext query).
3. The SDK polls `getSignatureStatuses` until the funding transaction reaches
   `confirmed` **before** retrieving (the gateway's verifier reads at
   `confirmed` commitment; retrieving earlier races the RPC).
4. `POST /v1/retrieve {"chunk_id", "tx_signature", "escrow_address"}` →
   `200` with `decrypted_content`, `citation`, `verification`, `settlement`.
   The escrow credential is single-use: a second retrieve gets `409`.
5. The SDK verifies `sha256(decrypted_content)[:16] == chunk_id` (the chunk id
   is committed to the index as a content hash) before trusting the body.

## Layout

```
src/kalyx_sdk/
  __init__.py        small stable public API + __version__
  client.py          KalyxClient (sync, httpx.Client)
  aclient.py         AsyncKalyxClient (same API, httpx.AsyncClient)
  models.py          typed config / challenge / result models
  errors.py          KalyxError hierarchy mapped to real gateway behavior
  wallet.py          keypair loading (path|bytes|Keypair), repr masking
  escrow.py          instruction builders + PDA derivation from the IDL
  chain.py           JSON-RPC reads, confirmation polling, account decode
  budget.py          per-call cap + session budget guard
  retry.py           capped exponential backoff + Retry-After parsing
  integrations/
    langchain.py     BaseRetriever + StructuredTool (extra: langchain)
    llamaindex.py    BaseToolSpec + FunctionTool (extra: llamaindex)
  py.typed
```

Dependencies: `httpx` (sync+async HTTP, one session, sane timeouts) and
`solders` (keypair/pubkey/instruction/transaction primitives). RPC is plain
JSON-RPC over httpx — no `solana-py`. Test doubles live under `tests/` only
(respx); the shipped package contains no simulate/mock path.

## Public API

```python
client = KalyxClient(
    gateway_url=...,            # env KALYX_GATEWAY_URL
    keypair=<path|bytes|Keypair>,  # env KALYX_KEYPAIR_PATH
    rpc_url=None,               # env KALYX_RPC_URL, default devnet
    max_price_lamports=...,     # per-call price cap
    session_budget_lamports=...,# session spend cap
    timeout=..., user_agent=...,
)
client.config()                  # GET /v1/config (program id, cluster, fees)
client.probe(query)              # relevance + price; never pays
client.query_and_retrieve(query, *, max_price_lamports=None) -> RetrievalResult
client.nodes(q=None, license=None, limit=25, cursor=0) -> NodePage
client.node(node_id) -> NodeDetail
client.refund_expired_escrow(escrow_address=...)  # consumer self-refund
client.escrow_state(escrow_address) -> EscrowState
client.close() / with KalyxClient(...) as c: ...
```

`AsyncKalyxClient` mirrors every method with `async`/`await`.

## Behavior rules (hard)

- **Verify before paying.** Program ID from the challenge must equal the ID
  the gateway reports in `/v1/config` (and the vendored IDL). Price must be
  within the per-call cap and session budget. The node PDA must re-derive from
  `(author, content_hash)` when the challenge carries an author. Escrow params
  must be internally consistent. Any failure raises *before* spending.
- **Confirm before retrieve.** Poll `getSignatureStatuses` at `confirmed`
  (short interval, bounded timeout) after broadcasting the funding tx.
- **Never auto-retry a payment.** Only safe reads and the `retrieve` call
  (same escrow credential) retry on transient network errors, with capped
  exponential backoff + jitter. `Retry-After` on 429 is honored exactly
  (delta-seconds or HTTP-date).
- **Insufficient funds checked before sending**: balance must cover
  `price + rent-exempt(escrow) + fee`, else `InsufficientFunds`.
- **Mainnet refusal.** The RPC's genesis hash is checked; mainnet (and any
  unknown non-local cluster) raises `ClusterMismatchError`. There is no
  mainnet switch.
- Served content is **untrusted data**, returned in a clearly named field,
  with the gateway's `content_safety` annotation carried through. The SDK
  never acts on instructions inside it.
- All gateway JSON is validated; malformed payloads raise `GatewayUnavailable`
  (unexpected shape) rather than surfacing `KeyError`s.

## Errors (all inherit `KalyxError`, none leak secrets)

| Error | When |
| --- | --- |
| `NoContextFound` | relevance below threshold (free; also a result status) |
| `PaymentRequired` | carries the verified challenge |
| `PriceExceedsBudget` | price > per-call cap |
| `BudgetExhausted` | session budget would be exceeded |
| `InsufficientFunds` | balance < price + rent + fees (pre-send) |
| `ReplayRejected` | 409 credential already used |
| `VerificationFailed` | 403 / content-hash mismatch / bad challenge |
| `RateLimited` | 429, exposes `retry_after` (seconds) |
| `GatewayUnavailable` | 503 / unreachable / malformed JSON |
| `DisabledError` | 403 disabled feature (e.g. demo disabled) |
| `ChainError` | RPC/broadcast/confirmation failure, carries signature |
| `ClusterMismatchError` | RPC is mainnet or an unknown cluster |
| `ConfigError` | bad local config (URL scheme, keypair, env) |

## Security

Keypairs load from a file path, raw bytes, or an existing `Keypair`; `repr`
is masked; secrets never appear in logs or exceptions; permissive keypair
file modes warn (POSIX, configurable). No telemetry. TLS verification always
on; `gateway_url` must be HTTPS unless it is loopback (explicit opt-in).

## Explicit non-goals

No mainnet, no PyPI publish, no publisher API, no admin/dispute/settle
instructions (admin-only on-chain), no gateway changes, no TS client, no MCP.
Consumer dispute resolution is operator-arbitrated off-chain; the on-chain
consumer safety valve is `refund_escrow` after `refund_timeout_slots`.
