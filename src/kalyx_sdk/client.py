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
from .chain import KnowledgeNodeState
from .errors import ConfigError, VerificationFailed
from .escrow import DEFAULT_PROGRAM_ID, derive_node_pda
from .models import Challenge
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


# --- challenge verification (verify before paying) ----------------------------

_HEX64 = frozenset("0123456789abcdefABCDEF")


def verify_challenge_offchain(
    challenge: Challenge, *, expected_program_id: str | None = None
) -> None:
    """Validate a 402 challenge without touching the network.

    Checks, in order (any failure raises :class:`VerificationFailed` *before*
    any money moves):

    * ``program_id`` matches the gateway-reported id (or the vendored IDL's
      address when the config could not be fetched);
    * the challenge names an on-chain ``node_address`` (a node that is not
      registered on-chain cannot be paid);
    * ``content_hash`` is 32 bytes of hex;
    * the node PDA re-derives from ``(author, content_hash)`` — the author is
      ``author_wallet`` when the gateway reports it, else
      ``publisher_wallet`` (see BLOCKERS: the challenge does not carry the
      author explicitly).
    """
    expected = expected_program_id or DEFAULT_PROGRAM_ID
    if challenge.program_id != expected:
        raise VerificationFailed(
            "challenge program_id does not match the configured program",
            reason="program_id_mismatch",
        )
    if not challenge.node_address:
        raise VerificationFailed(
            "challenge names no on-chain node (node_address empty); "
            "the node is not registered, so it cannot be paid",
            reason="node_not_registered",
        )
    ch = challenge.content_hash
    if len(ch) != 64 or any(c not in _HEX64 for c in ch):
        raise VerificationFailed(
            "challenge content_hash is not 32 bytes of hex", reason="bad_content_hash"
        )
    author = challenge.author_wallet or challenge.publisher_wallet
    try:
        derived, _bump = derive_node_pda(author, bytes.fromhex(ch), expected)
    except Exception as exc:  # solders ValueError for bad base58 / off-curve
        raise VerificationFailed(
            "challenge wallets/hash cannot form a node PDA", reason="bad_pda_inputs"
        ) from exc
    if str(derived) != challenge.node_address:
        raise VerificationFailed(
            "challenge node_address does not match the re-derived node PDA",
            reason="pda_mismatch",
        )


def verify_node_preflight(challenge: Challenge, node: KnowledgeNodeState | None) -> None:
    """Validate the on-chain node account against the challenge.

    Raises :class:`VerificationFailed` when the node is missing, inactive,
    priced above the challenge, or bound to different content. Pure function:
    the account read itself lives in :mod:`kalyx_sdk.chain`.
    """
    if node is None:
        raise VerificationFailed(
            "knowledge node account does not exist on-chain", reason="node_not_registered"
        )
    if not node.is_active:
        raise VerificationFailed("knowledge node is inactive on-chain", reason="node_inactive")
    if node.content_hash.hex() != challenge.content_hash.lower():
        raise VerificationFailed(
            "on-chain node content hash differs from the challenge",
            reason="content_hash_mismatch",
        )
    if node.price_lamports > challenge.price_lamports:
        raise VerificationFailed(
            "on-chain node price exceeds the challenge price; funding at the "
            "challenge price would fail on-chain",
            reason="onchain_price_higher",
        )
