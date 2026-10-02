"""Solana JSON-RPC reads for the KALYX SDK.

Plain JSON-RPC over the client's httpx session — no ``solana-py``
dependency. Covers exactly what the payment flow needs: genesis-hash cluster
gating, balance/rent reads, blockhash, broadcast, signature-status polling
and escrow account decoding.

Cluster policy fails closed: mainnet and unknown non-local genesis hashes are
refused with :class:`~kalyx_sdk.errors.ClusterMismatchError`; there is no
mainnet switch.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import httpx

from .errors import ChainError, ClusterMismatchError, GatewayUnavailable
from .models import EscrowState

# Well-known Solana genesis hashes (public constants).
GENESIS_MAINNET = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
GENESIS_DEVNET = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
GENESIS_TESTNET = "4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY"

#: Size in bytes of a KALYX Escrow account (8-byte discriminator + fields).
ESCROW_ACCOUNT_LEN = 8 + 32 + 32 + 8 + 32 + 1 + 8 + 8 + 1

#: Escrow account discriminator: sha256("account:Escrow")[:8].
ESCROW_DISCRIMINATOR = bytes([31, 213, 123, 187, 186, 22, 218, 155])

_STATUS_BY_BYTE = {0: "Created", 1: "Funded", 2: "Settled", 3: "Disputed"}

_LOOPBACK_HOSTMARKERS = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")  # noqa: S104


def classify_cluster(genesis_hash: str | None, rpc_url: str = "") -> str:
    """Map a genesis hash (+ loopback host hint) to a cluster label."""
    if genesis_hash == GENESIS_MAINNET:
        return "mainnet"
    if genesis_hash == GENESIS_DEVNET:
        return "devnet"
    if genesis_hash == GENESIS_TESTNET:
        return "testnet"
    url = (rpc_url or "").lower()
    if any(h in url for h in _LOOPBACK_HOSTMARKERS):
        return "localnet"
    return "unknown"


def assert_cluster_allowed(genesis_hash: str | None, rpc_url: str = "") -> str:
    """Return the cluster label, or raise :class:`ClusterMismatchError`.

    Only devnet and localnet are permitted. Mainnet is named explicitly in
    the error; unknown hashes fail closed.
    """
    label = classify_cluster(genesis_hash, rpc_url)
    if label == "mainnet":
        raise ClusterMismatchError(
            "the configured RPC is Solana MAINNET (genesis hash matched). "
            "The KALYX SDK is devnet-only and has no mainnet mode."
        )
    if label not in ("devnet", "localnet"):
        raise ClusterMismatchError(
            f"the configured RPC is not a permitted cluster (detected: {label}). "
            "Permitted: devnet or a localnet validator on a loopback host."
        )
    return label


def decode_escrow(raw: bytes, *, address: str = "") -> EscrowState:
    """Decode a raw KALYX escrow account into :class:`EscrowState`.

    Raises:
        ValueError: when the length or discriminator is wrong.
    """
    if len(raw) < ESCROW_ACCOUNT_LEN:
        raise ValueError(f"escrow account too short ({len(raw)} < {ESCROW_ACCOUNT_LEN})")
    if raw[:8] != ESCROW_DISCRIMINATOR:
        raise ValueError("account discriminator is not Escrow")
    consumer = raw[8:40]
    node = raw[40:72]
    amount = int.from_bytes(raw[72:80], "little")
    query_hash = raw[80:112]
    status_byte = raw[112]
    created_at = int.from_bytes(raw[113:121], "little", signed=True)
    created_at_slot = int.from_bytes(raw[121:129], "little")
    bump = raw[129]
    return EscrowState(
        address=address,
        consumer=_b58(consumer),
        node=_b58(node),
        amount_lamports=amount,
        query_hash=query_hash,
        status=_STATUS_BY_BYTE.get(status_byte, f"Unknown({status_byte})"),
        created_at=created_at,
        created_at_slot=created_at_slot,
        bump=bump,
    )


def _b58(raw: bytes) -> str:
    """Minimal base58 encode (avoids a dependency for 32-byte pubkeys)."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(raw, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = alphabet[r] + out
    pad = 0
    for b in raw:
        if b == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def _is_retryable_http(status: int) -> bool:
    return status in (408, 425, 429) or status >= 500


