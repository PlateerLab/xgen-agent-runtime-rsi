"""Policy-development agent: proposes the next exploration-policy version from replay feedback.

# The rules in the prompt below follow arXiv:2609.14858 (Dream-RSI) Appendix B
# (B.2, replay-based policy improvement) and are restated in this project's own
# words for our API, sandbox and file layout. No paper text is reproduced.

Given the history of policy versions of this cycle (source, ``beta_sweep.json``,
an excerpt of ``policy_execution_traces.jsonl``, Eq.1 values), the parallel-
refine floor, recent live-cycle manifests and the numeric cross-cycle β rule,
:class:`PolicyDeveloper` asks an LLM (duck-typed ``.generate(prompt, system=,
json_only=)``, e.g. ``roles.llm.RoleLLM`` / ``ScriptedRoleLLM``) for one
complete policy module, then validates it:

1. static sandbox checks plus the deliverable shape (``NAME``, ``class
   OptimalPolicy`` overriding ``solve`` and ``plan_grid``);
2. sandbox load (restricted imports/builtins);
3. ``plan_grid`` on a bootstrap context returns a valid ``GridPlan``;
4. smoke replays on up to two development worlds at the baked β and at the
   grid extremes finish without error or timeout.

A failed candidate is sent back with the validation report for a bounded
number of repair attempts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from xgen_rsi.explore.api import GridPlan, LiveCycleManifest
from xgen_rsi.rsi_math import validate_grid
from xgen_rsi.rsi_math.types import BetaDecision, GridPlanningContext

from .replay import run_episode
from .sandbox import (
    LoadedPolicy,
    PolicyRejected,
    PolicyTimeout,
    check_structure,
    load_policy,
)
from .world import World

POLICY_DEV_SYSTEM = (
    "You write deterministic, prefix-only Python exploration policies for a replay "
    "evaluator. Reply with exactly one fenced ```python code block holding the complete "
    "policy file and nothing else.")

# The rules follow arXiv:2609.14858 Appendix B and are restated for this project.
POLICY_DEV_PROMPT = """\
# Improve the exploration policy in `{method_file}`

You own one file, `{method_file}`. Your reply replaces it entirely. It must set
`NAME = "OptimalPolicy"` and define `class OptimalPolicy(LLMDesignedMethod)` with
`solve(self, question, budget=None)` and `plan_grid(self, context)`. You are not
solving the underlying task; you decide where exploration compute goes.

## The objective

Every evaluation world is a recorded, immutable grid of branches and attempts whose
rows have different lengths. In one decision round the policy submits a batch: it may
open branches that are still closed (their attempt 0) and/or continue opened branches
at their next attempt. Each revealed cell costs one probe. Only revealed cells are
visible; nothing is known about the rest.

The evaluator runs the policy at every value of a fixed beta grid and reports

    pareto.reward = pareto.auc - lambda * parallel_penalty

* `pareto.auc` grows when some beta value reaches high per-world attainment while
  spending a small share of the world's probes (area under the best-attainment-versus-
  work frontier, definition v1).
* `parallel_penalty` is the sweep mean of `effective_sequential_rounds / total_probes`.
  A batch of k cells with `W = question.max_parallelism` costs one decision round and
  `ceil(k / W)` sequential rounds, so probing one cell at a time scores about 1 and
  full useful batches approach 1/W.

Spend probes only where the prefix makes them look worthwhile, and put independent
worthwhile probes into the same batch. Versions are finally selected by the replay
value `V = best score - beta_cost * probes + beta_par * probes / rounds` measured at each
version's own default beta, and the current policy stays unless a version beats it.

An attempt that failed because of an implementation slip is weak evidence against its
parent direction. Weigh a retry from that parent against new branches and ordinary
refinements while keeping batches parallel.

## Interface (`from xgen_rsi.explore.api import ...`)

    question.reset()                      # only before the first probe
    question.observed() -> dict[str, Observation]   # revealed cells only
    question.legal_actions() -> list[str] # closed-branch roots + opened-branch frontiers
    question.legal_roots() -> list[str]   # roots of closed branches only
    question.opened_branches() -> list[int]
    question.meta(cell_id) -> CellMeta    # .branch .attempt .parent_id .seq .tags
    question.probe_batch(cells, on_reveal=None) -> list[Observation]
    question.baseline_score               # score of the starting point (may be None)
    question.max_parallelism              # W

