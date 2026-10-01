"""client_tool 구성요소 — 이번 호출에 모델이 볼 도구 표면.

레지스트리(어떤 도구가 있는가)는 턴 조립이 호스트 정책대로 만든다(입력). 이 구성요소는 그중 **무엇을
스키마까지 보여 줄지**를 정한다: 노출된 도구 + ToolSearch 로 연 도구, 앞 턴에 쓴 도구 되살리기, 숨긴 가족에
보이는 문이 있는지 검사. 레지스트리 버전이 바뀔 때만 다시 만든다(ToolSearch 활성화가 다음 호출에 바로 보인다).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from xgen_rsi.harness.runtime import Component

logger = logging.getLogger(__name__)


class ToolExposureComponent(Component):
    """Which tools the model sees on each call (the tool surface); execution itself is kernel-owned.

    Exposed tools plus tools opened through ToolSearch; ``restore_from_history`` re-exposes tools used
    in earlier turns of the conversation; ``gate_reachability`` checks that every gated tool family
    has a reachable entry point. ``executor`` ("sequential" or "parallel") and ``max_concurrency``
    are read by the kernel's tool runner from this component's params — they decide how the calls of
    one step run, never whether a call is permitted (permissions, user denials, HITL and the sandbox
    stay in the kernel).

    Params: restore_from_history (bool, True), gate_reachability (bool, True),
    executor (str, "sequential"), max_concurrency (int, 10).
    """
    kind = "client_tool"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._version: Any = object()
        self._tools: List[Dict[str, Any]] = []

    def exposed_tools(self, rt: Any) -> List[Dict[str, Any]]:
        registry = rt.registry
        if registry is None:
            return []
        version = getattr(registry, "version", None)
        if self._tools and version is not None and version == self._version:
            return self._tools
        restored: List[str] = []
        repaired: List[str] = []
        if self.param("restore_from_history", True):
            restored = _restore_from_history(registry, rt.state.messages)
            if restored:
                rt.emit("tool.surface_restored", {"opened": restored})
        if self.param("gate_reachability", True):
            repaired = _enforce_gate_reachability(registry)
            if repaired:
                rt.emit("tool.gate_reachability_repaired", {"opened": repaired})
        if restored or repaired:
            version = getattr(registry, "version", None)
        try:
            self._tools = registry.to_api_format(exposed_only=True)
        except TypeError:
            self._tools = registry.to_api_format()
        self._version = version
        return self._tools


def _enforce_gate_reachability(registry: Any) -> List[str]:
    names = getattr(registry, "list_names", None)
    is_exposed = getattr(registry, "is_exposed", None)
    activate = getattr(registry, "activate", None)
    if not (callable(names) and callable(is_exposed) and callable(activate)):
        return []
    from xgen_rsi.base.tools.gates import reachability_fixes

    try:
        fixes = reachability_fixes(names(), is_exposed)
    except Exception:  # noqa: BLE001
        logger.debug("gate reachability check failed", exc_info=True)
        return []
    return [n for n in fixes if activate(n)]


def _restore_from_history(registry: Any, messages: Any) -> List[str]:
    names = getattr(registry, "list_names", None)
    is_exposed = getattr(registry, "is_exposed", None)
    activate = getattr(registry, "activate", None)
    if not (callable(names) and callable(is_exposed) and callable(activate)):
        return []
    from xgen_rsi.base.tools.gates import restore_from_history

    try:
        wanted = restore_from_history(names(), is_exposed, messages or [])
    except Exception:  # noqa: BLE001
        logger.debug("surface restore from history failed", exc_info=True)
        return []
    return [n for n in wanted if activate(n)]
