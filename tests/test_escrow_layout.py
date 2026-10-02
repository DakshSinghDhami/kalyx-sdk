"""Byte-layout tests pinning escrow instruction data and account decode
against the vendored IDL (idl/kalyx.json).

If the on-chain program changes, the IDL is re-vendored and these tests fail
until the builders/decoders are updated to match — that is the point.
"""

import hashlib
import json
from pathlib import Path

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from kalyx_sdk import chain
from kalyx_sdk.escrow import (
    DEFAULT_PROGRAM_ID,
    create_and_fund_ix,
    derive_config_pda,
    derive_escrow_pda,
    derive_node_pda,
    query_hash,
    refund_escrow_ix,
)

IDL_PATH = Path(__file__).resolve().parent.parent / "idl" / "kalyx.json"
IDL = json.loads(IDL_PATH.read_text())

CONSUMER = Keypair.from_seed(bytes([7]) * 32).pubkey()
QHASH = query_hash("hello", nonce="deadbeef")
CONTENT_HASH = bytes.fromhex("ab" * 32)
NODE_PDA, _NODE_BUMP = derive_node_pda(CONSUMER, CONTENT_HASH, DEFAULT_PROGRAM_ID)

# Golden values computed once from the IDL-defined layouts (see git history).
GOLDEN_ESCROW_PDA = "ESjegp1UthQyybvetezma16t3rhLJsjwSVgyqkKedrVA"
GOLDEN_NODE_PDA = "GVMqgz28XahArkFqRo3RueHi8rwz34tTeTdUa8uMMGjP"
GOLDEN_CONFIG_PDA = "Bwca8f5zgZxdnqbaryxh16uTAzptQeeNLkSFqsw85EiF"
GOLDEN_CREATE_AND_FUND_DATA = (
    "51f153b313cba740"  # discriminator
    "81394c446cd541e1fa1a20cc5e136167fa71aaaad71459aa9ee072d92a8c2152"  # query_hash
    "204e000000000000"  # 20000 u64 LE
)


def _idl_ix(name):
    return next(i for i in IDL["instructions"] if i["name"] == name)


def _idl_type(name):
    return next(t for t in IDL["types"] if t["name"] == name)


def test_idl_program_address_matches_default():
    assert IDL["address"] == DEFAULT_PROGRAM_ID


def test_all_instruction_discriminators_match_idl():
    for ix in IDL["instructions"]:
        expected = hashlib.sha256(f"global:{ix['name']}".encode()).digest()[:8]
        assert bytes(ix["discriminator"]) == expected, ix["name"]


def test_all_account_discriminators_match_idl():
    for acct in IDL["accounts"]:
        expected = hashlib.sha256(f"account:{acct['name']}".encode()).digest()[:8]
        assert bytes(acct["discriminator"]) == expected, acct["name"]


def test_escrow_discriminator_constant_matches_idl():
    idl_escrow = next(a for a in IDL["accounts"] if a["name"] == "Escrow")
    assert chain.ESCROW_DISCRIMINATOR == bytes(idl_escrow["discriminator"])


def test_seed_constants_match_idl():
    caf = _idl_ix("create_and_fund")
    escrow_seeds = next(a for a in caf["accounts"] if a["name"] == "escrow")["pda"]["seeds"]
    assert escrow_seeds[0] == {"kind": "const", "value": list(b"escrow")}
    assert escrow_seeds[1] == {"kind": "account", "path": "consumer"}
    assert escrow_seeds[2] == {"kind": "arg", "path": "query_hash"}
    node_seeds = next(a for a in caf["accounts"] if a["name"] == "knowledge_node")["pda"]["seeds"]
    assert node_seeds[0] == {"kind": "const", "value": list(b"node")}
    cfg_seeds = _idl_ix("refund_escrow")["accounts"][0]["pda"]["seeds"]
    assert cfg_seeds[0] == {"kind": "const", "value": list(b"config")}


def test_create_and_fund_data_layout_pinned():
    caf = _idl_ix("create_and_fund")
    # IDL says: args are query_hash ([u8;32]) then amount (u64).
    assert [a["name"] for a in caf["args"]] == ["query_hash", "amount"]
    ix = create_and_fund_ix(
        consumer=CONSUMER,
        qhash=QHASH,
        node_address=NODE_PDA,
        amount_lamports=20_000,
    )
    assert bytes(ix.data).hex() == GOLDEN_CREATE_AND_FUND_DATA
    assert bytes(ix.data)[:8] == bytes(caf["discriminator"])
    assert bytes(ix.data)[8:40] == QHASH
    assert int.from_bytes(bytes(ix.data)[40:48], "little") == 20_000
    assert len(bytes(ix.data)) == 48


def test_create_and_fund_accounts_match_idl():
    caf = _idl_ix("create_and_fund")
    ix = create_and_fund_ix(
        consumer=CONSUMER, qhash=QHASH, node_address=NODE_PDA, amount_lamports=1
    )
    idl_accounts = caf["accounts"]
    assert len(ix.accounts) == len(idl_accounts)
    for meta, spec in zip(ix.accounts, idl_accounts, strict=True):
        assert meta.is_signer == bool(spec.get("signer", False)), spec["name"]
        assert meta.is_writable == bool(spec.get("writable", False)), spec["name"]
    # Concrete addresses in IDL order.
    assert str(ix.accounts[0].pubkey) == GOLDEN_ESCROW_PDA
    assert ix.accounts[1].pubkey == NODE_PDA
    assert ix.accounts[2].pubkey == CONSUMER
    assert str(ix.accounts[3].pubkey) == "11111111111111111111111111111111"
    assert ix.program_id == Pubkey.from_string(DEFAULT_PROGRAM_ID)


