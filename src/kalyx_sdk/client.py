"""KALYX SDK — synchronous client.

Configuration resolution and URL validation live here; the full client class
is built up feature by feature. Env fallbacks (``KALYX_GATEWAY_URL``,
``KALYX_KEYPAIR_PATH``, ``KALYX_RPC_URL``) are read but never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from . import __version__
from .errors import ConfigError
from .wallet import KeypairSource, Wallet, load_wallet

#: Default public gateway (Solana devnet deployment).
DEFAULT_GATEWAY_URL = "https://kalyxprotocol.xyz"
#: Default RPC endpoint (Solana devnet).
DEFAULT_RPC_URL = "https://api.devnet.solana.com"

_ENV_GATEWAY_URL = "KALYX_GATEWAY_URL"
_ENV_KEYPAIR_PATH = "KALYX_KEYPAIR_PATH"
_ENV_RPC_URL = "KALYX_RPC_URL"

# S104: "0.0.0.0" here is a *dialing* allowlist for local dev gateways, not a bind.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104


def _is_loopback(host: str) -> bool:
    return host.lower() in _LOOPBACK_HOSTS


def validate_gateway_url(url: str) -> str:
    """Validate a gateway base URL.

    TLS is mandatory except for loopback addresses: passing an ``http://``
    URL whose host is ``localhost``/``127.0.0.1``/``::1`` is the explicit
    opt-in for local development. Anything else raises :class:`ConfigError`.
    Returns the URL without a trailing slash.
    """
    if not isinstance(url, str) or not url.strip():
        raise ConfigError("gateway_url must be a non-empty string")
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(
            f"gateway_url must use https (got scheme {parsed.scheme!r}); "
            "http is only allowed for localhost"
        )
    if not parsed.hostname:
        raise ConfigError("gateway_url has no host")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ConfigError(
            "gateway_url must use https; plain http is only allowed for "
            "localhost (loopback) gateways"
        )
    return url


def validate_rpc_url(url: str) -> str:
    """Validate a Solana RPC URL (http/https/ws-less, any host)."""
    if not isinstance(url, str) or not url.strip():
        raise ConfigError("rpc_url must be a non-empty string")
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError("rpc_url must use http or https")
    if not parsed.hostname:
        raise ConfigError("rpc_url has no host")
    return url


def default_user_agent() -> str:
    """The SDK's identifying User-Agent (honest protocol citizenship)."""
    return f"kalyx-sdk/{__version__} (+https://kalyxprotocol.xyz)"


@dataclass(frozen=True)
class ClientConfig:
    """Resolved, validated client configuration.

    Built by :meth:`ClientConfig.resolve`; never contains the keypair secret,
    only the loaded :class:`~kalyx_sdk.wallet.Wallet` wrapper.
    """

    gateway_url: str
    rpc_url: str
    wallet: Wallet | None
    max_price_lamports: int | None
    session_budget_lamports: int | None
    timeout: float
    user_agent: str

    @classmethod
    def resolve(
        cls,
        *,
        gateway_url: str | None = None,
        keypair: KeypairSource | None = None,
        rpc_url: str | None = None,
        max_price_lamports: int | None = None,
        session_budget_lamports: int | None = None,
        timeout: float = 30.0,
        user_agent: str | None = None,
    ) -> ClientConfig:
        """Resolve constructor args against environment and defaults.

        Env fallbacks: ``KALYX_GATEWAY_URL``, ``KALYX_KEYPAIR_PATH``,
        ``KALYX_RPC_URL``. Values are never logged.
        """
        gw = gateway_url or os.environ.get(_ENV_GATEWAY_URL) or DEFAULT_GATEWAY_URL
        rpc = rpc_url or os.environ.get(_ENV_RPC_URL) or DEFAULT_RPC_URL
        kp_source = keypair
        if kp_source is None:
            env_path = os.environ.get(_ENV_KEYPAIR_PATH)
            if env_path:
                kp_source = env_path
        wallet = load_wallet(kp_source) if kp_source is not None else None
        if max_price_lamports is not None and max_price_lamports < 0:
            raise ConfigError("max_price_lamports must be >= 0")
        if session_budget_lamports is not None and session_budget_lamports < 0:
            raise ConfigError("session_budget_lamports must be >= 0")
        if timeout <= 0:
            raise ConfigError("timeout must be positive")
        return cls(
            gateway_url=validate_gateway_url(gw),
            rpc_url=validate_rpc_url(rpc),
            wallet=wallet,
            max_price_lamports=max_price_lamports,
            session_budget_lamports=session_budget_lamports,
            timeout=float(timeout),
            user_agent=user_agent or default_user_agent(),
        )
