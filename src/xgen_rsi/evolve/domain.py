"""EvolveDomain — everything the RRSI loop needs from an XGEN evaluation instance.

RRSI itself never runs an agent, scores a deliverable or reads a trajectory format; the domain
supplies those (reference ``rrsi/domain.py``):

    suite / splits      D_evolve, held-out splits, smoke tasks          evolve.tasks.Suite
    policy              the frozen policy π (registered XGEN model)     evolve.runner.PolicySpec
    client_factory      optional scripted/replay client (tests, replays)
    render / task_row   what the analyst, digester and proposer read     evolve.render
    critic_patterns     deterministic leakage denylist from the suite    evolve.critic
    guards              non-compensatory domain checks (rate_guard)      rsi_math.rate_guard
    briefs              role paragraphs (analyst / digester / proposer / critic)
    constitution        proposer SKILL.md + PATTERNS.md                  evolve/constitution/
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from xgen_rsi.evolve.render import TrialView, load_trial, render_trial, task_row
from xgen_rsi.evolve.runner import PolicySpec
from xgen_rsi.evolve.tasks import Suite, TaskSpec
from xgen_rsi.rsi_math import EvalResult, Signals, TaskResult, rate_guard

CONSTITUTION_DIR = Path(__file__).resolve().parent / "constitution"

#: File names too common to be "distinctive" of one task (they are not leakage on their own).
COMMON_FILE_NAMES = frozenset({
    "readme", "readme.md", "readme.txt", "notes.md", "notes.txt", "data.csv", "data.json",
    "input.txt", "input.csv", "input.json", "output.txt", "output.csv", "output.json",
    "report.md", "report.txt", "summary.md", "summary.txt", "result.json", "results.json",
    "config.json", "out.csv", "out.txt", "out.json", "answer.txt", "answer.json",
})
MIN_ID_LEN = 3
MIN_FILE_LEN = 6
MIN_TEXT_LEN = 4
MIN_NUMBER_LEN = 5

BRIEFS: Dict[str, str] = {
    "analyst": """The agent is an XGEN business agent: a frozen policy LLM driven by a typed
harness (prompt blocks, context management, control flow, output plumbing, tool exposure,
skills, memory policy) works on business tasks in an isolated workspace: reading documents
and data files, answering questions, and producing deliverable files (reports, CSV/JSON
data, edited documents) with file tools. A task's score is the fraction of its
deterministic checks that pass (the deliverable exists, its content and format are right,
the final answer states what was asked); missing or crashed trials score zero. Each
rendered trace ends with the VERIFIER section: per-check PASS/FAIL with details. Content
that was computed but never written into the deliverable is its own failure family.
Near-miss tasks that failed a single check are high-leverage. Where a failure digest hints
the agent was blocked by harness mechanics (context compaction, the tool surface, the
iteration budget, completion review, output parsing) rather than by judgment, use the
capability_gap lens.""",
    "digester": """The trajectory comes from an XGEN business agent working in an isolated
workspace with file tools (Read, Write, Edit, Glob, Grep). Each <task_id>.txt holds a
header (status, termination reason, score, policy tokens, steps), the task prompt, every
step ([step N] AGENT text, TOOL_CALL, TOOL_RESULT / TOOL_ERROR), the final answer and at
the end the VERIFIER section: per-check PASS/FAIL with details. Paths inside the trial
workspace are shown as <workspace>. Read the VERIFIER section first (which checks failed
and why), then grep for anchors (step numbers, tool names, file names, key values, error
strings) to find where the content was computed, lost, or never written.""",
    "proposer": """The tasks are XGEN business-agent tasks: each gives the agent an isolated
workspace with source files (documents, CSV/JSON data, notes) and a request; the agent
works with file tools and must leave the requested deliverables (exact file names and
formats) and a final answer. A task's score is the fraction of its deterministic checks
that pass, so partial credit exists: covering every requested item, exact file names and
formats, and carrying computed values into the deliverable matter as much as rescuing
disasters. The frozen policy is a registered XGEN model; do not assume it shares your
habits or judgment, read the trajectories for how it actually behaves.

The harness is not a codebase: it is manifest.json with typed components whose
implementations are registered platform code. You change params, data files (skills),
and which registered components are present. The kernel (model, provider, credentials,
tool execution, permissions and human-in-the-loop, iteration limits, usage accounting,
user-facing notices) and the verifier are out of reach.

Out of bounds, enforced before measurement: referencing or gaming the verifier or its
checks; evaluation task ids, task-specific file names, values or triggers; customer,
company or person names; anything that steers the model around a refusal or a
permission block. The harness serves many customers' agents and is judged on held-out
tasks the search never sees, so memorized task knowledge is worthless. Litmus test for
every edit: would this help a competent assistant working on MANY unfamiliar business
tasks for a different customer?""",
    "critic": """The harness drives a frozen policy LLM through XGEN business-agent tasks in an
