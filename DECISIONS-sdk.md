# SDK Decisions Log

## D1 — Reference checkout and program ID

The discovery rules point at the checkout under `/root` containing `gateway/`,
`programs/kalyx`, `kalyx_sdk/`, `web/`, `gateway/public_api.py` and a root
`openapi.yaml`. Two checkouts exist; only the one whose gateway matches the
live deployment at https://kalyxprotocol.xyz (`/v1/config` reports program
`GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2`, threshold `0.6127`) contains
`gateway/public_api.py` and a root `openapi.yaml`, so it is the reference.
The older checkout (program `hH73...`, threshold `0.647`) is a stale parallel
build. The vendored IDL (`idl/kalyx.json`) is pinned from the reference
checkout at commit `281949609eb87a4bba6e90a66f7c598b87a950f2` (2026-10-02).
The program ID is never hardcoded as a behavior default: the SDK reads it
from `GET /v1/config` and from each challenge's `escrow_params.program_id`
and cross-checks them; the IDL address is only the last-resort fallback.

## D2 — License

MIT, identical text and copyright holder as the main repository's LICENSE.

## D3 — HTTP layer

`httpx` for both sync and async clients (one dependency, shared API,
first-class timeout/session control). JSON-RPC to Solana over the same httpx
session — `solana-py` is not needed for the six RPC methods used.

## D4 — Keypair handling

`solders.keypair.Keypair` is the single in-memory representation. Loading
accepts a filesystem path (64-byte JSON array), raw 64 bytes, or an existing
`Keypair`. `__repr__` is masked; the secret never appears in logs or
exception messages.

## D5 — query_hash nonce

Escrow PDAs are keyed by `(consumer, query_hash)`. Repeating a query would
collide with the existing escrow and fail on-chain. Following the main repo's
demo runner, the SDK hashes `query + "\x1f" + <per-run nonce>`. The gateway
verifier reads `query_hash` from the on-chain escrow account (it does not
recompute it from the plaintext query), so salting does not break
verification. Confirmed against `gateway/verifier.py::verify_escrow_payment`.

## D6 — Content integrity check

`chunk_id == sha256(decrypted_content).hexdigest()[:16]` (see
`scripts/ingest.py::chunk_id` and the node-detail verification report). The
SDK recomputes it after retrieval and raises `VerificationFailed` on
mismatch. The 32-byte on-chain `content_hash` covers the whole pre-chunking
node body and is not recomputable client-side from a single served chunk, so
it is surfaced but not verified.

## D7 — Dispute vs refund

On-chain `dispute_escrow` requires the protocol admin signer; consumers
cannot call it, so the SDK does not expose it. The consumer-callable safety
valve is `refund_escrow` after `refund_timeout_slots` (4500 slots at
launch); exposed as `client.refund_expired_escrow(...)`. Disputes are
otherwise operator-arbitrated off-chain and documented as such.

## D8 — Cluster policy

Fail closed: allow devnet + localnet (loopback RPC), refuse mainnet and any
unknown genesis hash with `ClusterMismatchError`. Mirrors the gateway's
cluster guard. No mainnet switch exists.

## D9 — Config source of truth

`client.config()` = `GET /v1/config` (60s cached server-side; the SDK caches
per-client for its lifetime and revalidates per call chain only when a
challenge arrives). Program ID, threshold, default price, fee bps and refund
timeout all come from there, never hardcoded.
