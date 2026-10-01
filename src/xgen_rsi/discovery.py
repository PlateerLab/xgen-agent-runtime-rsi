"""라이브 탐색 — 검증기가 있는 과제를 branch × attempt 격자로 실제로 푼다(Dream-RSI 의 온라인 발견).

셀 하나 = RSI 엔진 위의 에이전트 턴 하나(:func:`xgen_rsi.evolve.runner.run_attempt`). 결정 인터페이스는 리플레이와
같은 :class:`~xgen_rsi.explore.grid.LiveQuestion` 이고 전이만 다르다 — 셀을 고르면 실제 시도가 돈다(04 §1).

* branch 를 여는 셀(attempt 0) — 과제의 시작 작업 공간에서 처음부터
* 정제 셀(attempt j>0) — 부모 셀의 작업 공간을 복사해 이어서, 부모의 점수와 실패한 검사를 피드백으로
* 루트 r 의 점수(baseline) = 시작 작업 공간 그대로 + 빈 답을 같은 검증기로 채점한 값(결정적, 시도 0 회)
* 관측 = :func:`xgen_rsi.dream.world.outcome_observation_fields` — 평가 시행으로 만든 world 와 같은 매핑이라
  같은 과제가 라이브와 리플레이에서 똑같이 보인다(부분 점수도 점수다)

시도마다 독립된 턴·원장이다. 트리 하나의 비용은 시도들의 정책 토큰 합이다. 탐색 트리(``tree.json``)는 그대로
Dream-RSI 의 world 가 되고(:meth:`World.from_replay_nodes`), 반복(iteration)마다 ``live_cycle_manifest.json`` 이
남아 다음 ``plan_grid`` 의 이력이 된다.

온라인 확인(:func:`rrsi_confirmation`) — 재생에서 이긴 탐색 정책 π_E 는 승격 전에 같은 과제를 실제로 탐색해
RRSI 의 판정(바닥 Eq.5 + 비용 규칙 Eq.7/17, ``rsi_math.judge``)을 한 번 더 통과해야 한다(설계 30 §P7, 35 R-9).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from xgen_rsi.dream.manifest import write_live_manifest
from xgen_rsi.dream.world import World, outcome_observation_fields
from xgen_rsi.evolve.runner import PolicySpec, TrialOutcome, run_attempt, seed_workspace
from xgen_rsi.evolve.tasks import TaskSpec
from xgen_rsi.evolve.verifiers import verify
from xgen_rsi.explore.api import CellMeta, GridPlan, LiveCycleManifest, Observation, cell_id
from xgen_rsi.explore.grid import AttemptResult, LiveGrid, LiveQuestion, solve_live, summarize_live
from xgen_rsi.rsi_math import (
    Candidate,
    EvalResult,
    RRSIParams,
    TaskResult,
    aggregate,
    judge,
    valid_measurement,
)
from xgen_rsi.rsi_math.modes import XGEN

FEEDBACK_MODES = ("checks", "score", "none")


def baseline_score(task: TaskSpec) -> float:
    """루트 r 의 점수 — 아무것도 하지 않은 상태(시작 파일 + 빈 답)의 검증기 보상."""
    with tempfile.TemporaryDirectory(prefix="rsi-root-") as tmp:
        ws = Path(tmp) / "workspace"
        seed_workspace(task, ws)
        return float(verify(task.checks, workspace=str(ws), answer="").reward)


def refine_prompt(task: TaskSpec, parent: TrialOutcome, attempt: int, *, feedback: str = "checks",
                  answer_chars: int = 2000) -> str:
    """정제 시도의 지시문 — 과제 + 이전 시도의 결과. 검증기 *규칙*은 주지 않고 실패한 검사의 이름·사유만 준다."""
    lines = [task.prompt, "", "---", f"This is attempt {attempt + 1} on this task. "
             "The workspace already contains the result of the previous attempt."]
    if feedback in ("checks", "score"):
        checks = list(parent.verifier.get("checks") or [])
        passed = sum(1 for c in checks if c.get("passed"))
        lines.append(f"Previous attempt score: {parent.reward:.2f} ({passed}/{len(checks)} checks passed).")
        if feedback == "checks":
            failed = [c for c in checks if not c.get("passed")]
            if failed:
                lines.append("Checks that failed:")
                for c in failed[:12]:
                    detail = str(c.get("detail") or "").strip()
                    lines.append(f"- {c.get('name')}" + (f": {detail[:200]}" if detail else ""))
    if parent.answer:
        lines += ["Previous final answer (truncated):", parent.answer[:answer_chars]]
    lines.append("Improve the result. Keep what already works.")
    return "\n".join(lines)


def outcome_to_observation(outcome: TrialOutcome, *, branch: int, attempt: int,
                           baseline: Optional[float], parent_score: Optional[float]) -> Observation:
    """시도 결과 → 정책이 보는 관측(델타는 질문이 다시 계산하지만 단독 사용을 위해 채운다)."""
    f = outcome_observation_fields(outcome)
    score = f["score"]
    return Observation(
        branch=branch,
        attempt=attempt,
        delta_vs_baseline=None if score is None or baseline is None else score - baseline,
        delta_vs_parent=None if score is None or parent_score is None else score - parent_score,
        cell_id=cell_id(branch, attempt),
        **f,
    )


@dataclass
class TaskAttempts:
    """``attempt_fn`` of :class:`LiveQuestion` — 셀마다 실제 시도 하나.

    시도는 동기 엔진 진입점(``GenyRSITurnExecutor().run``)이라 스레드에서 돌린다. 동시 시도 수는 질문의 W 가
    정한다. 시도 디렉터리 ``out_dir/<cell>/`` 는 재개 안전(이미 있으면 다시 돌리지 않음).
    """

    task: TaskSpec
    harness_dir: str
    policy: PolicySpec
    out_dir: str
    client_factory: Optional[Callable[[Any], Any]] = None
    feedback: str = "checks"
    keep_content: bool = True
    baseline: Optional[float] = None
    outcomes: Dict[str, TrialOutcome] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.feedback not in FEEDBACK_MODES:
            raise ValueError(f"feedback must be one of {FEEDBACK_MODES}")
        if self.baseline is None:
            self.baseline = baseline_score(self.task)

    def cell_dir(self, cid: str) -> Path:
        return Path(self.out_dir) / cid

    def parent_outcome(self, pid: str) -> TrialOutcome:
        out = self.outcomes.get(pid)
        if out is None:
            path = self.cell_dir(pid) / "outcome.json"
            if not path.exists():
                raise RuntimeError(f"parent attempt {pid} has no recorded outcome")
            out = TrialOutcome(**json.loads(path.read_text(encoding="utf-8")))
            self.outcomes[pid] = out
        return out

    async def __call__(self, meta: CellMeta, parent: Optional[Observation],
                       tags: Optional[Mapping[str, Any]] = None) -> AttemptResult:
        branch, attempt = int(meta.branch), int(meta.attempt)
        cid = cell_id(branch, attempt)
        prompt: Optional[str] = None
        start_from: Optional[Path] = None
        parent_score = self.baseline
        if attempt > 0:
            pid = meta.parent_id or cell_id(branch, attempt - 1)
            p_out = self.parent_outcome(pid)
            prompt = refine_prompt(self.task, p_out, attempt, feedback=self.feedback)
            start_from = self.cell_dir(pid) / "workspace"
            parent_score = parent.score if parent is not None else None
        direction = str((tags or {}).get("direction") or "").strip()
        if direction:
            prompt = (prompt if prompt is not None else self.task.prompt) + f"\n\nApproach for this attempt: {direction}"

        def _run() -> TrialOutcome:
            return run_attempt(
                self.task,
                attempt,
                self.cell_dir(cid),
                harness_dir=self.harness_dir,
                policy=self.policy,
                client_factory=self.client_factory,
                keep_content=self.keep_content,
                prompt=prompt,
                start_from=start_from,
                interaction_id=f"rsi-explore-{self.task.id}-{cid}",
            )

        outcome = await asyncio.to_thread(_run)
        self.outcomes[cid] = outcome
        obs = outcome_to_observation(outcome, branch=branch, attempt=attempt,
                                     baseline=self.baseline, parent_score=parent_score)
        calls = 0
        if outcome.record:
            try:
                calls = int(json.loads(Path(outcome.record).read_text(encoding="utf-8"))["steps"]["model_calls"])
            except (OSError, ValueError, KeyError, TypeError):
                calls = 0
        return AttemptResult(
            observation=obs,
            diagnostics={"status": outcome.status, "record": outcome.record, "reward": outcome.reward},
            policy_tokens=int(outcome.tokens or 0),
            model_calls=calls,
        )


# ── 과제 하나 / 스위트 탐색 ───────────────────────────────────────────────


@dataclass(frozen=True)
class Episode:
    """라이브 탐색 한 번(과제 하나)의 결과."""

    task_id: str
    best: Optional[float]
    baseline: Optional[float]
    probes: int
    policy_tokens: int
    weight: float
    manifest: LiveCycleManifest
    tree_path: str

    def to_json(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "best": self.best, "baseline": self.baseline, "probes": self.probes,
                "policy_tokens": self.policy_tokens, "weight": self.weight, "manifest": self.manifest.to_json(),
                "tree_path": self.tree_path}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "Episode":
        return cls(task_id=str(d["task_id"]), best=d.get("best"), baseline=d.get("baseline"),
                   probes=int(d["probes"]), policy_tokens=int(d["policy_tokens"]), weight=float(d["weight"]),
                   manifest=LiveCycleManifest.from_json(d["manifest"]), tree_path=str(d["tree_path"]))


async def explore_task(task: TaskSpec, explorer: Any, *, plan: GridPlan, W: int, harness_dir: str,
                       policy: PolicySpec, out_dir: str, iteration: int = 0, beta: float = 0.0,
                       policy_version: str = "", directions: Sequence[Mapping[str, Any]] = (),
                       client_factory: Optional[Callable[[Any], Any]] = None,
                       feedback: str = "checks") -> Episode:
    """과제 하나를 탐색 정책 ``explorer``(``solve`` 를 가진 π_E 인스턴스)로 끝까지 탐색한다.

    ``out_dir/episode.json`` 이 있으면 다시 돌리지 않는다. 시도 디렉터리는 ``out_dir/attempts/<cell>``.
    """
    d = Path(out_dir)
    done = d / "episode.json"
    if done.exists():
        return Episode.from_json(json.loads(done.read_text(encoding="utf-8")))
    d.mkdir(parents=True, exist_ok=True)
    attempts = TaskAttempts(task, harness_dir=harness_dir, policy=policy, out_dir=str(d / "attempts"),
                            client_factory=client_factory, feedback=feedback)
    grid = LiveGrid(plan, directions=directions, tree_id=f"explore-{task.id}-{iteration:04d}")
    q = LiveQuestion(grid, attempts, W=W, baseline_score=attempts.baseline)
    await solve_live(explorer, q)
    nodes = q.nodes()
    tree = d / "tree.json"
    tree.write_text(json.dumps({"task_id": task.id, "iteration": iteration, "plan": plan.__dict__,
                                "nodes": [n.__dict__ for n in nodes]}, ensure_ascii=True, indent=1, default=str),
                    encoding="utf-8")
    manifest = summarize_live(q, iteration=iteration, beta=beta, plan=plan, policy_version=policy_version)
    write_live_manifest(d, manifest)
    revealed = [o for o in q.observed().values() if o.score is not None]
    best = max((o.score for o in revealed), default=None)
    ep = Episode(task_id=task.id, best=best, baseline=attempts.baseline, probes=q.budget_spent,
                 policy_tokens=sum(int(n.policy_tokens or 0) for n in nodes), weight=task.weight,
                 manifest=manifest, tree_path=str(tree))
    tmp = done.with_name(done.name + ".tmp")
    tmp.write_text(json.dumps(ep.to_json(), ensure_ascii=True, indent=1), encoding="utf-8")
    os.replace(tmp, done)
    return ep


def explore_suite(tasks: Sequence[TaskSpec], make_explorer: Callable[[], Any], *, plan: GridPlan, W: int,
                  harness_dir: str, policy: PolicySpec, out_dir: str, iteration: int = 0, beta: float = 0.0,
                  policy_version: str = "", client_factory: Optional[Callable[[Any], Any]] = None,
                  feedback: str = "checks", parallel_tasks: int = 1) -> List[Episode]:
    """과제마다 새 탐색 정책 인스턴스로 탐색하고, 반복 단위 ``live_cycle_manifest.json`` 을 쓴다.

    결과: ``out_dir/<task_id>/{episode.json, tree.json, live_cycle_manifest.json, attempts/}`` 와
    ``out_dir/live_cycle_manifest.json``(과제들을 합친 반복 요약 — 다음 ``plan_grid`` 의 이력).
    """

    async def _all() -> List[Episode]:
        sem = asyncio.Semaphore(max(1, parallel_tasks))

        async def one(task: TaskSpec) -> Episode:
            async with sem:
                return await explore_task(task, make_explorer(), plan=plan, W=W, harness_dir=harness_dir,
                                          policy=policy, out_dir=str(Path(out_dir) / task.id),
                                          iteration=iteration, beta=beta, policy_version=policy_version,
                                          client_factory=client_factory, feedback=feedback)

        return list(await asyncio.gather(*(one(t) for t in tasks)))

    episodes = asyncio.run(_all())
    write_live_manifest(out_dir, merge_manifests([e.manifest for e in episodes], iteration=iteration,
                                                 beta=beta, plan=plan, policy_version=policy_version))
    return episodes


def merge_manifests(ms: Sequence[LiveCycleManifest], *, iteration: int, beta: float, plan: GridPlan,
                    policy_version: str = "") -> LiveCycleManifest:
    """과제별 요약 → 반복 요약: 개수는 합, 점수는 평균, 격자는 계획 그대로, 깊이·폭은 최댓값."""
    if not ms:
        return LiveCycleManifest(iteration=iteration, beta=beta, best_score=None,
                                 planned_branch_count=plan.branch_count, planned_refine_count=plan.refine_count,
                                 policy_version=policy_version, plan_reason=plan.reason)

    def mean(xs: List[Optional[float]]) -> Optional[float]:
        vals = [x for x in xs if x is not None]
        return sum(vals) / len(vals) if vals else None

    attempts = [m.best_attempt for m in ms if m.best_attempt is not None]
    return LiveCycleManifest(
        iteration=iteration, beta=beta, best_score=mean([m.best_score for m in ms]),
        baseline_score=mean([m.baseline_score for m in ms]),
        planned_branch_count=plan.branch_count, planned_refine_count=plan.refine_count,
        effective_branch_count=max(m.effective_branch_count for m in ms),
        effective_refine_count=max(m.effective_refine_count for m in ms),
        opened_width=max(m.opened_width for m in ms), max_depth=max(m.max_depth for m in ms),
        probes=sum(m.probes for m in ms), rounds=sum(m.rounds for m in ms), W=max(m.W for m in ms),
        improving_roots=sum(m.improving_roots for m in ms),
        late_gain_branches=sum(m.late_gain_branches for m in ms),
        best_attempt=max(set(attempts), key=attempts.count) if attempts else None,
        hard_failures=sum(m.hard_failures for m in ms),
        repairable_failures=sum(m.repairable_failures for m in ms),
        policy_version=policy_version, plan_reason=plan.reason)


def episodes_eval(episodes: Sequence[Episode], *, job: str = "") -> EvalResult:
    """탐색 결과를 RRSI 측정으로 — 과제마다 시행 1 회, 보상 = 찾은 최고 점수, 비용 = 트리의 정책 토큰.

    인프라 실패로 아무 시도도 평가되지 못한 과제는 누락(r 0, 분모 포함 — RRSI Eq.3 의 규칙)이다.
    """
    per: Dict[str, TaskResult] = {}
    for e in episodes:
        missing = e.best is None
        per[e.task_id] = TaskResult(rewards=[0.0 if missing else float(e.best)], weights=[e.weight],
                                    tokens=[None if missing else (e.policy_tokens or None)], missing=int(missing))
    return aggregate(per, 1, job=job, extra={"probes": sum(e.probes for e in episodes)})


def worlds_from_episodes(episodes: Sequence[Episode]) -> List[World]:
    """탐색 트리 → 재생 world(과제마다 하나)."""
    out = []
    for e in episodes:
        raw = json.loads(Path(e.tree_path).read_text(encoding="utf-8"))
        out.append(World.from_replay_nodes(raw["nodes"], world_id=f"live:{raw['iteration']:04d}:{e.task_id}",
                                           baseline_score=e.baseline,
                                           meta={"task_id": e.task_id, "iteration": raw["iteration"],
                                                 "source": "live"}))
    return out


# ── 온라인 확인 = RRSI 판정 ───────────────────────────────────────────────


def rrsi_confirmation(cand: EvalResult, inc: EvalResult, *, delta: float,
                      params: Optional[RRSIParams] = None) -> Dict[str, Any]:
    """재생 승자 π_E^{m★} 의 라이브 측정 ``cand`` 를 현재 π_E 의 측정 ``inc`` 와 RRSI 판정으로 비교한다.

    ``rsi_math.judge`` 그대로 — 바닥(S★ = 현재 π_E 의 Ŝ, Eq.5), 비용 규칙(Eq.7, 띠 안이면 Eq.17). 탐색 정책
    교체는 하네스 구성요소 편집이 아니므로 ν = 0(구조 신규성 가산 없음)이다.
    """
    p = params or RRSIParams()
    for side, ev in (("candidate", cand), ("incumbent", inc)):
        if not valid_measurement(ev, p.invalid_missing_frac):  # 정본 §2.9: admissible ⟺ 측정 유효 ∧ …
            return {"approved": False, "reason": f"{side} live measurement invalid: {ev.missing}/{ev.n_expected} missing",
                    "details": {"code": "invalid", "side": side, "missing": ev.missing, "n_expected": ev.n_expected}}
    dec = judge(Candidate("pi_E", (), cand, None), inc, inc.S, delta, p, {}, eps=XGEN.float_eps)
    return {"approved": bool(dec.admissible), "reason": dec.reason,
            "details": {"code": dec.reason_code, "S_cand": cand.S, "S_inc": inc.S, "C_cand": cand.C,
                        "C_inc": inc.C, "delta_S": dec.delta_S, "delta_C": dec.delta_C, "delta": delta}}


__all__ = ("FEEDBACK_MODES", "Episode", "TaskAttempts", "baseline_score", "episodes_eval", "explore_suite",
           "explore_task", "merge_manifests", "outcome_to_observation", "refine_prompt", "rrsi_confirmation",
           "worlds_from_episodes")
