# Security Policy

## Scope

This SDK moves real (devnet) SOL. The load-bearing guarantees:

* private keys are never logged, repr'd, or sent anywhere but the local
  signer;
* mainnet is always refused (genesis-hash check, fail-closed);
* challenges are verified (program id, PDA re-derivation, on-chain
  preflight) before any payment;
* served content is re-hashed against the committed chunk id;
* payments are never retried after broadcast.

## Reporting

Please report vulnerabilities privately to the maintainer via GitHub's
private vulnerability reporting on this repository. Do not open a public
issue for anything that could move funds or leak keys.

## Keypair handling

`kalyx_sdk.wallet.load_wallet` reads standard 64-byte Solana JSON keypair
files. It warns when the file is group/world-readable and hard-fails when
`KALYX_STRICT_KEYPAIR_MODE=1` is set. The `Wallet` wrapper masks `str()`/
`repr()` — only the public address is ever shown.
