"""Core engine: Pipeline, Stage, Strategy, State, Config, Result, Errors, Mutation, Environment, Diff."""

from xgen_rsi.base.core.errors import (
    ErrorCategory,
    GenyExecutorError,
    GuardRejectError,
    MutationError,
    MutationLocked,
    PipelineError,
    StageError,
)
from xgen_rsi.base.core.stage import Stage, Strategy
from xgen_rsi.base.core.state import CacheMetrics, PipelineState, TokenUsage
from xgen_rsi.base.core.config import ModelConfig, PipelineConfig
from xgen_rsi.base.core.result import PipelineResult
from xgen_rsi.base.core.run_status import RunStatus, TerminationReason
from xgen_rsi.base.core.continuation import CONTINUE_RUN, ContinuationInput
from xgen_rsi.base.core.pipeline import Pipeline
from xgen_rsi.base.core.schema import ConfigField, ConfigSchema
from xgen_rsi.base.core.slot import StrategySlot
from xgen_rsi.base.core.snapshot import PipelineSnapshot, StageSnapshot
from xgen_rsi.base.core.mutation import (
    MutationKind,
    MutationRecord,
    MutationResult,
    PipelineMutator,
)
from xgen_rsi.base.core.diff import DiffEntry, EnvironmentDiff
from xgen_rsi.base.core.environment import (
    EnvironmentManifest,
    EnvironmentManager,
    EnvironmentMetadata,
    EnvironmentResolver,
    EnvironmentSanitizer,
    EnvironmentSummary,
    ToolsSnapshot,
)
from xgen_rsi.base.core.presets import PipelinePresets, PresetInfo, PresetManager

__all__ = [
    # Engine
    "Pipeline",
    "PipelineConfig",
    "PipelineResult",
    "PipelineState",
    "Stage",
    "Strategy",
    "ModelConfig",
    "TokenUsage",
    "CacheMetrics",
    "RunStatus",
    "TerminationReason",
    "CONTINUE_RUN",
    "ContinuationInput",
    # Schema
    "ConfigField",
    "ConfigSchema",
    # Slot
    "StrategySlot",
    # Snapshot
    "PipelineSnapshot",
    "StageSnapshot",
    # Mutation
    "PipelineMutator",
    "MutationKind",
    "MutationRecord",
    "MutationResult",
    # Diff
    "DiffEntry",
    "EnvironmentDiff",
    # Environment
    "EnvironmentManifest",
    "EnvironmentManager",
    "EnvironmentMetadata",
    "EnvironmentResolver",
    "EnvironmentSanitizer",
    "EnvironmentSummary",
    "ToolsSnapshot",
    # Presets
    "PipelinePresets",
    "PresetInfo",
    "PresetManager",
    # Errors
    "ErrorCategory",
    "GenyExecutorError",
    "PipelineError",
    "StageError",
    "GuardRejectError",
    "MutationError",
    "MutationLocked",
]
