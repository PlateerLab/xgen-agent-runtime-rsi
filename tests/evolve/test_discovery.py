"""라이브 탐색 — 셀 하나가 실제 엔진의 턴 하나이고, 정제 셀은 부모 작업 공간과 피드백에서 이어간다."""

from __future__ import annotations

import asyncio
import json
import os

from tests.kernel.fakes import ScriptedClient, text_step, tool_step
from xgen_rsi.discovery import (
    TaskAttempts,
    baseline_score,
    episodes_eval,
    explore_suite,
    refine_prompt,
    rrsi_confirmation,
    worlds_from_episodes,
)
from xgen_rsi.dream.manifest import load_live_manifests
from xgen_rsi.dream.sandbox import load_policy
from xgen_rsi.evolve.runner import PolicySpec
from xgen_rsi.evolve.tasks import TaskSpec
from xgen_rsi.explore.api import CellMeta, GridPlan
from xgen_rsi.explore.policies import builtin_policy_source
from xgen_rsi.explore.signals import is_success
from xgen_rsi.kernel.executor import BUILTIN_H0
from xgen_rsi.rsi_math import EvalResult, TaskResult, aggregate

TASK = TaskSpec(
    id="write-csv",
    prompt="Create out.csv with header name,score and one row alice,3.",
    files={"README.txt": "workspace"},
    checks=(
        {"kind": "file_exists", "path": "out.csv", "required": True},
        {"kind": "file_csv", "path": "out.csv", "header": ["name", "score"], "rows": 1, "format": True},
        {"kind": "file_csv", "path": "out.csv", "header": ["name", "score"], "cells": [{"row": 0, "col": "score", "value": "3"}]},
    ),
)
POLICY = PolicySpec(provider="openai", model="fake-model")


def _fixer(plan):
    """처음엔 구분자를 틀리고, 이전 결과가 작업 공간에 있으면 고친다 — 정제가 이어받는지 본다."""
    ws = plan.run_tool_context.working_dir
    prior = os.path.exists(os.path.join(ws, "out.csv"))
    content = "name,score\nalice,3\n" if prior else "name;score\nalice;3\n"
    return ScriptedClient(
        [
            tool_step("", [("w1", "Write", {"file_path": os.path.join(ws, "out.csv"), "content": content})]),
            text_step("done"),
        ]
    )


def _meta(b, a):
    return CellMeta(branch=b, attempt=a, parent_id=f"b{b}a{a - 1}" if a else None, seq=-1)


def test_baseline_is_untouched_workspace_score():
    assert baseline_score(TASK) == 0.0


def test_root_then_refine_inherits_workspace_and_feedback(tmp_path):
    attempts = TaskAttempts(TASK, harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(tmp_path), client_factory=_fixer)
    root = asyncio.run(attempts(_meta(0, 0), None, {}))
    obs = root.observation
    # 부분 점수도 평가된 성공이다 — 평가 시행 world 와 같은 매핑
    assert is_success(obs) and obs.score == 1 / 3 and obs.n_valid == 1 and obs.n_total == 3
    assert root.policy_tokens > 0 and root.model_calls == 2
    child = asyncio.run(attempts(_meta(0, 1), obs, {})).observation
    assert child.score == 1.0 and abs(child.delta_vs_parent - 2 / 3) < 1e-12
    rec = json.load(open(attempts.outcomes["b0a1"].record))
    first_user = next(m for m in rec["transcript"] if m.get("role") == "user")
    text = first_user["content"] if isinstance(first_user["content"], str) else json.dumps(first_user["content"])
    assert "attempt 2" in text and "Checks that failed" in text
    # 재개: 같은 셀은 다시 돌지 않는다
    again = TaskAttempts(TASK, harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(tmp_path), client_factory=None)
    assert asyncio.run(again(_meta(0, 0), None, {})).observation.score == 1 / 3


def test_refine_prompt_feedback_modes(tmp_path):
    attempts = TaskAttempts(TASK, harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(tmp_path), client_factory=_fixer)
    asyncio.run(attempts(_meta(0, 0), None, {"direction": "write the file first"}))
    parent = attempts.outcomes["b0a0"]
    assert "Checks that failed" in refine_prompt(TASK, parent, 1, feedback="checks")
    assert "Checks that failed" not in refine_prompt(TASK, parent, 1, feedback="score")
    assert "Previous attempt score" not in refine_prompt(TASK, parent, 1, feedback="none")


def test_explore_suite_builds_trees_worlds_and_manifests(tmp_path):
    loaded = load_policy(builtin_policy_source("parallel_refine"), class_name="ParallelRefinePolicy")
    tasks = [TASK, TaskSpec(id="write-csv-2", prompt=TASK.prompt, files=TASK.files, checks=TASK.checks)]
    pool = tmp_path / "trace_pool" / "iter0000"
    eps = explore_suite(tasks, lambda: loaded({"beta": 0.6}), plan=GridPlan(2, 1, "test"), W=2,
                        harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(pool), client_factory=_fixer)
    assert [e.task_id for e in eps] == ["write-csv", "write-csv-2"]
    for e in eps:
        assert e.best == 1.0 and e.probes == 4 and e.policy_tokens > 0  # 2 branches x (root + 1 refine)
    worlds = worlds_from_episodes(eps)
    assert len(worlds) == 2 and worlds[0].N_max == 4 and worlds[0].root_score == 0.0
    ms = load_live_manifests(tmp_path / "trace_pool")
    assert len(ms) == 1 and ms[0].probes == 8 and ms[0].best_score == 1.0
    ev = episodes_eval(eps)
    assert ev.S == 1.0 and ev.n_expected == 2 and ev.C and ev.C > 0
    # 재개: episode.json 이 있으면 다시 돌지 않는다
    again = explore_suite(tasks, lambda: loaded({"beta": 0.6}), plan=GridPlan(2, 1), W=2,
                          harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(pool), client_factory=None)
    assert [e.best for e in again] == [1.0, 1.0]


def _ev(S: float, C: float) -> EvalResult:
    return aggregate({"t": TaskResult(rewards=[S], weights=[1.0], tokens=[int(C)])}, 1)


def test_online_confirmation_is_the_rrsi_judgement():
    inc = _ev(0.70, 1000)
    up = rrsi_confirmation(_ev(0.80, 1500), inc, delta=0.02)  # +0.10 > δ, ΔC = 0.5 <= 0.1 + 40 * 0.1
    assert up["approved"] and up["details"]["code"] == "admissible"
    worse = rrsi_confirmation(_ev(0.60, 500), inc, delta=0.02)  # below the floor S_inc - δ
    assert not worse["approved"] and worse["details"]["code"] == "floor"
    pricey = rrsi_confirmation(_ev(0.71, 2000), inc, delta=0.02)  # within the band, cost doubles
    assert not pricey["approved"] and pricey["details"]["code"] == "cost_rule"
