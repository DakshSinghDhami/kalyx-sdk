# QA receipts — local full-stack E2E (localnet)

Date: 2026-10-02. SDK: `main` @ `013df95` (pre-release working tree).
Reference stack: `kalyx2` @ `2819496` (`docs(deploy): runbook, receipts, blockers, report + production URLs`).

## Environment

- `solana-test-validator 2.0.21` on `http://127.0.0.1:18899` (custom ports:
  rpc 18899, faucet 18898, gossip 18897, dynamic 19100–19190; ports 8042/8055
  and 8899 untouched).
- Program `GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2` loaded via
  `--bpf-program` from a **fresh `anchor build` of kalyx2 HEAD with one
  one-line fix** (see "Main-repo finding" below) — the prebuilt
  `target/deploy/kalyx.so` in kalyx2 is byte-identical to a fresh build of
  HEAD (`md5 72131a60…`), so the fix was the only delta.
- Gateway: `python3 -m gateway.server` from a `git archive` copy of kalyx2 on
  port 18042, **live mode** (`MOCK_SOLANA` unset, `KALYX_ALLOW_SIM` unset),
  `SOLANA_RPC_URL=http://127.0.0.1:18899`, throwaway consumed-DB and index.
- Throwaway keypairs (public keys only; files deleted after the run):
  - admin/publisher/settlement: `EXr5dTDYLsDtBkGmjk7DmY2f5ZVGbkYrw7ovCSJmhmnw`
  - consumer (SDK wallet): `G4bnpNLia9aoZGCtUK1LYS4MdWdVVwFqe77y8pmq8XEB`
  - treasury: `9wmYsmH7pYmHxGVmr3SFZDsfG3XR3FVvegGfUDGozKHY`

## Main-repo finding (config PDA under-allocation)

`initialize_protocol` at kalyx2 HEAD fails on a fresh cluster with
`AnchorError AccountDidNotDeserialize (3003)` on `protocol_config`:

- `ProtocolConfig` has 8 fields incl. `settlement_authority: Pubkey`
  (appended later; see the "Appended `settlement_authority` last…" comment):
  `8 + 32+32+2+8+8+8+1+32 = 131` bytes required.
- `InitializeProtocol` declares `space = 8 + 32 + 32 + 2 + 8 + 8 + 8 + 1`
  = **99 bytes** — the trailing `+ 32` for `settlement_authority` is missing.
  The create CPI succeeds; the immediately following serialize/deserialize
  cannot fit 123 bytes of fields into 91 bytes of payload and init aborts.
- Patching `space` to `8 + 32 + 32 + 2 + 8 + 8 + 8 + 1 + 32` makes
  initialization succeed; the receipts below are from that one-line-patched
  build.
- Impact beyond fresh clusters: the **live devnet** config account
  (`Bwca8f5zgZxdnqbaryxh16uTAzptQeeNLkSFqsw85EiF`) is **99 bytes** (checked
  via `getAccountInfo` on api.devnet.solana.com), i.e. production runs a
  pre-`settlement_authority` binary. Redeploying HEAD to devnet without a
  migration would make every instruction that deserializes `ProtocolConfig`
  (`settle_escrow`, `dispute_escrow`, `refund_escrow`, `update_refund_timeout`,
  `update_settlement_authority`) fail against the legacy 99-byte account.
  Reported in `BLOCKERS-qa.md`.

`KnowledgeNode::LEN` and `Escrow::LEN` were audited and match their structs.

## Flow receipts (all localnet)

1. `POST /v1/publish` (signed, bearer token) → `201`,
   node_id `3a7bc19d8a8fac39b1f674b01a6cf64d142e7c38ea068dc42ca6576fe3e868ea`.
2. `GET /v1/node/<id>/content` → `402` with `content_hash` and
   `node_address = 5CmrTCZX2EQp7Z5pzqxP31fLJWVWj8NL1Bit5LVhGXJy`.
   The SDK's own `derive_node_pda(author, content_hash)` reproduces this
   address exactly (asserted in the harness).
3. `register_knowledge_node` sig
   `5epyEGTHtuMDu8EctWo2F7emaJuRe6XSnmHjhTZwFx6zFmKcBEg849vJjzQhYLAQE8GxarJvXHkc1MJ5NLkfcTGA`;
   `deposit_stake` (1_000_000 lamports) sig
   `3tJ1Qj6RxSo1rrtcKuZHjWdmGcykTqQiRg2wCmZJsd6s58WHFBfGy8Cvw2AgqGG71p3a7DihgUSGGhScudwJ1SMn`
   (both built with the SDK's `ix_discriminator`/`derive_node_pda` helpers).
4. SDK `probe("unrelated gibberish about cooking recipes and pasta")`
   → `no_context_found` (relevance below the 0.6127 threshold; no payment).
5. SDK `query_and_retrieve("solana proof of relevance escrow settlement")`:
   - challenge: `chunk_id = 3a7bc19d8a8fac39`, `price_lamports = 20000`;
   - funding sig
     `3ex63CZJTjtTXE2MphHo7MFvEFsuSbHahrrVoG5QH25Y1shyzLHrpn5MysDCZkQ6VWnZcKQVnD4NS3gJvjEt4XPD`;
   - escrow PDA `8LQVDWR7dLKtQMXesnfHXUDmkbfQh4tTMTbqnByMhwTy`;
   - retrieve `200` in 0.64 s end-to-end; served content byte-identical to
     the published text; `content_safety.trust = "untrusted"`;
   - gateway verification receipt: `mode = "onchain"`, `verified = true`,
     amount 20_000, slot 525;
   - settlement: `settled = true`, sig
     `5Uo1JSjKb69gN2ZKb5NFMwVgHMYysSbSTyfeooqir43jPkVB11eWotXuYvPwQrLRdYQHnnyJBtbizeBjnnNzwoMm`.

### Lamport accounting (from the confirmed transactions)

| party | funding tx | settle tx | net |
| --- | --- | --- | --- |
| consumer | −1_820_680 (20_000 price + 1_795_680 escrow rent + 5_000 fee) | +1_795_680 (rent back on close) | **−25_000** |
| escrow PDA | +1_815_680 | −1_815_680 (closed) | 0 |
| publisher | — | +14_800 | +14_800 (net of rent/stake/fees on its own txs) |
| treasury | — | +200 (1% of 20_000) | **+200** |

Settle logs: `Escrow Settled: total=20000, author=19800, fee=200`.
Finalized balances after the run: consumer `49_999_975_000`,
treasury `5_000_000_200` — both match the tx math exactly.

### Negatives against the live gateway

- Replay of the same credential (`chunk_id`, `tx_signature`,
  `escrow_address`) after settlement → **HTTP 409** `Payment credential
  already used`.
- Forged `tx_signature` (`"1"*88`) → **HTTP 403** `Payment verification
  failed`.

## Notes

- Reading balances immediately after a `confirmed` retrieve races
  finalization (default RPC commitment is `finalized`); the first read showed
  pre-settlement balances. Re-reading after finality matched the transaction
  math. The receipts above use the transaction-level accounting.
- The gateway hot-reloaded the published chunk without a restart.
