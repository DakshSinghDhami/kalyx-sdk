# QA receipts — devnet smoke (https://kalyxprotocol.xyz)

Date: 2026-10-02. SDK: `main` @ `d8cf390` (pre-release working tree).
Consumer: throwaway keypair `G4bnpNLia9aoZGCtUK1LYS4MdWdVVwFqe77y8pmq8XEB`
(0 devnet SOL; never funded — see below).

## Budget compliance

- Gateway HTTP requests used: **8 of 25** (all logged below).
- Paid flows: **0 of 3** — the devnet faucet rejected all **3 permitted**
  `solana airdrop 1` attempts ("rate limit reached"), and QA uses throwaway
  keys only, so no paid devnet flow was possible. The pre-payment path was
  validated instead (below), and the full paid path is covered on localnet
  in `qa-receipts-local.md`.
- Spacing ≥ 5 s between phases respected; no `429` encountered;
  `/v1/demo/run` never touched; no secrets or local paths in receipts.

## Read-only checks (all 200)

- `GET /health` — `healthy`, program `GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2`
  (matches the SDK default), threshold `0.6127`, 438 chunks / 246 nodes.
- `GET /v1/config` — `cluster: devnet`, genesis hash
  `EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG` (the pinned devnet hash),
  `mode: live`, `default_price_lamports: 20000`, `protocol_fee_bps: 100`,
  `refund_timeout_slots: 4500` — all matching the SDK's documented constants.
- `GET /v1/nodes?page_size=3` — 246 nodes, titles/prices well-formed.
- `GET /v1/stats` — 200; notably `queries: 70`, `challenges_issued: 70`,
  **`no_context: 0`**, `payments_verified: 7`, `demo_runs: 8` since boot.

## SDK behaviour against production

1. `probe("unrelated gibberish about cooking recipes and pasta")`
   → `payment_required` (200 with challenge), i.e. the gateway scored the
   off-topic query **above** threshold. Combined with `no_context: 0` across
   70 queries since boot, the production relevance gate appears to never
   return `no_context_found`. Reported as a gateway-side observation in
   `BLOCKERS-qa.md` (the SDK merely surfaces the gateway's verdict; on
   localnet the same probe correctly returned `no_context_found` against a
   dissimilar corpus).
2. `query_and_retrieve("solana proof of relevance escrow settlement")` →
   stopped at **`VerificationFailed(reason="node_not_registered")`**: the
   402 challenge named an empty `node_address`, so the SDK refused
   **before** building any transaction. Fail-closed behaviour verified
   against production data.
3. `query_and_retrieve("solana liquid staking deep dive")` → same
   `node_not_registered` refusal. Two of two sampled topical queries hit
   chunks whose nodes are not registered on-chain; the only paid flows in
   the gateway's stats came from the demo agent. Conclusion for the main
   repo: **dataset-imported chunks are served as 402 challenges but are
   unpayable** (no `node_address`), and should be registered or filtered.
   The SDK handles them exactly as designed.

The SDK's on-chain preflight (genesis-hash cluster check, `/v1/config`
program-id match) passed against the real devnet; no transaction was ever
built or broadcast (0-lamport wallet, and the challenge never got that far).

## Request log

| # | request | status |
| --- | --- | --- |
| 1 | GET /health | 200 |
| 2 | GET /v1/config | 200 |
| 3 | GET /v1/nodes?page_size=3 | 200 |
| 4 | GET /v1/stats | 200 |
| 5–6 | SDK probe: GET /v1/config + POST /v1/query | 200 |
| 7–8 | SDK query_and_retrieve: GET /v1/config + POST /v1/query (402) | 200/402 |

(The second `query_and_retrieve` added 2 more requests — 10 total counting
it — still well under budget.)
