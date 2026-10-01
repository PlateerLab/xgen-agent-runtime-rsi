"""Stage 9: Parse — default artifact."""

from xgen_rsi.base.stages.s09_parse.artifact.default.stage import ParseStage
from xgen_rsi.base.stages.s09_parse.artifact.default.parsers import (
    DefaultParser,
    StructuredOutputParser,
)
from xgen_rsi.base.stages.s09_parse.artifact.default.signals import (
    RegexDetector,
    StructuredDetector,
    HybridDetector,
)

Stage = ParseStage

__all__ = [
    "Stage",
    "ParseStage",
    "DefaultParser",
    "StructuredOutputParser",
    "RegexDetector",
    "StructuredDetector",
    "HybridDetector",
]