`Observation` fields: `branch, attempt, score, evaluated, valid, fail_class, error,
delta_vs_baseline, delta_vs_parent, n_valid, n_total, cell_id`. `meta()` answers for
revealed and currently legal cells; `seq` is -1 for cells not revealed yet; `tags`
carries structure such as the direction assigned to a branch. Treat cell ids as opaque:
in paper-reproduction mode `legal_roots()` lists the symbolic root `"r"` once per branch
that may still be opened (it opens the earliest-created closed branch), so code that
takes entries from `legal_roots()` works in both modes.
Signals in `xgen_rsi.explore.signals`: `is_success`, `failure_kind` ("ok" | "hard" |
"repairable"), `branch_promising`, `branch_failed_hard`, `probe_improved_vs_parent`,
`probe_improved_vs_baseline`, and the sets `HARD_FAIL_CLASSES`, `REPAIRABLE_FAIL_CLASSES`.
Loop helpers in the api module: `SimResult`, `_budget_done`, `_record_curve`,
`finalize_result`, `bootstrap_plan`, `GridPlan`.

Success rule: an observation counts as a successful evaluation exactly when
`evaluated` is true, `error is None` and `fail_class == "ok"`, whatever `valid` says and
even if `n_valid`/`n_total` are missing. A false `valid` alone never turns it into a
repairable failure. A branch's *successful anchor* is its best score among successful
evaluations.

`question.best_so_far` and `question.budget_spent` exist for bookkeeping only; any use
in policy code is rejected. Derive every statistic from `question.observed()`.

## How to read each opened branch

Rebuild the branch's ordered trajectory from the prefix, not just its last cell or best
score: its best successful attempt, whether scores are rising or falling, any drops, the
order of failures and fixes, and its depth. Before closing or demoting a failed frontier, put it
in one class: hard and unrecoverable; repairable implementation failure (output or
correctness mismatch, shared-memory or resource limits, variable/code errors,
mask/layout/shape errors are normally repairable — one such error is not evidence that
the algorithm is wrong); weak but underexplored; or repeatedly unpromising after enough
valid evidence. `n_valid == 0` and `branch_failed_hard(obs)` are signals, not automatic
closure: use `fail_class` and `error` to separate a repairable zero-valid result from an
environment or dependency failure, and do not treat `compile_other` alone as permanent.
Judge only the failure streak that is still running: once a later attempt succeeds, the
branch is open again and any closure based on the older failures no longer applies.

## Each decision round

1. Read the prefix and rebuild every trajectory. Close a branch only when the evidence
   gathered over several attempts says it cannot recover or keeps disappointing.
2. Rank legal roots and frontiers with prefix-derived signals only: anchor,
   parent-to-child gain, the whole trajectory, real success versus failure evidence,
   recoverability, earlier repair outcomes, depth explored, comparison across branches.
3. Hold repairable failures and thinly explored frontiers in fixed-order queues, sorted by
   their trajectory, how recoverable they look, their depth, how often they failed, and
   beta. A repairable failure keeps its place until more evidence pushes it down.
4. Assemble one dynamic portfolio of independent cells, at most `max_parallelism`:
   exploitation (strong normal refinements), exploration (closed roots or underexplored
   branches) and no more than one recovery (a genuine repairable failure). When several
   roles qualify, seat exploration and a justified recovery before filling the remaining
   slots by priority, sized by the evidence rather than fixed quotas. A recovery must not
   push out a successful refinement or leave workers idle. Never sample at random, and do
   not shrink to a single cell just because one candidate is clearly best.
5. Stop only after weighing the whole revealed portfolio — active, underexplored,
   recoverable and unopened candidates and anything else still legal. Do not stop while a
   high-priority recovery or thinly explored candidate is still waiting; each action that
   is still legal must be kept, parked or closed for a reason the evidence supports.

Batch legality: distinct cells, each legal *before* the call; several roots and/or at
most one frontier per opened branch; never a parent together with its child; at most
`max_parallelism` cells. An illegal batch raises `ValueError`. Avoid fixed
widen-everything / deepen-everything wave schedules; re-plan the batch after every
revealed prefix.

