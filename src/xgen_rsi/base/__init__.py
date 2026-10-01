"""xgen_rsi.base — geny-rsi 의 바탕 런타임. xgen-agent-runtime 4.80.0 을 복사해 이 패키지가 소유한다.

geny-rsi 는 xgen-agent-runtime 을 import 하지도, 의존성으로 두지도 않는다. 공급자 계층·도구·기억·호스트 계약·
21-stage 엔진(geny 기준선)까지 필요한 것은 전부 이 사본에서 온다. 원본이 바뀌어도 여기는 따로 갱신한다.

(원본 docstring) xgen-agent-runtime: Harness-engineered agent pipeline library.

Usage:
    from xgen_rsi.base import Pipeline, PipelineConfig
    from xgen_rsi.base.stages.s01_input import InputStage
    from xgen_rsi.base.stages.s06_api import APIStage, MockProvider
    from xgen_rsi.base.stages.s09_parse import ParseStage
    from xgen_rsi.base.stages.s21_yield import YieldStage

    pipeline = Pipeline(PipelineConfig(name="my-agent"))
    pipeline.register_stage(InputStage())
    pipeline.register_stage(APIStage(api_key="..."))
    pipeline.register_stage(ParseStage())
    pipeline.register_stage(YieldStage())

    result = await pipeline.run("Hello!")
"""

from xgen_rsi.base.core.pipeline import Pipeline
from xgen_rsi.base.core.config import PipelineConfig, ModelConfig, ModelOverrides
from xgen_rsi.base.core.state import PipelineState, TokenUsage, CacheMetrics
from xgen_rsi.base.core.result import PipelineResult
from xgen_rsi.base.core.run_status import RunStatus, TerminationReason
from xgen_rsi.base.core.continuation import CONTINUE_RUN, ContinuationInput
from xgen_rsi.base.core.stage import Stage, Strategy, StageDescription, StrategyInfo
from xgen_rsi.base.core.errors import (
    GenyExecutorError,
    PipelineError,
    StageError,
    GuardRejectError,
    APIError,
    ToolExecutionError,
    ErrorCategory,
    ExecutorErrorCode,
    MutationError,
    MutationLocked,
)
from xgen_rsi.base.core.schema import ConfigField, ConfigSchema
from xgen_rsi.base.core.slot import SlotChain, StrategySlot
from xgen_rsi.base.core.snapshot import PipelineSnapshot, StageSnapshot
from xgen_rsi.base.core.mutation import (
    PipelineMutator,
    MutationKind,
    MutationRecord,
    MutationResult,
)
from xgen_rsi.base.core.builder import PipelineBuilder
from xgen_rsi.base.core.presets import (
    PipelinePresets,
    PresetInfo,
    PresetManager,
    PresetRegistry,
    register_preset,
)
from xgen_rsi.base.core.diff import DiffEntry, EnvironmentDiff
from xgen_rsi.base.core.environment import (
    EnvironmentManifest,
    EnvironmentManager,
    EnvironmentMetadata,
    EnvironmentResolver,
    EnvironmentSanitizer,
    EnvironmentSummary,
    HostSelections,
    ManifestIssue,
    StageManifestEntry,
    ToolsSnapshot,
    validate_manifest,
)
from xgen_rsi.base.core.manifest_factory import (
    PresetDescriptor,
    build_manifest,
    build_manifest_for,
    get_preset_descriptor,
    known_manifest_presets,
    preset_catalog,
)
from xgen_rsi.base.core.artifact import (
    RETIRED_STAGE_ORDERS,
    ArtifactInfo,
    create_stage,
    describe_artifact,
    get_artifact_map,
    list_artifacts,
    list_artifacts_with_meta,
)
from xgen_rsi.base.core.introspection import (
    ChainIntrospection,
    IntrospectionUnsupported,
    SlotIntrospection,
    StageIntrospection,
    introspect_all,
    introspect_stage,
)
from xgen_rsi.base.events import (
    EVENT_CATALOG_VERSION,
    EventBus,
    EventTypes,
    PipelineEvent,
    known_event_types,
)
from xgen_rsi.base.llm_client import (
    APIRequest,
    APIResponse,
    BaseClient,
    ClaudeCodeCLIClient,
    ClientCapabilities,
    ClientRegistry,
    ConfigError,
    ContentBlock,
    CredentialBundle,
    ProviderCredentials,
)
from xgen_rsi.base.memory import (
    GenyPresets,
    MemoryAwareRetriever,
    MemoryProviderFactory,
    ProviderDrivenStrategy,
)
from xgen_rsi.base.memory.factory import provider_from_manifest_memory

