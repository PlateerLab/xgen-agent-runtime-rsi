"""Render one evaluation trial into the text the analyst, digester and proposer read.

A trial is the runner's ``outcome.json`` (reward, checks, answer, status) plus the kernel's
:class:`~xgen_rsi.kernel.recorder.TrajectoryRecord` (transcript, steps, policy tokens). Layout::

    TASK / STATUS / SCORE header
    === TASK PROMPT ===
    === TRAJECTORY ===          one assistant message = one step: [step N] AGENT / TOOL_CALL /
                                TOOL_RESULT; every line of a step carries its prefix so a step
                                range can be cut exactly
    === FINAL ANSWER ===
    === VERIFIER (ground truth checks) ===   per-check PASS/FAIL with details (always kept)

Absolute workspace paths are shown as ``<workspace>`` (they are run-directory noise and would
otherwise invite path-specific edits). Caps follow the reference renderers; ``detail=True``
widens them so a reader can see what tool results actually contained.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from xgen_rsi.rsi_math import TaskResult

CAPS = {"agent": 1500, "args": 500, "result": 800, "prompt": 4000, "final": 4000}
DETAIL_CAPS = {"agent": 6000, "args": 3000, "result": 6000, "prompt": 8000, "final": 8000}
MAX_CHARS = 300_000
VERIFIER_HEADER = "=== VERIFIER (ground truth checks) ==="
_STEP = re.compile(r"\[step (\d+)\]")


@dataclass(frozen=True)
class TrialView:
    """One trial's outcome + trajectory record, as loaded from a job directory."""

    task_id: str
    trial: int
    outcome: Mapping[str, Any]
    record: Optional[Mapping[str, Any]] = None
    workspace: Optional[str] = None

    @property
    def status(self) -> str:
        if self.record and self.record.get("status"):
            return str(self.record.get("status"))
        return str(self.outcome.get("status") or "unknown")

    @property
    def checks(self) -> List[Mapping[str, Any]]:
        return list((self.outcome.get("verifier") or {}).get("checks") or [])


