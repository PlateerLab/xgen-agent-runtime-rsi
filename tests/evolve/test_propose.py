"""P_reg: the strict-JSON proposer protocol and its done() validation."""

from __future__ import annotations

import json
import shutil

import pytest

from tests.evolve.support import GOOD, block_edit, edit_decl, j
from xgen_rsi.evolve.propose import Workspace, component_catalog, propose, validate_candidate
from xgen_rsi.harness.spec import load_manifest
from xgen_rsi.kernel.executor import BUILTIN_H0
from xgen_rsi.roles.llm import ScriptedRoleLLM
from xgen_rsi.rsi_math import K_ENABLED_XGEN


@pytest.fixture()
def hdir(tmp_path):
    d = tmp_path / "harness"
    shutil.copytree(BUILTIN_H0, d)
    return d


def _run(hdir, script, *, budget=2, reserved=False, untried=(), **kw):
    llm = ScriptedRoleLLM(list(script), role="proposer")
    out = propose(llm, hdir, report={"failure_modes": []}, history_rows=[], skill_md="SKILL", patterns_md="PATTERNS",
                  budget=budget, explore={"text": "", "untried": list(untried), "sigma": int(reserved)},
                  reserved_slot=reserved, prune_set=[], enabled_kinds=K_ENABLED_XGEN,
                  render=lambda v, d: "", task_row=lambda t, v, tr: t,
                  incumbent=load_manifest(BUILTIN_H0), **kw)
    return out, llm


def _results(llm):
    """The [result] lines the proposer saw, in order (the last prompt holds the whole log)."""
    log = llm.calls[-1]["prompt"].split("=== INTERACTION LOG ===")[-1]
    return [ln for ln in log.split("\n") if ln.startswith("[result]")]


def test_budget_is_enforced_with_rsi_math_cardinality(hdir):
    two = [edit_decl("C1"), edit_decl("C2")]
    out, llm = _run(hdir, [j(block_edit(GOOD)),
                           j({"action": "done", "summary": "s", "edits": two}),
                           j({"action": "done", "summary": "s", "edits": two[:1]})], budget=1)
    assert out["status"] == "done" and len(out["edits"]) == 1 and out["n_edits"] == 1
    assert any("2 edits exceed the budget b_t = 1" in r for r in _results(llm))
    assert "AT MOST 1 independent edit" in llm.calls[0]["prompt"]
    assert llm.calls[0]["cache_prefix"].startswith("=== CONSTITUTION (SKILL.md) ===")


def test_required_fields_and_vocabulary(hdir):
    bad = {"id": "C1", "component": "subagent", "hypothesis": "h"}
    out, llm = _run(hdir, [j(block_edit(GOOD)),
                           j({"action": "done", "edits": [bad]}),
                           j({"action": "done", "edits": [edit_decl(component="Prompt")]})])
    res = _results(llm)
    assert any("missing ['targets_mode', 'predicted_affected', 'retroactive_check']" in r for r in res)
    assert any("'subagent' not in the enabled vocabulary" in r for r in res)
    assert out["edits"][0]["component"] == "prompt"  # lower-cased


def test_zero_file_changes_and_abort_bounces(hdir):
    out, llm = _run(hdir, [j({"action": "done", "edits": [edit_decl()]})] + [j({"action": "abort"})] * 4)
    res = _results(llm)
    assert "made ZERO file changes" in res[0]
    assert sum("there is no abort action" in r for r in res) == 3
    assert out["status"] == "abort"
    out2, _ = _run(hdir, [j({"action": "done", "edits": []})])
    assert out2["status"] == "done" and out2["n_edits"] == 0  # -> no_proposal in the round


def test_reserved_slot_needs_an_untried_component(hdir):
    out, llm = _run(hdir, [j(block_edit(GOOD)),
                           j({"action": "done", "edits": [edit_decl(component="prompt")]}),
                           j({"action": "done", "edits": [edit_decl(component="skill")]})],
                    reserved=True, untried=["skill", "memory"])
    assert any("RESERVED EXPLORATION SLOT" in r for r in _results(llm))
    assert "RESERVED EXPLORATION SLOT: this variant holds one" in llm.calls[0]["prompt"]
    assert out["edits"][0]["component"] == "skill"


def test_manifest_errors_come_back_as_done_errors(hdir):
    broken = {"action": "edit_file", "path": "manifest.json", "old": '"kind": "control_flow"',
              "new": '"kind": "steering"'}
    fix = {"action": "edit_file", "path": "manifest.json", "old": '"kind": "steering"',
           "new": '"kind": "control_flow"'}
    out, llm = _run(hdir, [j(broken), j({"action": "done", "edits": [edit_decl()]}),
                           j(fix), j(block_edit(GOOD)), j({"action": "done", "edits": [edit_decl()]})])
    assert any("manifest error" in r for r in _results(llm))
    assert out["status"] == "done"


def test_locked_keys_platform_fields_and_orphans_are_refused(hdir):
    locked = {"action": "edit_file", "path": "manifest.json", "old": '"completion_review": true',
              "new": '"completion_review": true, "max_iterations": 99'}
    policy = {"action": "edit_file", "path": "manifest.json", "old": '"exploration_policy": null',
              "new": '"exploration_policy": {"id": "greedy"}'}
    orphan = {"action": "write_file", "path": "notes/idea.md", "content": "an unreferenced note"}
    out, llm = _run(hdir, [j(locked), j(policy), j(orphan), j({"action": "done", "edits": [edit_decl()]})] +
                    [j({"action": "abort"})] * 4)
    res = " ".join(_results(llm))
    assert "is locked by '*.params.max_iterations'" in res
    assert "'exploration_policy' is owned by the platform" in res
    assert "'notes/idea.md' is not listed under any component" in res
    assert out["status"] == "abort"


def test_workspace_rules(hdir):
    ws = Workspace(hdir)
    assert ws.write("x.py", "print(1)").startswith("ERROR: extension .py")
    assert ws.write("manifest.json", "{}").startswith("ERROR: manifest.json exists")
    assert ws.edit("manifest.json", '"schema"', '"schema"').startswith("OK")
    assert "occurs" in ws.edit("manifest.json", '"params"', "x")
    with pytest.raises(ValueError):
        ws.read("../outside.txt")
    assert ws.write("skills/a/SKILL.md", "---\nname: a\n---\nbody").startswith("OK")
    assert "skills/a/SKILL.md" in ws.list_files()


def test_catalog_lists_registered_impls_with_params(hdir):
    m = load_manifest(hdir)
    cat = component_catalog(m, K_ENABLED_XGEN)
    assert "xgen_rsi.components.skills:SkillLibraryComponent" in cat
    assert "completion_review=True" in cat and "used by: ['control.standard']" in cat
    assert "모든 구성요소의 바탕" not in cat  # never the inherited base-class docstring


def test_validate_candidate(hdir):
    assert validate_candidate(hdir, load_manifest(BUILTIN_H0)) == []
    data = json.loads((hdir / "manifest.json").read_text())
    data["components"].append({"id": "skills.library", "kind": "skill",
                               "impl": "xgen_rsi.components.skills:SkillLibraryComponent",
                               "files": ["skills/missing/SKILL.md"]})
    (hdir / "manifest.json").write_text(json.dumps(data))
    problems = validate_candidate(hdir, load_manifest(BUILTIN_H0))
    assert any("does not exist" in p for p in problems)
