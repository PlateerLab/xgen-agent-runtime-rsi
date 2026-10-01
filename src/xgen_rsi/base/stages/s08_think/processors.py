"""Think stage — processors. Backward-compatible re-exports."""

from xgen_rsi.base.stages.s08_think.types import ThinkingBlock, ThinkingResult
from xgen_rsi.base.stages.s08_think.interface import ThinkingProcessor
from xgen_rsi.base.stages.s08_think.artifact.default.processors import (
    PassthroughProcessor,
    ExtractAndStoreProcessor,
    ThinkingFilterProcessor,
)

__all__ = [
    "ThinkingBlock",
    "ThinkingResult",
    "ThinkingProcessor",
    "PassthroughProcessor",
    "ExtractAndStoreProcessor",
    "ThinkingFilterProcessor",
]
