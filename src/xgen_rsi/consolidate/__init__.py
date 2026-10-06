"""턴 정리 — 턴마다 Dream-RSI × RRSI 로 에이전트의 하네스를 고친다(설계 41).

* :mod:`~xgen_rsi.consolidate.world` — 턴 세계(기록 + 신호)
* :mod:`~xgen_rsi.consolidate.replay` — 세계 재생(도구는 기록된 결과, 모델만 새로)
* :mod:`~xgen_rsi.consolidate.signals` — 사용자 신호 → 판정 기준(다음 사용자 메시지 포함)
* :mod:`~xgen_rsi.consolidate.consolidator` — 정리 한 번(RRSI 라운드 하나를 재생으로)
"""

from xgen_rsi.consolidate.consolidator import (
    ConsolidationError,
    ConsolidationParams,
    ConsolidationResult,
    ConsolidationState,
    Consolidator,
)
from xgen_rsi.consolidate.replay import ReplayResult, replay_world
from xgen_rsi.consolidate.signals import checks_for, implicit_signal
from xgen_rsi.consolidate.world import TurnWorld

__all__ = ["ConsolidationError", "ConsolidationParams", "ConsolidationResult", "ConsolidationState", "Consolidator", "ReplayResult", "TurnWorld",
           "checks_for", "implicit_signal", "replay_world"]
