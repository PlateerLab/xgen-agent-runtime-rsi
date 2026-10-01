"""Emitters — backward-compatible re-exports."""

from xgen_rsi.base.stages.s17_emit.interface import Emitter
from xgen_rsi.base.stages.s17_emit.types import EmitResult, EmitterChain
from xgen_rsi.base.stages.s17_emit.artifact.default.emitters import (
    TextEmitter,
    CallbackEmitter,
    VTuberEmitter,
    TTSEmitter,
)

__all__ = [
    "Emitter",
    "EmitResult",
    "EmitterChain",
    "TextEmitter",
    "CallbackEmitter",
    "VTuberEmitter",
    "TTSEmitter",
]
