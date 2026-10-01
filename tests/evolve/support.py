"""Shared fixtures for the RRSI loop tests: a tiny suite, a harness-dependent scripted policy and
scripted role LLM responses.

The scripted policy writes ``report.txt``; what it writes depends on the system prompt the
harness built, so a proposer edit to ``prompt.system.params.extra_blocks`` measurably changes Ŝ:

* GOOD marker present → ``STATUS: DONE`` (both checks pass, reward 1)
* BAD marker present  → writes nothing (reward 0, one model call)
* neither (H0)        → ``STATUS: PENDING`` (file exists, content wrong: reward 0.5)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests.kernel.fakes import ScriptedClient, text_step, tool_step
from xgen_rsi.evolve.config import EvolveConfig
from xgen_rsi.evolve.domain import EvolveDomain
from xgen_rsi.evolve.round import EvolveRun, Roles
from xgen_rsi.evolve.runner import PolicySpec
from xgen_rsi.evolve.tasks import TaskSpec, load_suite, write_suite
from xgen_rsi.roles.llm import ScriptedRoleLLM
from xgen_rsi.rsi_math import RRSIParams

GOOD = "Before finishing, open the deliverable you wrote and confirm its status line."
BAD = "Never write files; reply in chat only."
EVOLVE_IDS = ["quarterly-ledger-close", "vendor-intake-review", "policy-digest-update"]
HELDOUT_IDS = ["travel-claim-audit"]
CHECKS = (
    {"kind": "file_exists", "path": "report.txt", "required": True},
    {"kind": "file_contains", "path": "report.txt", "text": "STATUS: DONE"},
)


class MarkerClient(ScriptedClient):
    """Behaves according to the harness-built system prompt of its first request."""

    def __init__(self, workspace: str, **kw: Any) -> None:
        super().__init__([text_step("unused")], **kw)
        self.workspace = workspace

    def _next(self) -> Dict[str, Any]:
        system = str(self.requests[0].get("system")) if self.requests else ""
        mode = "good" if GOOD in system else ("bad" if BAD in system else "plain")
        content = {"good": "STATUS: DONE\n", "plain": "STATUS: PENDING\n"}.get(mode)
        if len(self.requests) == 1 and content is not None:
            path = os.path.join(self.workspace, "report.txt")
            return tool_step("", [("w1", "Write", {"file_path": path, "content": content})])
        return text_step("finished")


def policy_factory(plan: Any) -> MarkerClient:
    return MarkerClient(plan.run_tool_context.working_dir)


def make_suite(root: Path) -> Any:
    tasks = [TaskSpec(id=i, prompt="Write report.txt with a status line for this request.", checks=CHECKS,
                      files={"notes.md": "Source material for the request."})
             for i in EVOLVE_IDS + HELDOUT_IDS]
    write_suite("tiny", tasks, {"evolve": EVOLVE_IDS, "heldout": HELDOUT_IDS}, root)
    return load_suite(root)


def make_domain(tmp: Path, **kw: Any) -> EvolveDomain:
    suite = make_suite(tmp / "suite")
    return EvolveDomain(name="tiny", suite=suite, policy=PolicySpec(provider="openai", model="fake-model"),
                        client_factory=policy_factory, **kw)


def make_config(**overrides: Any) -> EvolveConfig:
    params = dict(T=4, k=2, m=1, b_min=1, b_max=2, w=3, m_draft=1, delta=0.05, beta0=0.1, beta1=40.0,
                  w_s=100.0, w_c=15.0, w_n=0.5, n_prune=4, invalid_missing_frac=0.15)
    knobs = dict(repair_rounds=2, n_fail_traces=3, n_success_traces=1, trial_parallel=1,
                 digest_parallel=1, smoke_n=1, mode="xgen", early_stop=False)
    for key in list(overrides):
        if key in params:
            params[key] = overrides.pop(key)
    knobs.update(overrides)
    return EvolveConfig(params=RRSIParams(**params), **knobs)


# ------------------------------------------------------------- role scripts --


def j(obj: Any) -> str:
    return json.dumps(obj)


def edit_decl(eid: str = "C1", component: str = "prompt", **extra: Any) -> Dict[str, Any]:
    e = {"id": eid, "component": component,
         "hypothesis": "A bounded read-back of the deliverable makes the status line match the request.",
         "targets_mode": "unverified_deliverable",
         "predicted_affected": [EVOLVE_IDS[0]],
         "retroactive_check": "corrective: the failing trials end without confirming the file; "
                              "preservative: one read-back only; transfer: any deliverable task."}
    e.update(extra)
    return e


def block_edit(text: str) -> Dict[str, Any]:
    return {"action": "edit_file", "path": "manifest.json", "old": '"extra_blocks": []',
            "new": '"extra_blocks": [{"id": "deliverable_check", "text": "%s"}]' % text}


def proposer_script(marker: str = GOOD, component: str = "prompt") -> List[str]:
    return [j({"action": "list_components"}),
            j({"action": "read_trace", "task_id": EVOLVE_IDS[0], "from_step": 1, "to_step": 2}),
            j(block_edit(marker)),
            j({"action": "done", "summary": "read back the deliverable once", "edits": [edit_decl(component=component)]})]


def no_proposal_script() -> List[str]:
    return [j({"action": "done", "summary": "nothing", "edits": []})]


def analyst_script(task_id: str = EVOLVE_IDS[0]) -> List[str]:
    return [j({"action": "digest_many", "requests": [{"task_id": task_id, "lens": "failure"}]}),
            j({"action": "report",
               "failure_modes": [{"mode": "unverified_deliverable", "n_tasks": 3, "affected_tasks": EVOLVE_IDS,
                                  "description": "The deliverable is written once and never compared with the request.",
                                  "needed_instead": "A bounded read-back."}],
               "capability_gaps": [], "success_habits": []})]


def digester_script(task_id: str = EVOLVE_IDS[0]) -> List[str]:
    return [j({"action": "grep", "pattern": "VERIFIER|FAIL", "path": f"{task_id}.txt"}),
            j({"action": "return", "digest": {"lens": "failure", "blocker": "status line never confirmed",
                                              "narrative": "Wrote the file once and finished.",
                                              "evidence": [{"where": "step 1", "quote": "Write"}],
                                              "verifier_evidence": "check 1 failed",
                                              "needed_instead": "confirm the status line"}})]


def accept_verdicts(n: int = 1) -> List[str]:
    return [j({"verdict": "accept", "reasons": [], "risk_notes": []}) for _ in range(n)]


def make_roles(proposer: Optional[List[str]] = None, critic: Optional[List[str]] = None,
               analyst: Optional[List[str]] = None, digester: Optional[List[str]] = None) -> Roles:
    return Roles(proposer=ScriptedRoleLLM(list(proposer or []), role="proposer"),
                 critic=ScriptedRoleLLM(list(critic or []), role="critic"),
                 analyst=ScriptedRoleLLM(list(analyst or []), role="analyst"),
                 digester=ScriptedRoleLLM(list(digester or []), role="digester"))


def make_run(tmp: Path, roles: Optional[Roles] = None, cfg: Optional[EvolveConfig] = None,
             domain: Optional[EvolveDomain] = None) -> EvolveRun:
    dom = domain or make_domain(tmp)
    lines: List[str] = []
    run = EvolveRun(dom, cfg or make_config(), tmp / "run", roles=roles or make_roles(), log=lines.append)
    run.lines = lines  # type: ignore[attr-defined]
    return run


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").split("\n") if x.strip()]