#: 복사한 원본 버전 — 이 사본은 xgen-agent-runtime 배포본과 무관하게 이 값으로 자신을 밝힌다.
COPIED_FROM = "xgen-agent-runtime 4.80.0"
__version__ = "4.80.0"

__all__ = [
    # Core
    "Pipeline",
    "PipelineConfig",
    "PipelineState",
    "RunStatus",
    "TerminationReason",
    "CONTINUE_RUN",
    "ContinuationInput",
    "PipelineResult",
    "ModelConfig",
    "ModelOverrides",
    "TokenUsage",
    "CacheMetrics",
    # Abstractions
    "Stage",
    "Strategy",
    "StageDescription",
    "StrategyInfo",
    # Builder & Presets
    "PipelineBuilder",
    "PipelinePresets",
    "PresetInfo",
    "PresetManager",
    "PresetRegistry",
    "register_preset",
    # Environment & Diff
    "EnvironmentManifest",
    "EnvironmentManager",
    "EnvironmentMetadata",
    "EnvironmentResolver",
    "EnvironmentSanitizer",
    "EnvironmentSummary",
    "HostSelections",
    "ManifestIssue",
    "StageManifestEntry",
    "ToolsSnapshot",
    "validate_manifest",
    "build_manifest",
    "build_manifest_for",
    "known_manifest_presets",
    "preset_catalog",
    "get_preset_descriptor",
    "PresetDescriptor",
    "DiffEntry",
    "EnvironmentDiff",
    # Artifact system
    "RETIRED_STAGE_ORDERS",
    "ArtifactInfo",
    "create_stage",
    "describe_artifact",
    "get_artifact_map",
    "list_artifacts",
    "list_artifacts_with_meta",
    # Introspection
    "ChainIntrospection",
    "IntrospectionUnsupported",
    "SlotIntrospection",
    "StageIntrospection",
    "introspect_all",
    "introspect_stage",
    # Events
    "EVENT_CATALOG_VERSION",
    "EventBus",
    "EventTypes",
    "PipelineEvent",
    "known_event_types",
    # LLM clients (unified)
    "APIRequest",
    "APIResponse",
    "BaseClient",
    "ClaudeCodeCLIClient",
    "ClientCapabilities",
    "ClientRegistry",
    "ConfigError",
    "ContentBlock",
    "CredentialBundle",
    "ProviderCredentials",
    # Errors
    "GenyExecutorError",
    "PipelineError",
    "StageError",
    "GuardRejectError",
    "APIError",
    "ToolExecutionError",
    "ErrorCategory",
    "ExecutorErrorCode",
    "MutationError",
    "MutationLocked",
    # Schema & Mutation
    "ConfigField",
    "ConfigSchema",
    "StrategySlot",
    "SlotChain",
    "PipelineSnapshot",
    "StageSnapshot",
    "PipelineMutator",
    "MutationKind",
    "MutationRecord",
    "MutationResult",
    # Memory plumbing (provider-driven)
    "MemoryAwareRetriever",
    "MemoryProviderFactory",
    "ProviderDrivenStrategy",
    "GenyPresets",
    "provider_from_manifest_memory",
]
