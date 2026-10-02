"""Challenge verification tests: program id, price consistency, PDA re-derivation,
and the on-chain node preflight (with the node decoder itself)."""

import base64

import pytest
from solders.keypair import Keypair

from kalyx_sdk.chain import KNOWLEDGE_NODE_DISCRIMINATOR, decode_knowledge_node
from kalyx_sdk.client import verify_challenge_offchain, verify_node_preflight
from kalyx_sdk.errors import VerificationFailed
from kalyx_sdk.escrow import DEFAULT_PROGRAM_ID, derive_node_pda
from kalyx_sdk.models import Challenge

AUTHOR = Keypair.from_seed(bytes([3]) * 32)
AUTHOR_ADDR = str(AUTHOR.pubkey())
CONTENT_HASH_HEX = "cd" * 32
NODE_ADDR = str(
    derive_node_pda(AUTHOR_ADDR, bytes.fromhex(CONTENT_HASH_HEX), DEFAULT_PROGRAM_ID)[0]
)


def make_challenge(**over) -> Challenge:
    body = {
        "chunk_id": "0123456789abcdef",
        "title": "t",
        "domain": "example.com",
        "price_lamports": 20_000,
        "publisher_wallet": AUTHOR_ADDR,
        "author_wallet": AUTHOR_ADDR,
        "program_id": DEFAULT_PROGRAM_ID,
        "proof_of_relevance": {"similarity_score": 0.7, "confidence_threshold": 0.6},
        "escrow_params": {
            "program_id": DEFAULT_PROGRAM_ID,
            "node_address": NODE_ADDR,
            "content_hash": CONTENT_HASH_HEX,
            "price_lamports": 20_000,
        },
    }
    for k, v in over.items():
        if k == "escrow_params":
            body["escrow_params"] = {**body["escrow_params"], **v}
        else:
            body[k] = v
    return Challenge.from_dict(body)


def test_valid_challenge_passes():
    verify_challenge_offchain(make_challenge(), expected_program_id=DEFAULT_PROGRAM_ID)


def test_wrong_program_id_rejected():
    other = str(Keypair().pubkey())
    with pytest.raises(VerificationFailed) as ei:
        verify_challenge_offchain(
            make_challenge(program_id=other, escrow_params={"program_id": other}),
            expected_program_id=DEFAULT_PROGRAM_ID,
        )
    assert ei.value.reason == "program_id_mismatch"


def test_empty_node_address_rejected():
    with pytest.raises(VerificationFailed) as ei:
        verify_challenge_offchain(
            make_challenge(escrow_params={"node_address": ""}),
            expected_program_id=DEFAULT_PROGRAM_ID,
        )
    assert ei.value.reason == "node_not_registered"


def test_bad_content_hash_rejected():
    with pytest.raises(VerificationFailed) as ei:
        verify_challenge_offchain(
            make_challenge(escrow_params={"content_hash": "zz"}),
            expected_program_id=DEFAULT_PROGRAM_ID,
        )
    assert ei.value.reason == "bad_content_hash"


def test_tampered_node_address_rejected():
    fake = str(Keypair().pubkey())
    with pytest.raises(VerificationFailed) as ei:
        verify_challenge_offchain(
            make_challenge(escrow_params={"node_address": fake}),
            expected_program_id=DEFAULT_PROGRAM_ID,
        )
    assert ei.value.reason == "pda_mismatch"


def test_tampered_content_hash_rejected():
    with pytest.raises(VerificationFailed) as ei:
        verify_challenge_offchain(
            make_challenge(escrow_params={"content_hash": "ef" * 32}),
            expected_program_id=DEFAULT_PROGRAM_ID,
        )
    assert ei.value.reason == "pda_mismatch"


def test_fallback_to_publisher_wallet_as_author():
    """Challenges without author_wallet verify against publisher_wallet."""
    c = make_challenge()
    object.__setattr__(c, "author_wallet", None)  # frozen dataclass bypass
    verify_challenge_offchain(c, expected_program_id=DEFAULT_PROGRAM_ID)


def test_expected_program_falls_back_to_idl():
    verify_challenge_offchain(make_challenge(), expected_program_id=None)


# --- KnowledgeNode decoder + preflight ---------------------------------------


def _node_account_b64(
    *,
    price: int = 20_000,
    active: bool = True,
    stake: int = 5_000_000,
    content_hash: bytes = bytes.fromhex(CONTENT_HASH_HEX),
    uri: str = "https://example.com/meta.json",
) -> str:
    raw = bytearray(KNOWLEDGE_NODE_DISCRIMINATOR)
    raw += bytes(AUTHOR.pubkey())
    raw += bytes(AUTHOR.pubkey())  # payout wallet
    raw += content_hash
    raw += price.to_bytes(8, "little")
    raw += bytes([3])  # citation_depth
    enc = uri.encode()
    raw += len(enc).to_bytes(4, "little") + enc
    raw += (0).to_bytes(8, "little")  # total_earned
    raw += (0).to_bytes(8, "little")  # access_count
    raw += stake.to_bytes(8, "little")
    raw += (0).to_bytes(8, "little")  # slashed
    raw += bytes([1 if active else 0])
    raw += bytes([254])  # bump
    return base64.b64encode(bytes(raw)).decode()


def test_decode_knowledge_node_layout():
    st = decode_knowledge_node(base64.b64decode(_node_account_b64()))
    assert st.author == AUTHOR_ADDR
    assert st.price_lamports == 20_000
    assert st.is_active
    assert st.stake_lamports == 5_000_000
    assert st.content_hash.hex() == CONTENT_HASH_HEX


def test_decode_knowledge_node_rejects_wrong_discriminator():
    raw = bytearray(base64.b64decode(_node_account_b64()))
    raw[0] ^= 0x01
    with pytest.raises(ValueError):
        decode_knowledge_node(bytes(raw))


def test_decode_knowledge_node_rejects_short():
    with pytest.raises(ValueError):
        decode_knowledge_node(b"\x00" * 20)


def test_preflight_ok():
    node = decode_knowledge_node(base64.b64decode(_node_account_b64()))
    verify_node_preflight(make_challenge(), node)


def test_preflight_missing_node():
    with pytest.raises(VerificationFailed) as ei:
        verify_node_preflight(make_challenge(), None)
    assert ei.value.reason == "node_not_registered"


def test_preflight_inactive_node():
    node = decode_knowledge_node(base64.b64decode(_node_account_b64(active=False)))
    with pytest.raises(VerificationFailed) as ei:
        verify_node_preflight(make_challenge(), node)
    assert ei.value.reason == "node_inactive"


def test_preflight_onchain_price_higher():
    node = decode_knowledge_node(base64.b64decode(_node_account_b64(price=99_999)))
    with pytest.raises(VerificationFailed) as ei:
        verify_node_preflight(make_challenge(), node)
    assert ei.value.reason == "onchain_price_higher"


def test_preflight_hash_mismatch():
    node = decode_knowledge_node(
        base64.b64decode(_node_account_b64(content_hash=bytes.fromhex("ef" * 32)))
    )
    with pytest.raises(VerificationFailed) as ei:
        verify_node_preflight(make_challenge(), node)
    assert ei.value.reason == "content_hash_mismatch"
