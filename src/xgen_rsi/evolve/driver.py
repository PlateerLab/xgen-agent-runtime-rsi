"""Sequential driver: baseline + calibration if missing, then rounds start..T-1 (in process).

A round is settled once the frontier trajectory has an entry for t + 1. ``touch <run>/STOP``
stops the driver before the next round. A round that raises or does not settle counts as an
infrastructure failure and is retried (rounds are resume-safe); ``MAX_CONSECUTIVE_INFRA``
consecutive failures stop the driver so a broken environment cannot burn the whole budget.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/driver.py``), Copyright 2026 The
rrsi Authors / Google LLC, Apache License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import traceback
from typing import Any, Dict, List, Optional, Sequence

from xgen_rsi.evolve import frontier as F

MAX_CONSECUTIVE_INFRA = 3
DEFAULT_CALIBRATION_JOBS = ("base", "base_r2")


def drive(run: Any, T: Optional[int] = None, start: int = 0,
          calibration_jobs: Sequence[str] = DEFAULT_CALIBRATION_JOBS,
          max_consecutive_infra: int = MAX_CONSECUTIVE_INFRA) -> Dict[str, Any]:
    """Drive ``run`` (an :class:`~xgen_rsi.evolve.round.EvolveRun`) up to round T.

    Returns ``{"status": "done" | "stopped" | "infra_failures", "settled": n, "failures": [...]}``.
    """
    horizon = run.cfg.T if T is None else int(T)
    failures: List[Dict[str, Any]] = []
    if not run.frontier_path.exists():
        run.log("driver: baseline")
        run.baseline(calibration_jobs[0] if calibration_jobs else "base")
    if run.cfg.delta is None and not run.calibration_path.exists():
        run.log(f"driver: calibrate over {list(calibration_jobs)}")
        run.calibrate(list(calibration_jobs))

    def result(status: str) -> Dict[str, Any]:
        return {"status": status, "settled": F.settled_rounds(run.frontier_path), "failures": failures}

    infra = 0
    t = start
    while t < horizon:
        if run.stop_path.exists():
            run.log("driver: STOP present; exiting")
            return result("stopped")
        if F.settled_rounds(run.frontier_path) >= t + 1:
            t += 1
            continue
        run.log(f"driver: === round {t}")
        error = ""
        try:
            run.round(t)
        except Exception as exc:  # noqa: BLE001 — any round failure counts as infrastructure
            error = f"{type(exc).__name__}: {exc}"
            with open(run.logs / f"r{t}.error.log", "a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc() + "\n")
        if error or F.settled_rounds(run.frontier_path) < t + 1:
            infra += 1
            failures.append({"t": t, "error": error or "round did not settle"})
            run.log(f"driver: round {t} did not settle ({error or 'no trajectory entry'}; "
                    f"{infra}/{max_consecutive_infra})")
            if infra >= max_consecutive_infra:
                run.log("driver: too many consecutive failures; stopping")
                return result("infra_failures")
            continue  # resume-safe: the same round is retried
        infra = 0
        inc = run.frontier()["incumbent"]
        run.log(f"driver:   incumbent t={inc['t']} {str(inc['commit'])[:12]} S={inc['S']:.4f}")
        t += 1
    return result("done")
