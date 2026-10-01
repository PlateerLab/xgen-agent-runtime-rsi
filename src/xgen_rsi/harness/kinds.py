"""하네스 구성요소 어휘 𝒦 와 구성요소 인터페이스.

RRSI(arXiv 2609.24972) Eq.(12) 의 어휘를 그대로 쓴다 — 수식(Eq.15/16 의 𝒦_str, ν_t)이 이 집합 위에서
정의되므로 이름·순서를 바꾸지 않는다. 하네스의 모든 구성요소는 이 중 정확히 하나의 kind 를 선언한다.

구성요소는 커널이 주는 :class:`~xgen_rsi.harness.runtime.TurnRuntime` 으로만 턴에 닿는다. 정책 호출은
커널 게이트웨이(``rt.gateway``)를 거쳐야 원장에 남는다 — 구성요소에는 공급자 클라이언트를 직접 주지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

if TYPE_CHECKING:  # pragma: no cover
    from xgen_rsi.harness.runtime import TurnRuntime

#: RRSI Eq.(12) — 편집 가능한 구성요소 어휘. 순서는 공식 구현(rrsi/components.py)과 같다.
K: Tuple[str, ...] = (
    "prompt",
    "control_flow",
    "config",
    "output_plumbing",
    "context_mgmt",
    "client_tool",
    "skill",
    "memory",
    "subagent",
)

#: RRSI Eq.(15) — 구조 구성요소(새로움 ν 가 세는 것).
K_STR: Tuple[str, ...] = ("client_tool", "skill", "memory", "subagent")

Kind = Literal[
    "prompt",
    "control_flow",
    "config",
    "output_plumbing",
    "context_mgmt",
    "client_tool",
    "skill",
    "memory",
    "subagent",
]

#: 루프 결정 — control_flow 구성요소만 정한다(커널 한도는 예외로 더 일찍 멈출 수만 있다).
Decision = Literal["continue", "complete", "suspend", "error", "escalate"]


@dataclass(frozen=True)
class SystemPrompt:
    """prompt 구성요소의 산출물.

    ``system`` 은 공급자에 그대로 가는 시스템 프롬프트(문자열 또는 블록 목록), ``turn_context`` 는 캐시
    접두 밖(최신 사용자 메시지 옆)에 붙는 이번 턴 맥락(시각·검색된 기억·턴 안내)이다.
    """

    system: Any
    turn_context: str = ""
    parts: Tuple[Tuple[str, str], ...] = ()


@dataclass
class ParsedStep:
    """output_plumbing 이 모델 응답에서 읽어 낸 것."""

    text: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    signal: Optional[str] = None
    signal_detail: Optional[str] = None
    stop_reason: Optional[str] = None
    thinking_texts: List[str] = field(default_factory=list)


@runtime_checkable
class PromptComponent(Protocol):
    kind: str

    def build(self, rt: "TurnRuntime") -> SystemPrompt: ...


@runtime_checkable
class ContextComponent(Protocol):
    kind: str

    async def before_call(self, rt: "TurnRuntime") -> None: ...

    async def ensure_fits(self, rt: "TurnRuntime") -> None: ...


@runtime_checkable
class ControlComponent(Protocol):
    kind: str

    async def decide(self, rt: "TurnRuntime", step: ParsedStep, tools_ran: bool) -> Decision: ...


@runtime_checkable
class OutputComponent(Protocol):
    kind: str

    def parse(self, rt: "TurnRuntime", response: Any) -> ParsedStep: ...

    def settle(self, text: str, schema: Optional[Dict[str, Any]]) -> str: ...


@runtime_checkable
class ToolPolicyComponent(Protocol):
    kind: str

    def exposed_tools(self, rt: "TurnRuntime") -> List[Dict[str, Any]]: ...


@runtime_checkable
class MemoryComponent(Protocol):
    kind: str

    def retriever(self, rt: "TurnRuntime") -> Any: ...

    async def on_slice_end(self, rt: "TurnRuntime") -> None: ...


@runtime_checkable
class ConfigComponent(Protocol):
    kind: str

    def apply_request(self, rt: "TurnRuntime") -> None: ...


#: kind → 이 kind 구성요소가 만족해야 하는 프로토콜. skill·subagent 는 H0 에 없고 확장용이다.
PROTOCOLS: Dict[str, Any] = {
    "prompt": PromptComponent,
    "context_mgmt": ContextComponent,
    "control_flow": ControlComponent,
    "output_plumbing": OutputComponent,
    "client_tool": ToolPolicyComponent,
    "memory": MemoryComponent,
    "config": ConfigComponent,
}

#: 턴 실행에 반드시 하나씩 있어야 하는 kind. 없으면 하네스 로드가 실패한다.
REQUIRED_KINDS: Tuple[str, ...] = ("prompt", "context_mgmt", "control_flow", "output_plumbing")