def load_trial(job_dir: Path | str, task_id: str, trial: int) -> Optional[TrialView]:
    """``<job>/<task>__<trial>/outcome.json`` + its trajectory record (None when absent)."""
    tdir = Path(job_dir) / f"{task_id}__{trial}"
    op = tdir / "outcome.json"
    if not op.exists():
        return None
    try:
        outcome = json.loads(op.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    record = None
    rp = outcome.get("record")
    if rp and Path(rp).exists():
        try:
            record = json.loads(Path(rp).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = None
    ws = tdir / "workspace"
    return TrialView(task_id=task_id, trial=int(trial), outcome=outcome, record=record,
                     workspace=str(ws.resolve()) if ws.exists() else None)


def clip(value: Any, n: int) -> str:
    s = "" if value is None else str(value)
    return s if len(s) <= n else s[:n] + f" ...[+{len(s) - n} chars]"


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, Mapping):
                if b.get("type") == "text":
                    parts.append(str(b.get("text") or ""))
                elif b.get("type") == "tool_result":
                    parts.append(_text_of(b.get("content")))
            else:
                parts.append(str(b))
        return "\n".join(p for p in parts if p)
    return str(content)


def _lines(prefix: str, text: str) -> List[str]:
    rows = text.split("\n") or [""]
    return [prefix + rows[0]] + [f"{prefix.split(']')[0]}]   {r}" for r in rows[1:]]


def render_messages(messages: List[Mapping[str, Any]], caps: Mapping[str, int]) -> str:
    names: Dict[str, str] = {}
    out: List[str] = []
    step = 0
    seen_prompt = False
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "assistant":
            step += 1
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
            for b in blocks:
                if not isinstance(b, Mapping):
                    continue
                if b.get("type") == "text" and str(b.get("text") or "").strip():
                    out += _lines(f"[step {step}] AGENT: ", clip(b.get("text"), caps["agent"]))
                elif b.get("type") == "tool_use":
                    names[str(b.get("id"))] = str(b.get("name"))
                    args = json.dumps(b.get("input") or {}, ensure_ascii=False)
                    out += _lines(f"[step {step}] TOOL_CALL ", f"{b.get('name')}({clip(args, caps['args'])})")
        elif role == "user":
            if isinstance(content, list) and any(isinstance(b, Mapping) and b.get("type") == "tool_result" for b in content):
                for b in content:
                    if isinstance(b, Mapping) and b.get("type") == "tool_result":
                        name = names.get(str(b.get("tool_use_id")), "tool")
                        tag = "TOOL_ERROR" if b.get("is_error") else "TOOL_RESULT"
                        out += _lines(f"[step {step}] {tag} ", f"{name}: {clip(_text_of(b.get('content')), caps['result'])}")
            elif not seen_prompt:
                seen_prompt = True  # the task prompt is rendered in its own section
            else:
                out += _lines(f"[step {step}] USER: ", clip(_text_of(content), caps["agent"]))
    return "\n".join(out)


def _first_prompt(messages: List[Mapping[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user":
            return _text_of(m.get("content"))
    return ""


def render_verifier(outcome: Mapping[str, Any]) -> str:
    v = outcome.get("verifier") or {}
    rows = []
    for i, c in enumerate(v.get("checks") or []):
        verdict = "PASS" if c.get("passed") else "FAIL"
        detail = f": {clip(c.get('detail'), 400)}" if c.get("detail") else ""
        rows.append(f"check {i} [{verdict}] {c.get('name') or c.get('kind')}{detail}")
    rows.append(f"valid_output: {v.get('valid_output')} | no_submission: {v.get('no_submission')} | "
                f"fail_class: {v.get('fail_class')}")
    return "\n".join(rows)


def render_trial(view: TrialView, detail: bool = False) -> str:
    caps = DETAIL_CAPS if detail else CAPS
    o, rec = view.outcome, view.record or {}
    msgs = list(rec.get("transcript") or [])
    steps = rec.get("steps") or {}
    v = o.get("verifier") or {}
    checks = v.get("checks") or []
    passed = sum(1 for c in checks if c.get("passed"))
    header = [
        f"TASK: {view.task_id} | trial: {view.trial}",
        f"STATUS: {view.status} | termination: {rec.get('termination_reason') or '-'} | "
        f"steps: model_calls={steps.get('model_calls')} tool_calls={steps.get('tool_calls')} "
        f"tool_errors={steps.get('tool_errors')} | policy_tokens: {o.get('tokens')} | "
        f"missing: {o.get('missing')}",
        f"SCORE: reward {float(o.get('reward') or 0.0):.3f} = {passed}/{len(checks)} checks passed",
    ]
    if o.get("error"):
        header.append(f"ERROR: {clip(o.get('error'), 600)}")
    body = [*header, "", "=== TASK PROMPT ===", clip(_first_prompt(msgs), caps["prompt"]),
            "", "=== TRAJECTORY ===",
            render_messages(msgs, caps) if msgs else "(no transcript recorded)",
            "", "=== FINAL ANSWER ===", clip(o.get("answer"), caps["final"])]
    text = "\n".join(body)
    if len(text) > MAX_CHARS:
        head, tail = int(MAX_CHARS * 0.6), int(MAX_CHARS * 0.4)
        text = text[:head] + f"\n...[TRUNCATED {len(text) - head - tail} chars]...\n" + text[-tail:]
    text = text + "\n\n" + VERIFIER_HEADER + "\n" + render_verifier(o)
    if view.workspace:
        text = text.replace(view.workspace, "<workspace>")
        parent = str(Path(view.workspace).parent)
        text = text.replace(parent, "<trial>")
    return text


def task_row(task_id: str, view: Optional[TrialView], tr: Optional[TaskResult]) -> str:
    """One line per trace in the analyst / proposer task tables."""
    mean = f"{tr.mean:.3f}" if tr is not None else "-"
    if view is None:
        return f"{task_id} | mean over trials {mean} | (no trace)"
    checks = view.checks
    failed = [i for i, c in enumerate(checks) if not c.get("passed")]
    steps = (view.record or {}).get("steps") or {}
    return (f"{task_id} | trial {view.trial} | checks {len(checks) - len(failed)}/{len(checks)} "
            f"(mean over trials {mean}) | status={view.status} | "
            f"steps={steps.get('model_calls')} | tokens={view.outcome.get('tokens')} | "
            f"failed_checks={failed[:25]}")


def cut_steps(rendered: str, from_step: Optional[int], to_step: Optional[int], cap: int) -> str:
    """Keep the header, step lines in [from_step, to_step] and the VERIFIER section."""
    if from_step is None and to_step is None:
        out = rendered
    else:
        lo, hi = int(from_step or 0), int(to_step if to_step is not None else 10**9)
        keep: List[str] = []
        in_verifier = False
        for line in rendered.split("\n"):
            if line.startswith("=== ") and "VERIFIER" in line:
                in_verifier = True
            m = _STEP.match(line)
            if in_verifier or m is None or lo <= int(m.group(1)) <= hi:
                keep.append(line)
        out = "\n".join(keep)
    if len(out) > cap:
        return out[:cap] + f"\n...[capped at {cap} chars; use from_step/to_step]"
    return out
