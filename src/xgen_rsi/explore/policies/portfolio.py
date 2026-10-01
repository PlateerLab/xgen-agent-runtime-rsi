"""Portfolio policy — a hand-written baseline that follows the Appendix B policy rules.

Prefix signals. Each round the policy rebuilds every opened branch's ordered
trajectory from ``question.observed()``: successful anchor (best success
score), anchor attempt, successes since the anchor, the trailing failure
episode (count and kind via ``signals.failure_kind``) and depth. A branch is
compared with the global anchor relative to the prefix score spread
(max − min of successful scores and the baseline); there are no absolute
score cutoffs. Success semantics are those of ``signals.is_success``.

Classification of an opened branch with a frontier:

- *closed (hard)*: the trailing failure episode is hard (environment) and
  repeated ``hard_limit`` times (+1 when the branch has an anchor);
- *closed (unpromising)*: at least ``min_evidence`` successes, ``patience``
  non-improving successes since the anchor, and the anchor below the global
  anchor by more than ``prune_gap`` × spread;
- *closed (repeated repair failures)*: more than ``max_repairs`` trailing
  repairable failures and no anchor near the global anchor;
- *recovery*: the latest probe failed (repairable, or a first hard failure);
  a later success cancels any failure-based closure because classification is
  recomputed from the current failure episode every round;
- *underexplored*: latest probe succeeded, fewer than ``min_evidence``
  probes, anchor not near the global anchor;
- *exploit*: latest probe succeeded and the branch is near the global anchor
  or still within its patience; a stagnant exploit branch is demoted to
  *reserve* after ``patience`` non-improving successes.

Batch rule (dynamic portfolio, at most W cells, deterministic queues):
exploration first gets ``ceil(explore_share × W)`` representatives
(underexplored branches, then new roots ranked by direction-tag novelty and
creation order, never by branch id alone), then at most one recovery when it
does not displace a successful refinement, then exploitation by priority,
then the remaining exploration, then reserve candidates when the schedule
says idle workers should be filled. Roots are opened only while the number
of active branches is below ``width_share`` of all roots.

β schedule. Every threshold comes from ``_schedule(beta)``: higher β widens
(more active roots, larger exploration share), waits longer (patience,
repairs, stall window) and prunes less; lower β opens fewer roots, stops
earlier on stagnation and prunes harder, yet never closes a branch on a
single repairable failure. β never changes inside ``solve``.

Default β: 0.6 — the moderately exploratory default used when live history
is insufficient (05 §4.4).

Stop rule (portfolio level): stop when no candidate remains, or when there has
been no progress for ``stall × W`` probes — progress being a new global anchor
or a contending branch (near the global anchor) improving on its parent — and
no high-priority candidate is left (a still-improving branch, or one justified
recovery of an anchored branch); under stagnation only those are probed.

Grid planning (``plan_grid``): with no history, an explicit bootstrap from
the context's fallback grid; otherwise width/depth follow the last live
manifests — hard-failure dominated cycles shrink both, early-improving roots
with no late gains widen, late gains on few directions deepen, a plateau
with the whole width used widens — always clamped to the hard caps and, in
replay, to the trace support.

Safeguards: no closure from one weak shallow score (``min_evidence``), no
closure from one repairable failure (``max_repairs``), recovery stays
eligible until cumulative evidence closes it, stagnation stops only after
the whole portfolio was considered, and batches are filled in parallel
instead of probing one cell at a time.
"""

from __future__ import annotations

import math

from xgen_rsi.explore.api import (
    GridPlan,
    LLMDesignedMethod,
    SimResult,
    _budget_done,
    _record_curve,
    bootstrap_plan,
    finalize_result,
)
from xgen_rsi.explore.signals import failure_kind, is_success

NAME = "OptimalPolicy"
DEFAULT_BETA = 0.6


def _schedule(beta):
    """All behavioural thresholds as one function of β ∈ [0, 1]."""
    b = min(1.0, max(0.0, float(beta)))
    return {
        "beta": b,
        "width_share": 0.25 + 0.75 * b,
        "explore_share": 0.15 + 0.35 * b,
        "patience": 1 + int(round(3 * b * b)),
        "min_evidence": 2 + int(round(2 * b)),
        "prune_gap": 0.15 + 0.6 * b,
        "max_repairs": 1 + int(round(2 * b)),
        "hard_limit": 1 + int(round(b)),
        "stall": 1.0 + 3.0 * b * b,
        "fill_idle": b >= 0.5,
        "recovery_gap": 0.5 + 0.5 * b,
    }


def _trajectories(prefix):
    """Opened branches → their revealed observations in attempt order."""
    by_branch = {}
    for obs in prefix.values():
        by_branch.setdefault(obs.branch, []).append(obs)
    for traj in by_branch.values():
        traj.sort(key=lambda o: o.attempt)
    return by_branch


