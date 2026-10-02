"""턴 정리 — 턴이 끝난 직후 그 에이전트의 하네스를 Dream-RSI × RRSI 로 고친다(설계 41).

    from xgen_rsi.consolidate import Consolidator, ConsolidationState, TurnWorld

    result = Consolidator(policy=..., roles={...}).consolidate(state, worlds, current_payload, latest="io-42")
    save(result.state)                    # 에이전트의 정리 상태(편집 이력·번호·캐시) — 다음 정리가 이어 쓴다
    save_signals(result.signals)          # 새로 읽은 암묵 신호(세계 id → 신호)
    if result.adopted:
        save(result.payload)              # 다음 턴부터 rsi_agent_harness() 로 돌려준다

한 번의 정리:

1. 신호 — 방금 끝난 턴(``latest``)의 사용자 메시지로 같은 대화의 앞 턴 답을 판정한다(정정·불만·수용·중립).
2. 평가 세계 W_t — 신호가 있는 재생 가능 세계 중 새 신호(``fresh`` + 1 의 결과) → 고칠 것을 말하는 신호 → 지키라는 신호, 최근 순, 최대 N_eval.
3. 지금 하네스를 W_t 에서 잰다 — 지금 하네스로 기록된 턴은 그 자체가 시행이고, 모자란 시행은 재생한다(버전별 캐시).
4. 고칠 근거가 없으면(새 신호가 없거나 지금 하네스가 다 통과하고 가지치기 대상도 없으면) 여기서 끝난다.
5. RRSI 라운드 하나: 분석 → b_t·σ_t·𝒰_t·𝓑_t(에이전트의 이어진 이력) → 후보 m 개(제안 → 누설 검토·수리 → 태그 → 적재 확인)
   → 재생 평가(정확 경계 조기 종료) → 선택(바닥·비용 규칙·띠 안 규칙·가드) → 이력 기록 → 채택이면 페이로드.

판정 규칙은 :mod:`xgen_rsi.rsi_math` 그대로다 — 이 모듈은 세계·재생·상태를 잇기만 한다.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from xgen_rsi.consolidate.replay import ReplayResult, replay_world
from xgen_rsi.consolidate.signals import checks_for, implicit_signal, negative, score
from xgen_rsi.consolidate.world import TurnWorld, previous_in_conversation, text_of
from xgen_rsi.rsi_math import (
    K_STR,
    Candidate,
    EvalResult,
    RRSIParams,
    TaskResult,
    aggregate,
    calibrate,
    can_stop_exactly,
    early_stop_record_delta,
    edit_budget,
    exploration,
    outcome_of,
    rate_guard,
    reserved_variants,
    select_round,
    stall_flag,
    valid_measurement,
)
from xgen_rsi.rsi_math.modes import K_ENABLED_XGEN

CONSTITUTION_DIR = Path(__file__).resolve().parent / "constitution"
VARIANT_LABELS = "ABCDEFGH"
_RENDER_CLIP = 1500
_REQUEST_HISTORY = 6

BRIEFS: Dict[str, str] = {
    "analyst": """The agent is one XGEN agent in production. Each WORLD is one of its past turns with real
