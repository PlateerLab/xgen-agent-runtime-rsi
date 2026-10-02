"""신호 → 판정 기준 — 턴 세계를 채점할 기준을 사용자 신호에서 만든다(설계 41 §2 "평가기 점수").

신호는 세 가지다.

* 명시 — 기대 답(품질평가), 별점·이슈·코멘트(피드백), 사용자가 쓴 기준. :func:`xgen_rsi.usage.criteria_for` 와 같은 기준이다.
* 암묵 — **다음 사용자 메시지**. 같은 대화에서 사용자가 이어서 한 말이 앞 답에 대한 정정·불만인지, 수용인지를 판정 모델이 읽고,
  정정·불만이면 "더 나은 답이 지켜야 할 것" 하나를 기준으로 쓴다(사용자가 말한 것에서만). 수용이면 그 답의 요점을 지키라는 회귀 기준.
* (판정 모델과 기준은 하네스 밖이다 — 하네스는 실행 중 기준을 보지 못한다.)

채점: 기준마다 판정 모델의 통과/실패, 점수 = 통과 수 / 기준 수, 가중치 = 기준 수(05 §2.1 의 w).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Tuple

from xgen_rsi.usage import KEEPS_ACCEPTED, UsageItem, criteria_for

IMPLICIT_KINDS = ("correction", "complaint", "accept", "neutral")
_CLIP = 6000

IMPLICIT_SYSTEM = """You read one exchange between a user and an AI agent: the user's EARLIER request, the agent's ANSWER,
and the user's NEXT message in the same conversation. Decide what the NEXT message says about the ANSWER.

kind:
- "correction": the user says the answer was wrong, incomplete, misunderstood the request, did the wrong thing,
  ignored an instruction, or re-asks / repeats the request because it was not done.
- "complaint": the user is dissatisfied with how it was answered (format, length, language, tone, speed) without
  saying the content was wrong.
- "accept": the user explicitly approves, thanks, or confirms the answer was right.
- "neutral": anything else (a new topic, a follow-up that builds on the answer, a question that does not judge it).

For "correction" or "complaint", write ONE criterion that a better answer to the EARLIER request must satisfy:
a general requirement on the answer (what it must do or must not do), grounded only in what the user said in the
NEXT message. Do not add facts the user did not state. Write it in English.

Reply with JSON only: {"kind": "...", "criterion": "...", "evidence": "<short quote from the NEXT message>"}"""


def implicit_signal(llm: Any, *, request: str, answer: str, next_message: str) -> Dict[str, Any]:
    """다음 사용자 메시지 → ``{"kind", "criterion", "evidence"}``. 판정 실패면 ``{"kind": "neutral", "error": ...}``."""
    prompt = "\n\n".join([
        f"EARLIER REQUEST:\n{request[:_CLIP]}",
        f"ANSWER:\n{answer[:_CLIP]}",
        f"NEXT MESSAGE:\n{next_message[:_CLIP]}",
    ])
    try:
        raw = llm.generate_json(prompt, system=IMPLICIT_SYSTEM)
    except Exception as exc:  # noqa: BLE001 — 신호를 못 읽으면 신호가 없는 것
        return {"kind": "neutral", "error": f"{type(exc).__name__}: {exc}"[:300]}
    if not isinstance(raw, Mapping):
        return {"kind": "neutral", "error": "no verdict"}
    kind = str(raw.get("kind") or "neutral").strip().lower()
    if kind not in IMPLICIT_KINDS:
        kind = "neutral"
    criterion = str(raw.get("criterion") or "").strip()
    if kind in ("correction", "complaint") and not criterion:
        kind = "neutral"
    return {"kind": kind, "criterion": criterion if kind in ("correction", "complaint") else "",
            "evidence": str(raw.get("evidence") or "")[:300]}


def checks_for(signals: Mapping[str, Any], *, answer: str) -> List[Dict[str, Any]]:
    """세계에 붙은 신호 → 판정 기준(``answer_criteria``) 목록. 신호가 없으면 빈 목록."""
    stars = signals.get("stars")
    item = UsageItem(
        id="w",
        request="-",
        answer=answer,
        stars=int(stars) if stars not in (None, "") else None,
        issue=str(signals.get("issue") or ""),
        comment=str(signals.get("comment") or ""),
        expected=str(signals.get("expected") or ""),
        criteria=tuple(str(c) for c in signals.get("criteria") or () if str(c).strip()),
    )
    checks = criteria_for(item)
    implicit = signals.get("implicit") or {}
    kind = str(implicit.get("kind") or "")
    if kind in ("correction", "complaint") and str(implicit.get("criterion") or "").strip():
        checks.append({"kind": "answer_criteria", "name": f"implicit_{kind}", "criterion": str(implicit["criterion"]).strip()})
    elif kind == "accept" and answer.strip() and not any(c["name"] == "accepted" for c in checks):
        checks.append({"kind": "answer_criteria", "name": "implicit_accept", "criterion": KEEPS_ACCEPTED,
                       "reference": answer.strip()})
    return checks


def negative(signals: Mapping[str, Any]) -> bool:
    """고칠 것을 말하는 신호인가(정정·불만·낮은 별점·이슈·기대 답)."""
    if str((signals.get("implicit") or {}).get("kind") or "") in ("correction", "complaint"):
        return True
    if str(signals.get("expected") or "").strip() or list(signals.get("criteria") or ()):
        return True
    stars = signals.get("stars")
    if stars not in (None, "") and int(stars) <= 2:
        return True
    from xgen_rsi.usage import NO_ISSUE

    return str(signals.get("issue") or "").strip() not in NO_ISSUE


def score(checks: List[Dict[str, Any]], *, request: str, answer: str,
          judge: Callable[..., Tuple[bool, str]]) -> Tuple[float, float, List[Dict[str, Any]]]:
    """(r, w, 판정 목록). 기준이 없으면 (0, 0, [])."""
    if not checks:
        return 0.0, 0.0, []
    verdicts: List[Dict[str, Any]] = []
    passed = 0
    for c in checks:
        ok, reason = judge(str(c.get("criterion") or ""), reference=str(c.get("reference") or ""), request=request,
                           answer=answer)
        passed += int(bool(ok))
        verdicts.append({"name": c.get("name"), "criterion": c.get("criterion"), "pass": bool(ok), "reason": reason})
    return passed / len(checks), float(len(checks)), verdicts


__all__ = ["IMPLICIT_KINDS", "IMPLICIT_SYSTEM", "checks_for", "implicit_signal", "negative", "score"]
