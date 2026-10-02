"""턴 세계 — 에이전트 턴 하나의 기록(:mod:`xgen_rsi.kernel.capture`)과 그 턴에 붙은 신호.

호스트(XGEN)가 저장하고 정리할 때 넘긴다. 세계는 하네스 밖이다 — 하네스는 실행 중 세계·신호·판정 기준을 보지 못한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from xgen_rsi.kernel.capture import WORLD_SCHEMA

_PREVIEW = 160


def text_of(content: Any) -> str:
    """메시지·입력 내용 → 글(텍스트 블록만)."""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        if "text" in content and isinstance(content.get("text"), str) and content.get("type") in (None, "text"):
            return str(content["text"])
        return text_of(content.get("content")) if "content" in content else ""
    if isinstance(content, list):
        return "\n".join(t for t in (text_of(b) for b in content if not (isinstance(b, Mapping) and b.get("type") in
                                                                      ("tool_use", "tool_result", "thinking"))) if t)
    return ""


@dataclass
class TurnWorld:
    """세계 하나. ``id`` 는 호스트의 턴 id(예: ``io-123``), ``data`` 는 기록 원본, ``signals`` 는 그 턴에 붙은 사용자 신호."""

    id: str
    data: Dict[str, Any]
    signals: Dict[str, Any] = field(default_factory=dict)
    """``{"expected", "stars", "issue", "comment", "criteria": [...], "implicit": {"kind", "criterion", "evidence"}}``."""
    interaction_id: str = ""
    seq: int = 0
    """같은 대화 안의 순서(호스트가 정한다 — 앞 턴 찾기)."""

    # ── 읽기 ────────────────────────────────────────────────────────────
    @property
    def replayable(self) -> bool:
        d = self.data or {}
        return d.get("schema") == WORLD_SCHEMA and bool(d.get("replayable", True)) and bool(d.get("plan"))

    @property
    def harness_version(self) -> str:
        return str((self.data or {}).get("harness_version") or "")

    @property
    def model(self) -> str:
        return str((self.data or {}).get("model") or "")

    @property
    def request(self) -> str:
        return text_of((self.data or {}).get("input")).strip()

    @property
    def outcome(self) -> Dict[str, Any]:
        return dict((self.data or {}).get("outcome") or {})

    @property
    def answer(self) -> str:
        return str(self.outcome.get("final_text") or "")

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(((self.data or {}).get("state") or {}).get("messages") or [])

    @property
    def tool_calls(self) -> List[Dict[str, Any]]:
        return list(((self.data or {}).get("tools") or {}).get("calls") or [])

    def preview(self, n: int = _PREVIEW) -> str:
        text = " ".join(self.request.split())
        return text if len(text) <= n else text[: n - 1] + "…"

    # ── 직렬화 ──────────────────────────────────────────────────────────
    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "data": self.data, "signals": self.signals, "interaction_id": self.interaction_id, "seq": self.seq}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "TurnWorld":
        data = dict(raw.get("data") or {})
        return cls(id=str(raw["id"]), data=data, signals=dict(raw.get("signals") or {}),
                   interaction_id=str(raw.get("interaction_id") or data.get("interaction_id") or ""),
                   seq=int(raw.get("seq") or 0))


def previous_in_conversation(worlds: Sequence[TurnWorld], world: TurnWorld) -> Optional[TurnWorld]:
    """같은 대화에서 ``world`` 바로 앞 턴."""
    if not world.interaction_id:
        return None
    earlier = [w for w in worlds if w.interaction_id == world.interaction_id and w.seq < world.seq]
    return max(earlier, key=lambda w: w.seq) if earlier else None


__all__ = ["TurnWorld", "previous_in_conversation", "text_of"]
