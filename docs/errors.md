# Error Reference

Every failure the SDK raises derives from `kalyx_sdk.errors.KalyxError`.
Errors that wrap a gateway response may carry `request_id` (echoed from the
`X-Request-Id` header when present).

## Decision tree

```
KalyxError
├── PaymentRequired          payment needed but impossible/refused
│   ├── PriceExceedsBudget   price > per-call cap
│   └── DisabledError        gateway feature off (demo_disabled, cluster_refused)
├── BudgetExhausted          session budget would be exceeded
├── InsufficientFunds        payer < price + rent + fee
├── VerificationFailed       challenge / node / content / escrow verification
├── ClusterMismatchError     mainnet, unknown, or gateway/client mismatch
├── RateLimited              HTTP 429 (carries retry_after seconds)
├── ReplayRejected           HTTP 409 (signature reused for another escrow)
├── GatewayUnavailable       5xx / network / malformed body (carries reason)
├── ChainError               RPC / broadcast / confirmation (retryable flag)
└── ConfigError              bad client configuration
```

## Attributes worth branching on

| Error | Attribute | Meaning |
| --- | --- | --- |
| `PaymentRequired` | `.challenge` | raw challenge payload for inspection/retry |
| `PriceExceedsBudget` | `.challenge` | same |
| `BudgetExhausted` | `.spent_lamports`, `.budget_lamports` | current accounting |
| `InsufficientFunds` | `.required_lamports`, `.available_lamports` | shortfall detail |
| `VerificationFailed` | `.reason` | machine code (below) |
| `DisabledError` | `.reason` | `demo_disabled`, `cluster_refused`, ... |
| `GatewayUnavailable` | `.reason` | machine code from the gateway (`rpc_error`, ...) |
| `RateLimited` | `.retry_after` | seconds (header or body), else None |
| `ChainError` | `.retryable`, `.signature` | transient? / tx signature when known |

## `VerificationFailed` reasons

| `reason` | Raised when |
| --- | --- |
| `program_id_mismatch` | challenge program ≠ gateway/IDL program |
| `program_not_deployed` | `/v1/config` reports the program is not deployed |
| `node_not_registered` | challenge names no (or a nonexistent) on-chain node |
| `bad_content_hash` | challenge content hash is not 32 bytes of hex |
| `bad_pda_inputs` | wallets/hash cannot form a node PDA |
| `pda_mismatch` | `node_address` does not re-derive from `(author, content_hash)` |
| `node_inactive` | on-chain node is deactivated |
| `onchain_price_higher` | on-chain price exceeds the challenge price |
| `content_hash_mismatch` | served bytes ≠ committed chunk id (or node hash drift) |
| `not_found` | `/v1/nodes/{id}` 404 |
| `escrow_not_found` | refund target does not exist on-chain |
| `not_consumer` | refund target belongs to another consumer |
| `escrow_not_refundable` | escrow already Settled/Disputed |
| `escrow_not_expired` | refund timeout slots have not elapsed |
| `escrow_pda_mismatch` | derived refund PDA ≠ given address |

## Retrying safely

* `RateLimited` / `GatewayUnavailable` / `ChainError(retryable=True)` on
  **probe, config, or retrieve** are safe to retry — the client already
  does this per its `RetryPolicy`.
* A failed **payment broadcast** is never automatically retried. Inspect
  `ChainError.signature`: when set, the transaction may have landed anyway —
  check `client`'s chain helpers before deciding to repay.