def _summarize(traj):
    """Trajectory statistics of one branch (anchor, trend, failure episode, depth)."""
    anchor = None
    anchor_at = -1
    successes = 0
    since_anchor = 0
    for o in traj:
        if is_success(o) and o.score is not None:
            successes += 1
            if anchor is None or o.score > anchor:
                anchor = o.score
                anchor_at = o.attempt
                since_anchor = 0
            else:
                since_anchor += 1
    tail = 0
    tail_kind = None
    tail_hard = 0
    for o in reversed(traj):
        if is_success(o):
            break
        tail += 1
        kind = failure_kind(o)
        if tail_kind is None:
            tail_kind = kind
        if kind == "hard":
            tail_hard += 1
    last = traj[-1]
    return {
        "anchor": anchor,
        "anchor_at": anchor_at,
        "successes": successes,
        "since_anchor": since_anchor,
        "tail": tail,
        "tail_kind": tail_kind,
        "tail_hard": tail_hard,
        "depth": len(traj),
        "last_success": is_success(last),
        "last_gain": last.delta_vs_parent if is_success(last) else None,
    }


def _tags_key(tags):
    return tuple(sorted((str(k), str(v)) for k, v in tags.items()))


class PortfolioPolicy(LLMDesignedMethod):
    """Dynamic portfolio of exploitation, exploration and at most one recovery per batch."""

    DEFAULT_BETA = DEFAULT_BETA

    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", DEFAULT_BETA))
        self.sched = _schedule(self.beta)

    # ------------------------------------------------------------ episode --

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        state = {"best": None, "last_improve": 0}
        while not _budget_done(question, budget):
            batch = self._select(question, budget, state)
            if not batch:
                break
            revealed = question.probe_batch(
                batch, on_reveal=lambda _obs: _record_curve(res, question))
            self._note_progress(question, revealed, state)
        return finalize_result(question, res)

    def _note_progress(self, question, revealed, state):
        """Progress = a new global anchor, or a contending branch improving on its parent."""
        prefix = question.observed()
        scores = [o.score for o in prefix.values() if is_success(o) and o.score is not None]
        if question.baseline_score is not None:
            scores.append(question.baseline_score)
        if not scores:
            return
        top, spread = max(scores), max(scores) - min(scores)
        n = len(prefix)
        for obs in revealed:
            if not (is_success(obs) and obs.score is not None):
                continue
            new_best = state["best"] is None or obs.score > state["best"]
            contender = (obs.delta_vs_parent is not None and obs.delta_vs_parent > 0
                         and obs.score >= top - 0.5 * self.sched["prune_gap"] * spread)
            if new_best:
                state["best"] = obs.score
            if new_best or contender:
                state["last_improve"] = n

    def _select(self, question, budget, state):
        s = self.sched
        W = question.max_parallelism
        prefix = question.observed()
        slots = W if budget is None else min(W, budget - len(prefix))
        if slots <= 0:
            return []
        baseline = question.baseline_score
        roots = question.legal_roots()
        root_set = set(roots)
        frontier = {}
        for c in question.legal_actions():
            if c not in root_set:
                frontier[question.meta(c).branch] = c
        trajs = _trajectories(prefix)
        infos = {b: _summarize(t) for b, t in trajs.items()}

        scores = [o.score for o in prefix.values() if is_success(o) and o.score is not None]
        if baseline is not None:
            scores.append(baseline)
        g_anchor = max(scores) if scores else None
        spread = (max(scores) - min(scores)) if scores else 0.0

        def near(anchor, share):
            if anchor is None or g_anchor is None:
                return False
            return anchor >= g_anchor - share * spread

        explore, exploit, recovery, reserve = [], [], [], []
        active = 0
        for b in sorted(frontier):
            info = infos.get(b)
            if info is None:
                continue
            cell = frontier[b]
            anchor = info["anchor"]
            rel = 0.0 if anchor is None or spread <= 0 else (anchor - (g_anchor - spread)) / spread
            if not info["last_success"]:
                if info["tail_kind"] == "hard" and info["tail_hard"] >= s["hard_limit"] + (1 if anchor is not None else 0):
                    continue
                if info["tail"] > s["max_repairs"]:
                    if not near(anchor, s["recovery_gap"]):
                        continue
                    active += 1
                    reserve.append(((0, rel, -info["tail"], b), cell))
                    continue
                active += 1
                justified = near(anchor, s["recovery_gap"]) or anchor is None
                key = (1 if anchor is not None else 0, rel, -info["tail"], -info["depth"], -b)
                recovery.append((key, cell, justified))
                continue
            strong = near(anchor, s["prune_gap"])
            if (info["successes"] >= s["min_evidence"] and info["since_anchor"] >= s["patience"]
                    and not strong):
                continue
            active += 1
            if info["depth"] < s["min_evidence"] and not strong:
                gain = info["last_gain"] if info["last_gain"] is not None else 0.0
                explore.append(((1, gain > 0, rel, -info["depth"], -b), cell))
                continue
            if info["since_anchor"] >= 2 * s["patience"] + 1:
                continue
            if info["since_anchor"] >= s["patience"]:
                reserve.append(((1, rel, -info["since_anchor"], -b), cell))
                continue
            gain = info["last_gain"] if info["last_gain"] is not None else 0.0
            exploit.append(((rel, gain > 0, -info["since_anchor"], -b), cell))

        total_roots = len(trajs) + len(roots)
        cap = max(1, int(math.ceil(total_roots * s["width_share"])))
        opened_tags = set()
        for b in trajs:
            first = trajs[b][0]
            if first.cell_id:
                opened_tags.add(_tags_key(question.meta(first.cell_id).tags))
        ranked_roots = []
        for r in roots:
            m = question.meta(r)
            novel = _tags_key(m.tags) not in opened_tags
            ranked_roots.append(((0 if novel else 1, m.seq), r))
        ranked_roots.sort(key=lambda kv: kv[0])
        new_roots = [r for _k, r in ranked_roots][:max(0, cap - active)]

        explore.sort(key=lambda kv: kv[0], reverse=True)
        exploit.sort(key=lambda kv: kv[0], reverse=True)
        recovery.sort(key=lambda kv: kv[0], reverse=True)
        reserve.sort(key=lambda kv: kv[0], reverse=True)

        stalled = (len(prefix) - state["last_improve"]) >= int(math.ceil(s["stall"] * W))
        if stalled and prefix:
            # stagnation: keep only high-priority candidates (still-improving branches and
            # one justified recovery of an anchored branch); none left → portfolio stop
            keep = [c for k, c in exploit if k[1]] + [c for k, c in explore if k[1]]
            anchored = [c for k, c, j in recovery if j and k[0] == 1]
            if anchored and len(keep) < slots:
                keep.append(anchored[0])
            return keep[:slots]

        explore_q = [c for _k, c in explore] + new_roots
        exploit_q = [c for _k, c in exploit]
        recovery_q = [c for _k, c, j in recovery if j]
        late_recovery_q = [c for _k, c, j in recovery if not j]
        reserve_q = [c for _k, c in reserve]

        batch = []
        target = min(len(explore_q), max(1, int(math.ceil(s["explore_share"] * W))))
        batch += explore_q[:target]
        explore_q = explore_q[target:]
        took_recovery = False
        if recovery_q and (slots - len(batch) - len(exploit_q) >= 1 or not exploit_q):
            batch.append(recovery_q[0])
            took_recovery = True
        for queue in (exploit_q, explore_q):
            for c in queue:
                if len(batch) >= slots:
                    break
                batch.append(c)
        if not took_recovery and len(batch) < slots:
            pool = recovery_q + late_recovery_q
            if pool:
                batch.append(pool[0])
        if s["fill_idle"] or not batch:
            for c in reserve_q:
                if len(batch) >= slots:
                    break
                batch.append(c)
        return batch[:slots]

    # ------------------------------------------------------- grid planning --

    def plan_grid(self, context):
        history = list(context.history)
        if not history:
            return bootstrap_plan(context, "insufficient history: conservative bootstrap from fallback grid")
        rows = []
        for m in history:
            rows.append(m if isinstance(m, dict) else m.to_json())
        rows.sort(key=lambda r: r.get("iteration", 0))
        last = rows[-1]
        B0 = int(last.get("effective_branch_count") or last.get("planned_branch_count") or 0)
        R0 = int(last.get("effective_refine_count") or last.get("planned_refine_count") or 0)
        if B0 < 1:
            return bootstrap_plan(context, "insufficient history: last manifest has no grid")
        probes = max(1, int(last.get("probes") or 0))
        opened = int(last.get("opened_width") or 0)
        step = max(1, int(round(B0 * 0.25)))
        hard_share = int(last.get("hard_failures") or 0) / probes
        improving = int(last.get("improving_roots") or 0)
        late = int(last.get("late_gain_branches") or 0)
        plateau = False
        if len(rows) >= 2:
            prev, cur = rows[-2].get("best_score"), last.get("best_score")
            plateau = prev is not None and cur is not None and cur <= prev
        if hard_share >= 0.5:
            B, R = B0 - step, R0 - 1
            reason = f"hard failures dominated the last cycle ({hard_share:.2f} of probes): shrink width and depth"
        elif opened and improving * 2 >= opened and late == 0:
            B, R = B0 + step, R0
            reason = f"{improving}/{opened} roots improved early with no late gains: widen, hold depth"
        elif late >= 1 and improving * 2 < max(1, opened):
            B, R = B0, R0 + 1
            reason = f"late gains on {late} branch(es) from few directions: hold width, deepen"
        elif plateau and opened >= B0:
            B, R = B0 + step, R0
            reason = "live best plateaued with the whole width used: widen"
        else:
            B, R = B0, R0
            reason = "mixed evidence: hold the previous grid"
        B = max(1, min(B, int(context.hard_max_branch_count)))
        R = max(0, min(R, int(context.hard_max_refine_count)))
        if context.trace_branch_count is not None:
            B = max(1, min(B, int(context.trace_branch_count)))
        if context.trace_refine_count is not None:
            R = max(0, min(R, int(context.trace_refine_count)))
        return GridPlan(branch_count=B, refine_count=R, reason=reason)


OptimalPolicy = PortfolioPolicy
