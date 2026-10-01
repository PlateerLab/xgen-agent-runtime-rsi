"""GenyRSITurnExecutor — geny-rsi 의 진입점. 기존 엔진의 ``AgentTurnExecutor`` 와 **같은 계약**이다.

    from xgen_rsi.base.host.turn_executor import AgentTurnExecutor   # geny (기존 21-stage)
    from xgen_rsi import GenyRSITurnExecutor                             # geny-rsi

    out = GenyRSITurnExecutor().run(host, **kwargs)    # host: HostServices, kwargs: 노드 입력 그대로

반환도 같다 — ``streaming=True`` 면 글 조각·사건 dict 의 이터레이터, ``False`` 면 최종 글. 조립 실패는 예외 대신
``"[ERROR] geny agent could not start: …"`` 출력으로 낸다(기존과 같은 문구). xgen-agent-runtime 과 이 패키지는 서로 의존하지 않는다 — 어느
엔진을 쓸지는 **호스트가** 고른다(같은 자리에서 클래스만 바꾼다).

턴 조립은 :mod:`xgen_rsi.assembly`(호스트 계약의 자체 사본), 실행 코어는 :class:`~xgen_rsi.kernel.executor.RSITurnExecutor`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("xgen_rsi.turn_executor")


class GenyRSITurnExecutor:
    """``AgentTurnExecutor().run(host, **kwargs)`` 자리에 그대로 들어가는 geny-rsi 실행부."""

    def run(self, host: Any, **kwargs: Any) -> Any:
        from xgen_rsi.assembly import _close_resources, assemble_turn
        from xgen_rsi.kernel.executor import RSITurnExecutor

        streaming = bool(kwargs.get("streaming", True))
        resources: Dict[str, Any] = {}
        try:
            executor = RSITurnExecutor()
            plan = assemble_turn(host, kwargs, resources)
            if isinstance(plan, str):
                return iter([plan]) if streaming else plan
            prepared = executor.prepare(plan, host)
        except Exception as exc:  # noqa: BLE001 — 조립 실패는 출력으로(기존 엔진과 같은 계약)
            logger.exception("geny-rsi: failed to build the turn")
            _close_resources(resources)
            err = f"[ERROR] geny agent could not start: {exc}"
            return iter([err]) if streaming else err
        return executor.execute(prepared, plan, host)


__all__ = ["GenyRSITurnExecutor"]
