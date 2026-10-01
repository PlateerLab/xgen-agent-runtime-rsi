"""frontier.json — the incumbent, S★ and the incumbent trajectory of one evolution run.

Layout (reference ``loop.Run`` frontier, plus the manifest version of the incumbent)::

    {"name": ..., "incumbent": {"t", "commit", "harness_tree", "harness_version", "job",
                                "S", "C", "extra", "variant"?},
     "S_star": ..., "trajectory": [{"t", "S", "C", "commit", "job"}, ...], "config": {...}}

``trajectory[τ]`` is Ŝ(H_τ), the incumbent at the start of round τ (rejected rounds repeat the
incumbent score) — exactly the sequence σ_t reads (R-Eq13). A round t is settled once the
trajectory has an entry for t + 1.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/loop.py``: the frontier.json
layout), Copyright 2026 The rrsi Authors / Google LLC, Apache License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from xgen_rsi.rsi_math import EvalResult, update_s_star


class FrontierError(RuntimeError):
    pass


def load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FrontierError(f"no {path}; run baseline first")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FrontierError(f"{path}: not a JSON object")
    return data


def save(path: Path, fr: Mapping[str, Any]) -> None:
    """Atomic write (temp file + rename) so a crash never leaves half a frontier."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(fr), ensure_ascii=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def incumbent_entry(t: int, commit: str, harness_tree: str, harness_version: str, job: str,
                    ev: EvalResult, variant: Optional[str] = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"t": t, "commit": commit, "harness_tree": harness_tree,
                             "harness_version": harness_version, "job": job, "S": ev.S,
                             "C": ev.C, "extra": dict(ev.extra)}
    if variant is not None:
        entry["variant"] = variant
    return entry


def seed(name: str, incumbent: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    """The frontier after the H_0 baseline: S★ = Ŝ(H_0), trajectory = [H_0]."""
    inc = dict(incumbent)
    return {"name": name, "incumbent": inc, "S_star": inc["S"],
            "trajectory": [traj_entry(0, inc)], "config": dict(config)}


def traj_entry(t: int, incumbent: Mapping[str, Any]) -> Dict[str, Any]:
    entry = {"t": t, "S": incumbent["S"], "C": incumbent["C"], "commit": incumbent["commit"],
             "job": incumbent["job"]}
    if incumbent.get("harness_version"):
        entry["harness_version"] = incumbent["harness_version"]
    return entry


def settle(fr: Mapping[str, Any], t: int, new_incumbent: Optional[Mapping[str, Any]],
           S_star: Optional[float] = None) -> Dict[str, Any]:
    """Frontier after round t: H_{t+1} = winner (or H_t), S★ ← max(S★, Ŝ_{t+1}), trajectory
    truncated to τ ≤ t plus the t + 1 entry. ``S_star`` overrides the stored S★ (re-adjudication
    recomputes it from the trajectory prefix)."""
    out = dict(fr)
    base_star = float(fr["S_star"] if S_star is None else S_star)
    inc = dict(new_incumbent) if new_incumbent is not None else dict(fr["incumbent"])
    out["incumbent"] = inc
    out["S_star"] = update_s_star(base_star, float(inc["S"]))
    out["trajectory"] = [x for x in fr["trajectory"] if int(x["t"]) <= t] + [traj_entry(t + 1, inc)]
    return out


def scores(fr: Mapping[str, Any]) -> List[float]:
    """traj[τ] = Ŝ(H_τ) for σ_t (R-Eq13)."""
    return [float(x["S"]) for x in sorted(fr["trajectory"], key=lambda x: int(x["t"]))]


def settled_rounds(path: Path) -> int:
    """Number of settled rounds (−1 before the baseline)."""
    if not path.exists():
        return -1
    fr = json.loads(path.read_text(encoding="utf-8"))
    return len(fr.get("trajectory") or []) - 1