Skeleton:

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        while not _budget_done(question, budget):
            batch = self._select(question)          # your prefix-only portfolio
            if not batch:
                break
            question.probe_batch(batch, on_reveal=lambda _o: _record_curve(res, question))
        return finalize_result(question, res)

## Hard rules

* Prefix-only: decisions may use revealed observations, `baseline_score`, the legal
  sets, structural `meta` and the signals. Never use unrevealed scores, a known optimum,
  hardcoded cell ids that won before, absolute score targets or trace internals.
* Each choice to close, open, refine, batch or end has to be justified by what has been
  revealed so far. Do not give up on a branch because its first one or two scores are
  weak; later refinements often recover. When a branch's newest attempt fails in a
  repairable way, keep its earlier successful attempt as the anchor, and do not let that
  single failure leave the branch idle indefinitely.
* Replay calls `solve(question, budget=None)`; there is no probe cap to rely on. The
  episode must end when no batch is chosen.

## The beta knob

`__init__` reads a single number and nothing else:
`self.beta = float(self.config.get("beta", <your default>))` (call `super().__init__(config)`
first). Beta plays three separate roles:

1. Inside one replay or live episode it is constant. Route every behavioural threshold
   through a single `_schedule(beta) -> dict`: higher beta means more width, more patience
   and weaker pruning; lower beta means fewer probes, earlier stops on stagnation and
   stronger pruning. Recovery eligibility, hold thresholds and waiting go through the same
   schedule (high beta waits longer; low beta stays selective but never closes a branch on
   one repairable failure). Never adapt beta from observations inside `solve()`.
2. During offline evaluation the evaluator sweeps a fixed grid of beta values to see
   whether turning beta actually trades attainment against work and parallelism. A sweep in
   which beta changes nothing is degenerate, and the sweep also shows whether you batch.
3. When you write a new version, bake one default beta, chosen from the live cycles and
   their sweeps; it stays fixed for the whole next live episode.

All thresholds are relative to the prefix; no absolute score cut-offs.

Default-beta rule — use the live manifests (final best score and the beta actually used
per iteration) together with the matching `beta_sweep.json` (pareto.reward, AUC, parallel
penalty, per-beta frontier); scores alone do not show that beta caused a change:
* live best still rising: leave the default where it was, unless the matching sweep gives
  clear evidence that a neighbouring beta does better;
* live best flat, and the sweep shows a higher beta reaching higher attainment at a
  reasonable work and parallelism cost: raise the default by roughly 0.1-0.2 (clamp to
  [0, 1]);
* a high default was already used through a plateau and the high-beta sweep points add
  work without attainment: lower it a little;
* too little history or conflicting evidence: use a moderately exploratory default near
  0.6 and do not mistake the replay ceiling for a reason to stop live exploration.
Never pick the default as the smallest beta that reaches a recorded trace's ceiling.
Numeric reference computed by our checker for this cycle: {beta_hint}

## plan_grid (required)

`plan_grid(self, context) -> GridPlan(branch_count=B, refine_count=R, reason=...)` runs
before a new live grid exists. It is not an in-episode decision and must never look at
the current episode. It must return a non-None `GridPlan` on every path; do not inherit
the base stub. Branches `0..B-1` and attempts `0..R` exist (R = refinements after each
root); `1 <= B <= context.hard_max_branch_count`, `0 <= R <= context.hard_max_refine_count`.
In replay, `context.trace_branch_count` / `context.trace_refine_count` describe the
recorded grid; a plan beyond them is out of support and earns no replay reward.
Available facts: `context.history` (completed live manifests, oldest first; each has
`iteration, beta, best_score, baseline_score, planned_/effective_branch_count,
planned_/effective_refine_count, opened_width, max_depth, probes, rounds, W,
improving_roots, late_gain_branches, best_attempt, hard_failures, repairable_failures`
and a `to_json()` method), fallback and hard caps, `worker_cap`, and the replay support
fields. Choose width versus depth from evidence:
* many distinct roots improve early while deep refinements stall: widen, hold or reduce
  depth;
* large gains arrive late on a few repeatable directions: hold or reduce width, deepen;
* every explored direction plateaus after enough depth while direction classes remain
  untried: widen;
