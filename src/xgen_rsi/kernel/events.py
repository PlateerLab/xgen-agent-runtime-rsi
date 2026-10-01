"""이벤트 허브 — 턴의 모든 사건이 한 줄로 지나는 곳.

출처는 둘이다. (1) 메커니즘·도구가 ``state.add_event`` 로 내는 사건 — 커널이 ``state._bus_emitter`` 를 이
허브에 연결해 받는다(기존 파이프라인이 이벤트 버스에 연결하던 자리와 같다). (2) 커널이 직접 내는 수명 사건
(``pipeline.start``/``pipeline.complete``/``pipeline.error``). 허브는 각 사건에 run_id·seq 를 붙여

* 스트림 번역기(외부 청크로)
* rollout 기록기(관리자 옵트인, 기존 형식 ``asdict(PipelineEvent)`` 한 줄)
* 궤적 기록기(RSI 내부)

에 같은 순서로 넘긴다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from xgen_rsi.base.events.types import PipelineEvent

logger = logging.getLogger(__name__)

Listener = Callable[[PipelineEvent], None]


class EventHub:
    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self.run_id = uuid.uuid4().hex
        self._seq = 0
        self._queue: Optional[asyncio.Queue] = None
        self._listeners: List[Listener] = []

    # ── 연결 ────────────────────────────────────────────────────────────
    def attach_state(self, state: Any) -> None:
        """``state.add_event`` 가 이 허브로 흐르게 한다."""
        state._bus_emitter = self._from_state

    def new_run(self) -> None:
        """슬라이스(이어가기 포함)마다 새 run_id — 기존 파이프라인과 같은 상관 규칙."""
        self.run_id = uuid.uuid4().hex

    def open_queue(self) -> asyncio.Queue:
        self._queue = asyncio.Queue()
        return self._queue

    def close_queue(self) -> None:
        self._queue = None

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    # ── 발행 ────────────────────────────────────────────────────────────
    def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None, *, stage: str = "", iteration: int = 0) -> PipelineEvent:
        self._seq += 1
        event = PipelineEvent(
            type=event_type,
            stage=stage,
            iteration=iteration,
            timestamp=datetime.now(timezone.utc).isoformat(),
            data=dict(data or {}),
            session_id=self.session_id,
            run_id=self.run_id,
            seq=self._seq,
        )
        self._dispatch(event)
        return event

    def _from_state(self, event_dict: Dict[str, Any]) -> None:
        self._seq += 1
        event = PipelineEvent(
            type=str(event_dict.get("type") or ""),
            stage=str(event_dict.get("stage") or ""),
            iteration=int(event_dict.get("iteration") or 0),
            timestamp=str(event_dict.get("timestamp") or datetime.now(timezone.utc).isoformat()),
            data=dict(event_dict.get("data") or {}),
            session_id=self.session_id,
            run_id=self.run_id,
            seq=self._seq,
        )
        self._dispatch(event)

    def _dispatch(self, event: PipelineEvent) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001 — 관측이 실행을 깨지 않는다
                logger.warning("rsi: event listener failed for %s (ignored)", event.type, exc_info=True)
        if self._queue is not None:
            self._queue.put_nowait(event)
