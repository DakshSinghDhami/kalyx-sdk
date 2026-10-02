# Contributing

Thanks for helping improve the KALYX SDK. This file covers the mechanics;
`docs/design.md` explains the architecture and invariants.

## Setup

```bash
git clone https://github.com/DakshSinghDhami/kalyx-sdk.git
cd kalyx-sdk
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Checks (must all pass)

```bash
ruff check src tests && ruff format --check src tests
mypy src/kalyx_sdk          # strict
pytest tests/ -q            # hermetic; no network, no mainnet
```

## Rules of the house

* **Never break the verify-before-pay order.** A payment may only happen
  after challenge verification, budget checks, cluster checks, the on-chain
  node preflight, and the funds check — in that order.
* **No mainnet.** There is deliberately no switch. Do not add one.
* **Payments are never retried.** Only idempotent calls (probe, config,
  retrieve with the same escrow+signature) may retry.
* **No secrets, ever.** Tests use seeded throwaway keypairs only. The
  pre-push scan (`grep` over tracked files for keys/IPs/absolute paths) must
  stay clean.
* **Small, logical commits** with Conventional Commit messages
  (`feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`).
* Tests are hermetic: `respx` fakes the gateway and JSON-RPC. A test that
  touches the network will be rejected.

## Adding an endpoint

1. Add the parser to `models.py` (shape-check everything, keep `raw`).
2. Add the method to `KalyxClient` and `AsyncKalyxClient`.
3. Map error statuses through `_map_error_status`; extend it if a new
   status carries meaning.
4. Add hermetic tests for the happy path and every mapped error.
