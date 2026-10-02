"""Unit tests for wallet loading, masking, and file-mode checks."""

import json
import os
import stat
import warnings

import pytest
from solders.keypair import Keypair

from kalyx_sdk.errors import ConfigError
from kalyx_sdk.wallet import Wallet, load_wallet


@pytest.fixture()
def keypair_file(tmp_path):
    kp = Keypair()
    path = tmp_path / "test-wallet.json"
    path.write_text(json.dumps(list(bytes(kp))))
    os.chmod(path, 0o600)
    return path, kp


def test_load_from_path(keypair_file):
    path, kp = keypair_file
    w = load_wallet(str(path))
    assert isinstance(w, Wallet)
    assert w.address == str(kp.pubkey())


def test_load_from_bytes(keypair_file):
    _, kp = keypair_file
    w = load_wallet(bytes(kp))
    assert w.address == str(kp.pubkey())


def test_load_passthrough(keypair_file):
    _, kp = keypair_file
    w = load_wallet(kp)
    assert w.address == str(kp.pubkey())
    assert load_wallet(w) is w


def test_repr_is_masked(keypair_file):
    path, kp = keypair_file
    w = load_wallet(str(path))
    r = repr(w)
    assert "***" in r
    # solders renders the full base58 secret via str(); it must not leak.
    assert str(kp) not in r
    assert str(kp) not in str(w)
    assert w.address in r


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_wallet(str(tmp_path / "nope.json"))


def test_bad_json_raises_config_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError):
        load_wallet(str(path))


def test_wrong_length_raises_config_error(tmp_path):
    path = tmp_path / "short.json"
    path.write_text(json.dumps([1, 2, 3]))
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError):
        load_wallet(str(path))


def test_non_list_json_raises_config_error(tmp_path):
    path = tmp_path / "obj.json"
    path.write_text(json.dumps({"secret": 1}))
    os.chmod(path, 0o600)
    with pytest.raises(ConfigError):
        load_wallet(str(path))


def test_unsupported_source_raises_config_error():
    with pytest.raises(ConfigError):
        load_wallet(12345)  # type: ignore[arg-type]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_permissive_mode_warns(tmp_path):
    kp = Keypair()
    path = tmp_path / "wide-open.json"
    path.write_text(json.dumps(list(bytes(kp))))
    os.chmod(path, 0o644)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_wallet(str(path))
    assert any("permissive mode" in str(w.message) for w in caught)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_permissive_mode_raises_in_strict_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("KALYX_STRICT_KEYPAIR_MODE", "1")
    kp = Keypair()
    path = tmp_path / "wide-open.json"
    path.write_text(json.dumps(list(bytes(kp))))
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError):
        load_wallet(str(path))


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_tight_mode_does_not_warn(tmp_path):
    kp = Keypair()
    path = tmp_path / "tight.json"
    path.write_text(json.dumps(list(bytes(kp))))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_wallet(str(path))
    assert not caught
