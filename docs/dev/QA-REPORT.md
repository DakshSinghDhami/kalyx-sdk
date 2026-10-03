# KALYX Python SDK — QA Report

Date: 2026-10-02 · Scope: `kalyx-sdk` repo, `main` 7ce9c8b → HEAD ·
Reference deployment: kalyx2 `2819496`, program
`GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2` (devnet, threshold 0.6127).

Verdict: **release-ready as 0.1.1** after the fixes below. 246 tests green on
CPython 3.10–3.13, strict mypy clean, ruff clean, 12/12 mutation mutants
killed, full paid flow proven on a local validator against a live-mode
gateway with exact lamport accounting.

---

## 1. Packaging & supply chain (PASS)

- `pip install .` and `[langchain]`, `[llamaindex]`, `[dev]` extras install
  cleanly on 3.10.19 / 3.11.17 / 3.12.15 / 3.13.16.
- Wheel: 14 files, no tests or private modules, `py.typed` shipped,
  `LICENSE` in `dist-info/licenses/`, correct METADATA; imports from a clean
  venv outside the source tree.
- `pip-audit --strict`: 1 known vulnerability — `nltk 3.10.3`
  GHSA-8mgp-746c-j5xp (model-artifact path check bypass; **no upstream fix
  released**). It is a transitive dep of `llama-index-core` in the optional
  `[llamaindex]` extra; the SDK never imports nltk. Risk accepted and
  documented; recheck at the next llama-index bump.
- Dependency licenses all permissive (`jsonalias` MIT, `solders` Apache-2.0
  verified via GitHub API; no copyleft).
- `gitleaks` over all commits: clean. No secrets, local absolute paths,
  private IPs, or prototype names anywhere in tree or history;
  `.gitignore` covers keypair files.

## 2. Static checks & unit suite (PASS)

