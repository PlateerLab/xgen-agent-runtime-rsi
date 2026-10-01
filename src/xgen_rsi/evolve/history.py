"""The edit history 𝓛_t (R-Eq10) as JSONL, and its summaries for the proposer.

One record per EDIT (05 §3.2): a candidate bundling n edits writes n records that share
(ΔS, ΔC, a, outcome). Candidates dropped before a measurement (critic_reject, smoke_fail,
eval_invalid, no_proposal, not_evaluated) carry ΔS = ΔC = None and never enter 𝒯_t, g_t or N_t.

Every summary converts the rows to :class:`~xgen_rsi.rsi_math.EditRecord` and calls the formula
library (``tried``, ``recent_yield``, ``prune_set``, ``accepted_counts``, ``edit_records``) —
nothing here re-implements a formula.

Storage: written with ``ensure_ascii=True`` and read by splitting on ``"\\n"`` only. The reference
writes ``ensure_ascii=False`` and reads with ``str.splitlines()``, which also splits on U+2028,
U+2029, U+0085 … inside a hypothesis string and corrupts the file.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/history.py``: record layout and
the history rendering for the proposer), Copyright 2026 The rrsi Authors / Google LLC, Apache
License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from xgen_rsi.rsi_math import (
    EditRecord,
    accepted_counts,
    edit_records,
    prune_set,
    recent_yield,
    tried,
)

RENDER_KEYS = ("t", "variant", "edit_id", "component", "hypothesis", "targets_mode", "delta_S",
               "delta_C", "accepted", "outcome", "bundle", "detail", "early_stopped",
               "delta_C_partial")
MAX_UNMEASURED_RENDERED = 4


def dumps_line(rec: Mapping[str, Any]) -> str:
    """One JSONL line (ASCII only, so no Unicode line separator can appear inside it)."""
    return json.dumps(dict(rec), ensure_ascii=True) + "\n"


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Rows of a JSONL file split on ``\\n`` only (never ``splitlines``)."""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    lines = [ln for ln in path.read_text(encoding="utf-8").split("\n") if ln.strip()]
    for i, line in enumerate(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                break  # 쓰다 끊긴 마지막 줄 — 없는 것으로 본다(재개가 다시 쓴다)
            raise
    return out


class History:
    """𝓛_t stored at ``path`` (``<run>/history.jsonl``)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------ storage --
    def records(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.path)

    def edit_records(self, before_t: Optional[int] = None) -> List[EditRecord]:
        """Rows as formula-library records (the H_0 baseline row has no component). ``before_t`` keeps
        only rounds t_i < before_t — a resumed round must not see its own partial records."""
        return [EditRecord.from_json(r) for r in self.records()
                if "t" in r and (before_t is None or int(r["t"]) < before_t)]

    def append(self, rec: Mapping[str, Any]) -> None:
        row = dict(rec)
        row.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(dumps_line(row))

    def append_candidate(self, t: int, variant: str, edits: Sequence[Mapping[str, Any]],
                         outcome: str, delta_S: Optional[float], delta_C: Optional[float],
                         accepted: bool, S: Optional[float], C: Optional[float],
                         diff: Optional[str], detail: str = "", *, early_stopped: bool = False,
                         delta_C_partial: bool = False) -> List[Dict[str, Any]]:
        """Write the per-edit records of one candidate H' (R-Eq10) and return them."""
        recs = edit_records(t, variant, edits, outcome, delta_S, delta_C, accepted, S, C,
                            detail, early_stopped=early_stopped)
        rows: List[Dict[str, Any]] = []
        sources = list(edits) or [{}]
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        for rec, src in zip(recs, sources):
            row = rec.to_json()
            row["diff"] = diff
            if src.get("declared_component") and src.get("declared_component") != row["component"]:
                row["declared_component"] = src.get("declared_component")
            if src.get("addresses"):
                row["addresses"] = list(src.get("addresses") or [])
            if delta_C_partial:
                row["delta_C_partial"] = True
            row.setdefault("ts", stamp)
            rows.append(row)
        # 후보 하나의 기록은 한꺼번에(원자적으로) — 중간에 죽어 일부만 남으면 has() 가 참이 되어 나머지가 영영 빠진다
        self.rewrite(self.records() + rows)
        return rows

    def replace_round(self, t: int, keep: Optional[Callable[[Dict[str, Any]], bool]] = None) -> None:
        """Drop the per-edit records of round t (a re-adjudication rewrites them)."""
        self.rewrite([r for r in self.records()
                      if not (r.get("t") == t and r.get("edit_id")) or (keep is not None and keep(r))])

    def set_baseline(self, rec: Mapping[str, Any]) -> None:
        """Replace the H_0 baseline record (outcome BASELINE) with ``rec``."""
        rows = [r for r in self.records() if r.get("outcome") != "BASELINE"]
        row = dict(rec)
        row.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.rewrite([row] + rows)

    def rewrite(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Atomically replace the whole file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("".join(dumps_line(r) for r in rows), encoding="utf-8")
        os.replace(tmp, self.path)

    def has(self, t: int, variant: str) -> bool:
        return any(r.get("t") == t and r.get("variant") == variant for r in self.records())

    # ---------------------------------------------------------- summaries --
    def tried(self, before_t: Optional[int] = None) -> set[str]:
        """𝒯_t (R-Eq11)."""
        return tried(self.edit_records(before_t))

    def recent_yield(self, t: int, n_prune: int) -> Dict[str, float]:
        """g_t(ℓ) (R-Eq11)."""
        return recent_yield(self.edit_records(before_t=t), t, n_prune)

    def prune_set(self, t: int, n_prune: int) -> List[Dict[str, Any]]:
        """𝓑_t (R-Eq14) with the accepted machinery per component, as JSON (records of rounds < t)."""
        return [p.to_json() for p in prune_set(self.edit_records(before_t=t), t, n_prune)]

    def accepted_counts(self, before_t: Optional[int] = None) -> Dict[str, int]:
        """N_t(ℓ) (R-Eq16); ``before_t=t`` for re-adjudication."""
        return accepted_counts(self.edit_records(), before_t=before_t)

    # ------------------------------------------------------- for prompts --
    def render(self, n: int = 40) -> List[Dict[str, Any]]:
        """Compact view of the most recent records for the proposer. Measured outcomes dominate:
        gate failures without a measurement keep only their most recent few, because a wall of
        aborts is a feedback loop, not evidence (reference ``History.render``)."""
        kept: List[Dict[str, Any]] = []
        unmeasured = 0
        for r in reversed(self.records()):
            if r.get("delta_S") is None:
                unmeasured += 1
                if unmeasured > MAX_UNMEASURED_RENDERED:
                    continue
            kept.append(r)
            if len(kept) >= n:
                break
        out = []
        for r in reversed(kept):
            row = {k: r.get(k) for k in RENDER_KEYS if r.get(k) is not None}
            if not row.get("early_stopped"):
                row.pop("early_stopped", None)
            out.append(row)
        return out
