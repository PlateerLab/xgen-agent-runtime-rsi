"""평가 러너 — 실제 엔진·실제 도구로 시행을 돌리고 검증기로 채점한다(각본 정책)."""

from __future__ import annotations

import json
import os

from tests.kernel.fakes import ScriptedClient, text_step, tool_step
from xgen_rsi.evolve.runner import EarlyStop, PolicySpec, evaluate, run_trial
from xgen_rsi.evolve.tasks import TaskSpec
from xgen_rsi.kernel.executor import BUILTIN_H0

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


def _writer(correct: bool):
    def factory(plan):
        ws = plan.run_tool_context.working_dir
        content = "name,score\nalice,3\n" if correct else "name;score\nalice;3\n"
        return ScriptedClient(
            [
                tool_step("", [("w1", "Write", {"file_path": os.path.join(ws, "out.csv"), "content": content})]),
                text_step("out.csv 를 만들었습니다."),
            ]
        )

    return factory


POLICY = PolicySpec(provider="openai", model="fake-model")


def test_trial_scores_by_verifier(tmp_path):
    good = run_trial(TASK, 0, harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(tmp_path / "g"), client_factory=_writer(True))
    bad = run_trial(TASK, 0, harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(tmp_path / "b"), client_factory=_writer(False))
    assert good.reward == 1.0 and good.weight == 3.0 and good.valid_output is True and not good.missing
    assert bad.reward == 1 / 3 and bad.valid_output is False
    assert good.tokens and good.tokens > 0
    rec = json.load(open(good.record))
    assert rec["harness_name"] == "h0" and rec["transcript"]


def test_evaluate_aggregates_and_resumes(tmp_path):
    ev = evaluate(str(BUILTIN_H0), [TASK], 2, policy=POLICY, out_dir=str(tmp_path / "e"), client_factory=_writer(True), parallel=2)
    assert ev.S == 1.0 and ev.n_expected == 2 and ev.missing == 0 and ev.C and ev.C > 0
    assert ev.extra["valid_rate"] == 1.0
    # 재개: 결과 파일이 있으면 다시 돌지 않는다(실패하는 팩토리로도 같은 결과)
    ev2 = evaluate(str(BUILTIN_H0), [TASK], 2, policy=POLICY, out_dir=str(tmp_path / "e"), client_factory=_writer(False))
    assert ev2.S == 1.0


def test_exact_early_stop_skips_hopeless_candidate(tmp_path):
    tasks = [TaskSpec(id=f"t{i}", prompt=TASK.prompt, checks=TASK.checks) for i in range(6)]
    ev = evaluate(
        str(BUILTIN_H0),
        tasks,
        2,
        policy=POLICY,
        out_dir=str(tmp_path / "s"),
        client_factory=_writer(False),
        parallel=1,
        early_stop=EarlyStop(S_star=0.9, delta=0.02, S_inc=0.9),
    )
    assert ev.extra["early_stopped"] is True
    assert ev.extra["trials_run"] < ev.extra["trials_planned"]
    assert ev.extra["S_upper_bound"] < 0.88
