"""코드 검토(2026-10-01)에서 실측으로 확인된 결함의 회귀 테스트."""

from __future__ import annotations

import json

from tests.kernel.fakes import ScriptedClient, error_step
from xgen_rsi.evolve.config import EvolveConfig
from xgen_rsi.evolve.history import History, read_jsonl
from xgen_rsi.evolve.runner import PolicySpec, run_trial
from xgen_rsi.evolve.tasks import TaskSpec
from xgen_rsi.kernel.executor import BUILTIN_H0

TASK = TaskSpec(id="t", prompt="say hi", checks=(
    {"kind": "file_unchanged", "path": "README.txt", "sha256": "0" * 64},
    {"kind": "answer_not_contains", "text": "never"},
), files={"README.txt": "x"})
POLICY = PolicySpec(provider="openai", model="m")


def test_provider_failure_is_missing_not_partial_credit(tmp_path):
    """재시도를 다 쓴 공급자 오류로 끝난 턴("[ERROR] …")은 누락 — r 0, 토큰은 Ĉ 에서 빠진다(P1-1)."""
    out = run_trial(TASK, 0, harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(tmp_path),
                    client_factory=lambda plan: ScriptedClient([error_step("upstream 503")]))
    assert out.answer.startswith("[ERROR]")
    assert out.missing and out.reward == 0.0 and out.tokens is None


def test_corrupt_outcome_is_rerun_not_fatal(tmp_path):
    """반쯤 쓰인 outcome.json 은 없는 것으로 보고 다시 돈다(P1-4)."""
    tdir = tmp_path / "t__0"
    tdir.mkdir()
    (tdir / "outcome.json").write_text('{"task_id": "t", "tri', encoding="utf-8")
    out = run_trial(TASK, 0, harness_dir=str(BUILTIN_H0), policy=POLICY, out_dir=str(tmp_path),
                    client_factory=lambda plan: ScriptedClient([error_step("x")]))
    assert out.task_id == "t" and json.loads((tdir / "outcome.json").read_text())["task_id"] == "t"


def test_truncated_last_history_line_is_ignored(tmp_path):
    p = tmp_path / "history.jsonl"
    p.write_text('{"t": 0, "a": 1}\n{"t": 1, "b"', encoding="utf-8")
    assert read_jsonl(p) == [{"t": 0, "a": 1}]


def test_resumed_round_does_not_count_its_own_records(tmp_path):
    """라운드 t 의 기록이 이미 일부 쓰인 뒤 재개해도 N_t·𝒯_t 는 t 이전 기록만 센다(P1-2)."""
    h = History(tmp_path / "history.jsonl")
    edit = {"id": "e1", "component": "skill", "hypothesis": "h", "mechanism": "m", "targets_mode": "x"}
    h.append_candidate(0, "A", [edit], "ACCEPTED", 0.05, 0.0, True, 0.8, 100.0, None)
    h.append_candidate(1, "A", [dict(edit, id="e2", component="memory")], "ACCEPTED", 0.01, 0.0, True, 0.81, 100.0, None)
    counts = h.accepted_counts(before_t=1)
    assert counts["skill"] == 1 and counts.get("memory", 0) == 0 and h.accepted_counts()["memory"] == 1
    assert "memory" not in h.tried(before_t=1) and "memory" in h.tried()


def test_config_dump_never_writes_credentials():
    cfg = EvolveConfig.from_dict({"roles": {"analyser": {"api_key": "sk-leak"}}, "policy": {"api_key": "sk-leak2"},
                                  "proposer": {"provider": "openai", "model": "m", "api_key": "sk-leak3"}})
    assert "sk-leak" not in json.dumps(cfg.dump())