isolated workspace (documents, data files, deliverable files, a final answer) scored by
deterministic checks. General professional method ("re-read the request and list every
deliverable before finishing", "write computed values into the deliverable, not only the
chat") is legitimate and NOT leakage; the line is crossed at task-specific facts, names,
file names or answers ("the quarterly total is 4,210" = REJECT). Existing safety
mechanisms that must not be disabled without a working replacement: context compaction
and the budget guard (context component), completion review, repeat-stop and the turn
input budget (control component), gate reachability (tool exposure). Kernel limits,
the model and provider, credentials, permissions and user-facing notices are not harness
content; an edit that aims at them is a rejection.""",
}


@dataclass
class EvolveDomain:
    """One XGEN evaluation instance."""

    name: str
    suite: Suite
    policy: PolicySpec
    evolve_split: str = "evolve"
    smoke_split: str = "smoke"
    client_factory: Optional[Callable[[Any], Any]] = None
    extra_denylist: Sequence[str] = ()
    """Customer names, internal host names … (admin-provided, case-insensitive)."""
    briefs: Mapping[str, str] = field(default_factory=lambda: dict(BRIEFS))
    constitution_dir: Path = CONSTITUTION_DIR
    component_signals: Signals = ()
    max_valid_rate_drop: Optional[float] = None
    max_nosub_rise: Optional[float] = None

    # ---- task sets ----------------------------------------------------------
    def evolve_ids(self) -> List[str]:
        return self.suite.ids(self.evolve_split)

    def evolve_tasks(self) -> List[TaskSpec]:
        return self.suite.split(self.evolve_split)

    def split_tasks(self, split: str) -> List[TaskSpec]:
        if split not in self.suite.splits:
            raise KeyError(f"suite {self.suite.name!r} has no split {split!r}")
        return self.suite.split(split)

    def tasks_by_id(self, ids: Iterable[str]) -> List[TaskSpec]:
        return [self.suite.tasks[i] for i in ids]

    def smoke_ids(self, incumbent_per_task: Optional[Mapping[str, TaskResult]] = None,
                  n: int = 2) -> List[str]:
        """The smoke split when present, else the incumbent's best-scoring evolve tasks (most
        likely to run cleanly — smoke is a liveness check, not a selection rule)."""
        fixed = self.suite.ids(self.smoke_split)
        if fixed:
            return fixed[:n] if n else fixed
        ids = self.evolve_ids()
        if incumbent_per_task:
            ids = sorted(ids, key=lambda i: (-(incumbent_per_task[i].mean if i in incumbent_per_task else 0.0), i))
        return ids[:n]

    def regression_threshold(self, k: int) -> float:
        """Per-task mean drop that counts as a regression in attribution (reference: 1/k)."""
        return 1.0 / max(1, k)

    # ---- evidence -------------------------------------------------------------
    def load_trial(self, job_dir: Path, task_id: str, trial: int) -> Optional[TrialView]:
        return load_trial(job_dir, task_id, trial)

    def render_trace(self, view: TrialView, detail: bool = False) -> str:
        return render_trial(view, detail=detail)

    def task_row(self, task_id: str, view: Optional[TrialView], tr: Optional[TaskResult]) -> str:
        return task_row(task_id, view, tr)

    # ---- gates and texts ---------------------------------------------------------
    def critic_patterns(self) -> List[Tuple[str, str]]:
        """Deterministic leakage denylist derived from the WHOLE suite (every split): task ids,
        distinctive task file names, verifier check values, admin denylist."""
        pats: List[Tuple[str, str]] = []
        tasks = list(self.suite.tasks.values())
        for t in tasks:
            if len(t.id) >= MIN_ID_LEN:
                pats.append((_bounded(t.id), "evaluation task id"))
        uses: Counter[str] = Counter()
        for t in tasks:
            names = {Path(p).name for p in t.files}
            names |= {Path(str(c["path"])).name for c in t.checks if c.get("path")}
            uses.update(names)
        for fname, n in sorted(uses.items()):
            if n == 1 and len(fname) >= MIN_FILE_LEN and fname.lower() not in COMMON_FILE_NAMES:
                pats.append((_bounded(fname), "task-specific file name"))
        for value in sorted(_check_values(tasks)):
            pats.append((_bounded(value), "verifier check value"))
        for name in self.extra_denylist:
            if name.strip():
                pats.append(("(?i)" + _bounded(name.strip()), "denylisted name"))
        return list(dict.fromkeys(pats))

    def guards(self, incumbent: EvalResult, candidate: EvalResult, *,
               max_valid_drop: Optional[float] = None,
               max_nosub_rise: Optional[float] = None) -> List[str]:
        """g(H_t, H') of R-C.3 through :func:`rsi_math.rate_guard` when a threshold is set
        (the run's config wins over the domain's own thresholds); none set → no guard."""
        vd = max_valid_drop if max_valid_drop is not None else self.max_valid_rate_drop
        nr = max_nosub_rise if max_nosub_rise is not None else self.max_nosub_rise
        if vd is None and nr is None:
            return []
        guard = rate_guard(vd if vd is not None else float("inf"),
                           nr if nr is not None else float("inf"),
                           valid_key="valid_rate", nosub_key="no_submission_rate")
        return guard(incumbent, candidate)

    def constitution(self) -> Tuple[str, str]:
        d = Path(self.constitution_dir)
        return ((d / "SKILL.md").read_text(encoding="utf-8"),
                (d / "PATTERNS.md").read_text(encoding="utf-8"))


def _bounded(literal: str) -> str:
    return rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])"


def _check_values(tasks: Sequence[TaskSpec]) -> set[str]:
    out: set[str] = set()

    def take(v: Any) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, str):
            if len(v.strip()) >= MIN_TEXT_LEN:
                out.add(v.strip())
        elif isinstance(v, (int, float)):
            s = repr(v)
            if len(s) >= MIN_NUMBER_LEN:
                out.add(s)
        elif isinstance(v, Mapping):
            for x in v.values():
                take(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                take(x)

    for t in tasks:
        for c in t.checks:
            take(c.get("text"))
            take(c.get("equals"))
            take(c.get("path_equals"))
            take(c.get("value"))
            for cell in c.get("cells") or ():
                if isinstance(cell, Mapping):
                    take(cell.get("value"))
    return out
