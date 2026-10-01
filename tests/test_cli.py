"""``rsi`` 명령 — 하네스·스위트·evolve·dream 경로가 실제 모듈로 이어지는지(각본 정책)."""

from __future__ import annotations

import json
import os

from tests.kernel.fakes import ScriptedClient, text_step, tool_step
from xgen_rsi import cli
from xgen_rsi.dream import cli as dream_cli
from xgen_rsi.evolve.runner import PolicySpec
from xgen_rsi.evolve.tasks import TaskSpec, write_suite
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


def _fixer(plan):
    ws = plan.run_tool_context.working_dir
    prior = os.path.exists(os.path.join(ws, "out.csv"))
    content = "name,score\nalice,3\n" if prior else "name;score\nalice;3\n"
    return ScriptedClient([
        tool_step("", [("w1", "Write", {"file_path": os.path.join(ws, "out.csv"), "content": content})]),
        text_step("done"),
    ])


def _suite(tmp_path):
    tasks = [TASK, TaskSpec(id="write-csv-2", prompt=TASK.prompt, files=TASK.files, checks=TASK.checks)]
    root = tmp_path / "suite"
    write_suite("mini", tasks, {"evolve": [t.id for t in tasks], "smoke": [TASK.id]}, str(root))
    return root


def test_harness_and_suite_commands(tmp_path, capsys):
    assert cli.main(["harness", "validate", str(BUILTIN_H0)]) == 0
    assert capsys.readouterr().out.startswith("ok h0 sha256:")
    assert cli.main(["suite", "build", str(tmp_path / "xc")]) == 0
    assert (tmp_path / "xc" / "suite.json").exists()


def test_evolve_init_and_status(tmp_path, capsys):
    suite = _suite(tmp_path)
    (tmp_path / "policy.json").write_text(json.dumps({"provider": "openai", "model": "m", "api_key": "k"}))
    (tmp_path / "rrsi.json").write_text(json.dumps({"T": 3, "k": 1, "delta": 0.02}))
    run = tmp_path / "run"
    assert cli.main(["evolve", "init", str(run), "--suite", str(suite), "--policy", str(tmp_path / "policy.json"),
                     "--config", str(tmp_path / "rrsi.json")]) == 0
    spec = json.loads((run / "run.json").read_text())
    assert "api_key" not in json.dumps(spec) and spec["harness"] == str(BUILTIN_H0)
    capsys.readouterr()
    assert cli.main(["evolve", "status", str(run)]) == 0
    assert json.loads(capsys.readouterr().out)["settled_rounds"] == -1


def test_dream_explore_worlds_cycle_and_confirmation(tmp_path, capsys, monkeypatch):
    suite = _suite(tmp_path)
    pool = tmp_path / "pool"
    policy = PolicySpec(provider="openai", model="fake")
    tasks = dream_cli._tasks(str(suite), "evolve", 0)
    live = dream_cli.run_live(dream_cli.policy_source("builtin:parallel_refine"), tasks,
                              out_dir=str(pool / "iter0000"), policy=policy, harness=str(BUILTIN_H0),
                              iteration=0, plan_mode="fixed", B=2, R=1, W=2, beta=None, history=[],
                              client_factory=_fixer)
    assert live["summary"]["S"] == 1.0 and live["summary"]["plan"]["branch_count"] == 2

    worlds = tmp_path / "worlds.json"
    assert cli.main(["dream", "build-worlds", str(worlds), "--trace-pool", str(pool)]) == 0
    assert json.loads(capsys.readouterr().out)["worlds"] == 2

    # 온라인 확인 훅: 같은 과제를 두 정책으로 라이브 탐색해 RRSI 판정을 낸다
    real = dream_cli.run_live
    monkeypatch.setattr(dream_cli, "run_live", lambda *a, **k: real(*a, **{**k, "client_factory": _fixer}))
    (tmp_path / "policy.json").write_text(json.dumps({"provider": "openai", "model": "fake"}))
    args = dream_cli.build_parser().parse_args([
        "cycle", str(tmp_path / "c0"), "--worlds", str(worlds), "--incumbent", "builtin:parallel_refine",
        "--iteration", "1", "--confirm-suite", str(suite), "--policy", str(tmp_path / "policy.json"),
        "--delta", "0.02", "--plan", "fixed", "--branches", "1", "--refine", "1", "--W", "1"])
    confirm = dream_cli.make_confirmation(args, tmp_path / "c0")

    class Req:
        iteration = 1
        candidate_source = dream_cli.policy_source("builtin:portfolio")
        candidate_beta = None
        incumbent_beta = None

    verdict = confirm(Req())
    # 두 정책 모두 같은 각본에서 만점이고 비용이 같다 → ΔS = 0, ΔC = 0 → Eq.17 의 0 > 0 은 거짓(엄격) → 반려
    assert verdict.details["S_cand"] == verdict.details["S_inc"] == 1.0
    assert not verdict.approved and verdict.details["code"] == "cost_rule"

    # 사이클(개발 LLM 없이 M=1): π^0 만 평가 → 그대로 유지
    assert cli.main(["dream", "cycle", str(tmp_path / "c1"), "--worlds", str(worlds), "--incumbent",
                     "builtin:parallel_refine", "--iteration", "1", "--M", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["m_star"] == 0 and (tmp_path / "c1" / "promoted_policy.py").exists()
    assert cli.main(["dream", "status", str(tmp_path / "c1")]) == 0
    assert json.loads(capsys.readouterr().out)["m_star"] == 0
