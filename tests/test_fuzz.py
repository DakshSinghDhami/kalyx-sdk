"""Deterministic fuzz-style tests: randomized-but-seeded adversarial inputs
against the parsers and guards. No external fuzzer dependency; the seeds are
fixed so failures reproduce exactly."""

import base64
import random
import string

import pytest

from kalyx_sdk.chain import (
    decode_escrow,
    decode_knowledge_node,
)
from kalyx_sdk.client import parse_retry_after, verify_content_binding
from kalyx_sdk.errors import GatewayUnavailable, VerificationFailed
from kalyx_sdk.escrow import ix_discriminator, query_hash
from kalyx_sdk.models import Challenge

rng = random.Random(0xCA1FEF)


def rand_b64(rng, n):
    return base64.b64encode(bytes(rng.randrange(256) for _ in range(n))).decode()


# --- query_hash ---------------------------------------------------------------


def test_query_hash_always_32_bytes():
    for _ in range(500):
        q = "".join(rng.choice(string.printable) for _ in range(rng.randrange(0, 600)))
        assert len(query_hash(q)) == 32


def test_query_hash_deterministic_with_explicit_nonce():
    # The default nonce is random per call (domain separation). Determinism
    # is only expected when the caller pins the nonce.
    nonce = b"\x07" * 16
    for i in range(200):
        q = f"q{i} " + "x" * rng.randrange(0, 100)
        assert query_hash(q, nonce=nonce) == query_hash(q, nonce=nonce)


def test_query_hash_domain_separates_nonce():
    for i in range(200):
        q = f"q{i}"
        assert query_hash(q, nonce=b"a" * 16) != query_hash(q, nonce=b"b" * 16)


# --- escrow decoder -----------------------------------------------------------


def test_decode_escrow_never_crashes_on_garbage():
    for _ in range(500):
        n = rng.randrange(0, 300)
        raw = bytes(rng.randrange(256) for _ in range(n))
        try:
            decode_escrow(raw)
        except ValueError:
            pass
        else:  # only possible if length+discriminator accidentally match
            assert n >= 130
            assert raw[:8] == bytes([31, 213, 123, 187, 186, 22, 218, 155])


def test_decode_knowledge_node_never_crashes_on_garbage():
    for _ in range(500):
        n = rng.randrange(0, 300)
        raw = bytes(rng.randrange(256) for _ in range(n))
        with pytest.raises(ValueError):
            decode_knowledge_node(raw)


def test_decode_escrow_random_status_byte_survives():
    # A full-length account with a random status byte decodes to Unknown(n).
    for _ in range(200):
        raw = bytearray(bytes([31, 213, 123, 187, 186, 22, 218, 155]))
        raw += bytes(rng.randrange(256) for _ in range(122))
        st = decode_escrow(bytes(raw))
        assert st.status in {"Created", "Funded", "Settled", "Disputed"} or st.status.startswith(
            "Unknown("
        )


# --- challenge parser ---------------------------------------------------------


def test_challenge_parser_rejects_weird_shapes():
    base = {
        "chunk_id": "a" * 16,
        "price_lamports": 1,
        "publisher_wallet": "w",
        "program_id": "p",
        "proof_of_relevance": {},
        "escrow_params": {
            "program_id": "p",
            "node_address": "n",
            "content_hash": "c" * 64,
            "price_lamports": 1,
        },
    }
    for _ in range(300):
        d = dict(base)
        key = rng.choice(list(base.keys()) + list(base["escrow_params"].keys()))
        junk = rng.choice([None, 3.14, [], {}, True, "\x00\x01", 2**65])
        if key in base["escrow_params"] and rng.random() < 0.5:
            d["escrow_params"] = dict(base["escrow_params"], **{key: junk})
        else:
            d[key] = junk
        try:
            c = Challenge.from_dict(d)
        except GatewayUnavailable:
            continue
        # If parsing succeeded the invariants must hold.
        assert isinstance(c.price_lamports, int)
        assert c.program_id  # non-empty string required
        assert isinstance(c.node_address, str)


# --- content binding ----------------------------------------------------------


def test_content_binding_fuzz():
    for _ in range(300):
        content = "".join(rng.choice(string.printable) for _ in range(rng.randrange(0, 2000)))
        import hashlib

        cid = hashlib.sha256(content.encode()).hexdigest()[:16]
        verify_content_binding(cid, content)  # must not raise
        with pytest.raises(VerificationFailed):
            verify_content_binding(cid, content + "x")


# --- retry-after parser -------------------------------------------------------


def test_parse_retry_after_fuzz():
    import httpx

    for _ in range(300):
        raw = rng.choice(["", "abc", "-5", "0", "12", "999999999", "1.5", "12 "])
        headers = httpx.Headers({"retry-after": raw}) if raw else httpx.Headers()
        body = rng.choice([None, {}, {"retry_after": 3}, {"retry_after": "7"}])
        out = parse_retry_after(headers, body)
        if out is not None:
            assert out >= 0.0


# --- instruction discriminators ------------------------------------------------


def test_ix_discriminator_stability_over_names():
    names = [
        "create_and_fund",
        "refund_escrow",
        "settle_escrow",
        "dispute_escrow",
        "register_knowledge_node",
        "deposit_stake",
        "initialize_protocol",
        "create_escrow",
        "fund_escrow",
    ]
    seen = set()
    for n in names:
        d = ix_discriminator(n)
        assert len(d) == 8
        seen.add(d)
    assert len(seen) == len(names)  # no collisions among our instructions