users: the earlier conversation, the request, the agent's steps (tool calls with the results recorded in that
session) and its final answer, followed by the JUDGE section: criteria made from the user's own signals (an
expected answer, a rating with an issue or comment, or the user's next message correcting or accepting the
answer) with PASS/FAIL per criterion. The trajectories shown are the CURRENT harness's trials. Find failure
modes that recur across worlds (or would recur for this agent's next requests) and the success habits the
passing worlds rely on.""",
    "proposer": """The worlds are past turns of THIS agent with its real users, replayed: your harness rebuilds
the turn, the frozen model answers again, and every tool call returns the result recorded in that session
(calls the session never made return "no recorded result"). Criteria come from the users' own signals,
including their next message. Fit these users' recurring needs; never encode the content of a conversation.
The kernel (model, provider, credentials, tool execution, permissions, limits), the agent's own instructions
and the judge are out of reach.""",
    "critic": """The harness belongs to ONE XGEN agent and is evolved from its users' conversations. Encoding a
recurring preference or procedure of these users is legitimate ("answer in the language of the request", "show
the query that produced a number", "lead with a table when comparing options"). Encoding the content of a
conversation is leakage and must be rejected: facts, numbers, answers, file names, quoted user text, people,
company or customer names, or a trigger that only fires for one recorded request. Injecting summaries of past
conversations or "lessons learned" lists into the harness is also a rejection (that is memory, i.e. user data).
Existing safety mechanisms (context compaction and the budget guard, completion review, repeat-stop, the turn
input budget, gate reachability) must not be disabled without a working replacement. Kernel limits, the model
and provider, credentials, permissions and user-facing notices are not harness content.""",
}

ANALYST_SYSTEM = """You analyse why an AI agent's answers failed its users' criteria, to guide a harness change.
{brief}

Reply with JSON only:
{{"failure_modes": [{{"mode": "<short name>", "description": "<what goes wrong and why, in general terms>",
   "worlds": ["<world ids>"], "lens": "instruction|wrong_method|capability_gap|format|context|control",
   "evidence": "<where in the trajectories>"}}],
 "success_habits": [{{"habit": "<short name>", "description": "...", "worlds": ["..."]}}],
 "notes": "<anything the proposer must know, e.g. failures the harness cannot fix>"}}
Rank failure modes by how many worlds they explain. Name modes generally (never by conversation content).
Reuse a prior name when it is the same mode."""


@dataclass(frozen=True)
class ConsolidationParams:
    """정리의 손잡이(설계 41 §5). RRSI 기호는 :class:`~xgen_rsi.rsi_math.RRSIParams` 로 넘긴다."""

    m: int = 2
    k: int = 2
    n_eval: int = 8
    T: int = 20
    b_min: int = 1
    b_max: int = 3
    w: int = 3
    m_draft: int = 1
    n_prune: int = 4
    beta0: float = 0.10
    beta1: float = 35.4
    w_s: float = 100.0
    w_c: float = 15.0
    w_n: float = 0.5
    delta_z: float = 2.0
    invalid_missing_frac: float = 0.15
    repair_rounds: int = 1
    parallel: int = 8
    early_stop: bool = True
    max_valid_rate_drop: float = 0.15
    max_nosub_rise: float = 0.15
    history_rounds: int = 200
    cache_versions: int = 3
    verdict_cache: int = 4000
    signal_pairs: int = 4
    """한 번의 정리가 읽는 다음 메시지 신호 수(방금 끝난 턴 + 놓친 최근 쌍)."""

    def rrsi(self) -> RRSIParams:
        return RRSIParams(T=self.T, k=self.k, m=self.m, b_min=self.b_min, b_max=self.b_max, w=self.w,
                          m_draft=self.m_draft, delta_z=self.delta_z, beta0=self.beta0, beta1=self.beta1,
                          w_s=self.w_s, w_c=self.w_c, w_n=self.w_n, n_prune=self.n_prune,
                          invalid_missing_frac=self.invalid_missing_frac)


@dataclass
class ConsolidationState:
    """에이전트 하나의 정리 상태 — 호스트가 저장하고 다음 정리에 그대로 돌려준다(JSON)."""

    t: int = 0
    """다음 라운드 번호(에이전트마다 이어진다)."""
    records: List[Dict[str, Any]] = field(default_factory=list)
    """𝓛 — 측정된 편집의 기록(R-Eq10)."""
    progress: List[float] = field(default_factory=lambda: [0.0])
    """라운드마다 채택된 ΔS 의 누적(σ_t 의 궤적 — 세계 풀이 바뀌어도 비교 가능)."""
    analysis: Dict[str, Any] = field(default_factory=dict)
    """앞 분석의 실패 양상·성공 습관 이름(이름을 안정시킨다)."""
    scoreboard: List[Dict[str, Any]] = field(default_factory=list)
    cache: Dict[str, Dict[str, List[Dict[str, Any]]]] = field(default_factory=dict)
    """하네스 버전 → 세계 id → 재생 결과들(답·토큰 — 기준이 바뀌면 다시 채점한다)."""
    verdicts: Dict[str, List[Any]] = field(default_factory=dict)
    """판정 캐시 (기준, 참조, 요청, 답) 해시 → [통과, 이유]."""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Optional[Mapping[str, Any]]) -> "ConsolidationState":
        raw = dict(raw or {})
        return cls(t=int(raw.get("t") or 0), records=list(raw.get("records") or []),
                   progress=[float(x) for x in raw.get("progress") or [0.0]] or [0.0],
                   analysis=dict(raw.get("analysis") or {}), scoreboard=list(raw.get("scoreboard") or []),
                   cache={str(v): {str(w): list(rs) for w, rs in (ws or {}).items()} for v, ws in (raw.get("cache") or {}).items()},
                   verdicts=dict(raw.get("verdicts") or {}))


@dataclass
class ConsolidationResult:
    status: str
    """``recorded``(라운드 없음) · ``adopted`` · ``kept`` · ``failed``."""
    reason: str
    state: ConsolidationState
    start_version: str = ""
    version: str = ""
    adopted: bool = False
    payload: Optional[Dict[str, Any]] = None
    round: Optional[Dict[str, Any]] = None
    signals: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """이번 정리가 새로 읽은 신호(세계 id → 그 세계의 신호 전체)."""
    seconds: float = 0.0
    usage: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        """호스트 기록용(상태·페이로드 제외)."""
        return {"status": self.status, "reason": self.reason, "start_version": self.start_version, "version": self.version,
                "adopted": self.adopted, "round": self.round, "seconds": self.seconds, "usage": self.usage}


@dataclass
class _Trial:
    result: ReplayResult
    source: str  # "recorded" | "replay"
    reward: float = 0.0
    weight: float = 0.0
    verdicts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class _Draft:
    variant: str
    edits: List[Dict[str, Any]] = field(default_factory=list)
    diff: str = ""
    commit: Optional[str] = None
    harness_version: str = ""
    hdir: Optional[Path] = None
    gate_failure: Optional[str] = None
    detail: str = ""
    mechanism: str = ""
    touched: List[str] = field(default_factory=list)
    ev: Optional[EvalResult] = None
    trials: Dict[str, List[_Trial]] = field(default_factory=dict)
    early: Optional[Dict[str, float]] = None


class _RoleSet:
    def __init__(self, proposer: Any, critic: Any, analyst: Any) -> None:
        self.proposer, self.critic, self.analyst = proposer, critic, analyst


class Consolidator:
    """에이전트 하나의 턴 정리. 정책 π 는 그 에이전트의 모델, 역할 모델은 XGEN 에 등록된 LLM(호출자가 자격증명을 넘긴다)."""

    def __init__(
        self,
        *,
        policy: Any,
        roles: Optional[Mapping[str, Any]] = None,
        params: ConsolidationParams = ConsolidationParams(),
        client_factory: Optional[Callable[[Any], Any]] = None,
        role_llms: Any = None,
        judge: Optional[Callable[..., Tuple[bool, str]]] = None,
        signal_llm: Any = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        """``role_llms``(proposer·critic·analyst 속성)·``judge``·``signal_llm`` 를 주면 ``roles`` 대신 쓴다(호스트 맞춤·테스트)."""
        from xgen_rsi.roles.llm import RoleLLM, RoleModel

        roles = dict(roles or {})

        def llm(name: str, fallback: Optional[str] = None) -> Any:
            model = roles.get(name) or (roles.get(fallback) if fallback else None)
            if model is None:
                raise ValueError(f"no model for role {name!r}")
            return RoleLLM(model if isinstance(model, RoleModel) else RoleModel(**dict(model)), role=name)

        self.policy = policy
        self.params = params
        self.client_factory = client_factory
        self._log_fn = log
        self.roles = role_llms if role_llms is not None else _RoleSet(llm("proposer"), llm("critic"), llm("analyst"))
        if judge is None:
            from xgen_rsi.evolve.judge import CriteriaJudge

            model = roles.get("judge")
            if model is None:
                raise ValueError("no model for role 'judge'")
            judge = CriteriaJudge(model if isinstance(model, RoleModel) else RoleModel(**dict(model)))
        self.judge = judge
        self.signal_llm = signal_llm if signal_llm is not None else getattr(judge, "llm", None)
        self._verdict_lock = threading.Lock()
        self._git_lock = threading.Lock()

    # ── 공용 ────────────────────────────────────────────────────────────
    def log(self, msg: str) -> None:
        if self._log_fn is not None:
            self._log_fn(f"[consolidate] {time.strftime('%H:%M:%S')} {msg}")

    def _judge_cached(self, state: ConsolidationState) -> Callable[..., Tuple[bool, str]]:
        def judged(criterion: str, *, reference: str = "", request: str = "", answer: str = "") -> Tuple[bool, str]:
            key = hashlib.sha256(json.dumps([criterion, reference, request, answer], ensure_ascii=False).encode()).hexdigest()[:32]
            with self._verdict_lock:
                hit = state.verdicts.get(key)
            if hit is not None:
                return bool(hit[0]), str(hit[1])
            ok, reason = self.judge(criterion, reference=reference, request=request, answer=answer)
            if not str(reason).startswith("judge error"):
                with self._verdict_lock:
                    state.verdicts[key] = [bool(ok), str(reason)[:300]]
            return bool(ok), str(reason)

        return judged

    # ── 진입점 ──────────────────────────────────────────────────────────
    def consolidate(
        self,
        state: Optional[ConsolidationState],
        worlds: Sequence[TurnWorld],
        current: Optional[Mapping[str, Any]],
        *,
        latest: Optional[str] = None,
        fresh: Sequence[str] = (),
        stop: Optional[Callable[[], bool]] = None,
    ) -> ConsolidationResult:
        started = time.monotonic()
        state = state or ConsolidationState()
        stop = stop or (lambda: False)
        by_id = {w.id: w for w in worlds}
        signals_out: Dict[str, Dict[str, Any]] = {}
        fresh_ids = {str(f) for f in fresh if str(f) in by_id}

        # 1) 신호 — 사용자 메시지가 같은 대화의 앞 턴 답에 대해 말하는 것. 방금 끝난 턴(``latest``)부터, 아직 읽지 않은 최근 쌍까지
        #    (정리가 건너뛰어지거나 다른 파드에서 턴이 끝나도 신호를 놓치지 않는다).
        if self.signal_llm is not None:
            for prev, cur in self._unread_pairs(worlds, latest):
                sig = implicit_signal(self.signal_llm, request=prev.request, answer=prev.answer, next_message=cur.request)
                prev.signals = dict(prev.signals, implicit=sig)
                signals_out[prev.id] = dict(prev.signals)
                if sig.get("kind") in ("correction", "complaint", "accept"):
                    fresh_ids.add(prev.id)
                self.log(f"signal on {prev.id}: {sig.get('kind')} {str(sig.get('criterion') or '')[:120]}")

        from xgen_rsi.harness.payload import materialize
        from xgen_rsi.harness.spec import load_manifest
        from xgen_rsi.kernel.executor import BUILTIN_H0

        with tempfile.TemporaryDirectory(prefix="rsi-consolidate-") as tmp:
            tmpdir = Path(tmp)
            inc_dir = materialize(current, tmpdir / "harness-cache") if current else BUILTIN_H0
            inc_manifest = load_manifest(inc_dir)
            inc_version = inc_manifest.version_id()

            def done(status: str, reason: str, **kw: Any) -> ConsolidationResult:
                res = ConsolidationResult(status=status, reason=reason, state=state, start_version=inc_version,
                                          version=kw.pop("version", inc_version), signals=signals_out, **kw)
                res.seconds = round(time.monotonic() - started, 3)
                self.log(f"{status}: {reason} ({res.seconds}s)")
                return res

            # 2) 평가 세계
            checks = {w.id: checks_for(w.signals, answer=w.answer) for w in worlds if w.replayable}
            scored = [w for w in worlds if w.replayable and checks.get(w.id)]
            if not scored:
                return done("recorded", "no turn has a signal to learn from yet")
            hist_path = tmpdir / "history.jsonl"
            from xgen_rsi.evolve.history import History

            history = History(hist_path)
            history.rewrite(state.records)
            t = int(state.t)
            prune = history.prune_set(t, self.params.n_prune)
            fresh_scored = [w for w in scored if w.id in fresh_ids]
            fresh_negative = [w for w in fresh_scored if negative(w.signals)]
            if not fresh_negative:
                # 라운드는 새 증거가 열고(RRSI 의 라운드 = 새 측정), 가지치기 대상 𝓑_t 는 그 라운드의 제안 입력이다.
                return done("recorded", "no new correction or problem to fix" if not fresh_scored else
                            "new signals only confirm the current answers")
            W = self._select_worlds(scored, fresh_ids)
            if stop():
                return done("recorded", "stopped")

            # 3) 지금 하네스를 W 에서 잰다
            judged = self._judge_cached(state)
            have = self._incumbent_have(state, inc_version, W)
            inc_ev, inc_trials = self._evaluate("incumbent", inc_dir, W, checks, have, judged, early=None, stop=stop)
            self._remember(state, inc_version, inc_trials)
            if not valid_measurement(inc_ev, self.params.invalid_missing_frac):
                return done("failed", f"the current harness could not be replayed ({inc_ev.missing}/{inc_ev.n_expected} trials failed)",
                            round={"t": t, "worlds": [w.id for w in W], "incumbent": _ev_brief(inc_ev)})
            failing = [w for w in W if inc_ev.per_task[w.id].mean < 1.0 - 1e-9]
            if not failing:
                return done("kept", "the current harness already meets every criterion on these turns",
                            round={"t": t, "worlds": [w.id for w in W], "incumbent": _ev_brief(inc_ev)})
            unit = 1.0 / max(1.0, sum(sum(tr.weights) for tr in inc_ev.per_task.values()))
            cal = calibrate([inc_ev], z=self.params.delta_z)
            delta = max(float(cal.delta), unit)
            S_star = float(inc_ev.S)

            # 4) 라운드 — 지시(b_t, σ_t, 𝒰_t, 𝓑_t)
            p = self.params
            budget = edit_budget(min(t, p.T), p.T, p.b_min, p.b_max)
            sigma = stall_flag(list(state.progress), t, p.w, delta)
            enabled = [k for k in K_ENABLED_XGEN if k in inc_manifest.enabled_kinds]
            tried_set = history.tried(before_t=t)
            expl = exploration(sigma, tried_set, p.m_draft, enabled=enabled)
            from xgen_rsi.evolve.round import exploration_directive

            explore = exploration_directive(expl)
            reserved = reserved_variants(p.m, p.m_draft, sigma, expl.untried)
            self.log(f"round t={t} worlds={len(W)} failing={len(failing)} S={inc_ev.S:.4f} delta={delta:.4f} "
                     f"b_t={budget} sigma={sigma} prune={[x['component'] for x in prune]}")

            views = {w.id: self._view(w, inc_trials.get(w.id) or [], worst=w in failing) for w in W}
            report = self._analyze(state, views, inc_ev)
            if stop():
                return done("recorded", "stopped")

            from xgen_rsi.evolve.gitops import HarnessRepo

            repo = HarnessRepo(tmpdir / "repo")
            repo.init(inc_dir, "evolve/agent")
            skill_md = (CONSTITUTION_DIR / "SKILL.md").read_text(encoding="utf-8")
            from xgen_rsi.evolve.domain import CONSTITUTION_DIR as EVOLVE_CONSTITUTION

            patterns_md = (EVOLVE_CONSTITUTION / "PATTERNS.md").read_text(encoding="utf-8")
            ctx = {
                "t": t, "budget": budget, "explore": explore, "prune": prune, "report": report, "views": views,
                "inc_ev": inc_ev, "inc_manifest": inc_manifest, "enabled": enabled, "hist_rows": history.render(),
                "skill_md": skill_md, "patterns_md": patterns_md, "patterns": self._critic_patterns(worlds),
                "selection": self._selection_text(S_star, delta, inc_ev, enabled), "scoreboard": state.scoreboard[-20:],
                "tmp": tmpdir,
            }
            drafts: List[_Draft] = []
            with ThreadPoolExecutor(max_workers=max(1, p.m)) as ex:
                futs = [ex.submit(self._draft, repo, VARIANT_LABELS[v], v in reserved, ctx) for v in range(p.m)]
                drafts = [f.result() for f in futs]
            if stop():
                return done("recorded", "stopped")

            # 5) 재생 평가 — 후보끼리도 동시에(각 후보는 자기 조기 종료를 따로 본다)
            live = [d for d in drafts if d.gate_failure is None and d.hdir is not None]

            def measure(d: _Draft) -> None:
                es = {"S_star": S_star, "delta": delta, "S_inc": inc_ev.S} if p.early_stop else None
                ev, trials = self._evaluate(f"r{t}{d.variant}", d.hdir, W, checks, {}, judged, early=es, stop=stop)  # type: ignore[arg-type]
                if not valid_measurement(ev, p.invalid_missing_frac) and not ev.extra.get("early_stopped"):
                    d.gate_failure, d.detail = "eval_invalid", f"{ev.missing}/{ev.n_expected} replays failed"
                    return
                touched_params = [a for a in d.touched if ".params." in a]
                ev = EvalResult(job=ev.job, k=ev.k, per_task=ev.per_task, S=ev.S, C=ev.C, n_expected=ev.n_expected,
                                missing=ev.missing, extra=dict(ev.extra, touched_params=touched_params))
                d.ev, d.trials = ev, trials
                if ev.extra.get("early_stopped"):
                    d.early = dict(ev.extra.get("early_stop") or {})

            if live:
                with ThreadPoolExecutor(max_workers=len(live)) as ex:
                    list(ex.map(measure, live))

            # 6) 선택
            counts = history.accepted_counts(before_t=t)
            cands = [Candidate(d.variant, d.edits, d.ev, d.gate_failure if d.ev is None else None, d.detail, d.commit) for d in drafts]
            from xgen_rsi.evolve.round import exercised_guard

            guard = rate_guard(p.max_valid_rate_drop, p.max_nosub_rise, valid_key="valid_rate", nosub_key="no_submission_rate")

            def guard_fn(inc: EvalResult, cand: EvalResult) -> List[str]:
                return list(guard(inc, cand)) + exercised_guard(cand)

            winner_c, decisions = select_round(cands, inc_ev, S_star, delta, p.rrsi(), counts, guard_fn, tie="xgen", enabled=enabled)
            winner = next((d for d in drafts if winner_c is not None and d.variant == winner_c.variant), None)

            # 7) 이력·상태
            cand_rows = []
            for d, c, dec in zip(drafts, cands, decisions):
                outcome = outcome_of(c, c if winner is d else None, dec)
                if d.ev is None:
                    history.append_candidate(t, d.variant, d.edits, outcome, None, None, False, None, None,
                                             d.diff[:4000] or None, d.detail)
                else:
                    dS = dec.delta_S
                    if d.early is not None:
                        dS = float(d.early.get("delta_S", dS or 0.0))
                    history.append_candidate(t, d.variant, d.edits, outcome, dS, dec.delta_C, outcome == "ACCEPTED",
                                             dec.S, dec.C, d.diff[:4000] or None, dec.reason,
                                             early_stopped=d.early is not None, delta_C_partial=d.early is not None)
                    self._attribute(state, t, d, inc_ev)
                cand_rows.append({"variant": d.variant, "outcome": outcome, "reason": dec.reason, "reason_code": dec.reason_code,
                                  "S": dec.S, "delta_S": dec.delta_S, "delta_C": dec.delta_C, "mechanism": d.mechanism[:300],
                                  "components": [e.get("component") for e in d.edits], "early_stopped": d.early is not None,
                                  "detail": (d.detail or "")[:300]})
            gain = float(next((dec.delta_S or 0.0) for dec in decisions if winner is not None and dec.variant == winner.variant)) if winner else 0.0
            state.records = _trim_rounds(history.records(), t, p.history_rounds)
            state.progress = list(state.progress[: t + 1]) + [0.0] * max(0, t + 1 - len(state.progress))
            state.progress.append(state.progress[t] + max(0.0, gain))
            state.t = t + 1
            round_info = {
                "t": t, "b_t": budget, "sigma": sigma, "delta": delta, "S_star": S_star, "worlds": [w.id for w in W],
                "failing": [w.id for w in failing], "fresh": sorted(fresh_ids & {w.id for w in W}),
                "prune": [x.get("component") for x in prune], "incumbent": _ev_brief(inc_ev),
                "candidates": cand_rows, "winner": winner.variant if winner else None,
                "failure_modes": [m.get("mode") for m in (report.get("failure_modes") or [])[:5]],
            }
            if winner is None or winner.hdir is None:
                return done("kept", "no candidate beat the current harness under the RRSI rules", round=round_info)
            from xgen_rsi.harness.payload import to_payload

            payload = to_payload(winner.hdir)
            version = str(payload["version"])
            self._remember(state, version, winner.trials)  # 채택 하네스의 재생 = 다음 정리의 지금 하네스 시행
            self._prune_cache(state, keep=[version, inc_version])
            return done("adopted", f"variant {winner.variant} adopted ({gain:+.4f})", version=version, adopted=True,
                        payload=payload, round=round_info)

    def _unread_pairs(self, worlds: Sequence[TurnWorld], latest: Optional[str]) -> List[Tuple[TurnWorld, TurnWorld]]:
        """(앞 턴, 다음 턴) 중 앞 턴의 암묵 신호를 아직 읽지 않은 것 — ``latest`` 가 먼저, 그다음 최근 순, 최대 ``signal_pairs``."""
        pairs: List[Tuple[TurnWorld, TurnWorld]] = []
        for cur in worlds:
            prev = previous_in_conversation(worlds, cur)
            if prev is None or "implicit" in prev.signals or not prev.answer.strip() or not cur.request:
                continue
            pairs.append((prev, cur))
        pairs.sort(key=lambda pc: (pc[1].id != latest, -float((pc[1].data or {}).get("clock") or 0.0), -pc[1].seq))
        return pairs[: max(0, self.params.signal_pairs)]

    # ── 평가 세계 ───────────────────────────────────────────────────────
    def _select_worlds(self, scored: Sequence[TurnWorld], fresh: set) -> List[TurnWorld]:
        def recency(w: TurnWorld) -> Tuple[float, int]:
            return (float((w.data or {}).get("clock") or 0.0), w.seq)

        ordered: List[TurnWorld] = []
        for group in (
            [w for w in scored if w.id in fresh],
            [w for w in scored if w.id not in fresh and negative(w.signals)],
            [w for w in scored if w.id not in fresh and not negative(w.signals)],
        ):
            ordered.extend(sorted(group, key=recency, reverse=True))
        return ordered[: max(1, self.params.n_eval)]

    def _incumbent_have(self, state: ConsolidationState, version: str, W: Sequence[TurnWorld]) -> Dict[str, List[_Trial]]:
        """지금 하네스의 이미 있는 시행 — 그 하네스로 기록된 턴(on-policy) + 캐시된 재생."""
        out: Dict[str, List[_Trial]] = {}
        cached = state.cache.get(version) or {}
        for w in W:
            trials: List[_Trial] = []
            if w.harness_version == version and w.model == self.policy.model and w.answer.strip():
                o = w.outcome
                trials.append(_Trial(ReplayResult(world_id=w.id, answer=w.answer, status=str(o.get("status") or "completed"),
                                                  termination_reason=str(o.get("termination_reason") or ""),
                                                  policy_tokens=int(o.get("policy_tokens") or 0), steps=dict(o.get("steps") or {}),
                                                  transcript=list(o.get("transcript") or [])), "recorded"))
            for raw in cached.get(w.id) or []:
                trials.append(_Trial(ReplayResult.from_json(raw), "replay"))
            out[w.id] = trials[: self.params.k]
        return out

    def _remember(self, state: ConsolidationState, version: str, trials: Mapping[str, List[_Trial]]) -> None:
        bucket = state.cache.setdefault(version, {})
        for wid, ts in trials.items():
            reps = [t.result.to_json() for t in ts if t.source == "replay" and not t.result.error]
            if reps:
                bucket[wid] = reps[: self.params.k]
        self._prune_cache(state, keep=[version])

    def _prune_cache(self, state: ConsolidationState, keep: Sequence[str]) -> None:
        versions = list(state.cache.keys())
        limit = max(1, self.params.cache_versions)
        if len(versions) > limit:
            drop = [v for v in versions if v not in keep][: len(versions) - limit]
            for v in drop:
                state.cache.pop(v, None)
        if len(state.verdicts) > self.params.verdict_cache:
            for key in list(state.verdicts.keys())[: len(state.verdicts) - self.params.verdict_cache]:
                state.verdicts.pop(key, None)

    # ── 재생 평가 ───────────────────────────────────────────────────────
    def _request_text(self, w: TurnWorld) -> str:
        lines = []
        for m in w.history[-_REQUEST_HISTORY:]:
            text = text_of(m.get("content")).strip()
            if text:
                who = "User" if m.get("role") == "user" else "Assistant"
                lines.append(f"{who}: {text[:1500]}")
        if not lines:
            return w.request
        return "Earlier in this conversation:\n" + "\n".join(lines) + f"\n\nCurrent request:\n{w.request}"

    def _score(self, w: TurnWorld, trial: _Trial, checks: Mapping[str, List[Dict[str, Any]]], judged: Callable[..., Tuple[bool, str]]) -> _Trial:
        cs = checks.get(w.id) or []
        if trial.result.missing:
            trial.reward, trial.weight, trial.verdicts = 0.0, float(len(cs)), []
            return trial
        trial.reward, trial.weight, trial.verdicts = score(cs, request=self._request_text(w), answer=trial.result.answer, judge=judged)
        return trial

    def _evaluate(self, job: str, hdir: Path, W: Sequence[TurnWorld], checks: Mapping[str, List[Dict[str, Any]]],
                  have: Mapping[str, List[_Trial]], judged: Callable[..., Tuple[bool, str]], *,
                  early: Optional[Mapping[str, float]], stop: Callable[[], bool]) -> Tuple[EvalResult, Dict[str, List[_Trial]]]:
        """Evaluate(H, W, k) by replay. ``have`` = 이미 있는 시행(지금 하네스). ``early`` 면 정확 경계 조기 종료(설계 33 §4)."""
        k = self.params.k
        trials: Dict[str, List[_Trial]] = {w.id: [] for w in W}
        weight = {w.id: float(len(checks.get(w.id) or [])) for w in W}
        for w in W:
            for tr in list(have.get(w.id) or [])[:k]:
                trials[w.id].append(self._score(w, tr, checks, judged))
        pending = [(w, j) for w in W for j in range(len(trials[w.id]), k)]
        cancel = threading.Event()
        early_info: Optional[Dict[str, float]] = None

        def run(w: TurnWorld) -> _Trial:
            res = replay_world(w, hdir, self.policy, client_factory=self.client_factory,
                               cancelled=lambda: cancel.is_set() or stop())
            return self._score(w, _Trial(res, "replay"), checks, judged)

        if pending:
            with ThreadPoolExecutor(max_workers=max(1, self.params.parallel)) as ex:
                futs: Dict[Future, TurnWorld] = {ex.submit(run, w): w for w, _ in pending}
                remaining = set(futs)
                while remaining:
                    finished, remaining = wait(remaining, return_when=FIRST_COMPLETED)
                    for f in finished:
                        w = futs[f]
                        try:
                            trials[w.id].append(f.result())
                        except Exception as exc:  # noqa: BLE001 — 재생 실패는 누락 시행
                            trials[w.id].append(_Trial(ReplayResult(world_id=w.id, error=f"{type(exc).__name__}: {exc}"[:300]),
                                                       "replay", 0.0, weight[w.id]))
                    if early is not None and remaining:
                        A = sum(t.reward * t.weight for ts in trials.values() for t in ts)
                        B = sum(t.weight for ts in trials.values() for t in ts)
                        R = sum(weight[futs[f].id] for f in remaining)
                        if can_stop_exactly(A, B, R, float(early["S_star"]), float(early["delta"]), float(early["S_inc"])):
                            cancel.set()
                            for f in remaining:
                                f.cancel()
                            early_info = {"A": A, "B": B, "R": R, "delta_S": early_stop_record_delta(A, B, R, float(early["S_inc"]))}
                            break
                if early_info is not None:
                    wait([f for f in futs if not f.cancelled()])
        per_task: Dict[str, TaskResult] = {}
        n_trials = valid = nosub = off = 0
        read: Optional[set] = set()
        for w in W:
            ts = trials[w.id]
            rewards = [t.reward for t in ts]
            weights = [t.weight or weight[w.id] for t in ts]
            tokens = [t.result.policy_tokens or None for t in ts]
            missing = sum(1 for t in ts if t.result.missing)
            if not ts:
                if early_info is not None:
                    continue  # 조기 종료 — 돌리지 않은 시행은 부분 평가에 넣지 않는다(ΔS 는 상한으로 기록)
                rewards, weights, tokens, missing = [0.0], [weight[w.id] or 1.0], [None], 1
            per_task[w.id] = TaskResult(rewards=rewards, weights=weights, tokens=tokens, missing=missing)
            for t in ts:
                n_trials += 1
                ok = not t.result.missing and t.result.answer.strip() and t.result.status in ("completed", "")
                valid += int(bool(ok))
                nosub += int(not t.result.answer.strip())
                off += t.result.off_support
                if t.source == "replay" and read is not None:
                    read |= set(t.result.params_read or [])
        extra = {"valid_rate": valid / max(1, n_trials), "no_submission_rate": nosub / max(1, n_trials),
                 "off_support_calls": off, "trials_run": n_trials,
                 "params_read": sorted(read) if read is not None else None}
        if early_info is not None:
            extra.update(early_stopped=True, early_stop=early_info)
        ev = aggregate(per_task, k, job=job, extra=extra)
        return ev, trials

    # ── 증거 ────────────────────────────────────────────────────────────
    def _view(self, w: TurnWorld, trials: List[_Trial], *, worst: bool) -> Dict[str, Any]:
        pick = None
        if trials:
            pick = min(trials, key=lambda t: t.reward) if worst else max(trials, key=lambda t: t.reward)
        return {"world": w, "trial": pick, "mean": (sum(t.reward for t in trials) / len(trials)) if trials else 0.0}

    def render(self, view: Mapping[str, Any], detail: bool = False) -> str:
        w: TurnWorld = view["world"]
        tr: Optional[_Trial] = view.get("trial")
        clip = 6000 if detail else _RENDER_CLIP
        lines = [f"WORLD {w.id} — one past turn of this agent ({'trial: ' + tr.source if tr else 'no trial'})"]
        if tr is not None:
            r = tr.result
            lines.append(f"status={r.status or '-'} termination={r.termination_reason or '-'} policy_tokens={r.policy_tokens} "
                         f"steps={r.steps} off_support_calls={r.off_support} score={tr.reward:.2f}")
        hist = w.history[-_REQUEST_HISTORY:]
        if hist:
            lines.append("EARLIER CONVERSATION (last messages):")
            for m in hist:
                text = text_of(m.get("content")).strip()
                if text:
                    lines.append(f"  {m.get('role')}: {text[:600]}")
        lines.append(f"REQUEST:\n{w.request[:clip]}")
        if tr is not None:
            step = 0
            steps = tr.result.transcript
            if not steps:
                steps = list(w.outcome.get("transcript") or [])
                if steps:
                    lines.append(f"(steps below are the recorded live turn under harness {w.harness_version[:19] or '?'}; "
                                 f"the final answer and verdicts are this trial's)")
            for m in steps or []:
                content = m.get("content")
                blocks = content if isinstance(content, list) else [{"type": "text", "text": text_of(content)}]
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    btype = b.get("type")
                    if m.get("role") == "assistant" and btype == "text" and str(b.get("text") or "").strip():
                        step += 1
                        lines.append(f"[step {step}] AGENT: {str(b.get('text'))[:clip]}")
                    elif btype == "tool_use":
                        lines.append(f"  TOOL_CALL {b.get('name')}({json.dumps(b.get('input'), ensure_ascii=False)[:400]})")
                    elif btype == "tool_result":
                        body = text_of(b.get("content")) if not isinstance(b.get("content"), str) else str(b.get("content"))
                        tag = "TOOL_ERROR" if b.get("is_error") else "TOOL_RESULT"
                        lines.append(f"  {tag}: {body[: (clip // 2)]}")
            lines.append(f"FINAL ANSWER:\n{tr.result.answer[:clip]}")
            lines.append("JUDGE:")
            for v in tr.verdicts:
                lines.append(f"  [{'PASS' if v.get('pass') else 'FAIL'}] ({v.get('name')}) {v.get('criterion')} — {v.get('reason')}")
        return "\n".join(lines)

    def task_row(self, task_id: str, view: Optional[Mapping[str, Any]], tr: Optional[TaskResult]) -> str:
        w = view["world"] if view else None
        mean = f"{tr.mean:.2f}" if tr is not None else "-"
        failed = []
        if view and view.get("trial") is not None:
            failed = [str(v.get("name")) for v in view["trial"].verdicts if not v.get("pass")]
        return f"{task_id} | score {mean} | failed {failed or '-'} | {w.preview(100) if w else ''}"

    def _analyze(self, state: ConsolidationState, views: Mapping[str, Mapping[str, Any]], inc_ev: EvalResult) -> Dict[str, Any]:
        ordered = sorted(views, key=lambda i: inc_ev.per_task[i].mean)
        failing = [i for i in ordered if inc_ev.per_task[i].mean < 1.0 - 1e-9][:6]
        passing = [i for i in reversed(ordered) if inc_ev.per_task[i].mean >= 1.0 - 1e-9][:3]
        prior = state.analysis or {}
        prompt = "\n\n".join([
            "=== WORLDS THE CURRENT HARNESS FAILS (worst trial each) ===",
            *[self.render(views[i], detail=True) for i in failing],
            "=== WORLDS IT PASSES (best trial each) ===",
            *([self.render(views[i]) for i in passing] or ["(none)"]),
            "=== PRIOR NAMES ===",
            json.dumps({"failure_modes": prior.get("failure_modes") or [], "success_habits": prior.get("success_habits") or []},
                       ensure_ascii=False),
        ])
        try:
            raw = self.roles.analyst.generate(prompt, system=ANALYST_SYSTEM.format(brief=BRIEFS["analyst"]), json_only=True)
            report = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(report, dict):
                raise ValueError("not an object")
        except Exception as exc:  # noqa: BLE001 — 분석 실패는 빈 보고(제안자는 기록을 직접 읽는다)
            self.log(f"analyst failed: {exc!r}")
            report = {"failure_modes": [], "success_habits": [], "notes": f"analysis unavailable: {type(exc).__name__}"}
        state.analysis = {
            "failure_modes": [{"mode": m.get("mode"), "description": m.get("description")} for m in report.get("failure_modes") or [] if isinstance(m, dict)][:12],
            "success_habits": [{"habit": h.get("habit"), "description": h.get("description")} for h in report.get("success_habits") or [] if isinstance(h, dict)][:12],
        }
        return report

    # ── 후보 ────────────────────────────────────────────────────────────
    def _draft(self, repo: Any, vid: str, reserved: bool, ctx: Mapping[str, Any]) -> _Draft:
        from xgen_rsi.evolve.critic import review
        from xgen_rsi.evolve.propose import propose
        from xgen_rsi.evolve.tagging import normalize_edits, touched
        from xgen_rsi.harness.runtime import instantiate
        from xgen_rsi.harness.spec import load_manifest

        p = self.params
        t = int(ctx["t"])
        branch = f"agent/r{t}{vid}"
        wt = Path(ctx["tmp"]) / "wt" / vid
        d = _Draft(vid)
        try:
            with self._git_lock:
                repo.worktree_new_branch(wt, branch, "evolve/agent")
            hdir = repo.harness_dir(wt)
            inc_manifest = ctx["inc_manifest"]
            explore = ctx["explore"]
            untried = list(explore.get("untried") or [])
            variant_brief = (f"You are variant {vid} of this consolidation round (t = {t}). {p.m} variants are drafted "
                             f"independently from the current harness and each is replayed on the same worlds; the best "
                             f"admissible one becomes the agent's harness from its next turn.")

            def run(repair: Optional[Dict[str, Any]]) -> Dict[str, Any]:
                return propose(self.roles.proposer, hdir, report=ctx["report"], history_rows=ctx["hist_rows"],
                               skill_md=ctx["skill_md"], patterns_md=ctx["patterns_md"], budget=int(ctx["budget"]),
                               explore=explore, reserved_slot=reserved, prune_set=ctx["prune"], enabled_kinds=ctx["enabled"],
                               render=self.render, task_row=self.task_row, traces=ctx["views"],
                               per_task=ctx["inc_ev"].per_task, findings=[], scoreboard=ctx["scoreboard"],
                               variant_brief=variant_brief, repair_brief=repair, brief=BRIEFS["proposer"],
                               incumbent=inc_manifest, selection=ctx["selection"], prior_changes=repair is not None)

            prop = run(None)
            if prop.get("status") != "done" or not prop.get("n_edits"):
                d.gate_failure, d.detail = "no_proposal", str(prop.get("reason") or prop.get("status"))
                return d
            verdict: Dict[str, Any] = {}
            for attempt in range(1 + p.repair_rounds):
                with self._git_lock:
                    diff = repo.diff_with_new_files(wt)
                verdict = review(self.roles.critic, diff, str(prop.get("mechanism") or ""), str(prop.get("targets_mode") or ""),
                                 edits=prop.get("edits"), patterns=ctx["patterns"], brief=BRIEFS["critic"])
                if verdict.get("verdict") == "accept":
                    try:
                        ts = touched(inc_manifest, load_manifest(hdir))
                        tagged = normalize_edits(prop.get("edits") or [], ts, ())
                    except Exception as exc:  # noqa: BLE001
                        verdict = {"verdict": "reject", "reasons": [f"manifest error: {exc}"]}
                        tagged = []
                    if verdict.get("verdict") == "accept" and reserved and untried and not any(e["component"] in untried for e in tagged):
                        verdict = {"verdict": "reject", "reasons": [
                            f"this variant holds a RESERVED EXPLORATION SLOT: at least one edit must be on a never-exercised "
                            f"component kind from {untried}, judged by the touched addresses, and none is"]}
                if verdict.get("verdict") == "accept" or attempt >= p.repair_rounds:
                    break
                prop = run({"reasons": verdict.get("reasons"), "risk_notes": verdict.get("risk_notes"),
                            "your_declared_edits": prop.get("edits")})
                if prop.get("status") != "done":
                    break
            d.edits = list(prop.get("edits") or [])
            d.mechanism = str(prop.get("mechanism") or "")
            with self._git_lock:
                d.diff = repo.diff_with_new_files(wt)
            if verdict.get("verdict") != "accept":
                d.gate_failure, d.detail = "critic_reject", str(verdict.get("reasons"))[:600]
                return d
            ts = touched(inc_manifest, load_manifest(hdir))
            d.edits = normalize_edits(d.edits, ts, ())
            d.touched = list(ts.addresses)
            with self._git_lock:
                d.commit = repo.commit(wt, f"r{t}{vid}: {d.mechanism[:120]}")
            try:
                instantiate(load_manifest(hdir))
            except Exception as exc:  # noqa: BLE001 — 적재 확인(선택 규칙이 아니라 살아 있는가)
                d.gate_failure, d.detail = "smoke_fail", f"{type(exc).__name__}: {exc}"[:600]
                return d
            d.harness_version = load_manifest(hdir).version_id()
            d.hdir = hdir
            self.log(f"{vid}: {len(d.edits)} edit(s) on {[e.get('component') for e in d.edits]}")
            return d
        except Exception as exc:  # noqa: BLE001 — 후보 하나의 실패가 정리를 깨지 않는다
            d.gate_failure, d.detail = d.gate_failure or "no_proposal", f"{type(exc).__name__}: {exc}"[:600]
            self.log(f"{vid}: draft failed {d.detail}")
            return d

    def _critic_patterns(self, worlds: Sequence[TurnWorld]) -> List[Tuple[str, str]]:
        """결정적 누설 목록 — 세계 id 와 기대 답의 긴 줄(대화의 답을 그대로 옮기면 걸린다)."""
        pats: List[Tuple[str, str]] = []
        for w in worlds:
            pats.append((_bounded(w.id), "recorded world id"))
            expected = str((w.signals or {}).get("expected") or "")
            for line in expected.splitlines():
                line = line.strip()
                if len(line) >= 24:
                    pats.append((re.escape(line), "expected answer text"))
        return list(dict.fromkeys(pats))

    def _selection_text(self, S_star: float, delta: float, inc_ev: EvalResult, enabled: Sequence[str]) -> str:
        p = self.params
        structural = [k for k in K_STR if k in enabled]
        return (f"- Current harness S = {inc_ev.S:.4f} on these worlds (= S*), noise band delta = {delta:.4f}.\n"
                f"- Noise-adjusted floor: S' must be >= S* - delta.\n"
                f"- Gain larger than delta: relative growth of mean policy tokens per trial must be <= {p.beta0} + {p.beta1} x gain.\n"
                f"- Gain within delta: kept only if {p.w_s} x gain - {p.w_c} x relative_cost_change + {p.w_n} x novelty > 0, "
                f"novelty = structural kinds {structural} never accepted before.\n"
                f"- Every edited param must be read during the replays; the valid-answer rate must not drop by more than "
                f"{p.max_valid_rate_drop}.\n"
                f"- Among admissible candidates the highest S wins (ties: lower cost, fewer edits); otherwise the current harness stays.")

    def _attribute(self, state: ConsolidationState, t: int, d: _Draft, inc_ev: EvalResult) -> None:
        from xgen_rsi.rsi_math import attribute

        if d.ev is None:
            return
        for e in d.edits:
            try:
                row = attribute(e, inc_ev, d.ev, self.params.k, t=t, variant=d.variant, threshold=1.0 / max(1, self.params.k)).to_json()
            except Exception:  # noqa: BLE001
                continue
            if d.early is not None:
                row["partial"] = True
            state.scoreboard.append(row)
        state.scoreboard = state.scoreboard[-40:]


def _bounded(literal: str) -> str:
    return rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])"


def _ev_brief(ev: EvalResult) -> Dict[str, Any]:
    return {"S": ev.S, "C": ev.C, "missing": ev.missing, "n_expected": ev.n_expected,
            "valid_rate": ev.extra.get("valid_rate"), "off_support_calls": ev.extra.get("off_support_calls")}


def _trim_rounds(rows: List[Dict[str, Any]], t: int, keep: int) -> List[Dict[str, Any]]:
    floor = t - max(1, keep) + 1
    return [r for r in rows if "t" not in r or int(r.get("t") or 0) >= floor]


__all__ = ["BRIEFS", "ConsolidationParams", "ConsolidationResult", "ConsolidationState", "Consolidator"]