def test_refund_escrow_accounts_match_idl():
    ref = _idl_ix("refund_escrow")
    assert ref["args"] == []
    ix = refund_escrow_ix(consumer=CONSUMER, qhash=QHASH)
    assert bytes(ix.data) == bytes(ref["discriminator"])
    idl_accounts = ref["accounts"]
    assert len(ix.accounts) == len(idl_accounts)
    for meta, spec in zip(ix.accounts, idl_accounts, strict=True):
        assert meta.is_signer == bool(spec.get("signer", False)), spec["name"]
        assert meta.is_writable == bool(spec.get("writable", False)), spec["name"]
    assert str(ix.accounts[0].pubkey) == GOLDEN_CONFIG_PDA
    assert str(ix.accounts[1].pubkey) == GOLDEN_ESCROW_PDA
    assert ix.accounts[2].pubkey == CONSUMER


def test_pda_derivations_pinned():
    pda, bump = derive_escrow_pda(CONSUMER, QHASH, DEFAULT_PROGRAM_ID)
    assert str(pda) == GOLDEN_ESCROW_PDA
    assert 0 <= bump <= 255
    assert str(derive_node_pda(CONSUMER, CONTENT_HASH, DEFAULT_PROGRAM_ID)[0]) == GOLDEN_NODE_PDA
    assert str(derive_config_pda(DEFAULT_PROGRAM_ID)[0]) == GOLDEN_CONFIG_PDA
    # Off-curve: PDAs must not be valid ed25519 points (cannot be signed for).
    for addr in (GOLDEN_ESCROW_PDA, GOLDEN_NODE_PDA, GOLDEN_CONFIG_PDA):
        assert not Pubkey.from_string(addr).is_on_curve()


def test_query_hash_is_salted_sha256():
    expected = hashlib.sha256(b"hello\x1fdeadbeef").digest()
    assert QHASH == expected
    # Distinct default nonces: repeated calls never collide.
    assert query_hash("hello") != query_hash("hello")


def test_escrow_account_decode_matches_idl_fields():
    """Build a synthetic account following the IDL's Escrow field order and
    verify decode_escrow reads every field from the right offset."""
    escrow_ty = _idl_type("Escrow")
    field_order = [f["name"] for f in escrow_ty["type"]["fields"]]
    assert field_order == [
        "consumer",
        "node",
        "amount",
        "query_hash",
        "status",
        "created_at",
        "created_at_slot",
        "bump",
    ]
    variants = [v["name"] for v in _idl_type("EscrowStatus")["type"]["variants"]]
    assert variants == ["Created", "Funded", "Settled", "Disputed"]

    raw = bytearray(chain.ESCROW_DISCRIMINATOR)
    raw += b"\x11" * 32  # consumer
    raw += bytes(NODE_PDA)  # node
    raw += (555_000).to_bytes(8, "little")  # amount
    raw += QHASH  # query_hash
    raw += bytes([2])  # Settled
    raw += (-42).to_bytes(8, "little", signed=True)  # created_at
    raw += (9_876_543).to_bytes(8, "little")  # created_at_slot
    raw += bytes([255])  # bump

    st = chain.decode_escrow(bytes(raw), address="addr")
    assert st.amount_lamports == 555_000
    assert st.node == str(NODE_PDA)
    assert st.query_hash == QHASH
    assert st.status == "Settled"
    assert st.created_at == -42
    assert st.created_at_slot == 9_876_543
    assert st.bump == 255
    assert len(raw) == chain.ESCROW_ACCOUNT_LEN


@pytest.mark.parametrize("bad_len", [0, 7, 129])
def test_decode_rejects_bad_lengths(bad_len):
    with pytest.raises(ValueError):
        chain.decode_escrow(b"\x00" * bad_len)


def test_signed_transaction_roundtrip():
    from kalyx_sdk.escrow import build_create_and_fund_tx
    from kalyx_sdk.wallet import load_wallet

    wallet = load_wallet(Keypair.from_seed(bytes([7]) * 32))
    payment = build_create_and_fund_tx(
        wallet=wallet,
        qhash=QHASH,
        node_address=str(NODE_PDA),
        amount_lamports=20_000,
        recent_blockhash="4uQeVj5tqViQh7yWWGStvkEG1Zmhx6uasJtWCJziofM",
    )
    assert payment.escrow_address == GOLDEN_ESCROW_PDA
    assert payment.signature  # base58, non-empty
    # The transaction must deserialize back with one instruction to the program.
    import base64

    from solders.transaction import Transaction

    tx = Transaction.from_bytes(base64.b64decode(payment.transaction_b64))
    assert len(tx.message.instructions) == 1
    assert str(tx.message.instructions[0].program_id(tx.message.account_keys)) == (
        DEFAULT_PROGRAM_ID
    )
