"""구성요소가 턴에 닿는 유일한 손잡이(:class:`TurnRuntime`)와 하네스 로더.

구성요소는 턴마다 새로 만든다 — 턴을 넘어 구성요소 안에 상태가 남지 않게(세션을 넘는 것은 기억 저장소와
대화 이력뿐이다). 커널이 소유한 것(원장·이벤트·도구 실행·한도)은 메서드로만 노출한다.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional

from xgen_rsi.harness.kinds import PROTOCOLS
from xgen_rsi.harness.spec import (
    ALLOWED_IMPL_PREFIXES,
    ComponentSpec,
    HarnessManifest,
    HarnessSpecError,
)

if TYPE_CHECKING:  # pragma: no cover
    from xgen_rsi.kernel.ledger import UsageLedger
    from xgen_rsi.kernel.model_call import ModelCaller


class Component:
    """모든 구성요소의 바탕. ``spec.params`` 를 읽기 쉽게 감싼다."""

    kind: str = ""

    def __init__(self, spec: ComponentSpec) -> None:
        if self.kind and spec.kind != self.kind:
            raise HarnessSpecError(
                f"component {spec.id!r} declares kind {spec.kind!r} but {type(self).__name__} is {self.kind!r}"
            )
        self.spec = spec

    @property
    def id(self) -> str:
        return self.spec.id

    def param(self, key: str, default: Any = None) -> Any:
        node: Any = self.spec.params
        for part in key.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node


@dataclass
class LoadedHarness:
    """manifest + 이번 턴의 구성요소 인스턴스."""

    manifest: HarnessManifest
    version_id: str
    components: Dict[str, Component]

    def one(self, kind: str) -> Any:
        for c in self.components.values():
            if c.spec.kind == kind:
                return c
        raise KeyError(kind)

    def all(self, kind: str) -> List[Any]:
        return [c for c in self.components.values() if c.spec.kind == kind]

    def maybe(self, kind: str) -> Optional[Any]:
        found = self.all(kind)
        return found[0] if found else None


def resolve_impl(impl: str) -> type:
    if not impl.startswith(ALLOWED_IMPL_PREFIXES):
        raise HarnessSpecError(f"impl {impl!r} outside allowed prefixes {ALLOWED_IMPL_PREFIXES}")
    module_name, _, attr = impl.partition(":")
    module = importlib.import_module(module_name)
    try:
        cls = getattr(module, attr)
    except AttributeError as exc:
        raise HarnessSpecError(f"impl {impl!r}: {attr!r} not found in {module_name}") from exc
    if not isinstance(cls, type):
        raise HarnessSpecError(f"impl {impl!r} is not a class")
    return cls


def instantiate(manifest: HarnessManifest, *, version_id: Optional[str] = None) -> LoadedHarness:
    """manifest 의 활성 구성요소를 만든다. kind 별 프로토콜을 만족하지 않으면 실패."""
    comps: Dict[str, Component] = {}
    for spec in manifest.components:
        if not spec.enabled:
            continue
        cls = resolve_impl(spec.impl)
        inst = cls(spec)
        proto = PROTOCOLS.get(spec.kind)
        if proto is not None and not isinstance(inst, proto):
            raise HarnessSpecError(
                f"component {spec.id!r} ({spec.impl}) does not implement the {spec.kind!r} interface"
            )
        comps[spec.id] = inst
    return LoadedHarness(
        manifest=manifest,
        version_id=version_id or manifest.version_id(),
        components=comps,
    )


@dataclass
class TurnRuntime:
    """이번 턴의 커널 서비스 묶음 — 구성요소에게 주는 유일한 손잡이.

    ``state`` 는 턴 기록(PipelineState)이다. 도구 ABI(``ToolContext.state_view``)와 기억 전략이 이 형식에
    기대므로 커널이 그대로 유지한다. 구성요소는 이 기록을 읽고, 바꾸는 것은 문서화된 필드(메시지·shared 의
    자기 키·metadata 의 자기 키)로 한정한다.
    """

    plan: Any
    state: Any
    harness: LoadedHarness
    gateway: "ModelCaller"
    ledger: "UsageLedger"
    emit: Callable[[str, Dict[str, Any]], None]
    model_config: Any
    registry: Any = None
    tool_context_provider: Callable[[], Any] = field(default=lambda: None)
    memory_provider: Any = None
    is_cli: bool = False
    #: 에이전트 설정(입력 x)의 명시 값 — 하네스 기본값보다 우선한다(설계 30 문서 P3).
    agent_settings: Mapping[str, Any] = field(default_factory=dict)
    scratch: Dict[str, Any] = field(default_factory=dict)

    @property
    def iteration(self) -> int:
        return int(getattr(self.state, "iteration", 0) or 0)

    @property
    def is_continuation(self) -> bool:
        return bool(getattr(self.state, "_is_continuation_slice", False))

    def knob(self, name: str, harness_default: Any) -> Any:
        """에이전트가 명시했으면 그 값, 아니면 하네스 기본값."""
        if name in self.agent_settings and self.agent_settings[name] is not None:
            return self.agent_settings[name]
        return harness_default
