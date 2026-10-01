"""궤적 기록기 — 모든 실행을 발견 트리(Dream-RSI 의 세계)와 τ 요약(RRSI 의 측정 단위)으로 남긴다.

* :class:`TrajectoryRecord` — τ 하나: 하네스 버전, 정책, 상태, 종료 사유, 스텝, 원장 요약(c(τ)), 발화한
  하네스 장치, 트리 id. 오프라인 평가에서는 검증기 결과(r, valid_output, no_submission)가 여기에 붙는다.
* :class:`ReplayNode` — 트리 노드. 일반 대화 턴은 루트 + 사슬 하나(퇴화 트리)다. 턴 안 탐색이 열린 하위
  작업은 branch × attempt 격자가 된다(``explore`` 계층이 기록).

운영 턴의 기본은 **구조·점수·비용만** 저장(내용 비보존)이다(설계 30 문서 §5). 평가 실행은 렌더링용 전사
(transcript)까지 남긴다. 저장 위치가 없으면 메모리에만 두고 버린다.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ReplayNode:
    node_id: str
    tree_id: str
    parent_id: Optional[str]
    branch: int
    attempt: int
    created_seq: int
    score: Optional[float] = None
    evaluated: bool = False
    valid: Optional[bool] = None
    fail_class: str = "ok"
    error: Optional[str] = None
    n_valid: Optional[int] = None
    n_total: Optional[int] = None
    tags: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    policy_tokens: int = 0
    model_calls: int = 0


@dataclass
class TrajectoryRecord:
    trajectory_id: str
    task_id: str
    harness_id: str
    harness_name: str
    lineage: str
    explore_policy_id: Optional[str]
    provider: str
    model: str
    thinking_level: Optional[str]
    status: str = "running"
    termination_reason: str = ""
    error: Optional[str] = None
    steps: Dict[str, int] = field(default_factory=lambda: {"model_calls": 0, "iterations": 0, "tool_calls": 0, "tool_errors": 0, "slices": 0})
    policy_tokens: int = 0
    usage_by_purpose: Dict[str, Dict[str, int]] = field(default_factory=dict)
    components_fired: Dict[str, int] = field(default_factory=dict)
    tree_id: str = ""
    nodes: List[ReplayNode] = field(default_factory=list)
    final_text_chars: int = 0
    final_text: Optional[str] = None
    transcript: Optional[List[Dict[str, Any]]] = None
    verifier: Optional[Dict[str, Any]] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


class TrajectoryRecorder:
    """턴 하나를 기록한다. 커널이 사건·원장을 먹이고, 끝에서 :meth:`finish` 로 레코드를 얻는다."""

    def __init__(
        self,
        *,
        task_id: str,
        harness_id: str,
        harness_name: str,
        lineage: str,
        provider: str,
        model: str,
        thinking_level: Optional[str],
        explore_policy_id: Optional[str] = None,
        sink_dir: Optional[str] = None,
        keep_content: bool = False,
    ) -> None:
        tree_id = uuid.uuid4().hex
        self.record = TrajectoryRecord(
            trajectory_id=uuid.uuid4().hex,
            task_id=task_id,
            harness_id=harness_id,
            harness_name=harness_name,
            lineage=lineage,
            explore_policy_id=explore_policy_id,
            provider=provider,
            model=model,
            thinking_level=thinking_level,
            tree_id=tree_id,
        )
        self._sink_dir = sink_dir
        self._keep_content = keep_content
        self._seq = 0
        root = self._node(parent=None, branch=0, attempt=0, tags={"role": "root"})
        self._tip = root.node_id

    def _node(self, *, parent: Optional[str], branch: int, attempt: int, tags: Dict[str, Any]) -> ReplayNode:
        self._seq += 1
        node = ReplayNode(
            node_id=f"{self.record.tree_id[:8]}-{self._seq}",
            tree_id=self.record.tree_id,
            parent_id=parent,
            branch=branch,
            attempt=attempt,
            created_seq=self._seq,
            tags=dict(tags),
        )
        self.record.nodes.append(node)
        return node

    # ── 커널이 부른다 ───────────────────────────────────────────────────
    def on_event(self, event: Any) -> None:
        etype = getattr(event, "type", "")
        data = getattr(event, "data", {}) or {}
        steps = self.record.steps
        if etype == "api.response":
            steps["model_calls"] += 1
        elif etype == "tool.execute_complete":
            steps["tool_calls"] += int(data.get("count") or 0)
            steps["tool_errors"] += int(data.get("errors") or 0)
        elif etype in ("loop.continue", "loop.complete", "loop.suspend", "loop.error", "loop.escalate"):
            steps["iterations"] += 1

    def step(self, *, attempt: int, tool_calls: int, tool_errors: int, decision: str, policy_tokens: int, model_calls: int) -> None:
        """메인 루프 한 번 = 사슬의 노드 하나(가지 0)."""
        node = self._node(parent=self._tip, branch=0, attempt=attempt, tags={"role": "step", "decision": decision})
        node.diagnostics = {"tool_calls": tool_calls, "tool_errors": tool_errors}
        node.policy_tokens = policy_tokens
        node.model_calls = model_calls
        self._tip = node.node_id

    def add_nodes(self, nodes: List[ReplayNode]) -> None:
        """탐색 격자 노드(explore 계층) — 같은 트리 id 로 붙인다."""
        for n in nodes:
            n.tree_id = self.record.tree_id
            self.record.nodes.append(n)

    def slice_started(self) -> None:
        self.record.steps["slices"] += 1

    def finish(
        self,
        *,
        status: str,
        termination_reason: str,
        final_text: str,
        ledger: Any,
        components_fired: Optional[Dict[str, int]],
        error: Optional[str] = None,
        transcript: Optional[List[Dict[str, Any]]] = None,
    ) -> TrajectoryRecord:
        rec = self.record
        rec.status = status
        rec.termination_reason = termination_reason or ""
        rec.error = error
        rec.policy_tokens = int(ledger.policy_tokens())
        rec.usage_by_purpose = ledger.by_purpose()
        rec.components_fired = dict(components_fired or {})
        rec.final_text_chars = len(final_text or "")
        if self._keep_content:
            rec.final_text = final_text
            rec.transcript = transcript
        if self._sink_dir:
            self._write()
        return rec

    def _write(self) -> None:
        try:
            Path(self._sink_dir or ".").mkdir(parents=True, exist_ok=True)
            path = os.path.join(str(self._sink_dir), f"{self.record.trajectory_id}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.record.to_json(), fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001 — 기록이 턴을 깨지 않는다
            import logging

            logging.getLogger(__name__).warning("rsi: trajectory write failed", exc_info=True)


def load_record(path: os.PathLike[str] | str) -> TrajectoryRecord:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = [ReplayNode(**n) for n in raw.pop("nodes", [])]
    rec = TrajectoryRecord(**raw)
    rec.nodes = nodes
    return rec
