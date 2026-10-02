"""Unit tests for the KalyxError hierarchy."""

from kalyx_sdk.errors import (
    BudgetExhausted,
    ChainError,
    ClusterMismatchError,
    ConfigError,
    DisabledError,
    GatewayUnavailable,
    InsufficientFunds,
    KalyxError,
    NoContextFound,
    PaymentRequired,
    PriceExceedsBudget,
    RateLimited,
    ReplayRejected,
    VerificationFailed,
)

ALL_ERRORS = [
    ConfigError,
    ClusterMismatchError,
    NoContextFound,
    PaymentRequired,
    PriceExceedsBudget,
    BudgetExhausted,
    InsufficientFunds,
    ReplayRejected,
    VerificationFailed,
    RateLimited,
    GatewayUnavailable,
    DisabledError,
    ChainError,
]


def test_all_errors_inherit_kalyx_error():
    for cls in ALL_ERRORS:
        assert issubclass(cls, KalyxError), cls


def test_request_id_carried():
    err = VerificationFailed("nope", request_id="req-123")
    assert err.request_id == "req-123"
    assert "req-123" not in str(err)  # attribute, not message


def test_payment_required_carries_challenge():
    challenge = {"chunk_id": "abc", "price_lamports": 20_000}
    err = PaymentRequired("pay up", challenge=challenge)
    assert err.challenge == challenge


def test_price_exceeds_budget_is_payment_required():
    err = PriceExceedsBudget("too pricey", challenge={"price_lamports": 9})
    assert isinstance(err, PaymentRequired)
    assert err.challenge == {"price_lamports": 9}


def test_rate_limited_exposes_retry_after():
    err = RateLimited("slow down", retry_after=12.5)
    assert err.retry_after == 12.5
    assert RateLimited("x").retry_after is None


def test_insufficient_funds_amounts():
    err = InsufficientFunds("broke", required_lamports=100, available_lamports=50)
    assert err.required_lamports == 100
    assert err.available_lamports == 50


def test_budget_exhausted_amounts():
    err = BudgetExhausted("spent", spent_lamports=10, budget_lamports=20)
    assert err.spent_lamports == 10
    assert err.budget_lamports == 20


def test_chain_error_signature():
    err = ChainError("boom", signature="sig123")
    assert err.signature == "sig123"


def test_disabled_and_unavailable_reasons():
    assert DisabledError("off", reason="demo_disabled").reason == "demo_disabled"
    assert GatewayUnavailable("down", reason="wallet_unfunded").reason == "wallet_unfunded"


def test_verification_failed_reason():
    assert VerificationFailed("bad", reason="hash_mismatch").reason == "hash_mismatch"
