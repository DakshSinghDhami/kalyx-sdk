# SDK Blockers

(none yet)

## Wishlist (not blockers; would need gateway/server changes — out of scope)

- The 402 challenge does not carry `author_wallet`; node-PDA re-derivation
  falls back to `publisher_wallet` when absent. A challenge field with the
  node author would make the check exact for nodes whose author differs from
  the payout wallet.
- `/v1/retrieve` 403 responses return only `{"error": "Payment verification
  failed"}`; the verifier's machine-readable `reason` is logged server-side
  but not returned. Returning a safe reason enum would let the SDK surface
  sharper `VerificationFailed` causes without leaking internals.
- No consumer-initiated on-chain dispute exists (admin-only); an off-chain
  dispute ticket endpoint would close the loop for bad content.
