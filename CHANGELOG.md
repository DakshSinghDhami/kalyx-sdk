# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-10-04

### Fixed

- **`kalyx_sdk.langchain` imports cleanly without the `langchain` extra**:
  runtime-evaluated annotations referenced names bound only inside the
  `try:` import block, so the import died with
  `NameError: name 'Document' is not defined` when `langchain-core` was
  absent. Annotations are now deferred; constructing the retriever/tool
  without the extra still raises the documented helpful `ImportError`.
  Regression tests simulate the missing extra in a subprocess (LlamaIndex
  module covered too — it was already clean).

### Docs

- **Install instructions no longer claim PyPI availability** — the index
  404s; README and the usage guide now pin the tagged git release
  (`git+https://github.com/DakshSinghDhami/kalyx-sdk.git@v0.1.2`).
- **Error taxonomy table names the real `BudgetExhausted` attributes**
  (`spent_lamports` / `budget_lamports`, previously "spend/budget").
- New `tests/test_docs_consistency.py` pins both doc facts so they cannot
  drift from the code again.

## [0.1.1] - 2026-10-02

Post-release QA hardening. No API breaks; all changes are bug fixes found
by adversarial testing (see `docs/dev/QA-REPORT.md`).

### Fixed

- **Session-budget race**: two concurrent `query_and_retrieve` calls sharing
  one client could both pass the budget check before either recorded its
  spend and overshoot `session_budget_lamports`. `BudgetTracker` now uses an
  atomic reserve/commit/release protocol; holds are released only when the
  payment provably never reached the wire. As a side effect, spend is now
  accounted at broadcast time, so a payment that succeeds on-chain but fails
  retrieval correctly counts against the budget.
- **Post-payment verification failures are now actionable**:
  `VerificationFailed` raised after the funding transaction confirmed
  carries `funding_signature` and `escrow_address` attributes and its
  message names the signature plus the `refund_expired_escrow` self-refund
  path. `ReplayRejected` (already-consumed credential) is deliberately not
  wrapped.
- **Unusable success bodies map to `GatewayUnavailable`**: a 2xx/3xx
  response with a non-JSON, truncated, or wrong-content-type body (and any
  redirect — redirects are never followed, so credentials cannot leak to
  another host) previously surfaced as a confusing generic `KalyxError`.
- **Pre-payment input bounds**: challenges with a malformed `chunk_id`
  (not 16 hex chars) are rejected before any transaction is built, and
  `escrow_params` amounts above the u64 range raise `ConfigError` instead
  of a bare `OverflowError` from the serializer.

### Added

- `tests/evil_gateway.py`: a scenario-driven adversarial gateway/RPC harness
  over real loopback sockets, with 22 scenarios (tampered challenges,
  replay, hangs, mid-body drops, redirects, prompt injection, oversized
  payloads, unconfirmed transactions).
- `tests/test_concurrency.py`, `tests/test_robustness.py`,
  `tests/test_property.py` (hypothesis): race reproducers,
  confirm-before-retrieve ordering, transport robustness, and
  property-based invariants. 246 tests total; all 12 targeted mutation
  mutants killed.
- `docs/dev/qa-receipts-local.md` and `docs/dev/qa-receipts-devnet.md`:
  full-stack localnet paid-flow receipts (with exact lamport accounting)
  and the devnet smoke log.
- Dev extras now include `hypothesis`.

## [0.1.0] - 2026-10-02

Initial release.

### Added

- `KalyxClient` / `AsyncKalyxClient` with the full verify-then-pay flow:
  `probe` (free), `query_and_retrieve` (402 challenge → on-chain escrow
  funding → confirmed → retrieved → content-hash verified).
- Challenge verification: program-id match, node PDA re-derivation,
  on-chain node preflight (exists, active, price not above challenge).
- Cluster safety: genesis-hash allowlist (devnet/localnet only, mainnet
  always refused), gateway/client cluster cross-check.
- Budget guards: per-call price cap and session budget with
  `spent_lamports` / `remaining_budget_lamports` introspection.
- Retry policy with exponential backoff + jitter honoring `Retry-After`
  (idempotent calls only; payments are never retried).
- Node catalog browsing (`list_nodes`, `node_detail` with the gateway's
  live verification report).
- Consumer self-refund for expired escrows (`refund_expired_escrow`,
  sync + async).
- LangChain integration (`KalyxRetriever`, `kalyx_tool`) and LlamaIndex
  integration (`KalyxLlamaIndexRetriever`, `KalyxToolSpec`,
  `kalyx_query_tool`) as optional extras.
- Typed error taxonomy rooted at `KalyxError` (payment, budget,
  verification, cluster, rate-limit, replay, availability, chain).
- 196 hermetic tests (87% coverage) including deterministic fuzz-style
  suites; GitHub Actions CI on Python 3.10–3.13 with ruff, strict mypy,
  pip-audit, and a build check.
