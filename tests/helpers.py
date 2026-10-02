"""Shared test doubles for the SDK test suite.

Hermetic fakes only — no network. ``FakeRpc`` dispatches JSON-RPC calls by
method; ``make_challenge_body``/``node_account_b64`` build consistent
gateway payloads and on-chain account bytes.
"""

import base64
import hashlib
import json

import httpx
from solders.keypair import Keypair

from kalyx_sdk.chain import KNOWLEDGE_NODE_DISCRIMINATOR
from kalyx_sdk.escrow import DEFAULT_PROGRAM_ID, derive_node_pda

GATEWAY = "https://gateway.test"
RPC_URL = "https://rpc.test"
DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
BLOCKHASH = "4uQeVj5tqViQh7yWWGStvkEG1Zmhx6uasJtWCJziofM"
RENT = 1_795_680

AUTHOR = Keypair.from_seed(bytes([3]) * 32)
AUTHOR_ADDR = str(AUTHOR.pubkey())
CONTENT = "KALYX test content: escrow settlement notes for agents."
CHUNK_ID = hashlib.sha256(CONTENT.encode()).hexdigest()[:16]
CONTENT_HASH_HEX = "cd" * 32
NODE_ADDR = str(
    derive_node_pda(AUTHOR_ADDR, bytes.fromhex(CONTENT_HASH_HEX), DEFAULT_PROGRAM_ID)[0]
)
PRICE = 20_000


def config_body(**over):
    body = {
        "cluster": "devnet",
        "genesis_hash": DEVNET_GENESIS,
        "cluster_verified": True,
        "rpc_label": "api.devnet.solana.com",
        "program_id": DEFAULT_PROGRAM_ID,
        "program_deployed": True,
        "mode": "live",
        "relevance_threshold": 0.6127,
        "default_price_lamports": PRICE,
        "protocol_fee_bps": 100,
        "refund_timeout_slots": 4500,
        "dataset_version": "abc123",
        "node_count": 7,
        "settlement_pubkey": None,
    }
    body.update(over)
    return body


def challenge_body(**over):
    body = {
        "error": "Payment Required",
        "status_code": 402,
        "chunk_id": CHUNK_ID,
        "title": "Escrow notes",
        "domain": "example.com",
        "price_lamports": PRICE,
        "publisher_wallet": AUTHOR_ADDR,
        "author_wallet": AUTHOR_ADDR,
        "program_id": DEFAULT_PROGRAM_ID,
        "proof_of_relevance": {
            "similarity_score": 0.71,
            "confidence_threshold": 0.6127,
            "free_preview": "preview",
        },
        "escrow_params": {
            "program_id": DEFAULT_PROGRAM_ID,
            "node_address": NODE_ADDR,
            "content_hash": CONTENT_HASH_HEX,
            "price_lamports": PRICE,
        },
    }
    for k, v in over.items():
        if k == "escrow_params":
            body["escrow_params"] = {**body["escrow_params"], **v}
        else:
            body[k] = v
    return body


def no_context_body():
    return {
        "status": "no_context_found",
        "similarity_score": 0.12,
        "threshold": 0.6127,
        "payment_required": False,
        "message": "No relevant publisher context found in vault.",
    }


def retrieve_ok_body(**over):
    body = {
        "status": "access_granted",
        "chunk_id": CHUNK_ID,
        "title": "Escrow notes",
        "domain": "example.com",
        "decrypted_content": CONTENT,
        "content_trust": "untrusted",
        "content_safety": {"trust": "untrusted", "prompt_injection_suspected": False},
        "citation": "Source: Escrow notes (example.com) | Verified on Solana: abc...",
        "verification": {"valid": True, "mode": "onchain", "verified": True},
        "settlement": {"settled": True, "signature": "settle-sig"},
    }
    body.update(over)
    return body


_CONTENT_HASH_BYTES = bytes.fromhex(CONTENT_HASH_HEX)


def node_account_b64(*, price=PRICE, active=True, content_hash=None):
    if content_hash is None:
        content_hash = _CONTENT_HASH_BYTES
    raw = bytearray(KNOWLEDGE_NODE_DISCRIMINATOR)
    raw += bytes(AUTHOR.pubkey())
    raw += bytes(AUTHOR.pubkey())
    raw += content_hash
    raw += price.to_bytes(8, "little")
    raw += bytes([3])
    uri = b"https://example.com/meta.json"
    raw += len(uri).to_bytes(4, "little") + uri
    raw += (0).to_bytes(8, "little") * 2
    raw += (5_000_000).to_bytes(8, "little")
    raw += (0).to_bytes(8, "little")
    raw += bytes([1 if active else 0])
    raw += bytes([254])
    return base64.b64encode(bytes(raw)).decode()


class FakeRpc:
    """Dispatching JSON-RPC fake. ``calls`` records (method, params) in order."""

    def __init__(self, **overrides):
        self.calls: list[tuple[str, object]] = []
        self._handlers = {
            "getGenesisHash": lambda p: DEVNET_GENESIS,
            "getBalance": lambda p: {"value": 10**9},
            "getLatestBlockhash": lambda p: {"value": {"blockhash": BLOCKHASH}},
            "getMinimumBalanceForRentExemption": lambda p: RENT,
            "getAccountInfo": self._account_info,
            "sendTransaction": lambda p: "funding-sig-111",
            "getSignatureStatuses": lambda p: {
                "value": [{"confirmationStatus": "confirmed", "err": None}]
            },
        }
        self._handlers.update(overrides)
        self._accounts: dict[str, dict] = {
            NODE_ADDR: {
                "owner": DEFAULT_PROGRAM_ID,
                "data": [node_account_b64(), "base64"],
            }
        }

    def _account_info(self, params):
        addr = params[0]
        return {"value": self._accounts.get(addr)}

    def set_account(self, address, value):
        self._accounts[address] = value

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        method = payload["method"]
        self.calls.append((method, payload.get("params")))
        fn = self._handlers.get(method)
        if fn is None:
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": f"no fake for {method}"}}
            )
        try:
            result = fn(payload.get("params") or [])
        except Exception as exc:  # surfaced as a JSON-RPC error
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": str(exc)}}
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


# --- escrow account builder (refund tests) -----------------------------------

ESCROW_DISCRIMINATOR = bytes([31, 213, 123, 187, 186, 22, 218, 155])
ESCROW_STATUS = {"Created": 0, "Funded": 1, "Settled": 2, "Disputed": 3}


def escrow_account_b64(
    *,
    consumer,
    node=None,
    amount=PRICE,
    qhash=None,
    status="Funded",
    created_at=1_759_000_000,
    created_at_slot=1000,
    bump=255,
):
    from solders.pubkey import Pubkey

    node_pk = Pubkey.from_string(node) if node else Pubkey.from_string(NODE_ADDR)
    raw = bytearray(ESCROW_DISCRIMINATOR)
    raw += bytes(Pubkey.from_string(consumer) if isinstance(consumer, str) else consumer)
    raw += bytes(node_pk)
    raw += amount.to_bytes(8, "little")
    raw += qhash if qhash else bytes(32)
    raw += bytes([ESCROW_STATUS[status]])
    raw += created_at.to_bytes(8, "little", signed=True)
    raw += created_at_slot.to_bytes(8, "little")
    raw += bytes([bump])
    return base64.b64encode(bytes(raw)).decode()
