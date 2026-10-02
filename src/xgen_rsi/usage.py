"""사용 기록 → 과제 — 에이전트를 쓰는 사용자의 실제 사용을 RRSI 가 잴 수 있는 과제로 만든다(설계 40 §2.3).

RSI 의 강점은 사용자 fit 이다. 그래서 진화의 재료는 실험 스위트가 아니라 **그 에이전트의 사용**이다. 호스트(XGEN)가 넘기는 것:

* 그 턴의 요청과 앞 대화, 그때의 답 — ``execution_io``
* 사용자 피드백 — 별점 1–5, 이슈 유형, 코멘트(``user_feedbacks``)
* 기대 답 — 품질평가(``execution_io.expected_output``)
* 사용자가 직접 쓴 판정 기준

과제 하나 = 그 요청(앞 대화 포함) + 판정 기준 목록. 판정 기준은 기준 판정 검사(``answer_criteria``, 판정 모델이 채점)다. 판정 모델과 기준은
하네스 밖이다 — 진화하는 하네스는 실행 중 기준을 보지 못한다. 신호가 없는 턴(피드백·기대 답·기준이 하나도 없는 턴)은 과제가 되지 않는다.

이 모듈은 호스트에 묶이지 않는 순수 함수다. 에이전트 설정(시스템 프롬프트·출력 스키마)은 과제 입력의 일부로 싣는다(진화 대상이 아니다).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from xgen_rsi.evolve.tasks import TaskSpec, write_suite

#: 문제 없음을 뜻하는 이슈 유형(XGEN 피드백의 기본값).
NO_ISSUE = ("", "이슈없음", "none")

SAME_AS_REFERENCE = "The answer conveys the same facts, numbers and conclusions as the reference answer (wording and order may differ)."
KEEPS_ACCEPTED = "The answer keeps every key point of the reference answer, which the user accepted (wording and order may differ)."
_HISTORY_MESSAGES = 6
_HISTORY_CHARS = 4000


class NotEnoughUsage(ValueError):
    """과제를 만들 사용 기록이 모자란다."""


@dataclass(frozen=True)
class UsageItem:
    """에이전트 턴 하나와 그에 붙은 신호."""

    id: str
    request: str
    answer: str = ""
    history: Sequence[Mapping[str, str]] = ()
    """앞 대화 ``[{"role": "user"|"assistant", "content": "..."}]``(오래된 것부터)."""
    stars: Optional[int] = None
    issue: str = ""
    comment: str = ""
    expected: str = ""
    criteria: Sequence[str] = ()

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "UsageItem":
        stars = raw.get("stars")
        return cls(
            id=str(raw["id"]),
            request=str(raw.get("request") or ""),
            answer=str(raw.get("answer") or ""),
            history=tuple(dict(m) for m in raw.get("history") or ()),
            stars=int(stars) if stars not in (None, "") else None,
            issue=str(raw.get("issue") or ""),
            comment=str(raw.get("comment") or ""),
            expected=str(raw.get("expected") or ""),
            criteria=tuple(str(c) for c in raw.get("criteria") or () if str(c).strip()),
        )


@dataclass(frozen=True)
class AgentSettings:
    """과제 입력에 싣는 에이전트 설정(진화 대상 하네스가 아니다 — 설계 30 P3)."""

    system_prompt: Optional[str] = None
    output_schema: Optional[Dict[str, Any]] = None
    max_iterations: int = 20
    toolset: str = "workspace"
    """평가에서 여는 도구 묶음. 기본은 독립 작업 공간의 파일 도구 — 평가가 사용자의 데이터·외부 시스템을 건드리지 않는다."""


def criteria_for(item: UsageItem) -> List[Dict[str, Any]]:
    """턴 하나의 신호 → 판정 기준(``answer_criteria`` 검사) 목록. 신호가 없으면 빈 목록."""
    checks: List[Dict[str, Any]] = []
    if item.expected.strip():
        checks.append({"kind": "answer_criteria", "name": "expected", "criterion": SAME_AS_REFERENCE,
                       "reference": item.expected.strip()})
    reported = item.issue.strip() not in NO_ISSUE or (item.stars is not None and item.stars <= 2)
    if reported:
        problem = item.issue.strip() if item.issue.strip() not in NO_ISSUE else "the user rated this answer as poor"
        note = f": {item.comment.strip()}" if item.comment.strip() else ""
        checks.append({"kind": "answer_criteria", "name": "reported_problem",
                       "criterion": f"The answer does not have the problem the user reported on an earlier answer to this request ({problem}{note})."})
    elif item.stars is not None and item.stars >= 4 and item.answer.strip():
        checks.append({"kind": "answer_criteria", "name": "accepted", "criterion": KEEPS_ACCEPTED,
                       "reference": item.answer.strip()})
    for i, text in enumerate(c for c in item.criteria if str(c).strip()):
        checks.append({"kind": "answer_criteria", "name": f"user_{i + 1}", "criterion": str(text).strip()})
    return checks


def render_prompt(item: UsageItem) -> str:
    """요청 + 앞 대화(마지막 몇 개, 길이 상한)."""
    if not item.history:
        return item.request
    lines: List[str] = []
    for m in list(item.history)[-_HISTORY_MESSAGES:]:
        who = "User" if str(m.get("role")) == "user" else "Assistant"
        lines.append(f"{who}: {str(m.get('content') or '').strip()}")
    earlier = "\n".join(lines)
    if len(earlier) > _HISTORY_CHARS:
        earlier = "…" + earlier[-_HISTORY_CHARS:]
    return f"Earlier in this conversation:\n{earlier}\n\nCurrent request:\n{item.request}"


def task_id_for(item: UsageItem) -> str:
    return "u-" + hashlib.sha256(item.id.encode("utf-8")).hexdigest()[:12]


def task_from_usage(item: UsageItem, agent: AgentSettings = AgentSettings()) -> Optional[TaskSpec]:
    """턴 하나 → 과제. 요청이 비었거나 판정 기준이 없으면 None."""
    if not item.request.strip():
        return None
    checks = criteria_for(item)
    if not checks:
        return None
    tags = tuple(sorted({c["name"].split("_")[0] for c in checks}))
    return TaskSpec(
        id=task_id_for(item),
        prompt=render_prompt(item),
        checks=tuple(checks),
        toolset=agent.toolset,
        output_schema=agent.output_schema,
        system_prompt=agent.system_prompt,
        max_iterations=agent.max_iterations,
        tags=("usage",) + tags,
        description=f"usage item {item.id}",
    )


def split_of(task_id: str, heldout_every: int = 4) -> str:
    """과제 id 의 해시로 evolve/heldout 을 고정한다(같은 과제는 언제나 같은 쪽)."""
    return "heldout" if int(hashlib.sha256(task_id.encode()).hexdigest(), 16) % max(2, heldout_every) == 0 else "evolve"


@dataclass
class UsageSuite:
    root: Path
    tasks: List[TaskSpec] = field(default_factory=list)
    splits: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    skipped: int = 0
    """신호가 없어 과제가 되지 않은 턴 수."""


def build_suite(items: Sequence[UsageItem], out_dir: os.PathLike[str] | str, *, agent: AgentSettings = AgentSettings(),
                name: str = "agent-usage", min_evolve: int = 4, heldout_every: int = 4, smoke: int = 2) -> UsageSuite:
    """사용 기록 → 스위트 디렉터리(``suite.json`` + ``tasks/``). evolve 과제가 ``min_evolve`` 보다 적으면 :class:`NotEnoughUsage`."""
    tasks: Dict[str, TaskSpec] = {}
    skipped = 0
    for item in items:
        task = task_from_usage(item, agent)
        if task is None:
            skipped += 1
            continue
        tasks[task.id] = task  # 같은 턴이 두 번 오면 마지막 것
    ordered = sorted(tasks.values(), key=lambda t: t.id)
    evolve = [t.id for t in ordered if split_of(t.id, heldout_every) == "evolve"]
    heldout = [t.id for t in ordered if split_of(t.id, heldout_every) == "heldout"]
    if len(evolve) < min_evolve:
        raise NotEnoughUsage(f"{len(evolve)} evolve task(s) from usage, need at least {min_evolve} "
                             f"({len(ordered)} task(s), {skipped} turn(s) without feedback, expected answer or criteria)")
    splits = {"evolve": tuple(evolve), "heldout": tuple(heldout), "smoke": tuple(evolve[:smoke])}
    root = write_suite(name, ordered, splits, out_dir)
    return UsageSuite(root=Path(root), tasks=ordered, splits=splits, skipped=skipped)


__all__ = ["AgentSettings", "NotEnoughUsage", "UsageItem", "UsageSuite", "build_suite", "criteria_for", "render_prompt",
           "split_of", "task_from_usage"]
