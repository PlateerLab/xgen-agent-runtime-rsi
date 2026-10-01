"""Edit addresses touched by a candidate, and the component tag ℓ of each declared edit.

A harness is a manifest of typed components (``harness/spec.py``), so what a candidate changed is
read from the two manifests, not guessed from a text diff:

* param leaves that differ  → ``<component_id>.params.<key>[.<key>…]``
* referenced files added / removed / edited → ``<component_id>.files.<path>``
* components added, removed, re-implemented or toggled → ``<component_id>`` (op says which)

The kind of the touched component is the evidence for ℓ (decision D10). Each declared edit is
normalized with :func:`xgen_rsi.rsi_math.normalize_component`; it receives the *address diff* (one
``+<address>`` line per touched address) instead of the raw text diff, so its regex fallbacks read
component addresses rather than prose — a prompt block that merely mentions "skills/" or "memory"
cannot make an edit look structural. A new component counts with its declared kind.

Platform-owned manifest fields (``locked``, ``enabled_kinds``, ``exploration_policy`` — the latter
belongs to the Dream loop, design 30 P5), locked param addresses and files that no component
references (inert machinery) are reported as violations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from xgen_rsi.harness.spec import ComponentSpec, HarnessManifest, HarnessSpecError
from xgen_rsi.rsi_math import K, Signals, normalize_component

MANIFEST = "manifest.json"
PLATFORM_FIELDS = ("locked", "enabled_kinds", "exploration_policy")


@dataclass(frozen=True)
class Touch:
    """One touched edit address."""

    address: str
    component_id: Optional[str]
    kind: Optional[str]
    op: str


@dataclass(frozen=True)
class TouchSet:
    touches: Tuple[Touch, ...]
    violations: Tuple[str, ...] = ()
    orphans: Tuple[str, ...] = ()

    @property
    def addresses(self) -> List[str]:
        return _unique(t.address for t in self.touches)

    @property
    def kinds(self) -> List[str]:
        """comp(H') from the manifests, in 𝒦 order."""
        return self.kinds_for(None)

    def kinds_for(self, addresses: Optional[Iterable[str]]) -> List[str]:
        wanted = None if addresses is None else set(addresses)
        found = {t.kind for t in self.touches if t.kind in K and (wanted is None or t.address in wanted)}
        return [k for k in K if k in found]

    def address_diff(self, addresses: Optional[Iterable[str]] = None) -> str:
        wanted = None if addresses is None else set(addresses)
        return "\n".join(f"+{a}" for a in self.addresses if wanted is None or a in wanted)

    def signals(self) -> Signals:
        """Address regexes per touched kind (𝒦 order) — the classify fallback of
        ``normalize_component`` then always lands on a kind the candidate really touched."""
        by_kind: Dict[str, List[str]] = {}
        for t in self.touches:
            if t.kind in K and t.component_id:
                by_kind.setdefault(t.kind, []).append(rf"(?m)^\+{re.escape(t.component_id)}(?:\.|$)")
        return [(k, tuple(_unique(by_kind[k]))) for k in K if k in by_kind]

    def to_json(self) -> Dict[str, Any]:
        return {"touches": [t.__dict__ for t in self.touches], "kinds": self.kinds,
                "violations": list(self.violations), "orphans": list(self.orphans)}


def touched(inc: HarnessManifest, cand: HarnessManifest) -> TouchSet:
    """Touched addresses of ``cand`` relative to ``inc`` (both loaded with a ``root``)."""
    touches: List[Touch] = []
    violations: List[str] = []
    inc_by = {c.id: c for c in inc.components}
    cand_by = {c.id: c for c in cand.components}

    def add_component(c: ComponentSpec) -> None:
        touches.append(Touch(c.id, c.id, c.kind, "add_component"))
        for f in c.files:
            touches.append(Touch(f"{c.id}.files.{f}", c.id, c.kind, "add_file"))
        _check_locked(inc, c.id, c.params, violations)

    def remove_component(c: ComponentSpec) -> None:
        touches.append(Touch(c.id, c.id, c.kind, "remove_component"))
        _check_locked(inc, c.id, c.params, violations)

    for c in cand.components:
        old = inc_by.get(c.id)
        if old is None:
            add_component(c)
            continue
        if old.kind != c.kind or old.impl != c.impl:
            remove_component(old)
            add_component(c)
            continue
        if old.enabled != c.enabled:
            touches.append(Touch(c.id, c.id, c.kind, "enable" if c.enabled else "disable"))
        for addr in _param_diffs(f"{c.id}.params", old.params, c.params):
            touches.append(Touch(addr, c.id, c.kind, "set"))
            try:
                inc.check_unlocked(addr)
            except HarnessSpecError as exc:
                violations.append(str(exc))
        old_files, new_files = set(old.files), set(c.files)
        for f in c.files:
            if f not in old_files:
                touches.append(Touch(f"{c.id}.files.{f}", c.id, c.kind, "add_file"))
            elif _read(cand, f) != _read(inc, f):
                touches.append(Touch(f"{c.id}.files.{f}", c.id, c.kind, "edit_file"))
        for f in old.files:
            if f not in new_files:
                touches.append(Touch(f"{c.id}.files.{f}", c.id, c.kind, "remove_file"))
    for c in inc.components:
        if c.id not in cand_by:
            remove_component(c)

    for name in PLATFORM_FIELDS:
        a, b = _plain(getattr(inc, name)), _plain(getattr(cand, name))
        if a != b:
            violations.append(f"manifest field {name!r} is owned by the platform and cannot change")
    for name in ("name", "description"):
        if getattr(inc, name) != getattr(cand, name):
            touches.append(Touch(f"manifest.{name}", None, None, "meta"))

    for c in cand.components:
        for f in c.files:
            if _read(cand, f) is None:
                violations.append(f"component {c.id!r} lists file {f!r} that does not exist")
    return TouchSet(tuple(touches), tuple(_unique(violations)), tuple(_orphans(inc, cand)))


def normalize_edits(edits: Sequence[Mapping[str, Any]], ts: TouchSet,
                    signals: Optional[Signals] = None) -> List[Dict[str, Any]]:
    """Each declared edit with ``component`` replaced by ``normalize_component`` (05 §2.11, D10).

    An edit may name the addresses it touches (``addresses``); its evidence is then those
    addresses only, otherwise the whole candidate. ``declared_component`` keeps the original tag.
    """
    sig = list(signals or []) + list(ts.signals())
    out: List[Dict[str, Any]] = []
    for e in edits:
        row = dict(e)
        declared = str(e.get("component") or "").strip().lower()
        own = [a for a in (e.get("addresses") or []) if isinstance(a, str) and a in ts.addresses]
        scope: Optional[List[str]] = own or None
        row["component"] = normalize_component(declared, ts.kinds_for(scope),
                                               ts.address_diff(scope), sig)
        row["declared_component"] = declared
        if own:
            row["addresses"] = own
        out.append(row)
    return out


# --------------------------------------------------------------------- internals --


def _param_diffs(prefix: str, old: Any, new: Any) -> Iterator[str]:
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        for key in sorted(set(old) | set(new), key=str):
            if key not in old or key not in new:
                yield f"{prefix}.{key}"
            else:
                yield from _param_diffs(f"{prefix}.{key}", old[key], new[key])
    elif old != new or type(old) is not type(new):
        yield prefix


def _leaves(prefix: str, node: Any) -> Iterator[str]:
    if isinstance(node, Mapping) and node:
        for key, value in node.items():
            yield from _leaves(f"{prefix}.{key}", value)
    else:
        yield prefix


def _check_locked(inc: HarnessManifest, cid: str, params: Mapping[str, Any], out: List[str]) -> None:
    for key, value in dict(params).items():
        for addr in _leaves(f"{cid}.params.{key}", value):
            try:
                inc.check_unlocked(addr)
            except HarnessSpecError as exc:
                out.append(str(exc))


def _read(m: HarnessManifest, rel: str) -> Optional[str]:
    try:
        return m.read_file(rel)
    except (OSError, HarnessSpecError, UnicodeDecodeError):
        return None


def _files_on_disk(m: HarnessManifest) -> Dict[str, Optional[str]]:
    if m.root is None:
        return {}
    root = Path(m.root)
    out: Dict[str, Optional[str]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            rel = p.relative_to(root).as_posix()
            if rel != MANIFEST:
                out[rel] = _read(m, rel)
    return out


def _orphans(inc: HarnessManifest, cand: HarnessManifest) -> List[str]:
    """New or changed files in the candidate that no component references (they do nothing)."""
    referenced = {f for c in cand.components for f in c.files}
    before = _files_on_disk(inc)
    return [rel for rel, text in _files_on_disk(cand).items()
            if rel not in referenced and (rel not in before or before[rel] != text)]


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _unique(items: Iterable[str]) -> List[str]:
    seen: Dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
