"""Unit tests for models.py parsing/validation."""

import pytest

from kalyx_sdk.errors import GatewayUnavailable
from kalyx_sdk.models import (
    Challenge,
    EscrowState,
    NodeDetail,
    NodePage,
    ProtocolConfig,
)

CHALLENGE = {
    "error": "Payment Required",
    "status_code": 402,
    "chunk_id": "0123456789abcdef",
    "title": "Solana escrow notes",
    "domain": "example.com",
    "price_lamports": 20_000,
    "publisher_wallet": "Pub1111111111111111111111111111111111111",
    "program_id": "GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2",
    "proof_of_relevance": {
        "similarity_score": 0.71,
        "confidence_threshold": 0.6127,
        "free_preview": "preview text",
    },
    "escrow_params": {
        "program_id": "GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2",
        "node_address": "Node111111111111111111111111111111111111",
        "content_hash": "ab" * 32,
        "price_lamports": 20_000,
    },
}


def test_challenge_parses():
    c = Challenge.from_dict(CHALLENGE)
    assert c.chunk_id == "0123456789abcdef"
    assert c.price_lamports == 20_000
    assert c.node_address == "Node111111111111111111111111111111111111"
    assert c.content_hash == "ab" * 32
    assert c.payable_onchain
    assert c.similarity_score == 0.71
    assert c.threshold == 0.6127
    assert c.free_preview == "preview text"


def test_challenge_rejects_missing_chunk_id():
    bad = {**CHALLENGE, "chunk_id": ""}
    with pytest.raises(GatewayUnavailable):
        Challenge.from_dict(bad)


def test_challenge_rejects_non_positive_price():
    bad = {**CHALLENGE, "price_lamports": 0}
    with pytest.raises(GatewayUnavailable):
        Challenge.from_dict(bad)


def test_challenge_rejects_program_mismatch():
    bad = {**CHALLENGE, "escrow_params": {**CHALLENGE["escrow_params"], "program_id": "X" * 44}}
    with pytest.raises(GatewayUnavailable):
        Challenge.from_dict(bad)


def test_challenge_rejects_price_mismatch():
    bad = {**CHALLENGE, "escrow_params": {**CHALLENGE["escrow_params"], "price_lamports": 1}}
    with pytest.raises(GatewayUnavailable):
        Challenge.from_dict(bad)


def test_challenge_tolerates_missing_escrow_params():
    body = {k: v for k, v in CHALLENGE.items() if k != "escrow_params"}
    c = Challenge.from_dict(body)
    assert c.node_address == ""
    assert not c.payable_onchain
    assert c.content_hash == ""


def test_challenge_rejects_bool_price():
    bad = {**CHALLENGE, "price_lamports": True}
    with pytest.raises(GatewayUnavailable):
        Challenge.from_dict(bad)


def test_protocol_config_parses_live_shape():
    cfg = ProtocolConfig.from_dict(
        {
            "cluster": "devnet",
            "genesis_hash": "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG",
            "cluster_verified": True,
            "rpc_label": "api.devnet.solana.com",
            "program_id": "GKaQ7LJbVQ6pUtSq3oWR1WZvyE7h9PMM7SF2vLTpGAS2",
            "program_deployed": True,
            "mode": "live",
            "relevance_threshold": 0.6127,
            "default_price_lamports": 20_000,
            "protocol_fee_bps": 100,
            "refund_timeout_slots": 4500,
            "dataset_version": "123411d63535",
            "node_count": 246,
            "settlement_pubkey": "2Azu8oA6H56SuGTjCeWzjWiAxYjV4uwPefKaPynE9z1Z",
            "extra_unknown": {"kept": True},
        }
    )
    assert cfg.cluster == "devnet"
    assert cfg.protocol_fee_bps == 100
    assert cfg.refund_timeout_slots == 4500
    assert cfg.raw["extra_unknown"] == {"kept": True}


def test_protocol_config_tolerates_sparse_payload():
    cfg = ProtocolConfig.from_dict({"cluster": "devnet"})
    assert cfg.cluster == "devnet"
    assert cfg.program_id is None


def test_node_page_parses():
    page = NodePage.from_dict(
        {
            "items": [
                {
                    "node_id": "ab" * 32,
                    "title": "t",
                    "price_lamports": 20_000,
                    "chunk_count": 3,
                    "tags": ["a", 1, "b"],
                    "registered": True,
                }
            ],
            "total": 1,
            "next_cursor": None,
        }
    )
    assert page.total == 1
    assert page.next_cursor is None
    assert page.items[0].tags == ("a", "b")
    assert page.items[0].registered is True


def test_node_page_rejects_missing_items():
    with pytest.raises(GatewayUnavailable):
        NodePage.from_dict({"total": 0})


def test_node_detail_parses():
    detail = NodeDetail.from_dict(
        {
            "node_id": "cd" * 32,
            "title": "t",
            "verification_report": {"chunk_ids_match": True, "chunks_checked": 2},
        }
    )
    assert detail.summary.node_id == "cd" * 32
    assert detail.verification_report["chunk_ids_match"] is True


def test_escrow_state_funded_property():
    st = EscrowState(
        address="a",
        consumer="c",
        node="n",
        amount_lamports=1,
        query_hash=b"\x00" * 32,
        status="Funded",
        created_at=0,
        created_at_slot=0,
        bump=255,
    )
    assert st.funded
    assert not EscrowState(**{**st.__dict__, "status": "Settled"}).funded
