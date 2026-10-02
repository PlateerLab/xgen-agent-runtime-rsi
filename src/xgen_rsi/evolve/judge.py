"""기준 판정 모델 — ``answer_criteria`` 검사를 채점한다.

사용 기록에서 만든 과제(피드백·기대 답)는 정답 문자열이 아니라 **기준**으로 판정한다("기대 답과 내용이 같다", "이 문제가 없어야 한다").
판정은 하네스 밖이다 — 판정 모델은 진화 대상 하네스를 보지 않고, 하네스는 실행 중 기준을 보지 못한다(설계 34 §1). 같은 (기준, 참조,
요청, 답)은 한 번만 묻는다(평가 반복에서 같은 답이 나오면 비용을 다시 쓰지 않는다).

모델은 XGEN 에 등록된 LLM 이다(:class:`~xgen_rsi.roles.llm.RoleModel` — 자격증명은 호출자가 넘긴다).
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Dict, Optional, Tuple

from xgen_rsi.roles.llm import RoleLLM, RoleModel

SYSTEM = """You grade one answer of an AI agent against one criterion.

You get the user's request, the agent's final answer, the criterion and, when given, a reference answer.
Decide only whether the answer satisfies the criterion. A reference answer shows what the user accepted or expected:
the answer satisfies a "same substance as the reference" criterion when it carries the same facts, numbers and
conclusions, in any wording or order. Do not reward length or style. Do not use knowledge the request does not need.

Reply with JSON: {"pass": true|false, "reason": "<one short sentence>"}"""

_MAX = 12_000


class CriteriaJudge:
    """``judge(criterion, *, reference, request, answer) -> (passed, reason)`` — :func:`xgen_rsi.evolve.verifiers.verify` 에 넘긴다."""

    def __init__(self, model: RoleModel, *, llm: Optional[RoleLLM] = None) -> None:
        self.llm = llm or RoleLLM(model, role="judge")
        self._cache: Dict[str, Tuple[bool, str]] = {}
        self._lock = threading.Lock()

    @property
    def ledger(self) -> Any:
        """판정에 쓴 토큰(진화 비용 — 정책 비용 c(τ)와 별도)."""
        return self.llm.ledger

    def __call__(self, criterion: str, *, reference: str = "", request: str = "", answer: str = "") -> Tuple[bool, str]:
        key = hashlib.sha256(json.dumps([criterion, reference, request, answer], ensure_ascii=False).encode()).hexdigest()
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        parts = [f"REQUEST:\n{request[:_MAX]}", f"ANSWER:\n{answer[:_MAX]}", f"CRITERION:\n{criterion}"]
        if reference:
            parts.append(f"REFERENCE ANSWER:\n{reference[:_MAX]}")
        try:
            raw = self.llm.generate_json("\n\n".join(parts), system=SYSTEM)
        except Exception as exc:  # noqa: BLE001 — 판정 실패는 통과로 치지 않는다(이유를 남긴다)
            return False, f"judge error: {type(exc).__name__}: {exc}"[:300]
        verdict = (bool(raw.get("pass")), str(raw.get("reason") or "")) if isinstance(raw, dict) else (False, "judge returned no verdict")
        with self._lock:
            self._cache[key] = verdict
        return verdict


__all__ = ["CriteriaJudge", "SYSTEM"]
