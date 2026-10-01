"""Context strategies — backward-compatible re-export wrapper."""

from xgen_rsi.base.stages.s02_context.interface import ContextStrategy
from xgen_rsi.base.stages.s02_context.artifact.default.strategies import (
    SimpleLoadStrategy,
    HybridStrategy,
    ProgressiveDisclosureStrategy,
)

__all__ = [
    "ContextStrategy",
    "SimpleLoadStrategy",
    "HybridStrategy",
    "ProgressiveDisclosureStrategy",
]
