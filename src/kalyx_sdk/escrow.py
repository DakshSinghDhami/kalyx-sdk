"""Escrow instruction builders and PDA derivation, from the vendored IDL.

Implements exactly the consumer-callable surface of the KALYX escrow program
(``idl/kalyx.json``):

* ``create_and_fund(query_hash, amount)`` — create + fund an escrow PDA in one
  instruction (accounts: escrow, knowledge_node, consumer, system_program).
* ``refund_escrow()`` — consumer self-refund after ``refund_timeout_slots``
  (accounts: protocol_config, escrow, consumer).

Admin-only instructions (settle, dispute, register, stake, init) are
deliberately absent: the SDK is consumer-side only.

Byte layouts are pinned against the vendored IDL in
``tests/test_escrow_layout.py``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from .errors import ConfigError
from .wallet import Wallet

#: Program id of the devnet deployment, from the vendored IDL's ``address``.
#: Used only as a fallback default; the effective program id always comes
#: from the gateway's /v1/config + challenge (see client.py).
DEFAULT_PROGRAM_ID = "GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2"

SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")

#: PDA seed constants from the IDL (["escrow", consumer, query_hash] etc.).
SEED_ESCROW = b"escrow"
SEED_NODE = b"node"
SEED_CONFIG = b"config"

#: Separator between the user's query and the per-run nonce (D5).
NONCE_SEPARATOR = "\x1f"

#: Maximum lamport amount encodable in an instruction's u64 field.
_U64_MAX = 2**64 - 1


def ix_discriminator(name: str) -> bytes:
    """Anchor instruction discriminator: sha256("global:<name>")[:8]."""
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def query_hash(query: str, *, nonce: str | None = None) -> bytes:
    """SHA-256 of the query, salted with a per-run nonce by default.

    Escrow PDAs are keyed by ``(consumer, query_hash)``; without a nonce a
    repeated query would collide with the existing escrow account and fail
    on-chain. The gateway verifies the hash stored in the escrow account and
    never recomputes it from the plaintext query, so salting is safe.
    """
    nonce = nonce if nonce is not None else secrets.token_hex(8)
    return hashlib.sha256(f"{query}{NONCE_SEPARATOR}{nonce}".encode()).digest()


def derive_escrow_pda(
    consumer: Pubkey | str, qhash: bytes, program_id: Pubkey | str = DEFAULT_PROGRAM_ID
) -> tuple[Pubkey, int]:
    """Return ``(escrow_pda, bump)`` for seeds ["escrow", consumer, query_hash]."""
    consumer_pk = consumer if isinstance(consumer, Pubkey) else Pubkey.from_string(consumer)
    program_pk = program_id if isinstance(program_id, Pubkey) else Pubkey.from_string(program_id)
    if len(qhash) != 32:
        raise ConfigError(f"query_hash must be 32 bytes (got {len(qhash)})")
    return Pubkey.find_program_address([SEED_ESCROW, bytes(consumer_pk), qhash], program_pk)


def derive_node_pda(
    author: Pubkey | str,
    content_hash: bytes,
    program_id: Pubkey | str = DEFAULT_PROGRAM_ID,
) -> tuple[Pubkey, int]:
    """Return ``(node_pda, bump)`` for seeds ["node", author, content_hash]."""
    author_pk = author if isinstance(author, Pubkey) else Pubkey.from_string(author)
    program_pk = program_id if isinstance(program_id, Pubkey) else Pubkey.from_string(program_id)
    if len(content_hash) != 32:
        raise ConfigError(f"content_hash must be 32 bytes (got {len(content_hash)})")
    return Pubkey.find_program_address([SEED_NODE, bytes(author_pk), content_hash], program_pk)


def derive_config_pda(program_id: Pubkey | str = DEFAULT_PROGRAM_ID) -> tuple[Pubkey, int]:
    """Return ``(config_pda, bump)`` for seeds ["config"]."""
    program_pk = program_id if isinstance(program_id, Pubkey) else Pubkey.from_string(program_id)
    return Pubkey.find_program_address([SEED_CONFIG], program_pk)


def create_and_fund_ix(
    *,
    consumer: Pubkey,
    qhash: bytes,
    node_address: Pubkey | str,
    amount_lamports: int,
    program_id: Pubkey | str = DEFAULT_PROGRAM_ID,
) -> Instruction:
    """Build the ``create_and_fund(query_hash, amount)`` instruction.

    Data layout: discriminator(8) || query_hash(32) || amount(u64 LE).
    Accounts (IDL order): escrow (writable), knowledge_node (readonly),
    consumer (writable signer), system_program (readonly).
    """
    if amount_lamports <= 0:
        raise ConfigError("amount_lamports must be positive")
    if amount_lamports > _U64_MAX:
        raise ConfigError("amount_lamports exceeds the u64 range of the instruction")
    program_pk = program_id if isinstance(program_id, Pubkey) else Pubkey.from_string(program_id)
    node_pk = node_address if isinstance(node_address, Pubkey) else Pubkey.from_string(node_address)
    escrow_pda, _bump = derive_escrow_pda(consumer, qhash, program_pk)
    data = ix_discriminator("create_and_fund") + qhash + amount_lamports.to_bytes(8, "little")
    metas = [
        AccountMeta(pubkey=escrow_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=node_pk, is_signer=False, is_writable=False),
        AccountMeta(pubkey=consumer, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    return Instruction(program_pk, data, metas)


def refund_escrow_ix(
    *,
    consumer: Pubkey,
    qhash: bytes,
    program_id: Pubkey | str = DEFAULT_PROGRAM_ID,
) -> Instruction:
    """Build the consumer ``refund_escrow()`` instruction (no args).

    Accounts (IDL order): protocol_config (readonly PDA), escrow (writable),
    consumer (writable signer).
    """
    program_pk = program_id if isinstance(program_id, Pubkey) else Pubkey.from_string(program_id)
    config_pda, _cb = derive_config_pda(program_pk)
    escrow_pda, _eb = derive_escrow_pda(consumer, qhash, program_pk)
    metas = [
        AccountMeta(pubkey=config_pda, is_signer=False, is_writable=False),
        AccountMeta(pubkey=escrow_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=consumer, is_signer=True, is_writable=True),
    ]
    return Instruction(program_pk, ix_discriminator("refund_escrow"), metas)


@dataclass(frozen=True)
class SignedPayment:
    """A signed transaction plus the addresses a caller needs afterwards."""

    transaction_b64: str
    signature: str
    escrow_address: str
    query_hash: bytes
    recent_blockhash: str


def _sign(ix: Instruction, wallet: Wallet, recent_blockhash: str) -> tuple[Transaction, Hash]:
    try:
        blockhash = Hash.from_string(recent_blockhash)
    except ValueError as exc:
        raise ConfigError("recent_blockhash is not a valid base58 hash") from exc
    msg = Message.new_with_blockhash([ix], wallet.pubkey, blockhash)
    kp: Keypair = wallet.keypair
    return Transaction([kp], msg, blockhash), blockhash


def build_create_and_fund_tx(
    *,
    wallet: Wallet,
    qhash: bytes,
    node_address: str,
    amount_lamports: int,
    recent_blockhash: str,
    program_id: str = DEFAULT_PROGRAM_ID,
) -> SignedPayment:
    """Build and sign a ``create_and_fund`` transaction.

    Returns the base64-encoded signed transaction (ready for
    ``sendTransaction``), its signature, and the derived escrow PDA.
    """
    ix = create_and_fund_ix(
        consumer=wallet.pubkey,
        qhash=qhash,
        node_address=node_address,
        amount_lamports=amount_lamports,
        program_id=program_id,
    )
    tx, blockhash = _sign(ix, wallet, recent_blockhash)
    escrow_pda, _ = derive_escrow_pda(wallet.pubkey, qhash, program_id)
    return SignedPayment(
        transaction_b64=base64.b64encode(bytes(tx)).decode(),
        signature=str(tx.signatures[0]),
        escrow_address=str(escrow_pda),
        query_hash=qhash,
        recent_blockhash=str(blockhash),
    )


def build_refund_tx(
    *,
    wallet: Wallet,
    qhash: bytes,
    recent_blockhash: str,
    program_id: str = DEFAULT_PROGRAM_ID,
) -> SignedPayment:
    """Build and sign a consumer ``refund_escrow`` transaction."""
    ix = refund_escrow_ix(consumer=wallet.pubkey, qhash=qhash, program_id=program_id)
    tx, blockhash = _sign(ix, wallet, recent_blockhash)
    escrow_pda, _ = derive_escrow_pda(wallet.pubkey, qhash, program_id)
    return SignedPayment(
        transaction_b64=base64.b64encode(bytes(tx)).decode(),
        signature=str(tx.signatures[0]),
        escrow_address=str(escrow_pda),
        query_hash=qhash,
        recent_blockhash=str(blockhash),
    )
