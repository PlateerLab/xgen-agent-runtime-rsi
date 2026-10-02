"""memory 구성요소 — 기억 **정책**(무엇을 주입하고 어떻게 기록하는가). 기억 **내용**은 사용자 데이터라 하네스가 아니다.

H0: 기존 운영과 같은 두 배선 — 검색(``MemoryAwareRetriever``: 고정 사실·관련 지식)과 슬라이스 끝 기록
(``ConversationArchivingStrategy``: STM 기록 + vault 대화 rollup, 메모리 브라우저가 읽는 형식). 기억
provider 는 턴 조립이 호스트에서 받아 오고, 수명(닫기·증류)은 커널 teardown 이 소유한다.
"""

from __future__ import annotations

import logging
from typing import Any

from xgen_rsi.harness.runtime import Component

logger = logging.getLogger(__name__)


def is_replay(provider: Any) -> bool:
    """재생 기억 자리(:class:`xgen_rsi.consolidate.replay.ReplayMemoryProvider`)인가 — 클래스 속성으로만 본다
    (속성을 무엇이든 돌려주는 기억 객체가 재생으로 오인되지 않게)."""
    return provider is not None and getattr(type(provider), "rsi_replay", False) is True


class ArchiveMemoryComponent(Component):
    """Memory policy (the memory *contents* are user data and never part of the harness).

    ``retrieve``: provide the retriever that injects pinned facts and relevant knowledge at the first
    iteration (used by the context component). ``archive``: at the end of each slice, write the
    short-term record and update the conversation rollup.

    Params: retrieve (bool, True), archive (bool, True).
    """
    kind = "memory"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._retriever: Any = None
        self._strategy: Any = None

    def retriever(self, rt: Any) -> Any:
        if rt.memory_provider is None or not self.param("retrieve", True):
            return None
        if is_replay(rt.memory_provider):  # 재생(설계 41): 기록된 검색 결과를 돌려준다
            return rt.memory_provider.rsi_retriever()
        if self._retriever is None:
            from xgen_rsi.base.memory.retriever import MemoryAwareRetriever

            self._retriever = MemoryAwareRetriever(rt.memory_provider)
        return self._retriever

    async def on_slice_end(self, rt: Any) -> None:
        if rt.memory_provider is None or not self.param("archive", True):
            return
        if is_replay(rt.memory_provider):  # 재생은 기억에 쓰지 않는다
            return
        if self._strategy is None:
            from xgen_rsi.base.host.conversation_archive import ConversationArchivingStrategy

            self._strategy = ConversationArchivingStrategy(rt.memory_provider)
        try:
            await self._strategy.update(rt.state)
        except Exception:  # noqa: BLE001 — 기록 실패가 턴을 깨지 않는다
            logger.warning("rsi: memory archive update failed", exc_info=True)
            return
        rt.emit("memory.updated", {"strategy": type(self._strategy).__name__})
