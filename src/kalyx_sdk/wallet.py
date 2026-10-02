"""Keypair loading with secret hygiene.

A :class:`Wallet` wraps a ``solders.keypair.Keypair`` so the secret can never
leak through ``repr``/``str`` (note: ``str(solders.Keypair)`` renders the
base58 secret — never print one directly). Loading accepts a filesystem path
to a 64-byte JSON-array keypair file, raw 64 bytes, or an existing keypair.

On POSIX systems a keypair file readable by group/other triggers a warning
(``KALYX_STRICT_KEYPAIR_MODE=1`` turns the warning into :class:`ConfigError`).
"""

from __future__ import annotations

import json
import os
import stat
import warnings
from pathlib import Path
from typing import Union

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from .errors import ConfigError

KeypairSource = Union[str, bytes, "os.PathLike[str]", Keypair, "Wallet"]

_STRICT_ENV = "KALYX_STRICT_KEYPAIR_MODE"


def _strict_mode() -> bool:
    return os.environ.get(_STRICT_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _check_file_mode(path: Path) -> None:
    """Warn (or raise in strict mode) when a keypair file is group/other accessible."""
    if os.name == "nt":  # POSIX mode bits are meaningless on Windows
        return
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ConfigError(f"keypair file is not readable: {exc.strerror or exc}") from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        msg = (
            f"keypair file has permissive mode {oct(mode)}; recommend 0o600 "
            f"(chmod 600). Set {_STRICT_ENV}=1 to make this an error."
        )
        if _strict_mode():
            raise ConfigError(msg)
        warnings.warn(msg, UserWarning, stacklevel=3)


def _from_path(path: Path) -> Keypair:
    _check_file_mode(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"keypair file is not readable: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError("keypair file is not valid JSON") from exc
    if not isinstance(data, list):
        raise ConfigError("keypair file must contain a JSON array of 64 bytes")
    try:
        blob = bytes(int(b) & 0xFF for b in data)
    except (TypeError, ValueError) as exc:
        raise ConfigError("keypair file must contain a JSON array of 64 bytes") from exc
    return _from_bytes(blob)


def _from_bytes(blob: bytes) -> Keypair:
    if len(blob) != 64:
        raise ConfigError(
            f"keypair must be exactly 64 bytes (got {len(blob)}); "
            "expected a Solana CLI id.json-style secret"
        )
    try:
        return Keypair.from_bytes(blob)
    except ValueError as exc:
        raise ConfigError("keypair bytes are not a valid ed25519 keypair") from exc


class Wallet:
    """A signing identity whose secret is masked from repr/str and logs.

    Use :func:`load_wallet` rather than constructing this directly.
    """

    __slots__ = ("_keypair",)

    def __init__(self, keypair: Keypair) -> None:
        self._keypair = keypair

    @property
    def keypair(self) -> Keypair:
        """The underlying ``solders`` keypair (handle with care; never log)."""
        return self._keypair

    @property
    def pubkey(self) -> Pubkey:
        """The wallet's public key."""
        return self._keypair.pubkey()

    @property
    def address(self) -> str:
        """Base58 public address (safe to display/log)."""
        return str(self._keypair.pubkey())

    def __repr__(self) -> str:
        return f"Wallet(address={self.address!r}, secret=***)"

    __str__ = __repr__


def load_wallet(source: KeypairSource) -> Wallet:
    """Load a :class:`Wallet` from a path, raw bytes, or an existing keypair.

    Args:
        source: One of:
            * ``str``/``os.PathLike`` — path to a Solana CLI style JSON
              keypair file (array of 64 integers),
            * ``bytes`` — the raw 64-byte secret,
            * ``solders.keypair.Keypair`` — used as-is,
            * ``Wallet`` — returned unchanged.

    Raises:
        ConfigError: when the source is unreadable or malformed. The message
            never contains the secret material.
    """
    if isinstance(source, Wallet):
        return source
    if isinstance(source, Keypair):
        return Wallet(source)
    if isinstance(source, (bytes, bytearray)):
        return Wallet(_from_bytes(bytes(source)))
    if isinstance(source, (str, os.PathLike)):
        path = Path(source).expanduser()
        if not path.exists():
            raise ConfigError("keypair file does not exist")
        return Wallet(_from_path(path))
    raise ConfigError("unsupported keypair source; expected path, 64 raw bytes, Keypair, or Wallet")