* repeated unrecoverable failures or strongly redundant directions: shrink width and
  depth conservatively;
* thin or conflicting history: an explicit conservative bootstrap from the context
  (`bootstrap_plan(context, reason)`), saying that evidence is insufficient.
Give every plan a short factual `reason`. `plan_grid` decides how many directions to
offer; a separate direction provider assigns them, and `solve` still chooses which legal
roots and frontiers it expands, deepens, drops or ends on. Do not prefer roots for having a
small branch id. The grid is a hard bound: thresholds may use less, never more.

## Using history without leaking outcomes

Earlier versions of this cycle appear below as `{history_dir}/r####_*/` with their
policy code and `proposal_results/beta_sweep.json`. Build on a recent version that scored
well, carry over whatever lifted `pareto.reward`, and change something specific when the
scores stop moving. `{history_dir}/baseline/` is the parallel-refine floor to beat. Sweeps marked
with a different `pareto_auc_version` are not numerically comparable.
`proposal_results/policy_execution_traces.jsonl` excerpts (one replay per world and beta,
with the prefix state, chosen batch and revealed outcomes per round) are feedback between
versions only: use them to spot serial batches, early stops, over-pruning or wasted
probes; never read them inside `solve()` and never copy a world-specific branch, cell id,
score or target into the policy. Prefer live manifests over raw replay outcomes for the
live trend.

## Sandbox

Allowed imports: `math, statistics, collections, collections.abc, itertools, functools,
dataclasses, typing, heapq, bisect, xgen_rsi.explore.api, xgen_rsi.explore.signals`.
Not available: `open, eval, exec, compile, getattr, setattr, vars, dir, globals, type, id,
hash`, randomness, clocks, files or the network. Do not access dunder attributes or
`_private` attributes of objects other than `self`; no bare `except`, no `finally`, no
`async`; `str.format` only on constant strings without attribute fields (prefer
f-strings). Policies must be deterministic; sort before iterating over sets. Each episode
runs under a wall-clock limit.

## Deliverable

The complete `{method_file}` with a short module docstring covering: prefix signals,
batch rule, beta schedule, why the default beta was chosen, grid-planning rule, and the
safeguards against over-pruning, stopping too early, starving a branch after repairable
failures, and serial probing. Before answering, check: ranking uses whole trajectories,
the success rule above is applied, zero-valid results are not closed automatically,
recovery competes deterministically, and stopping is decided at the portfolio level.

{history}
"""

REPAIR_PROMPT = """\

## Your previous file was rejected

Validation report:
{report}

Previous file:
```python
{source}
```

