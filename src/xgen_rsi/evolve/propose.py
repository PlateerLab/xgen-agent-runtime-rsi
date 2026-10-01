"""The regularized proposer P_reg(· | H_t, F_t, 𝓛_t, b_t, 𝓔_t, 𝓑_t) of Algorithm 1.

A role LLM edits the candidate harness IN PLACE (``<worktree>/harness``) through a strict-JSON
action protocol and finishes with ``done(edits=[...])``. Every edit is tagged with a component ℓ'
and a hypothesis h'; the diff d' is taken from git afterwards and the tag is checked against the
addresses the diff touches (``tagging.py``). Conditioning, as in the reference:

  F_t   three-lens report, per-task digests, read-only access to the rendered trajectories
  𝓛_t   the edit history (component, hypothesis, ΔS, ΔC, accepted) + attribution scoreboard
  b_t   at most b_t independent edits (‖z_t‖_0 ≤ b_t, ``rsi_math.check_edit_cardinality``)
  𝓔_t   exploration directives (stall flag, untried components, reserved slot)
  𝓑_t   components to prune with the accepted machinery to remove

done() bounces (never accepts) an over-budget, untagged, mis-vocabularied or reserved-slot-violating
submission, and — new here — a candidate whose manifest no longer loads or that touches
platform-owned fields, locked keys or files no component references. There is no abort action.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/propose.py``: code structure,
the proposer prompt and the action-protocol texts), Copyright 2026 The rrsi Authors / Google LLC,
Apache License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import pkgutil
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from xgen_rsi.evolve.render import cut_steps
from xgen_rsi.evolve.tagging import touched
from xgen_rsi.harness.spec import HarnessManifest, addresses_for, load_manifest
from xgen_rsi.rsi_math import K, check_edit_cardinality

MAX_TURNS = 40
MAX_EDITS = 80
TRACE_READ_CAP = 60_000
ALLOWED_EXTS = frozenset({".json", ".md", ".txt"})
REQUIRED_FIELDS = ("id", "component", "hypothesis", "targets_mode", "predicted_affected",
                   "retroactive_check")
ABORT_BOUNCES = 3
READ_RESULT_CAP = 150_000
OTHER_RESULT_CAP = 4000

SYSTEM_TMPL = """You are a harness engineer agent. You directly modify the harness of an
LLM agent to fix recurring failure modes observed on an evolve set of tasks. The policy LLM
is frozen and is a DIFFERENT model from you: do not assume it shares your capabilities,
habits or judgment. Improve the harness from ITS perspective, using the trajectories as
evidence of how it actually behaves. The ONLY thing you can change is the harness package
in your working directory; the kernel, the verifier and the task set are frozen.

{domain_brief}

HARNESS FORMAT
- manifest.json declares typed components. Each has an "id", a "kind" from the fixed
  vocabulary, an "impl" (a REGISTERED platform implementation
  "xgen_rsi.components.<module>:<Class>"; you cannot add code), "params", optional "files"
  (data files in this directory, e.g. skills/<name>/SKILL.md) and optional "enabled".
- Atomic edit addresses: <component_id>.params.<key>[.<key>...] and
  <component_id>.files.<path>. The kind of the component an edit touches IS its component
  tag; the tag you declare is checked against the addresses your diff touches.
- You may change params, add or edit data files and list them under a component's
  "files", add a component that uses a registered impl (action list_components shows the
  catalog and the params each impl reads), and disable or remove a component.
- A file that no component lists has no effect and is refused. The manifest must still
  load after your edits: it is validated (schema, kinds, impls, one enabled prompt /
  control_flow / output_plumbing / context_mgmt component) when you call done, and a
  validation error comes back to you as a done() error.
- LOCKED (cannot change): params matching {locked}; the manifest fields "locked",
  "enabled_kinds" and "exploration_policy" (the exploration policy is owned by a
  separate loop). Kinds disabled for this harness by platform decision: {disabled}.
  The model, provider, credentials, kernel limits (iterations, budgets), tool execution,
  permissions and user-facing notices live in the kernel; editing anything aimed at them
  has no effect and is rejected.

HOW CANDIDATES ARE SELECTED (this is your reward)
{selection}

