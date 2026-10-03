# BLOCKERS-qa.md — main-repo (kalyx2) findings from SDK QA

Found while QA-ing the Python SDK against kalyx2 @ `2819496`. These are
**main-repo** issues; the SDK repo only documents them here. Ordered by
severity.

## B1 — `InitializeProtocol` under-allocates the config PDA; fresh clusters cannot initialize (critical)

`programs/kalyx/src/lib.rs`, `InitializeProtocol`:

```rust
space = 8 + 32 + 32 + 2 + 8 + 8 + 8 + 1,   // = 99 bytes
```

but `ProtocolConfig` has 8 fields including the later-appended
`settlement_authority: Pubkey` (`admin 32 + treasury 32 + fee_bps 2 +
total_volume 8 + total_queries 8 + refund_timeout 8 + bump 1 +
settlement_authority 32 = 123`), so init requires **131** bytes. Observed on
a fresh local validator (SDK QA localnet run): the create CPI succeeds, then
Anchor aborts with `AccountDidNotDeserialize (3003)` on `protocol_config`.

Impact:

1. Any fresh cluster (new localnet, a future devnet reset, a new deployment)
    can never initialize the protocol at HEAD.
2. The **live devnet** config account
    (`Bwca8f5zgZxdnqbaryxh16uTAzptQeeNLkSFqsw85EiF`) is **99 bytes**
    (`getAccountInfo`, 2026-10-02) — production runs a
    pre-`settlement_authority` binary. Redeploying HEAD to devnet without a
    migration breaks every instruction that deserializes `ProtocolConfig`
    (`settle_escrow`, `dispute_escrow`, `refund_escrow`,
    `update_refund_timeout`, `update_settlement_authority`) against the
    legacy account.

Suggested fix: `space = 8 + 32 + 32 + 2 + 8 + 8 + 8 + 1 + 32` (or better,
derive via `ProtocolConfig::INIT_SPACE`), plus an explicit migration story
for the existing 99-byte devnet config (or a documented decision to
re-initialize protocol state on redeploy). SDK QA validated the one-line fix
on localnet: initialization then succeeds and the full escrow flow passes
(`docs/dev/qa-receipts-local.md`).

Note: the prebuilt `target/deploy/kalyx.so` in kalyx2 is byte-identical to a
fresh `anchor build` of HEAD (md5 `72131a60…`), so this is a source bug, not
a stale artifact.

## B2 — Production 402 challenges with empty `node_address` are unpayable (high)

Against `https://kalyxprotocol.xyz` (2026-10-02), two of two sampled topical
queries ("solana proof of relevance escrow settlement", "solana liquid
staking deep dive") returned 402 challenges whose `node_address` is empty.
The SDK correctly refuses these pre-payment
(`VerificationFailed(node_not_registered)`), so no funds are at risk — but
those chunks can never be purchased. Gateway stats at the time showed
`payments_verified: 7`, all plausibly from the demo agent's own node, so the
bulk of the 246-node corpus may be affected (dataset-imported chunks lacking
on-chain registration).

Suggested fix: register dataset nodes on-chain (or derive and attach
`node_address` at challenge time for registered nodes), and/or filter
unregistered chunks out of 402 candidates. A gateway-side self-check
(re-derive PDA, verify account exists) before issuing a challenge would
catch this.

## B3 — Production relevance gate never answers `no_context_found` (medium)

Gateway stats since boot: `queries: 70`, `challenges_issued: 70`,
`no_context: 0`. An SDK probe with deliberately off-topic text ("unrelated
gibberish about cooking recipes and pasta") returned `payment_required`.
Either production traffic has been 100% on-topic (unlikely given the sample
above) or scoring is miscalibrated/broken in the live configuration. On the
localnet QA gateway the same probe correctly returned `no_context_found`
against a dissimilar corpus, so this may be corpus- or config-specific
(e.g. threshold env override, embedding fallback behavior). Worth a
calibration re-run against the production index.

## B4 — `openapi.yaml` `QueryNone` enum disagrees with the gateway (low)

`openapi.yaml` declares the no-context status enum value as `no_context`,
but the gateway emits `status: "no_context_found"` (`gateway/server.py`),
which is what the SDK (and its tests) expect. Fix the spec enum to
`no_context_found`.

## Non-blocking notes

- The devnet faucet rate-limited all three permitted airdrop attempts during
  QA; consider documenting an alternative funding path for integrators
  (e.g. a faucet link list or a funded demo flow) so "paid devnet smoke" is
  reproducible by third parties.
- Gateway `403` retrieve bodies carry only `"Payment verification failed"`
  (no reason field). The SDK copes, but a machine-readable `reason` would
  let clients distinguish "escrow not found" from "wrong amount" without
  parsing on-chain state themselves.
