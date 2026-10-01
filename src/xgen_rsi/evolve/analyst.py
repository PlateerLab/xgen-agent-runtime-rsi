"""Analyze(H_t, D_evolve) -> F_t (Algorithm 1, line 1).

The batch analyst never reads raw traces. It dispatches read-only digesters over the incumbent's
own evaluation and aggregates their digests into a three-lens report:

  failure_modes    what blockers lost checks, clustered across tasks
  capability_gaps  what the agent tried to do but could not
  success_habits   what let passing tasks finish cleanly (kept across rounds as a regression
                   guard for the proposer)

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/analyst.py``: code structure and
the analyst prompt texts), Copyright 2026 The rrsi Authors / Google LLC, Apache License 2.0;
modified by PlateerLab.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from xgen_rsi.evolve.digester import digest_task

MAX_TURNS = 30
MAX_REQUESTS_PER_CALL = 8
LENSES = ("failure_modes", "capability_gaps", "success_habits")

SYSTEM_TMPL = """You are the batch analyst in a harness-evolution loop. A frozen
policy LLM, driven by an evolvable harness, ran the evolve set of business
agent tasks; some trials scored well, some did not. Your job: produce a
three-lens analysis report that a harness engineer will act on.

{domain_brief}

You do NOT read traces yourself. You dispatch read-only digester subagents
that investigate one trace each and return structured digests. Budget their
use: digest FAILED / low-scoring tasks first (failure lens), prioritising
coverage of every suspected failure cluster over digesting every failure;
digest 3-5 representative SUCCESSFUL tasks (success lens, prefer ones that
resemble a big failure cluster); use the capability_gap lens or follow-up
questions where a failure digest hints the agent was blocked by the
environment or the harness's plumbing rather than by its own judgment.

Actions (STRICT JSON, one per turn):
  {{"action": "digest_many", "requests": [
      {{"task_id": "<id exactly as it appears in the task table>",
       "lens": "failure|capability_gap|success",
       "questions": ["optional targeted questions"]}}, ...]}}
      (up to 8 per call, they run in parallel; each returns a digest)
  {{"action": "report",
   "failure_modes": [
     {{"mode": "snake_case_label", "n_tasks": 0, "affected_tasks": [...],
      "description": "generalized mechanism, entity-free",
      "needed_instead": "...",
      "representative_evidence": [{{"task_id": "...", "where": "...", "quote": "..."}}]}}],
   "capability_gaps": [
     {{"gap": "snake_case_label", "n_tasks": 0, "affected_tasks": [...],
      "description": "what the agent could not do and why, entity-free",
      "representative_evidence": [...]}}],
   "success_habits": [
     {{"habit": "snake_case_label", "n_tasks": 0,
      "description": "the reusable behavior, entity-free",
      "risk_if_broken": "what regresses if a harness change disrupts it"}}]}}

Aggregation rules:
1. MERGE digests describing the same underlying mechanism even if worded
   differently; SPLIT a label that covers two distinct mechanisms.
2. RANK failure_modes by total impact (number of tasks weighted by how many
   checks the mode costs on each).
3. Descriptions must be entity-free and task-agnostic (no task names, no
   customer, company or person names, no subject-matter facts, no
   task-specific values or file names); evidence quotes may contain them.
4. Keep prior mode names when the same mechanism recurs (stable naming);
   prior names are provided in the context.
5. Do NOT prescribe harness changes and do NOT attribute blame to model vs
   harness.
6. If digests contradict or a cluster is unclear, dispatch follow-up digests
   with targeted questions before reporting. Report once, at the end.
