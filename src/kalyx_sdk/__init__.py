"""KALYX SDK — pay-per-citation context retrieval for autonomous AI agents.

The full public API lands module by module; see ``docs/design.md``.
"""

from ._version import __version__
from .client import KalyxClient

__all__ = ["KalyxClient", "__version__"]
