"""Trace digester: a READ-ONLY subagent that inspects one rendered trajectory for the analyst, so
the analyst's context carries structured conclusions, never whole traces.

Tools, jailed to the round's rendered-trace directory: ``read_file``, ``glob``, ``grep``. There is
no shell (the reference also offered an allow-listed bash; a read-only regex search covers the
same ground without a subprocess). The digest is hard-capped at ``DIGEST_MAX_CHARS``.
Lenses: failure / capability_gap / success.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/digester.py``: code structure,
the digester prompt and the digest schemas), Copyright 2026 The rrsi Authors / Google LLC, Apache
License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_TURNS = 15
DIGEST_MAX_CHARS = 6000
TOOL_OUT_CAP = 25_000
READ_DEFAULT_LIMIT = 400

SCHEMAS = {
    "failure": """{"task_id": "...", "lens": "failure",
 "blocker": "one sentence: what mechanism lost the checks",
 "narrative": "2-5 sentences: how the failure unfolded, concrete",
 "evidence": [{"where": "step 42", "quote": "short exact quote"}],
 "verifier_evidence": "what the verifier itself reported as failed",
 "capability_note": "optional: anything the agent tried but could not do",
 "needed_instead": "1-2 sentences: what the successful path required"}""",
    "capability_gap": """{"task_id": "...", "lens": "capability_gap",
 "wanted": "what the agent was trying to accomplish",
 "why_couldnt": "what stopped it (tool limits, missing information, harness mechanics, dead ends)",
 "evidence": [{"where": "step 12", "quote": "..."}],
 "workaround_seen": "optional: any partial workaround it attempted"}""",
    "success": """{"task_id": "...", "lens": "success",
 "habits": [{"habit": "reusable behavior that made this run clean",
             "where_shown": "step range"}],
 "risk_if_removed": "which habit is load-bearing and what breaks without it"}""",
}

SYSTEM_TMPL = """You are a trajectory digester: a read-only investigator that
inspects ONE agent trajectory in depth and returns a compact structured
digest. Another agent (the batch analyst) will rely on your digest without
reading the trace itself, so be precise and evidence-anchored.

{domain_brief}

The rendered trajectory files live in your working directory as <task_id>.txt.

Your lens for this assignment: {lens}
{focus}

You interact via STRICT JSON, one action per turn:
  {{"action": "read_file", "path": "<task_id>.txt", "offset": 120, "limit": 80}}
      (line-based; omit offset/limit to read from the start)
  {{"action": "glob", "pattern": "*.txt"}}
  {{"action": "grep", "pattern": "regex", "path": "<task_id>.txt", "max_hits": 40}}
      (returns matching lines with line numbers)
  {{"action": "return", "digest": {{...}}}}

Investigate efficiently: read the VERIFIER section first (what actually
failed), grep for anchors (step numbers, tool names, file names, key values,
error strings), then read the relevant slices. Do not read whole files top
to bottom.

Finish with action "return". The digest MUST follow this schema and MUST be
under {cap} characters total:
{schema}

Rules: quotes must be exact and short; every claim needs a "where"; do not
speculate beyond what the trace shows; no blame attribution to "model vs
harness"; descriptions in English."""


def _safe(root: Path, rel: str) -> Path:
    base = root.resolve()
    p = (base / str(rel)).resolve()
    if p != base and not str(p).startswith(str(base) + os.sep):
        raise ValueError("path escapes the traces dir")
    return p


def _read_file(root: Path, rel: str, offset: Any, limit: Any) -> str:
    p = _safe(root, rel)
    if not p.is_file():
        return f"ERROR: no such file {rel}"
    lines = p.read_text(encoding="utf-8").split("\n")
    lo = max(0, int(offset or 1) - 1)
    hi = lo + int(limit or READ_DEFAULT_LIMIT)
    body = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(lines[lo:hi], start=lo))
    return body + (f"\n...[file has {len(lines)} lines]" if hi < len(lines) else "")


def _glob(root: Path, pattern: str) -> str:
    base = root.resolve()
    hits = []
    for p in sorted(base.glob(pattern or "*")):
        rp = p.resolve()
        if rp.is_file() and str(rp).startswith(str(base) + os.sep):
            hits.append(rp.relative_to(base).as_posix())
    return "\n".join(hits) or "(no matches)"


def _grep(root: Path, pattern: str, rel: str, max_hits: int) -> str:
    p = _safe(root, rel)
    if not p.is_file():
        return f"ERROR: no such file {rel}"
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: bad regex: {exc}"
    out: List[str] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
        if rx.search(line):
            out.append(f"{i}: {line[:400]}")
            if len(out) >= max_hits:
                out.append("...[max hits reached]")
                break
    return "\n".join(out) if out else "(no matches)"


def digest_task(llm: Any, traces_dir: Path, task_id: str, lens: str, domain_brief: str,
                questions: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run one digester over ``<traces_dir>/<task_id>.txt``; returns the digest dict."""
    lens = lens if lens in SCHEMAS else "failure"
    focus = ""
    if questions:
        focus = "Specific questions from the analyst you MUST answer:\n" + \
            "\n".join(f"- {q}" for q in list(questions)[:5])
    system = SYSTEM_TMPL.format(lens=lens, focus=focus, cap=DIGEST_MAX_CHARS,
                                schema=SCHEMAS[lens], domain_brief=domain_brief)
    transcript = f"Assigned trace: {task_id}.txt\nFirst action:"
    for _ in range(MAX_TURNS):
        raw = llm.generate(transcript, system=system, json_only=True)
        try:
            act = json.loads(raw)
        except json.JSONDecodeError:
            transcript += f"\n[you] {str(raw)[:300]}\n[result] ERROR: invalid JSON"
            continue
        if isinstance(act, list):
            act = next((x for x in act if isinstance(x, dict)), None)
        if not isinstance(act, dict):
            transcript += ("\n[you] (non-object)\n[result] ERROR: reply with "
                           "EXACTLY ONE JSON action object, not a list or value")
            continue
        a = act.get("action")
        if a == "return":
            digest = act.get("digest") or {}
            if not isinstance(digest, dict):
                transcript += "\n[you] return\n[result] ERROR: digest must be a JSON object"
                continue
            blob = json.dumps(digest, ensure_ascii=False)
            if len(blob) > DIGEST_MAX_CHARS:
                transcript += (f"\n[you] return ({len(blob)} chars)\n[result] "
                               f"ERROR: digest is {len(blob)} chars, cap is "
                               f"{DIGEST_MAX_CHARS}. Shorten and return again.")
                continue
            digest.setdefault("task_id", task_id)
            digest.setdefault("lens", lens)
            return digest
        try:
            if a == "read_file":
                result = _read_file(traces_dir, act["path"], act.get("offset"), act.get("limit"))
            elif a == "glob":
                result = _glob(traces_dir, str(act.get("pattern", "*")))
            elif a == "grep":
                result = _grep(traces_dir, str(act.get("pattern", "")), str(act.get("path", "")),
                               int(act.get("max_hits", 40)))
            else:
                result = f"ERROR: unknown action {a} (read_file, glob, grep, return)"
        except Exception as exc:  # noqa: BLE001 — a bad action is feedback, not a crash
            result = f"ERROR: {exc}"
        transcript += f"\n[you] {json.dumps(act)[:600]}\n[result] {str(result)[:TOOL_OUT_CAP]}"
    return {"task_id": task_id, "lens": lens, "error": "digester hit max turns without returning"}