Return the corrected complete `{method_file}` as one ```python block.
"""


@dataclass
class VersionRecord:
    """One policy version as the development agent sees it."""

    index: int
    label: str
    source: str
    origin: str = "developed"
    eligible: bool = True
    V: float | None = None
    baked_beta: float | None = None
    sweep: Mapping[str, Any] | None = None
    traces: Sequence[str] = ()
    note: str = ""


@dataclass
class ValidationReport:
    ok: bool
    problems: list[str] = field(default_factory=list)
    baked_beta: float | None = None
    plan: GridPlan | None = None

    def text(self) -> str:
        return "ok" if self.ok else "\n".join(f"- {p}" for p in self.problems)

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems), "baked_beta": self.baked_beta,
                "plan": None if self.plan is None else {
                    "branch_count": self.plan.branch_count, "refine_count": self.plan.refine_count,
                    "reason": self.plan.reason}}


@dataclass
class DevelopResult:
    source: str | None
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.source is not None


_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S)


def extract_policy_source(text: str) -> str:
    """The policy file in an LLM reply: the largest fenced code block, else the raw text."""
    blocks = _FENCE.findall(text or "")
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return (text or "").strip() + "\n"


def _sweep_digest(sweep: Mapping[str, Any] | None) -> str:
    if not sweep:
        return "(no sweep)"
    keep = {k: sweep.get(k) for k in ("pareto_auc_version", "pareto.reward", "pareto.auc",
                                       "parallel_penalty", "lambda", "baked_beta", "V",
                                       "anytime_auc", "frontier")}
    return json.dumps(keep, indent=1, default=str)


def _trace_digest(lines: Sequence[str], max_chars: int) -> str:
    out, used = [], 0
    for line in lines:
        if used + len(line) > max_chars:
            out.append(f"... ({len(lines) - len(out)} more episodes truncated)")
            break
        out.append(line)
        used += len(line)
    return "\n".join(out) if out else "(no traces)"


def build_history_block(versions: Sequence[VersionRecord], *, baseline: VersionRecord | None,
                        live_manifests: Sequence[LiveCycleManifest], history_dir: str,
                        method_file: str, max_trace_chars: int = 12000) -> str:
    """The inlined history: live manifests, the baseline floor and every earlier version."""
    parts = ["## History (inlined files)", ""]
    parts.append("### trace_pool/iter*/live_cycle_manifest.json (oldest first)")
    if live_manifests:
        for m in sorted(live_manifests, key=lambda m: m.iteration):
            parts.append(json.dumps(m.to_json(), default=str))
    else:
        parts.append("(no completed live cycles yet)")
    parts.append("")
    if baseline is not None:
        parts += [f"### {history_dir}/baseline/{method_file}", "```python", baseline.source.rstrip(),
                  "```", f"### {history_dir}/baseline/proposal_results/beta_sweep.json",
                  _sweep_digest(baseline.sweep), ""]
    budget = max_trace_chars
    for v in versions:
        status = "eligible" if v.eligible else f"rejected ({v.note})"
        parts += [f"### {history_dir}/{v.label}/{method_file}  [{v.origin}, {status}, "
                  f"V={v.V}, baked beta={v.baked_beta}]", "```python", v.source.rstrip(), "```",
                  f"### {history_dir}/{v.label}/proposal_results/beta_sweep.json",
                  _sweep_digest(v.sweep)]
        if v is versions[-1] and v.traces:
            parts += [f"### {history_dir}/{v.label}/proposal_results/policy_execution_traces.jsonl"
                      " (excerpt)", _trace_digest(v.traces, budget)]
        parts.append("")
    return "\n".join(parts)


def build_dev_prompt(versions: Sequence[VersionRecord], *, baseline: VersionRecord | None = None,
                     live_manifests: Sequence[LiveCycleManifest] = (),
                     beta_decision: BetaDecision | None = None, method_file: str = "policy.py",
                     history_dir: str = "history", max_trace_chars: int = 12000) -> str:
    """The development prompt (rules restated from arXiv:2609.14858 Appendix B)."""
    hint = ("none (no decision available)" if beta_decision is None else
            f"beta = {beta_decision.beta} ({beta_decision.branch}: {beta_decision.reason})")
    history = build_history_block(versions, baseline=baseline, live_manifests=live_manifests,
                                  history_dir=history_dir, method_file=method_file,
                                  max_trace_chars=max_trace_chars)
    return POLICY_DEV_PROMPT.format(method_file=method_file, history_dir=history_dir,
                                    beta_hint=hint, history=history)


def validate_policy_source(source: str, *, worlds: Sequence[World] = (), W: int = 4,
                           cell_ids: Sequence[str] = (), score_values: Sequence[float] = (),
                           timeout: float | None = 10.0, root_policy: str = "choose",
                           root_multi: bool = True) -> ValidationReport:
    """Full deliverable check (structure, sandbox, plan_grid, smoke replays)."""
    problems: list[str] = []
    shape = check_structure(source)
    if not shape.ok:
        problems += [v.message for v in shape.violations]
    try:
        loaded = load_policy(source, timeout=timeout, cell_ids=cell_ids,
                             score_values=score_values, require=("solve", "plan_grid"))
    except PolicyRejected as exc:
        problems += [f"[{v.rule}] line {v.line}: {v.message}" for v in exc.report.violations]
        return ValidationReport(False, problems)
    plan, baked = None, None
    ctx = GridPlanningContext(hard_max_branch_count=8, hard_max_refine_count=6, worker_cap=W,
                              fallback_branch_count=4, fallback_refine_count=3)
    try:
        def probe_plan(inst: Any) -> tuple[Any, Any]:
            return inst.plan_grid(ctx), getattr(inst, "beta", None)
        raw_plan, baked = loaded.run(probe_plan, {})
        if not isinstance(raw_plan, GridPlan):
            problems.append(f"plan_grid returned {type(raw_plan).__name__}, not GridPlan")
        else:
            plan = raw_plan
            validity = validate_grid(plan, ctx, replay=False)
            if not validity.valid:
                problems.append("plan_grid bootstrap plan invalid: " + "; ".join(validity.reasons))
            if not plan.reason:
                problems.append("plan_grid must give a reason")
    except (Exception, PolicyTimeout) as exc:
        problems.append(f"plan_grid failed: {type(exc).__name__}: {exc}")
    if not (isinstance(baked, (int, float)) and 0.0 <= float(baked) <= 1.0):
        problems.append(f"default beta must be a number in [0, 1], got {baked!r}")
    for w in list(worlds)[:2]:
        for beta in (None, 0.0, 1.0):
            run = run_episode(loaded, w, beta, W, root_policy=root_policy,  # type: ignore[arg-type]
                              root_multi=root_multi, timeout=timeout)
            if run.error:
                problems.append(f"replay on {w.world_id} at beta={beta}: {run.error}")
    return ValidationReport(not problems, problems, baked_beta=baked if not problems else None,
                            plan=plan)