class RpcClient:
    """Synchronous JSON-RPC client bound to an httpx session."""

    def __init__(self, http: httpx.Client, rpc_url: str) -> None:
        self._http = http
        self._rpc_url = rpc_url
        self._req = 0

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        """One JSON-RPC call. Raises :class:`ChainError` on any failure."""
        self._req += 1
        payload = {"jsonrpc": "2.0", "id": self._req, "method": method, "params": params or []}
        try:
            resp = self._http.post(self._rpc_url, json=payload)
        except httpx.HTTPError as exc:
            raise ChainError(
                f"RPC {method} transport error: {type(exc).__name__}", retryable=True
            ) from exc
        if resp.status_code != 200:
            raise ChainError(
                f"RPC {method} HTTP {resp.status_code}",
                retryable=_is_retryable_http(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ChainError(f"RPC {method} returned non-JSON") from exc
        if not isinstance(data, dict):
            raise ChainError(f"RPC {method} returned malformed payload")
        if data.get("error") is not None:
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise ChainError(f"RPC {method} error: {msg}")
        return data.get("result")


class AsyncRpcClient:
    """Asynchronous JSON-RPC client bound to an httpx session."""

    def __init__(self, http: httpx.AsyncClient, rpc_url: str) -> None:
        self._http = http
        self._rpc_url = rpc_url
        self._req = 0

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        """One JSON-RPC call. Raises :class:`ChainError` on any failure."""
        self._req += 1
        payload = {"jsonrpc": "2.0", "id": self._req, "method": method, "params": params or []}
        try:
            resp = await self._http.post(self._rpc_url, json=payload)
        except httpx.HTTPError as exc:
            raise ChainError(
                f"RPC {method} transport error: {type(exc).__name__}", retryable=True
            ) from exc
        if resp.status_code != 200:
            raise ChainError(
                f"RPC {method} HTTP {resp.status_code}",
                retryable=_is_retryable_http(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ChainError(f"RPC {method} returned non-JSON") from exc
        if not isinstance(data, dict):
            raise ChainError(f"RPC {method} returned malformed payload")
        if data.get("error") is not None:
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise ChainError(f"RPC {method} error: {msg}")
        return data.get("result")


# --- shared read helpers (sync + async wrappers delegate here) ---------------


def genesis_hash(rpc: RpcClient) -> str | None:
    """Fetch the cluster genesis hash (None when the RPC has none)."""
    res = rpc.call("getGenesisHash")
    return res if isinstance(res, str) else None


async def agenesis_hash(rpc: AsyncRpcClient) -> str | None:
    """Async variant of :func:`genesis_hash`."""
    res = await rpc.call("getGenesisHash")
    return res if isinstance(res, str) else None


def balance(rpc: RpcClient, address: str) -> int:
    """Lamport balance of an address."""
    res = rpc.call("getBalance", [address])
    if not isinstance(res, dict) or not isinstance(res.get("value"), int):
        raise ChainError("RPC getBalance returned malformed result")
    return res["value"]


async def abalance(rpc: AsyncRpcClient, address: str) -> int:
    """Async variant of :func:`balance`."""
    res = await rpc.call("getBalance", [address])
    if not isinstance(res, dict) or not isinstance(res.get("value"), int):
        raise ChainError("RPC getBalance returned malformed result")
    return res["value"]


def latest_blockhash(rpc: RpcClient) -> str:
    """A recent blockhash at ``confirmed`` commitment."""
    res = rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])
    try:
        bh = res["value"]["blockhash"]
    except (TypeError, KeyError) as exc:
        raise ChainError("RPC getLatestBlockhash returned malformed result") from exc
    if not isinstance(bh, str) or not bh:
        raise ChainError("RPC getLatestBlockhash returned malformed result")
    return bh


async def alatest_blockhash(rpc: AsyncRpcClient) -> str:
    """Async variant of :func:`latest_blockhash`."""
    res = await rpc.call("getLatestBlockhash", [{"commitment": "confirmed"}])
    try:
        bh = res["value"]["blockhash"]
    except (TypeError, KeyError) as exc:
        raise ChainError("RPC getLatestBlockhash returned malformed result") from exc
    if not isinstance(bh, str) or not bh:
        raise ChainError("RPC getLatestBlockhash returned malformed result")
    return bh


def minimum_rent(rpc: RpcClient, data_len: int) -> int:
    """Rent-exempt minimum balance for an account of ``data_len`` bytes."""
    res = rpc.call("getMinimumBalanceForRentExemption", [data_len])
    if not isinstance(res, int):
        raise ChainError("RPC getMinimumBalanceForRentExemption returned malformed result")
    return res


async def aminimum_rent(rpc: AsyncRpcClient, data_len: int) -> int:
    """Async variant of :func:`minimum_rent`."""
    res = await rpc.call("getMinimumBalanceForRentExemption", [data_len])
    if not isinstance(res, int):
        raise ChainError("RPC getMinimumBalanceForRentExemption returned malformed result")
    return res


def get_account_info(rpc: RpcClient, address: str) -> dict[str, Any] | None:
    """Raw account info dict (base64 data), or None when the account is absent."""
    res = rpc.call("getAccountInfo", [address, {"encoding": "base64", "commitment": "confirmed"}])
    if res is None:
        return None
    if not isinstance(res, dict):
        raise ChainError("RPC getAccountInfo returned malformed result")
    return res.get("value")


async def aget_account_info(rpc: AsyncRpcClient, address: str) -> dict[str, Any] | None:
    """Async variant of :func:`get_account_info`."""
    res = await rpc.call(
        "getAccountInfo", [address, {"encoding": "base64", "commitment": "confirmed"}]
    )
    if res is None:
        return None
    if not isinstance(res, dict):
        raise ChainError("RPC getAccountInfo returned malformed result")
    return res.get("value")


def escrow_state(rpc: RpcClient, address: str, *, program_id: str) -> EscrowState | None:
    """Fetch and decode an escrow account; None when it does not exist.

    Raises :class:`GatewayUnavailable` when the account exists but is not a
    well-formed program-owned escrow (a shape the gateway would also reject).
    """
    value = get_account_info(rpc, address)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ChainError("RPC getAccountInfo returned malformed account value")
    if value.get("owner") != program_id:
        raise GatewayUnavailable("escrow account is not owned by the KALYX program")
    try:
        raw = base64.b64decode(value["data"][0])
        return decode_escrow(raw, address=address)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GatewayUnavailable(f"escrow account failed to decode: {exc}") from exc


async def aescrow_state(
    rpc: AsyncRpcClient, address: str, *, program_id: str
) -> EscrowState | None:
    """Async variant of :func:`escrow_state`."""
    value = await aget_account_info(rpc, address)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ChainError("RPC getAccountInfo returned malformed account value")
    if value.get("owner") != program_id:
        raise GatewayUnavailable("escrow account is not owned by the KALYX program")
    try:
        raw = base64.b64decode(value["data"][0])
        return decode_escrow(raw, address=address)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GatewayUnavailable(f"escrow account failed to decode: {exc}") from exc


def send_transaction(rpc: RpcClient, signed_tx_b64: str) -> str:
    """Broadcast a base64-encoded signed transaction; returns the signature."""
    res = rpc.call(
        "sendTransaction",
        [signed_tx_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
    )
    if not isinstance(res, str) or not res:
        raise ChainError("RPC sendTransaction returned malformed result")
    return res


async def asend_transaction(rpc: AsyncRpcClient, signed_tx_b64: str) -> str:
    """Async variant of :func:`send_transaction`."""
    res = await rpc.call(
        "sendTransaction",
        [signed_tx_b64, {"encoding": "base64", "preflightCommitment": "confirmed"}],
    )
    if not isinstance(res, str) or not res:
        raise ChainError("RPC sendTransaction returned malformed result")
    return res


def signature_status(rpc: RpcClient, signature: str) -> dict[str, Any] | None:
    """Confirmation status for one signature, or None when unseen."""
    res = rpc.call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
    try:
        return res["value"][0]
    except (TypeError, KeyError, IndexError) as exc:
        raise ChainError("RPC getSignatureStatuses returned malformed result") from exc


async def asignature_status(rpc: AsyncRpcClient, signature: str) -> dict[str, Any] | None:
    """Async variant of :func:`signature_status`."""
    res = await rpc.call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
    try:
        return res["value"][0]
    except (TypeError, KeyError, IndexError) as exc:
        raise ChainError("RPC getSignatureStatuses returned malformed result") from exc


def wait_for_confirmation(
    rpc: RpcClient,
    signature: str,
    *,
    timeout: float = 60.0,
    interval: float = 0.6,
    commitment: str = "confirmed",
) -> None:
    """Block until a transaction reaches ``confirmed`` (default) or fails.

    The gateway's verifier reads at ``confirmed`` commitment, so retrieving
    before this returns would race the RPC. Raises :class:`ChainError` on
    on-chain failure or timeout. Transient RPC errors during polling are
    tolerated until the deadline.
    """
    deadline = time.monotonic() + timeout
    wanted = ("confirmed", "finalized") if commitment == "confirmed" else (commitment, "finalized")
    while True:
        try:
            status = signature_status(rpc, signature)
        except ChainError as exc:
            if not exc.retryable:
                raise
            status = None
        if status is not None:
            if status.get("err") is not None:
                raise ChainError(
                    f"transaction failed on-chain: {status['err']}", signature=signature
                )
            if status.get("confirmationStatus") in wanted:
                return
        if time.monotonic() >= deadline:
            raise ChainError(
                f"transaction not {commitment} within {timeout:.0f}s", signature=signature
            )
        time.sleep(interval)


async def await_for_confirmation(
    rpc: AsyncRpcClient,
    signature: str,
    *,
    timeout: float = 60.0,
    interval: float = 0.6,
    commitment: str = "confirmed",
) -> None:
    """Async variant of :func:`wait_for_confirmation`."""
    deadline = time.monotonic() + timeout
    wanted = ("confirmed", "finalized") if commitment == "confirmed" else (commitment, "finalized")
    while True:
        try:
            status = await asignature_status(rpc, signature)
        except ChainError as exc:
            if not exc.retryable:
                raise
            status = None
        if status is not None:
            if status.get("err") is not None:
                raise ChainError(
                    f"transaction failed on-chain: {status['err']}", signature=signature
                )
            if status.get("confirmationStatus") in wanted:
                return
        if time.monotonic() >= deadline:
            raise ChainError(
                f"transaction not {commitment} within {timeout:.0f}s", signature=signature
            )
        await asyncio.sleep(interval)
