"""Input normalizers — backward-compatible re-exports.

Concrete implementations have moved to ``artifact.default.normalizers``.
ABCs live in ``interface.py``.
"""

from xgen_rsi.base.stages.s01_input.interface import InputNormalizer
from xgen_rsi.base.stages.s01_input.artifact.default.normalizers import (
    DefaultNormalizer,
    MultimodalNormalizer,
)

__all__ = [
    "InputNormalizer",
    "DefaultNormalizer",
    "MultimodalNormalizer",
]
