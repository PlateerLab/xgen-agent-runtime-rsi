"""Candidate drafts of one consolidation round, with plain files (no git).

A round drafts ``m`` candidates from the current harness. Each candidate needs its own copy of that harness (variants
never see each other's edits and are replayed concurrently), and the critic reads each candidate's change as a unified
diff. The consolidation used a throw-away git repository for exactly this; the servers that run it (XGEN workflow) have
no git binary, so the same is done here with directory copies and :mod:`difflib`.

Layout under the round's temporary directory::

    <root>/base/harness/        the current harness (read only by convention)
    <root>/wt/<variant>/harness/ one copy per candidate

The diff keeps git's shape (``diff --git``, ``--- a/…``, ``+++ b/…``, new and deleted files) because the critic's
deterministic precheck reads ``+++`` paths and ``+`` lines (:func:`xgen_rsi.evolve.critic.added_text`).
"""

from __future__ import annotations

import difflib
import shutil
from pathlib import Path
from typing import Dict, List, Optional

HARNESS = "harness"
_SKIP_DIRS = {"__pycache__"}
_SKIP_SUFFIXES = (".pyc", ".pyo")
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "__init__.py")


class DraftSpace:
    """The current harness and one independent copy per candidate."""

    def __init__(self, root: Path | str, incumbent: Path | str) -> None:
        self.root = Path(root)
        self.base = self.root / "base" / HARNESS
        if self.base.exists():
            shutil.rmtree(self.base)
        self.base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(Path(incumbent), self.base, ignore=_COPY_IGNORE)

    def new_draft(self, name: str) -> Path:
        """A fresh copy of the current harness for candidate ``name``; returns its harness directory."""
        dest = self.root / "wt" / name / HARNESS
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.base, dest)
        return dest

    def diff(self, draft: Path | str) -> str:
        """Unified diff from the current harness to ``draft`` (new and deleted files included)."""
        return diff_trees(self.base, Path(draft), prefix=HARNESS)


def _files(root: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts) or path.suffix in _SKIP_SUFFIXES:
            continue
        out[rel.as_posix()] = path
    return out


def _lines(path: Optional[Path]) -> List[str]:
    if path is None:
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def diff_trees(a: Path | str, b: Path | str, *, prefix: str = "") -> str:
    """Unified diff between two directories, in git's shape, paths shown as ``<prefix>/<relative path>``."""
    left, right = _files(Path(a)), _files(Path(b))
    out: List[str] = []
    for rel in sorted(set(left) | set(right)):
        old, new = left.get(rel), right.get(rel)
        old_lines, new_lines = _lines(old), _lines(new)
        if old is not None and new is not None and old_lines == new_lines:
            continue
        shown = f"{prefix}/{rel}" if prefix else rel
        out.append(f"diff --git a/{shown} b/{shown}")
        if old is None:
            out.append("new file mode 100644")
        elif new is None:
            out.append("deleted file mode 100644")
        body = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{shown}" if old is not None else "/dev/null",
            tofile=f"b/{shown}" if new is not None else "/dev/null",
            lineterm="",
        ))
        if not body:  # both empty: a new or deleted empty file
            body = [f"--- {'a/' + shown if old is not None else '/dev/null'}",
                    f"+++ {'b/' + shown if new is not None else '/dev/null'}"]
        out.extend(body)
    return "\n".join(out) + ("\n" if out else "")


__all__ = ["DraftSpace", "diff_trees"]