You interact through a STRICT JSON protocol: reply with EXACTLY ONE action
object per turn, nothing else. Actions:
  {{"action": "list_files"}}
  {{"action": "read_file", "path": "relative/path"}}
  {{"action": "list_components"}}
      (catalog of registered component implementations: kind, impl, what it does,
      the params it reads, and which components of this harness use it)
  {{"action": "list_traces"}}
      (this round's traces: task_id, score, status, steps, failed checks, modes hit)
  {{"action": "read_trace", "task_id": "<id from the task list>", "from_step": 3,
   "to_step": 9, "detail": true}}
      (READ-ONLY rendered trajectory segment: the raw evidence behind the
      failure modes. Step range optional. "detail": true expands per-message
      caps. The VERIFIER verdict is always included.)
  {{"action": "edit_file", "path": "p", "old": "exact substring", "new": "replacement"}}
      (old must occur EXACTLY ONCE in the file; this is how you edit manifest.json)
  {{"action": "write_file", "path": "p", "content": "full file content"}}
      (for NEW files only (.json .md .txt); never overwrite an existing file this way.
      A new file must be listed under a component's "files" to do anything.)
  {{"action": "done", "summary": "one-line summary of this candidate",
   "edits": [
     {{"id": "C1",
      "component": "one of: {components}",
      "addresses": ["optional: the edit addresses this edit touches"],
      "hypothesis": "one sentence: the mechanism and WHY it should move the score",
      "targets_mode": "failure mode / capability gap / habit it targets",
      "why_not_lower_lever": "why a plain instruction edit would NOT fix this
          (or, for a prompt edit, why prose IS the right lever here)",
      "trigger_condition": "the exact, checkable condition under which the
          mechanism activates ('always' is almost never right)",
      "predicted_affected": ["<task id>", "..."],
      "retroactive_check": "three-part counterfactual: (corrective) which cited
          failing tasks would have moved had this existed, walking the actual
          trajectory; (preservative) which success habits could this disrupt
          and why it won't; (transfer) why it generalizes beyond this evolve set",
      "regression_risk": "what could break outside predicted_affected"}},
     ...]}}

THERE IS NO ABORT ACTION. You must ship a candidate. A round that ships
nothing tests nothing: the history records what was MEASURED, and a mechanism
you declined to build has no measurement behind it. If your best idea violates
a hard rule, it is not your best idea; construct a different one, preferably on
a component the history shows was never exercised.

done() contract:
- An edit = ONE independent, attributable change (it works on its own and can
  answer "which tasks will it move" by itself). Dependent parts are ONE edit.
  Ship at most THIS ROUND'S EDIT BUDGET b_t given in the context, never more.
  Ship fewer if the evidence supports fewer.
- Every edit names its `component` from the vocabulary above. The tag is
  validated against the addresses the diff touches; a mislabelled edit is
  re-tagged from the diff.
- If the context says a RESERVED EXPLORATION SLOT applies to you, at least one
  edit must be on one of the listed never-exercised components, and the diff
  must really touch a component of that kind.
- If the context lists COMPONENTS TO PRUNE that hold accepted machinery, an
  edit that removes that machinery is a legitimate edit (component = the
  pruned component, hypothesis = "prune: ..."). Prefer it when the evidence
  says the machinery stopped earning its place.
- predicted_affected lists CONCRETE task ids from this round's traces.
  Predictions are checked against the evaluation and your hit/miss record
  (scoreboard) is shown back to you; over-claiming counts against you.

You must follow the constitution (SKILL.md) in the context: no task-specific
entities, names, file names or values anywhere in the harness; every edit
targets a reported failure mode, capability gap or habit; keep each edit's
diff scoped to its mechanism; the harness runs unattended on every task in the
set. All harness text (prompt blocks, skill files, descriptions) in English."""


class Workspace:
    """Path-jailed file operations inside one candidate's harness directory."""

    def __init__(self, harness_dir: Path | str, exts: frozenset[str] = ALLOWED_EXTS) -> None:
        self.dir = Path(harness_dir).resolve()
        self.exts = exts

    def _safe(self, rel: str) -> Path:
        rel = str(rel).lstrip("/")
        name = self.dir.name
        if rel == name or rel.startswith(name + "/"):
            rel = rel[len(name):].lstrip("/")
        p = (self.dir / rel).resolve()
        if p != self.dir and not str(p).startswith(str(self.dir) + os.sep):
            raise ValueError(f"path escapes harness dir: {rel}")
        return p

    def files(self) -> List[Path]:
        return [p for p in sorted(self.dir.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]

    def list_files(self) -> str:
        return "\n".join(f"{p.relative_to(self.dir).as_posix()} ({p.stat().st_size}B)" for p in self.files())

    def read(self, rel: str) -> str:
        p = self._safe(rel)
        return p.read_text(encoding="utf-8") if p.is_file() else f"ERROR: no such file {rel}"

    def edit(self, rel: str, old: str, new: str) -> str:
        p = self._safe(rel)
        if not p.is_file():
            return f"ERROR: no such file {rel}"
        src = p.read_text(encoding="utf-8")
        n = src.count(old) if old else 0
        if n == 0:
            return "ERROR: old string not found (must match exactly, including whitespace)"
        if n > 1:
            return f"ERROR: old string occurs {n} times; provide a longer unique context"
        p.write_text(src.replace(old, new, 1), encoding="utf-8")
        return f"OK: edited {rel}"

    def write(self, rel: str, content: str) -> str:
        p = self._safe(rel)
        if p.suffix not in self.exts:
            return f"ERROR: extension {p.suffix or '(none)'} not allowed (allowed: {sorted(self.exts)})"
        if p.exists():
            return f"ERROR: {rel} exists; use edit_file"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"OK: created {rel}"

    def dump(self) -> str:
        parts = []
        for p in self.files():
            if p.suffix in self.exts:
                parts.append(f"===== FILE: {p.relative_to(self.dir).as_posix()} =====\n"
                             f"{p.read_text(encoding='utf-8')}")
        return "\n\n".join(parts)


# ------------------------------------------------------------------ catalog --

_PARAM_RE = re.compile(r"""self\.param\(\s*["']([\w.]+)["']\s*(?:,\s*([^)\n]{0,80}))?\)""")


def component_catalog(manifest: Optional[HarnessManifest], enabled: Sequence[str]) -> str:
    """Registered component implementations (``xgen_rsi.components.*``): kind, impl, docstring,
    the params each reads (with defaults) and which components of this harness use it."""
    import xgen_rsi.components as pkg
    from xgen_rsi.harness.runtime import Component

    used: Dict[str, List[str]] = {}
    set_keys: Dict[str, List[str]] = {}
    if manifest is not None:
        for c in manifest.components:
            used.setdefault(c.impl, []).append(c.id + ("" if c.enabled else " (disabled)"))
            for key in c.params:
                if key not in set_keys.setdefault(c.impl, []):
                    set_keys[c.impl].append(str(key))
    rows: List[str] = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        modname = f"{pkg.__name__}.{info.name}"
        try:
            mod = importlib.import_module(modname)
        except Exception as exc:  # noqa: BLE001 — a broken module is reported, not fatal
            rows.append(f"- {modname}: (unavailable: {type(exc).__name__})")
            continue
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ != mod.__name__ or not issubclass(cls, Component) or not getattr(cls, "kind", ""):
                continue
            impl = f"{modname}:{cls.__name__}"
            own_doc = cls.__dict__.get("__doc__")  # never the inherited base-class docstring
            doc = inspect.cleandoc(own_doc) if own_doc else (inspect.getdoc(mod) or "")
            try:
                src = inspect.getsource(cls)
            except (OSError, TypeError):
                src = ""
            params: Dict[str, str] = {}
            for key, default in _PARAM_RE.findall(src):
                params.setdefault(key, default.strip() or "None")
            kind = str(cls.kind)
            status = "" if kind in enabled else "  [kind disabled in this harness: cannot be used]"
            read = ", ".join(f"{k}={v}" for k, v in params.items()) or "(none found)"
            rows.append(
                f"- impl: {impl}\n  kind: {kind}{status}\n"
                f"  params read (key=default): {read}\n"
                f"  params set in this harness: {set_keys.get(impl) or '(none)'}\n"
                f"  used by: {used.get(impl) or '(none)'}\n"
                f"  about: {' '.join(doc.split())[:700]}")
    return "\n".join(rows) or "(no registered components)"


# --------------------------------------------------------------- validation --


def validate_candidate(harness_dir: Path | str, incumbent: Optional[HarnessManifest]) -> List[str]:
    """Problems that make the candidate unusable: the manifest no longer loads or instantiates,
    or it touches platform-owned fields, locked keys or unreferenced files."""
    from xgen_rsi.harness.runtime import instantiate

    try:
        cand = load_manifest(harness_dir)
    except Exception as exc:  # noqa: BLE001 — JSON errors, schema errors, bad paths
        return [f"manifest error: {type(exc).__name__}: {exc}"]
    root = Path(harness_dir)
    missing = [f"component {c.id!r} lists file {f!r} that does not exist"
               for c in cand.components for f in c.files if not (root / f).is_file()]
    if missing:
        return missing
    try:
        instantiate(cand)
    except Exception as exc:  # noqa: BLE001
        return [f"manifest error: components do not instantiate: {type(exc).__name__}: {exc}"]
    if incumbent is None:
        return []
    ts = touched(incumbent, cand)
    problems = list(ts.violations)
    problems += [f"file {f!r} is not listed under any component's files, so it has no effect"
                 for f in ts.orphans]
    return problems


# ------------------------------------------------------------------- traces --


def _list_traces(traces: Mapping[str, Any], per_task: Mapping[str, Any], findings: Sequence[Mapping[str, Any]],
                 task_row: Callable[[str, Any, Any], str]) -> str:
    modes: Dict[str, List[str]] = {}
    for f in findings or []:
        label = f.get("blocker") or f.get("wanted") or f.get("lens") or "?"
        modes.setdefault(str(f.get("task_id")), []).append(str(label)[:80])
    rows = []
    for tid, view in traces.items():
        tr = per_task.get(tid)
        rows.append((tr.mean if tr is not None else 0.0, task_row(tid, view, tr) + f" | modes={modes.get(tid, [])}"))
    return "\n".join(r for _, r in sorted(rows, key=lambda x: x[0])) or "ERROR: no traces available"


def _read_trace(traces: Mapping[str, Any], task_id: Any, from_step: Any, to_step: Any, cache: Dict[Any, str],
                detail: bool, render: Callable[[Any, bool], str]) -> str:
    tid = str(task_id)
    view = traces.get(tid)
    if view is None:
        return f"ERROR: no trace for {tid} (use list_traces for valid ids)"
    key = (tid, bool(detail))
    if key not in cache:
        cache[key] = render(view, bool(detail))
    lo = None if from_step is None else int(from_step)
    hi = None if to_step is None else int(to_step)
    return cut_steps(cache[key], lo, hi, TRACE_READ_CAP)


# ------------------------------------------------------------------ propose --


def propose(llm: Any, harness_dir: Path | str, *, report: Mapping[str, Any],
            history_rows: Sequence[Mapping[str, Any]], skill_md: str, patterns_md: str,
            budget: int, explore: Mapping[str, Any], reserved_slot: bool,
            prune_set: Sequence[Mapping[str, Any]], enabled_kinds: Sequence[str],
            render: Callable[[Any, bool], str], task_row: Callable[[str, Any, Any], str],
            traces: Optional[Mapping[str, Any]] = None, per_task: Optional[Mapping[str, Any]] = None,
            findings: Optional[Sequence[Mapping[str, Any]]] = None,
            scoreboard: Optional[Sequence[Mapping[str, Any]]] = None, variant_brief: str = "",
            repair_brief: Optional[Mapping[str, Any]] = None, brief: str = "",
            incumbent: Optional[HarnessManifest] = None, selection: str = "",
            prior_changes: bool = False) -> Dict[str, Any]:
    """Run the proposer for one candidate variant (edits files in place under ``harness_dir``).
    Returns ``{status, edits, summary, mechanism, targets_mode, n_edits, log}``.

    ``prior_changes`` (repair rounds): the working tree already carries this candidate's
    changes, so a done() that only re-declares edits is not bounced as "zero file changes"."""
    ws = Workspace(harness_dir)
    traces = traces or {}
    per_task = per_task or {}
    enabled = [k for k in K if k in set(enabled_kinds)]
    render_cache: Dict[Any, str] = {}
    locked = list(incumbent.locked) if incumbent is not None else []
    system = SYSTEM_TMPL.format(
        domain_brief=brief, components=" | ".join(enabled), locked=locked or "(none)",
        disabled=[k for k in K if k not in enabled] or "(none)",
        selection=selection or "(see the constitution)")
    stable = "\n\n".join(["=== CONSTITUTION (SKILL.md) ===", skill_md,
                          "=== PATTERN LIBRARY (PATTERNS.md) ===", patterns_md])
    explore_text = str(explore.get("text", ""))
    untried = list(explore.get("untried") or [])
    if reserved_slot:
        explore_text += ("\n\nRESERVED EXPLORATION SLOT: this variant holds one. At least one of "
                         "your edits MUST be on a never-exercised component from: "
                         f"{untried}, and the diff must really touch a component of that kind.")
    try:
        editable = list(addresses_for(load_manifest(harness_dir)))
    except Exception:  # noqa: BLE001 — a broken working tree is shown as such
        editable = ["(manifest does not load)"]
    context = "\n\n".join([
        *(["=== THIS VARIANT'S BRIEF ===", variant_brief] if variant_brief else []),
        "=== EDIT HISTORY L_t (every measured edit: component, hypothesis, Delta S, Delta C, "
        "accepted). A rejected mechanism is negative evidence; do not redraw it unchanged. An "
        "accepted one carries the gain it produced; refine what has known credit, not what "
        "merely preceded a rise. early_stopped records carry an upper bound of Delta S. ===",
        json.dumps(list(history_rows), ensure_ascii=False, indent=1),
        "=== ATTRIBUTION SCOREBOARD (how past edits' predictions fared; unpredicted_regressions "
        "are tasks an edit likely broke) ===",
        json.dumps(list(scoreboard or []), ensure_ascii=False, indent=1),
        "=== EXPLORATION DIRECTIVES E_t ===", explore_text,
        "=== COMPONENTS TO PRUNE B_t (exercised, no strictly improving edit in the recent "
        "window; remove the accepted machinery listed, it has stopped earning its place) ===",
        json.dumps(list(prune_set), ensure_ascii=False, indent=1) if prune_set else "(none)",
        "=== THREE-LENS ANALYSIS REPORT F_t (failure modes ranked; capability gaps often need "
        "tool/plumbing fixes; success_habits are behaviors your change MUST NOT break) ===",
        json.dumps(dict(report), ensure_ascii=False, indent=1),
        "=== PER-TASK DIGESTS (evidence anchors; use read_trace for raw evidence) ===",
        json.dumps(list(findings or []), ensure_ascii=False, indent=1),
        "=== CURRENT HARNESS H_t (manifest.json and its data files) ===", ws.dump(),
        "=== EDITABLE ADDRESSES (current param leaves and files) ===", "\n".join(editable),
        "=== THIS ROUND'S EDIT BUDGET b_t ===",
        f"You may ship AT MOST {budget} independent edit(s) in this candidate (the budget "
        f"anneals over the run: early rounds explore, late rounds make single attributable "
        f"changes). Ship fewer if the evidence supports fewer.",
        "=== TASK ===",
        ("REPAIR ROUND: your previous edits for this candidate are already in the working tree "
         "(reflected in CURRENT HARNESS above). The reviewer raised the objections below. Fix ONLY "
         "what the objections require (remove leaked content, split or re-declare edits, wire up "
         "inert data, or delete the offending part) with minimal additional edits, then call done "
         "again with the corrected edits array.\n\n=== REVIEWER OBJECTIONS ===\n"
         + json.dumps(dict(repair_brief), ensure_ascii=False, indent=1) + "\nFirst action:")
        if repair_brief else
        "Address the highest-impact failure modes / capability gaps within your edit budget. "
        "Implement via the JSON actions, then call done with the edits array. First action:",
    ])

    log: List[Dict[str, Any]] = []
    transcript = ""
    n_edits = 0
    changed = bool(prior_changes)
    aborts_left = ABORT_BOUNCES
    for _turn in range(MAX_TURNS):
        prompt = ("=== ROUND CONTEXT ===\n" + context + "\n\n=== INTERACTION LOG ===\n"
                  + transcript + "\nReply with exactly one JSON action object.")
        raw = llm.generate(prompt, system=system, json_only=True, cache_prefix=stable)
        try:
            act = json.loads(raw)
        except json.JSONDecodeError:
            transcript += f"\n[you] {str(raw)[:500]}\n[result] ERROR: not valid JSON"
            continue
        if isinstance(act, list):
            act = next((x for x in act if isinstance(x, dict)), None)
        if not isinstance(act, dict):
            transcript += "\n[you] (non-object)\n[result] ERROR: reply with EXACTLY ONE JSON action object"
            continue
        a = act.get("action")
        log.append(act if a in ("done", "abort") else
                   {k: (v if len(str(v)) < 200 else str(v)[:200] + "...") for k, v in act.items()})
        if a == "done":
            raw_edits = act.get("edits") or act.get("candidates") or []
            edits = [dict(e) for e in raw_edits if isinstance(e, dict)] if isinstance(raw_edits, list) else []
            if n_edits == 0 and not changed and edits:
                transcript += ("\n[you] done\n[result] ERROR: you declared edits but made ZERO file "
                               "changes. Implement them with edit_file/write_file, then call done.")
                continue
            problems: List[str] = []
            live = n_edits > 0 or changed
            if live and not edits:
                problems.append("edits array is empty")
            if not check_edit_cardinality(len(edits), budget):
                problems.append(f"{len(edits)} edits exceed the budget b_t = {budget}")
            for e in edits:
                miss = [f for f in REQUIRED_FIELDS if not e.get(f)]
                if miss:
                    problems.append(f"edit {e.get('id', '?')} missing {miss}")
                if e.get("component") and str(e["component"]).lower() not in enabled:
                    problems.append(f"edit {e.get('id')} component {e['component']!r} not in the "
                                    f"enabled vocabulary {enabled}")
            if reserved_slot and untried and edits and not any(
                    str(e.get("component", "")).lower() in untried for e in edits):
                problems.append("this variant holds a RESERVED EXPLORATION SLOT: at least one edit "
                                f"must be on a never-exercised component from {untried}")
            if live and not problems:
                problems += validate_candidate(harness_dir, incumbent)
            if problems and live:
                transcript += (f"\n[you] done\n[result] ERROR: {problems}. Call done again fixed "
                               "(drop or merge edits if over budget; add the required edit if a "
                               "slot is reserved; repair the manifest if it no longer loads).")
                continue
            for e in edits:
                e["component"] = str(e.get("component", "")).lower()
                e.setdefault("mechanism", e.get("hypothesis"))
            return {"status": "done", "summary": act.get("summary"), "edits": edits,
                    "mechanism": act.get("summary") or "; ".join(str(e.get("hypothesis")) for e in edits)[:200],
                    "targets_mode": ", ".join(str(e.get("targets_mode")) for e in edits)[:200],
                    "n_edits": n_edits, "log": log}
        if a == "abort":
            if aborts_left > 0:
                aborts_left -= 1
                transcript += ("\n[you] abort\n[result] ERROR: there is no abort action. Pick the "
                               "most defensible mechanism you can build within the hard rules, "
                               "implement it, and call done. A rejection is data; an abort is not.")
                continue
            return {"status": "abort", "reason": act.get("reason"), "edits": [],
                    "n_edits": n_edits, "log": log}
        try:
            if a == "list_files":
                result = ws.list_files()
            elif a == "read_file":
                result = ws.read(act["path"])
            elif a == "list_components":
                result = component_catalog(incumbent, enabled)
            elif a == "list_traces":
                result = _list_traces(traces, per_task, findings or [], task_row)
            elif a == "read_trace":
                result = _read_trace(traces, act.get("task_id", ""), act.get("from_step"),
                                     act.get("to_step"), render_cache, bool(act.get("detail", False)), render)
            elif a in ("edit_file", "write_file"):
                if n_edits >= MAX_EDITS:
                    result = "ERROR: file-change budget exhausted; call done"
                elif a == "edit_file":
                    result = ws.edit(act["path"], str(act["old"]), str(act["new"]))
                    n_edits += result.startswith("OK")
                else:
                    result = ws.write(act["path"], str(act["content"]))
                    n_edits += result.startswith("OK")
            else:
                result = f"ERROR: unknown action {a}"
        except Exception as exc:  # noqa: BLE001 — a malformed action is feedback
            result = f"ERROR: {exc}"
        cap = READ_RESULT_CAP if a in ("read_file", "list_files", "read_trace", "list_traces",
                                        "list_components") else OTHER_RESULT_CAP
        transcript += f"\n[you] {json.dumps(act)[:1500]}\n[result] {str(result)[:cap]}"
    return {"status": "max_turns", "edits": [], "n_edits": n_edits, "log": log}
