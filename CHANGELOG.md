# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project adheres to
[Semantic Versioning](https://semver.org/).

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