7. English only."""


def safe_name(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(task_id))


def prepare_rendered(traces: Mapping[str, Any], rendered_dir: Path,
                     render: Callable[[Any, bool], str]) -> Dict[str, str]:
    """Write ``<rendered_dir>/<task_id>.txt`` for every trace; returns task_id -> file stem."""
    rendered_dir.mkdir(parents=True, exist_ok=True)
    names: Dict[str, str] = {}
    for tid, view in traces.items():
        stem = safe_name(tid)
        names[tid] = stem
        out = rendered_dir / f"{stem}.txt"
        if not out.exists():
            out.write_text(render(view, True), encoding="utf-8")
    return names


def task_table(traces: Mapping[str, Any], per_task: Mapping[str, Any],
               task_row: Callable[[str, Any, Any], str]) -> str:
    rows = []
    for tid, view in traces.items():
        tr = per_task.get(tid)
        rows.append((tr.mean if tr is not None else 0.0, task_row(tid, view, tr)))
    return "\n".join(r for _, r in sorted(rows, key=lambda x: x[0]))


def analyze(analyst_llm: Any, digester_llm: Any, traces: Mapping[str, Any],
            per_task: Mapping[str, Any], round_dir: Path, *, briefs: Mapping[str, str],
            render: Callable[[Any, bool], str], task_row: Callable[[str, Any, Any], str],
            prior_modes: Optional[List[Mapping[str, Any]]] = None,
            prior_habits: Optional[List[Mapping[str, Any]]] = None,
            parallel: int = 6) -> Dict[str, Any]:
    """Run the analyst; digests are written to ``<round>/analysis/digests``."""
    rendered_dir = round_dir / "analysis" / "rendered"
    names = prepare_rendered(traces, rendered_dir, render)
    digests_dir = round_dir / "analysis" / "digests"
    digests_dir.mkdir(parents=True, exist_ok=True)
    system = SYSTEM_TMPL.format(domain_brief=briefs.get("analyst", ""))
    context = "\n\n".join([
        "=== TASK TABLE (one rendered trace per task; lowest scores first) ===",
        task_table(traces, per_task, task_row),
        "=== PRIOR FAILURE-MODE NAMES (for stable naming) ===",
        json.dumps([{"mode": m.get("mode"), "description": m.get("description")}
                    for m in (prior_modes or [])], ensure_ascii=False, indent=1),
        "=== PRIOR SUCCESS-HABIT NAMES (for stable naming) ===",
        json.dumps([{"habit": h.get("habit"), "description": h.get("description")}
                    for h in (prior_habits or [])], ensure_ascii=False, indent=1),
        "=== TASK ===",
        "Dispatch digesters, then produce the report. First action:",
    ])
    transcript = ""
    n_digests = 0
    for _ in range(MAX_TURNS):
        prompt = (context + "\n\n=== INTERACTION LOG ===\n" + transcript
                  + "\nReply with exactly one JSON action object.")
        raw = analyst_llm.generate(prompt, system=system, json_only=True)
        try:
            act = json.loads(raw)
        except json.JSONDecodeError:
            transcript += f"\n[you] {str(raw)[:300]}\n[result] ERROR: invalid JSON"
            continue
        if isinstance(act, list):
            act = next((x for x in act if isinstance(x, dict)), None)
        if not isinstance(act, dict):
            transcript += ("\n[you] (non-object)\n[result] ERROR: reply with "
                           "EXACTLY ONE JSON action object")
            continue
        a = act.get("action")
        if a == "report":
            report: Dict[str, Any] = {}
            for key in LENSES:
                items = act.get(key) or []
                report[key] = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
            report["failure_modes"].sort(key=lambda m: -_int(m.get("n_tasks")))
            report["n_digests"] = n_digests
            return report
        if a == "digest_many":
            reqs = [r for r in (act.get("requests") or [])[:MAX_REQUESTS_PER_CALL] if isinstance(r, dict)]
            for r in reqs:
                if r.get("task_id") is not None:
                    r["task_id"] = str(r["task_id"])
            valid = [r for r in reqs if r.get("task_id") in traces]

            def run(r: Dict[str, Any]) -> Dict[str, Any]:
                lens = str(r.get("lens") or "failure")
                d = digest_task(digester_llm, rendered_dir, names[r["task_id"]], lens,
                                briefs.get("digester", ""), r.get("questions"))
                d["task_id"] = r["task_id"]
                (digests_dir / f"{names[r['task_id']]}_{safe_name(lens)}.json").write_text(
                    json.dumps(d, ensure_ascii=True, indent=1), encoding="utf-8")
                return d

            with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
                digests = list(ex.map(run, valid))
            n_digests += len(digests)
            result = json.dumps(digests, ensure_ascii=False)
            skipped = len(reqs) - len(valid)
            if skipped:
                known = next(iter(traces), "?")
                result += f"\n[{skipped} requests skipped: unknown task_id; ids look like {known!r}]"
            transcript += f"\n[you] digest_many ({len(valid)} requests)\n[result] {result}"
        else:
            transcript += f"\n[you] {json.dumps(act)[:300]}\n[result] ERROR: unknown action {a}"
    return {"failure_modes": [], "capability_gaps": [], "success_habits": [],
            "error": "analyst hit max turns", "n_digests": n_digests}


def load_digests(round_dir: Path) -> List[Dict[str, Any]]:
    dg = round_dir / "analysis" / "digests"
    if dg.exists():
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(dg.glob("*.json"))]
    return []


def _int(x: Any) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0