- `ruff check` + `ruff format --check src tests`: clean. (`ruff format
  --check .` additionally flags two Markdown files' code blocks under ruff
  0.16's Markdown formatting — cosmetic, CI scopes `src tests` only.)
- `mypy` strict: clean (13 source files).
- Baseline: 196 tests, 87% branch coverage. Now: **246 tests** on all four
  interpreters; new suites below.

## 3. Bugs found & fixed (all reproduced by failing tests first)

| # | Bug | Proof | Fix (commit) |
| --- | --- | --- | --- |
| 1 | **Session-budget overspend race**: `BudgetTracker` check-then-record was not atomic; two concurrent `query_and_retrieve` calls (threads *and* asyncio) both paid into a budget sized for one. | `tests/test_concurrency.py` reproducers (sync threads + asyncio, widened check→pay window) failed pre-fix | reserve/commit/release protocol under a lock; reservation released only when the payment provably never reached the wire (`fix: close session-budget overspend race under concurrency`) |
| 2 | **Post-payment 403/content-mismatch was unactionable**: bare `VerificationFailed`; the agent could not tell money had moved, which escrow held it, or that a self-refund exists. | adversarial scenarios `retrieve_mode=forbidden` / `tampered_content` | `VerificationFailed` gains `funding_signature`/`escrow_address`; message names the funding signature + `refund_expired_escrow(<pda>)`. `ReplayRejected` deliberately unwrapped (`fix: carry funding signature…`) |
| 3 | **Malformed 2xx/3xx bodies** (HTML error page, truncated JSON, redirects) surfaced as confusing `KalyxError("gateway error HTTP 200…")`. | respx + loopback-gateway scenarios | `_map_error_status` maps status < 400 with unusable body to `GatewayUnavailable`; redirects are never followed, so credentials cannot leak (`fix: map unusable success bodies…`) |
| 4 | **No pre-payment input bounds**: a challenge with a non-16-hex `chunk_id` could never satisfy the content binding but was paid anyway; `escrow_params.price_lamports ≥ 2^64` crashed with bare `OverflowError` while building the instruction. | adversarial scenarios `challenge_chunk_id="not-hex-at-all"`, `query_price=2**64+5` | `verify_challenge_offchain` rejects malformed chunk ids; `create_and_fund_ix` raises `ConfigError` above the u64 range (`fix: reject malformed chunk ids…`) |

Plus one test-side fix: the UA test hardcoded `kalyx-sdk/0.1.0` and now
derives from `__version__` (part of the 0.1.1 release commit).

## 4. Adversarial gateway matrix (22 scenarios, real loopback sockets)

`tests/evil_gateway.py` implements a scenario-driven mock gateway + JSON-RPC
server bound to 127.0.0.1 (loopback http is the SDK's explicit dev opt-in).
Every scenario asserts funds-safety and typed errors:

| Scenario | Expected & observed |
| --- | --- |
| price raised in `escrow_params` vs challenge | rejected pre-payment (`GatewayUnavailable`), 0 broadcasts |
| wrong `program_id` | `VerificationFailed(program_id_mismatch)`, 0 broadcasts |
| node PDA not deriving from `(author, content_hash)` | `VerificationFailed(pda_mismatch)`, 0 broadcasts |
| on-chain price higher than challenge | `VerificationFailed(onchain_price_higher)`, 0 broadcasts |
| on-chain content hash differs | `VerificationFailed(content_hash_mismatch)`, 0 broadcasts |
| `chunk_id` not 16 hex | `VerificationFailed(bad_chunk_id)`, 0 broadcasts |
| `price_lamports = 2**64 + 5` | typed `ConfigError`, 0 broadcasts, budget reservation released |
| zero price | `GatewayUnavailable` (malformed challenge), 0 broadcasts |
| prompt-injection content after funding | content returned verbatim as inert data; `content_safety.trust="untrusted"`, `prompt_injection_suspected=true` |
| retrieve 403 after funding | `VerificationFailed` carrying funding signature + refund guidance, exactly 1 broadcast |
| retrieve 409 replay | `ReplayRejected`, exactly 1 broadcast |
| tampered content after funding | `VerificationFailed(content_hash_mismatch)` + signature/guidance, 1 broadcast |
| gateway hangs (query/retrieve) | `GatewayUnavailable` after timeout+retry; no double pay |
| connection drop / chunked truncation mid-body | `GatewayUnavailable` |
| redirect to http / to another host | never followed; typed error; target host never contacted |
| 8 MiB response body | parsed/handled, no hang or crash |
| tx never confirms (blockhash expired) | `ChainError` with signature; `/v1/retrieve` never called |
| html content-type on 200 | `GatewayUnavailable` |

Mutation testing (12 targeted mutants: confirmation ordering, cap/budget
comparators, Retry-After parsing, content binding, program-id check, https
guard, mainnet guard, budget commit, replay mapping, PDA re-derivation,
on-chain price preflight): **12/12 KILLED** at HEAD. Two survivors in the
baseline suite (confirm-before-retrieve ordering, mainnet message) are now
pinned by `tests/test_robustness.py`.

Property-based tests (hypothesis) pin: `query_hash` determinism and nonce
sensitivity over arbitrary unicode; `parse_retry_after` clamping of skewed
HTTP-dates and arbitrary floats; challenge parsing accepts exactly the
shapes the gateway emits; budget reserve/commit/release conserves funds
under random interleavings.

## 5. Full-stack localnet E2E (PASS) — `docs/dev/qa-receipts-local.md`

Validator 2.0.21 + program built from kalyx2 HEAD + live-mode gateway
(`MOCK_SOLANA` unset), all on custom ports from a `/tmp` copy; throwaway
keys. Published → registered → staked → SDK `query_and_retrieve`:
challenge → `create_and_fund` → confirmed → retrieve → content
byte-identical → gateway verification `mode=onchain` → settled on-chain.
Lamport accounting verified to the lamport: consumer −25 000 (price + 1 tx
fee, rent refunded on escrow close), author +19 800, treasury +200 (1%
fee). Negatives: replayed credential → 409; forged signature → 403.

This run surfaced a **main-repo blocker**: `InitializeProtocol` at kalyx2
HEAD under-allocates the config PDA (99 bytes; `ProtocolConfig` needs 131
since `settlement_authority` was appended), so fresh clusters cannot
initialize, and the live devnet config account is still the legacy 99-byte
layout — see `BLOCKERS-qa.md`. The local E2E used a one-line-patched build
(disclosed in the receipts).

## 6. Devnet smoke (PASS within limits) — `docs/dev/qa-receipts-devnet.md`

10 of the 25 permitted requests, ≥5 s spacing, no 429, `/v1/demo/run`
untouched. The devnet faucet rejected all 3 permitted airdrop attempts, so
no paid devnet flow; the full pre-payment path was validated against
production instead (config match, 402 parse, offline challenge checks,
fail-closed refusal). Two production observations for the main repo:
relevance gate never answered `no_context_found` (70/70 challenged), and
sampled topical queries produced challenges with **empty `node_address`**
(unpayable chunks), which the SDK correctly refused pre-payment.

## 7. Docs & examples (PASS)

- All 8 `README.md` and all 6 `docs/usage.md` python blocks execute against
  the live local stack (recorded substitutions: env keypair, unique
  paid-query suffixes, fragment preludes, async wrapper). Refund fragments
  correctly produce a typed on-chain refusal for an already-settled escrow.
- `examples/basic_agent.py`, `async_agent.py`, `langchain_agent.py` run
  verbatim: paid flow with citation + budget printout, and the free
  no-context path on an off-topic query.
- CONTRIBUTING / SECURITY / CHANGELOG accurate; CI workflow matches the
  documented checks. `docs/errors.md` taxonomy matches the implementation
  (verified via the adversarial matrix).
- Release gap (not blocking this repo's code): `pip install kalyx-sdk` 404s
  — the package is not on PyPI yet. Either publish 0.1.1 or note
  "install from git" in the README until then.

## 8. Repo hygiene & CI (PASS)

- History: conventional commits, single author, monotonic timestamps; my QA
  work lands as 10 small green commits (test-first fixes) + release commit.
- `gh run list`: CI green on main (both runs pre-QA); will re-verify on the
  release commit before tagging `v0.1.1`.
- Repo description/topics set; `v0.1.0` tag exists and matches CHANGELOG.

## Compatibility notes (0.1.0 → 0.1.1)

- `BudgetTracker` spends are now accounted at broadcast (not retrieval) time;
  `remaining_lamports` subtracts outstanding holds. Behavior change only in
  failure windows that previously under-counted.
- `VerificationFailed` gains two attributes; constructor remains
  backwards-compatible (they default to `None`).
- Redirects and malformed success bodies now raise `GatewayUnavailable`
  instead of generic `KalyxError` (a subclass-visible change; both derive
  from `KalyxError`).

SDK QA DONE