class PolicyDeveloper:
    """LLM-driven policy development with validation and bounded repairs."""

    def __init__(self, llm: Any, *, max_repairs: int = 2, method_file: str = "policy.py",
                 history_dir: str = "history", timeout: float | None = 10.0,
                 max_trace_chars: int = 12000) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs must be >= 0")
        self.llm = llm
        self.max_repairs = max_repairs
        self.method_file = method_file
        self.history_dir = history_dir
        self.timeout = timeout
        self.max_trace_chars = max_trace_chars

    def develop(self, versions: Sequence[VersionRecord], *, worlds: Sequence[World] = (),
                W: int = 4, baseline: VersionRecord | None = None,
                live_manifests: Sequence[LiveCycleManifest] = (),
                beta_decision: BetaDecision | None = None, cell_ids: Sequence[str] = (),
                score_values: Sequence[float] = (), root_policy: str = "choose",
                root_multi: bool = True) -> DevelopResult:
        """Ask for a new version; validate; repair up to ``max_repairs`` times."""
        prompt = build_dev_prompt(versions, baseline=baseline, live_manifests=live_manifests,
                                  beta_decision=beta_decision, method_file=self.method_file,
                                  history_dir=self.history_dir,
                                  max_trace_chars=self.max_trace_chars)
        result = DevelopResult(source=None)
        current = prompt
        for attempt in range(self.max_repairs + 1):
            record: dict[str, Any] = {"attempt": attempt, "prompt": current}
            try:
                reply = self.llm.generate(current, system=POLICY_DEV_SYSTEM, json_only=False)
            except Exception as exc:  # noqa: BLE001 — an LLM failure is one failed attempt
                record.update(response=None, report={"ok": False, "problems": [
                    f"llm error: {type(exc).__name__}: {exc}"]})
                result.attempts.append(record)
                continue
            source = extract_policy_source(reply)
            report = validate_policy_source(source, worlds=worlds, W=W, cell_ids=cell_ids,
                                            score_values=score_values, timeout=self.timeout,
                                            root_policy=root_policy, root_multi=root_multi)
            record.update(response=reply, source=source, report=report.to_json())
            result.attempts.append(record)
            if report.ok:
                result.source = source
                return result
            current = prompt + REPAIR_PROMPT.format(report=report.text(), source=source.rstrip(),
                                                    method_file=self.method_file)
        return result


def run_plan_grid(loaded: LoadedPolicy, context: GridPlanningContext,
                  config: Mapping[str, Any] | None = None) -> GridPlan | None:
    """``plan_grid`` of a loaded policy under the sandbox limits (None on failure)."""
    try:
        plan = loaded.run(lambda inst: inst.plan_grid(context), dict(config or {}))
    except (Exception, PolicyTimeout):
        return None
    return plan if isinstance(plan, GridPlan) else None


__all__ = ["DevelopResult", "POLICY_DEV_PROMPT", "POLICY_DEV_SYSTEM", "PolicyDeveloper",
           "ValidationReport", "VersionRecord", "build_dev_prompt", "extract_policy_source",
           "run_plan_grid", "validate_policy_source"]
