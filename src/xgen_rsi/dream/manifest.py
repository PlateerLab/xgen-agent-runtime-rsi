"""``live_cycle_manifest.json`` sidecars (Appendix B file name) and their conversions.

Layout of a trace pool: ``<trace_pool>/iter####/live_cycle_manifest.json`` for
completed live cycles and ``<trace_pool>/_current/live_cycle_manifest.json``
for the cycle about to run (written by ``DreamCycle`` with the promoted
policy's baked β and planned grid; ``best_score`` stays None until the live
runner finalizes it, e.g. with ``explore.grid.summarize_live``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from xgen_rsi.explore.api import LiveCycleManifest
from xgen_rsi.rsi_math.types import LiveCycle

LIVE_MANIFEST_NAME = "live_cycle_manifest.json"


def write_live_manifest(directory: str | os.PathLike[str], manifest: LiveCycleManifest) -> Path:
    """Write ``<directory>/live_cycle_manifest.json`` atomically; return its path."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    path = d / LIVE_MANIFEST_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest.to_json(), indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_live_manifest(path: str | os.PathLike[str]) -> LiveCycleManifest:
    return LiveCycleManifest.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def load_live_manifests(trace_pool: str | os.PathLike[str], *,
                        include_current: bool = False) -> list[LiveCycleManifest]:
    """Completed live manifests under ``trace_pool/iter*/`` (oldest first), plus ``_current``
    when ``include_current`` and it already carries a final best score."""
    root = Path(trace_pool)
    out = [read_live_manifest(p) for p in sorted(root.glob(f"iter*/{LIVE_MANIFEST_NAME}"))]
    cur = root / "_current" / LIVE_MANIFEST_NAME
    if include_current and cur.exists():
        m = read_live_manifest(cur)
        if m.best_score is not None:
            out.append(m)
    return sorted(out, key=lambda m: m.iteration)


def live_cycles(manifests: Iterable[LiveCycleManifest]) -> list[LiveCycle]:
    """``rsi_math.LiveCycle`` records for the β rule (manifests without a best score skipped)."""
    return [LiveCycle(iteration=m.iteration, best_score=float(m.best_score), beta=float(m.beta))
            for m in sorted(manifests, key=lambda m: m.iteration) if m.best_score is not None]


__all__ = ["LIVE_MANIFEST_NAME", "live_cycles", "load_live_manifests", "read_live_manifest",
           "write_live_manifest"]
