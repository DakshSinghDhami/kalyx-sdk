"""Property-based tests (hypothesis): challenge parsing and budget arithmetic
must never raise anything outside the typed error hierarchy, never produce
negative amounts, and never overflow. Also covers the low-level parsers the
payment path relies on (retry-after, escrow decode, content binding)."""

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kalyx_sdk.budget import BudgetTracker
from kalyx_sdk.chain import decode_escrow
from kalyx_sdk.client import parse_retry_after, verify_content_binding
from kalyx_sdk.errors import GatewayUnavailable, KalyxError
from kalyx_sdk.escrow import create_and_fund_ix, query_hash
from kalyx_sdk.models import Challenge

from .helpers import AUTHOR_ADDR, NODE_ADDR

JSON_SCALAR = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=64),
)
JSON_VALUE = st.recursive(
    JSON_SCALAR,
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=24), children, max_size=6)
    ),
    max_leaves=20,
)

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=list(HealthCheck))


# --- challenge parsing -----------------------------------------------------------


@SETTINGS
@given(payload=st.dictionaries(st.text(max_size=24), JSON_VALUE, max_size=12))
def test_challenge_from_dict_only_raises_typed_errors(payload):
    try:
        ch = Challenge.from_dict(payload)
    except GatewayUnavailable:
        return
    except KalyxError:
        return
    # Parsed: invariants must hold.
    assert isinstance(ch.price_lamports, int) and ch.price_lamports > 0
    assert isinstance(ch.chunk_id, str) and ch.chunk_id
    assert isinstance(ch.program_id, str) and ch.program_id
    assert isinstance(ch.node_address, str)
    assert isinstance(ch.content_hash, str)


@SETTINGS
@given(payload=JSON_VALUE)
def test_challenge_from_dict_non_dict_payloads(payload):
    if isinstance(payload, dict):
        return  # covered by the dict strategy above
    with pytest.raises(GatewayUnavailable):
        Challenge.from_dict(payload)


# --- budget arithmetic -------------------------------------------------------------

NON_NEG = st.integers(min_value=0, max_value=2**70)


@SETTINGS
@given(budget=st.one_of(st.none(), NON_NEG), price=NON_NEG)
def test_check_spend_raises_exactly_when_over_budget(budget, price):
    t = BudgetTracker(budget_lamports=budget)
    over = budget is not None and price > budget
    try:
        t.check_spend(price)
        assert not over
    except KalyxError:
        assert over


@SETTINGS
@given(
    budget=st.one_of(st.none(), NON_NEG),
    ops=st.lists(
        st.tuples(st.sampled_from(["reserve", "commit", "release", "record"]), NON_NEG), max_size=12
    ),
)
def test_budget_invariants_under_random_operation_sequences(budget, ops):
    t = BudgetTracker(budget_lamports=budget)
    outstanding = 0
    recorded = 0  # record() accounts for spend that already happened (uncapped)
    for op, amount in ops:
        try:
            if op == "reserve":
                t.reserve(amount)
                outstanding += amount
            elif op == "commit":
                t.commit(min(amount, outstanding))
                outstanding = max(0, outstanding - amount)
            elif op == "release":
                t.release(min(amount, outstanding))
                outstanding = max(0, outstanding - amount)
            else:
                t.record(amount)
                recorded += amount
        except KalyxError:
            pass
        assert t.spent >= 0
        assert t._reserved >= 0
        if budget is not None:
            # cap-gated spend (commits) plus outstanding holds never exceed the
            # budget; only reality-accounting record() can push spent higher
            assert t.spent - recorded + t._reserved <= budget
        if t.remaining_lamports is not None:
            assert t.remaining_lamports >= 0


@SETTINGS
@given(cap=st.one_of(st.none(), NON_NEG), price=NON_NEG)
def test_check_price_raises_exactly_when_over_cap(cap, price):
    t = BudgetTracker()
    over = cap is not None and price > cap
    try:
        t.check_price(price, cap)
        assert not over
    except KalyxError:
        assert over


# --- retry-after parsing ------------------------------------------------------------


@SETTINGS
@given(raw=st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=127), max_size=80))
def test_parse_retry_after_never_negative_never_crashes(raw):
    # HTTP header values are ASCII on the wire; httpx enforces that itself.
    headers = httpx.Headers({"retry-after": raw})
    out = parse_retry_after(headers)
    assert out is None or out >= 0.0


# --- low-level decoders ----------------------------------------------------------------


@SETTINGS
@given(raw=st.binary(max_size=300))
def test_decode_escrow_only_raises_value_error(raw):
    try:
        st_ = decode_escrow(raw)
    except ValueError:
        return
    assert st_.amount_lamports >= 0
    assert len(st_.query_hash) == 32


@SETTINGS
@given(content=st.text(max_size=4000), chunk_id=st.text(max_size=24))
def test_content_binding_only_raises_verification_failed(content, chunk_id):
    import hashlib

    from kalyx_sdk.errors import VerificationFailed

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    try:
        verify_content_binding(chunk_id, content)
        assert chunk_id.lower() == digest
    except VerificationFailed:
        assert chunk_id.lower() != digest


@SETTINGS
@given(query=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=600))
def test_query_hash_always_32_bytes(query):
    assert len(query_hash(query)) == 32


# --- instruction builder amount bounds -----------------------------------------------


@SETTINGS
@given(amount=st.integers(min_value=-(2**70), max_value=2**70))
def test_create_and_fund_amount_bounds(amount):
    from solders.pubkey import Pubkey

    from kalyx_sdk.errors import ConfigError

    if 0 < amount <= 2**64 - 1:
        ix = create_and_fund_ix(
            consumer=Pubkey.from_string(AUTHOR_ADDR),
            qhash=query_hash("q"),
            node_address=NODE_ADDR,
            amount_lamports=amount,
        )
        assert int.from_bytes(ix.data[-8:], "little") == amount
    else:
        with pytest.raises(ConfigError):
            create_and_fund_ix(
                consumer=Pubkey.from_string(AUTHOR_ADDR),
                qhash=query_hash("q"),
                node_address=NODE_ADDR,
                amount_lamports=amount,
            )
