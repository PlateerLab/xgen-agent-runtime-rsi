"""도구 실행 — 커널 소유(권한·HITL·사용자 거부·반복 차단·읽기 장부·결과 필터는 하네스가 끌 수 없다).

실행 자체는 바탕 런타임(``xgen_rsi.base``)의 ``ToolStage.dispatch_calls`` 를 그대로 쓴다. 그 함수는 SDK 루프와 CLI 도구
표면(``host.tool_surface``)이 함께 쓰는 **유일한 도구 실행 경로**라서(2026-09-30 감사), 새 엔진도 같은
경로를 지나야 도구가 백엔드·엔진과 무관하게 같게 군다. 이것은 21-stage 파이프라인을 쓰는 것이 아니라
도구 ABI 계약 계층을 쓰는 것이다 — 파이프라인 객체 없이 실행 서비스로만 만든다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from xgen_rsi.base.stages.s10_tool.artifact.default.executors import (
    ParallelExecutor,
    PartitionExecutor,
    SequentialExecutor,
)
from xgen_rsi.base.stages.s10_tool.artifact.default.stage import ToolStage
from xgen_rsi.base.tools.base import ToolContext

_EXECUTORS = {
    "sequential": SequentialExecutor,
    "parallel": ParallelExecutor,
    "partition": PartitionExecutor,
}


class ToolRunner:
    """턴 하나의 도구 실행기."""

    def __init__(
        self,
        registry: Any,
        *,
        tool_context: Optional[ToolContext] = None,
        result_filter: Any = None,
        executor: str = "sequential",
        max_concurrency: int = 10,
        capture: Any = None,
    ) -> None:
        self.capture = capture
        executor_cls = _EXECUTORS.get(executor, SequentialExecutor)
        self._stage = ToolStage(
            registry=registry,
            executor=executor_cls(),
            context=tool_context or ToolContext(),
            max_concurrency=max_concurrency,
        )
        if result_filter is not None:
            # 호스트 결과 필터(4.71.0) — 스테이지 컨텍스트에 싣는다(기존 build_pipeline 과 같은 자리).
            self._stage._context.result_filter = result_filter

    @property
    def registry(self) -> Any:
        return self._stage.registry

    @property
    def context(self) -> ToolContext:
        """현재 도구 컨텍스트 — 산출물 대조(DeliverableReviewer)가 같은 컨텍스트로 파일을 읽는다."""
        return self._stage._context

    async def run(self, state: Any, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """도구 호출 묶음을 실행하고 결과를 사용자 메시지로 기록한다(기존 Stage 10 execute 와 같은 기록)."""
        if not tool_calls:
            return []
        state.add_event(
            "tool.execute_start",
            {"count": len(tool_calls), "tools": [tc.get("tool_name", "") for tc in tool_calls]},
        )
        results = await self._stage.dispatch_calls(list(tool_calls), state)
        if self.capture is not None:
            try:
                self.capture.on_tool_results(tool_calls, results)
            except Exception:  # noqa: BLE001 — 세계 기록이 턴을 깨지 않는다
                pass
        state.add_message("user", results)
        state.tool_results = results
        state.shared["executor.tool_calls_total"] = int(state.shared.get("executor.tool_calls_total", 0)) + len(
            results
        )
        state.pending_tool_calls = []
        state.add_event(
            "tool.execute_complete",
            {"count": len(results), "errors": sum(1 for r in results if r.get("is_error"))},
        )
        return results
