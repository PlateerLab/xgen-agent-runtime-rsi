"""config 구성요소 — 요청 모양을 바꾸는 노브(프롬프트 캐시 표시 등).

모델·공급자·자격증명·커널 한도는 잠금 키라 여기 없다(정책 π 고정, RRSI 헌법 hard rule 5).
"""

from __future__ import annotations

from typing import Any

from xgen_rsi.harness.runtime import Component


class PromptCacheComponent(Component):
    """Request configuration: Anthropic ``cache_control`` markers when the node enables prompt caching.

    Markers are added only for anthropic/bedrock (other providers cache prefixes automatically).
    ``strategy``: "aggressive" (tools, the stable system region and a moving history point) or
    "system" (system prompt only).

    Params: strategy (str, "aggressive").
    """

    kind = "config"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._strategy: Any = None

    def apply_request(self, rt: Any) -> None:
        if not bool(rt.plan.pipeline_kwargs.get("enable_prompt_cache", False)):
            return
        if self._strategy is None:
            from xgen_rsi.base.stages.s05_cache.artifact.default.strategies import (
                AggressiveCacheStrategy,
                SystemCacheStrategy,
            )

            name = str(self.param("strategy", "aggressive"))
            self._strategy = SystemCacheStrategy() if name == "system" else AggressiveCacheStrategy()
        self._strategy.apply_cache_markers(rt.state)
        rt.emit("cache.applied", {"strategy": type(self._strategy).__name__, "system_is_blocks": isinstance(rt.state.system, list)})
