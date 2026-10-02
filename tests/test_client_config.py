"""Unit tests for client configuration resolution and URL validation."""

import pytest

from kalyx_sdk.client import (
    DEFAULT_GATEWAY_URL,
    DEFAULT_RPC_URL,
    ClientConfig,
    default_user_agent,
    validate_gateway_url,
)
from kalyx_sdk.errors import ConfigError


def test_defaults(monkeypatch):
    for var in ("KALYX_GATEWAY_URL", "KALYX_RPC_URL", "KALYX_KEYPAIR_PATH"):
        monkeypatch.delenv(var, raising=False)
    cfg = ClientConfig.resolve()
    assert cfg.gateway_url == DEFAULT_GATEWAY_URL
    assert cfg.rpc_url == DEFAULT_RPC_URL
    assert cfg.wallet is None
    assert cfg.user_agent.startswith("kalyx-sdk/")


def test_env_fallbacks(monkeypatch):
    monkeypatch.setenv("KALYX_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setenv("KALYX_RPC_URL", "https://rpc.example")
    cfg = ClientConfig.resolve()
    assert cfg.gateway_url == "https://gateway.example"
    assert cfg.rpc_url == "https://rpc.example"


def test_explicit_args_beat_env(monkeypatch):
    monkeypatch.setenv("KALYX_GATEWAY_URL", "https://env.example")
    cfg = ClientConfig.resolve(gateway_url="https://arg.example")
    assert cfg.gateway_url == "https://arg.example"


def test_https_required_for_remote():
    with pytest.raises(ConfigError):
        validate_gateway_url("http://gateway.example")
    assert validate_gateway_url("https://gateway.example/") == "https://gateway.example"


def test_http_allowed_for_loopback():
    for url in ("http://localhost:8042", "http://127.0.0.1:8042", "http://[::1]:8042"):
        assert validate_gateway_url(url).startswith("http://")


def test_bad_scheme_rejected():
    for bad in ("ftp://x", "ws://x", "", "notaurl"):
        with pytest.raises(ConfigError):
            validate_gateway_url(bad)


def test_negative_caps_rejected():
    with pytest.raises(ConfigError):
        ClientConfig.resolve(max_price_lamports=-1)
    with pytest.raises(ConfigError):
        ClientConfig.resolve(session_budget_lamports=-1)


def test_bad_timeout_rejected():
    with pytest.raises(ConfigError):
        ClientConfig.resolve(timeout=0)


def test_user_agent_format():
    ua = default_user_agent()
    assert ua.startswith("kalyx-sdk/0.1.0")
    assert "kalyxprotocol.xyz" in ua
