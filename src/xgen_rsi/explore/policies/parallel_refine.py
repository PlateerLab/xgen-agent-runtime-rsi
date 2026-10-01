"""π_1 — parallel refine, the initial hand-written exploration policy of Dream-RSI.

Paper §4.1: launch several independent workspaces in parallel; every
workspace keeps its own local trajectory and repeatedly refines its current
candidate. As a policy over the branch × attempt grid:

- batch = the frontier of every opened branch (by branch id), up to W, then
  new roots to fill the remaining workers (so W workspaces stay busy);
- no pruning, no early stop: the episode ends when the probe budget (live) or
  the legal actions (replay: all revealed / K2) run out;
- β is read for interface compatibility but has no effect (π_1 is the fixed
  floor the evolved policies must beat; its β sweep is degenerate);
- ``plan_grid`` returns the context's fallback grid clamped to the hard caps
  and the trace support.

This module is also loadable as policy *source* in the replay sandbox (it
imports only ``xgen_rsi.explore.api``).
"""

from __future__ import annotations

from xgen_rsi.explore.api import (
    LLMDesignedMethod,
    SimResult,
    _budget_done,
    _record_curve,
    bootstrap_plan,
    finalize_result,
)

NAME = "OptimalPolicy"


class ParallelRefinePolicy(LLMDesignedMethod):
    """Open W workspaces, refine each independently, never prune or stop early."""

    DEFAULT_BETA = 0.6

    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", 0.6))

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        while not _budget_done(question, budget):
            batch = self._select(question, budget)
            if not batch:
                break
            question.probe_batch(batch, on_reveal=lambda _obs: _record_curve(res, question))
        return finalize_result(question, res)

    def _select(self, question, budget):
        slots = question.max_parallelism
        if budget is not None:
            slots = min(slots, budget - len(question.observed()))
        if slots <= 0:
            return []
        roots = question.legal_roots()
        root_set = set(roots)
        frontiers = [c for c in question.legal_actions() if c not in root_set]
        frontiers.sort(key=lambda c: question.meta(c).branch)
        batch = frontiers[:slots]
        for r in roots:
            if len(batch) >= slots:
                break
            batch.append(r)
        return batch

    def plan_grid(self, context):
        return bootstrap_plan(
            context, "parallel refine: fixed fallback grid (B workspaces x R refinements)")


OptimalPolicy = ParallelRefinePolicy
