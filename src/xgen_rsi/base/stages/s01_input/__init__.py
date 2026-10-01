"""Stage 1: Input — receive, validate, normalize user input."""

# Interfaces (ABCs)
from xgen_rsi.base.stages.s01_input.interface import InputValidator, InputNormalizer

# Types (shared)
from xgen_rsi.base.stages.s01_input.types import NormalizedInput

# Default artifact (backward-compatible)
from xgen_rsi.base.stages.s01_input.artifact.default.stage import InputStage
from xgen_rsi.base.stages.s01_input.artifact.default.validators import (
    DefaultValidator,
    PassthroughValidator,
    StrictValidator,
    SchemaValidator,
)
from xgen_rsi.base.stages.s01_input.artifact.default.normalizers import (
    DefaultNormalizer,
    MultimodalNormalizer,
)

__all__ = [
    "InputStage",
    "InputValidator",
    "DefaultValidator",
    "PassthroughValidator",
    "StrictValidator",
    "SchemaValidator",
    "InputNormalizer",
    "DefaultNormalizer",
    "MultimodalNormalizer",
    "NormalizedInput",
]
