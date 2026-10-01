"""Cache strategies — backward-compatible re-exports.

Concrete implementations have moved to:
  xgen_rsi.base.stages.s05_cache.artifact.default.strategies

ABCs and constants live in:
  xgen_rsi.base.stages.s05_cache.interface
"""

from xgen_rsi.base.stages.s05_cache.interface import CacheStrategy, EPHEMERAL_CACHE
from xgen_rsi.base.stages.s05_cache.artifact.default.strategies import (
    NoCacheStrategy,
    SystemCacheStrategy,
    AggressiveCacheStrategy,
)

__all__ = [
    "EPHEMERAL_CACHE",
    "CacheStrategy",
    "NoCacheStrategy",
    "SystemCacheStrategy",
    "AggressiveCacheStrategy",
]
