"""측정되지 않은 편집을 채택하지 않는다 — 구성요소가 읽은 파라미터 주소를 기록하고, 후보가 고친 주소가
평가 중 한 번도 읽히지 않았으면 도메인 가드로 거른다.

계기(2026-10-01 gpt-6-sol 진화 r3A): 평가는 기억 기능을 끄고 도는데(memory_provider 없음), 제안자가
``memory.archive.params.archive`` 를 끄는 편집을 냈다. 그 값은 한 번도 읽히지 않아 ΔS·ΔC 는 잡음이었지만
새 구성요소 보너스(ν=1)로 채택됐다. 그 하네스를 기억이 켜진 운영에 쓰면 대화 보관이 꺼진다.
"""

from __future__ import annotations

from dataclasses import replace

from xgen_agent_runtime.host import runner as runner_mod

from tests.kernel.fakes import ScriptedClient, text_step
from xgen_rsi import GenyRSI
from xgen_rsi.evolve.round import _covers, _touched_of, exercised_guard
from xgen_rsi.evolve.runner import TrialOutcome, _params_read
from xgen_rsi.harness.runtime import Component, LoadedHarness
from xgen_rsi.harness.spec import ComponentSpec
from xgen_rsi.rsi_math import EvalResult


def _ev(extra):
    return EvalResult(job="j", k=1, per_task={}, S=1.0, C=1.0, n_expected=0, missing=0, extra=extra)


def test_component_records_the_keys_it_reads():
    spec = ComponentSpec(id="memory.archive", kind="memory", impl="x:y", params={"archive": True, "a": {"b": 1}})
    c = Component(spec)
    assert c.params_read() == set()
    c.param("a.b")
    c.param("missing", 3)
    assert c.params_read() == {"memory.archive.params.a.b", "memory.archive.params.missing"}
    h = LoadedHarness(manifest=None, version_id="v", components={"m": c})  # type: ignore[arg-type]
    assert h.params_read() == ["memory.archive.params.a.b", "memory.archive.params.missing"]


def test_real_turn_with_memory_off_never_reads_the_archive_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: ScriptedClient([text_step("ok")]))
    agent = GenyRSI.minimal(provider="openai", model="m", api_key="k", record_dir=str(tmp_path / "rec"))
    read = agent.run_sync("hi").record["params_read"]
    assert read, "the turn must record what the harness read"
    assert any(a.startswith("prompt.") for a in read)
    assert not any(a.startswith("memory.archive.params.archive") for a in read)
    # r3A 의 편집은 가드에 걸린다
    fails = exercised_guard(_ev({"params_read": read, "touched_params": ["memory.archive.params.archive"]}))
    assert fails and "memory.archive.params.archive" in fails[0]


def test_guard_passes_exercised_edits_and_skips_unknown():
    read = ["prompt.system.params.extra_blocks", "context.standard.params.prune_over_tokens"]
    assert exercised_guard(_ev({"params_read": read,
                                "touched_params": ["prompt.system.params.extra_blocks.0.text",
                                                   "context.standard.params.prune_over_tokens"]})) == []
    # 일부만 읽혀도 읽히지 않은 편집이 있으면 거른다(측정 안 된 변화를 싣지 않는다)
    fails = exercised_guard(_ev({"params_read": read, "touched_params": ["prompt.system.params.extra_blocks",
                                                                          "tools.x.params.limit"]}))
    assert fails and "tools.x.params.limit" in fails[0] and "extra_blocks" not in fails[0]
    # 읽은 기록이 없는 옛 평가·파라미터가 아닌 편집은 판정하지 않는다
    assert exercised_guard(_ev({"params_read": None, "touched_params": ["tools.x.params.limit"]})) == []
    assert exercised_guard(_ev({"params_read": [], "touched_params": []})) == []


def test_prefix_cover_on_dot_boundaries():
    assert _covers("p.params.a", "p.params.a.b") and _covers("p.params.a.b", "p.params.a")
    assert not _covers("p.params.a", "p.params.ab")


def test_params_read_union_and_unknown():
    def o(read):
        return TrialOutcome(task_id="t", trial=0, reward=1.0, weight=1.0, tokens=1, missing=False,
                            valid_output=True, no_submission=False, status="complete", answer="", record=None,
                            params_read=read)

    assert _params_read([o(["a"]), o(["b", "a"])]) == ["a", "b"]
    assert _params_read([o(["a"]), o(None)]) is None
    assert _params_read([]) == []


def test_touched_falls_back_to_edit_addresses_for_old_drafts():
    assert _touched_of({"touched": ["x.params.y", "x.files.f"]}) == ["x.params.y", "x.files.f"]
    assert _touched_of({"edits": [{"addresses": ["m.params.a"]}, {"addresses": ["m.params.a", "n.params.b"]}]}) \
        == ["m.params.a", "n.params.b"]


def test_guard_runs_through_select_round(tmp_path):
    from xgen_rsi.rsi_math import Candidate, select_round
    from xgen_rsi.rsi_math.rrsi import RRSIParams

    inc = _ev({"params_read": ["p.params.a"]})
    inc = replace(inc, S=0.99, C=100.0)
    cand_ev = replace(_ev({"params_read": ["p.params.a"], "touched_params": ["memory.archive.params.archive"]}),
                      S=0.99, C=101.0)
    cand = Candidate("A", ({"component": "memory"},), cand_ev)
    win, decisions = select_round([cand], inc, 0.99, 0.0076, RRSIParams(), {},
                                  lambda i, c: exercised_guard(c), tie="xgen", enabled=["memory", "prompt"])
    assert win is None and decisions[0].reason_code == "guard"
