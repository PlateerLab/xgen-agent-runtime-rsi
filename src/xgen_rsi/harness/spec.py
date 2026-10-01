"""하네스 패키지 형식 — manifest, 구성요소 선언, 버전 식별자, 편집 주소, 계보(lineage).

하네스 = 디렉터리 하나(``manifest.json`` + manifest 가 가리키는 데이터 파일). 버전은 manifest 정규형과
파일 내용의 sha256 이다 — RRSI 공식 구현이 incumbent 를 git tree 해시로 식별하는 것과 같은 성질
(내용이 같으면 같은 버전, 한 바이트라도 다르면 다른 버전)을 갖는다.

편집 주소(원자 편집의 대상)는 ``<component_id>.params.<key>[.<key>...]`` 또는
``<component_id>.files.<relative_path>`` 다. 편집이 건드린 주소의 구성요소 kind 가 곧 RRSI 의 태그 ℓ 이다
(정규식 추측이 아니라 선언 대조, docs/design/33 §2 ``normalize_component``).
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from xgen_rsi.harness.kinds import REQUIRED_KINDS, K

SCHEMA = "xgen-rsi-harness/1"

#: 구성요소 구현(impl)이 올 수 있는 모듈 접두. 하네스 패키지가 임의 모듈을 import 하게 두지 않는다.
ALLOWED_IMPL_PREFIXES: Tuple[str, ...] = ("xgen_rsi.components.",)


class HarnessSpecError(ValueError):
    """manifest 가 형식·규칙을 어겼다."""


@dataclass(frozen=True)
class ComponentSpec:
    """manifest 의 구성요소 선언 하나."""

    id: str
    kind: str
    impl: str
    params: Mapping[str, Any] = field(default_factory=dict)
    files: Tuple[str, ...] = ()
    enabled: bool = True
    description: str = ""

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "kind": self.kind, "impl": self.impl}
        if self.params:
            out["params"] = _plain(self.params)
        if self.files:
            out["files"] = list(self.files)
        if not self.enabled:
            out["enabled"] = False
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class HarnessManifest:
    """하네스 한 버전."""

    name: str
    components: Tuple[ComponentSpec, ...]
    description: str = ""
    parent: Optional[str] = None
    #: 탐색 정책(Dream-RSI 소유). ``None`` 이면 탐색 계층 없이 퇴화 트리로 돈다.
    exploration_policy: Optional[Mapping[str, Any]] = None
    #: 이 하네스에서 활성인 kind. 기본은 𝒦 전부. 플랫폼 결정으로 끈 kind(예: subagent)는 빠진다.
    enabled_kinds: Tuple[str, ...] = K
    #: 하네스가 바꿀 수 없는 키(커널·입력 소유). 편집 주소가 여기에 닿으면 거부한다.
    locked: Tuple[str, ...] = ()
    root: Optional[Path] = None

    # ── 조회 ───────────────────────────────────────────────────────────
    def component(self, component_id: str) -> ComponentSpec:
        for c in self.components:
            if c.id == component_id:
                return c
        raise KeyError(component_id)

    def by_kind(self, kind: str) -> List[ComponentSpec]:
        return [c for c in self.components if c.kind == kind and c.enabled]

    def kind_of(self, component_id: str) -> str:
        return self.component(component_id).kind

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "schema": SCHEMA,
            "name": self.name,
            "components": [c.to_json() for c in self.components],
        }
        if self.description:
            out["description"] = self.description
        if self.parent:
            out["parent"] = self.parent
        if self.exploration_policy is not None:
            out["exploration_policy"] = _plain(self.exploration_policy)
        if tuple(self.enabled_kinds) != K:
            out["enabled_kinds"] = list(self.enabled_kinds)
        if self.locked:
            out["locked"] = list(self.locked)
        return out

    # ── 버전 ───────────────────────────────────────────────────────────
    def version_id(self) -> str:
        """``sha256:<hex>`` — manifest 정규형(부모 포인터 제외) + 참조 파일 내용."""
        h = hashlib.sha256()
        body = self.to_json()
        body.pop("parent", None)  # 같은 내용이면 어느 부모에서 왔든 같은 버전
        h.update(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
        for rel in sorted({f for c in self.components for f in c.files}):
            h.update(b"\0" + rel.encode() + b"\0")
            h.update(self.read_file(rel).encode())
        return "sha256:" + h.hexdigest()

    def read_file(self, rel: str) -> str:
        if self.root is None:
            raise HarnessSpecError(f"manifest has no root directory to read {rel!r}")
        path = _safe_join(self.root, rel)
        return path.read_text(encoding="utf-8")

    # ── 편집 ───────────────────────────────────────────────────────────
    def get(self, address: str) -> Any:
        comp_id, section, rest = parse_address(address)
        comp = self.component(comp_id)
        if section == "files":
            return self.read_file(rest[0])
        node: Any = comp.params
        for key in rest:
            node = node[key]
        return node

    def with_param(self, address: str, value: Any) -> "HarnessManifest":
        """``<id>.params.<path>`` 에 값을 넣은 새 manifest(파일 편집은 디렉터리 단위라 여기서 다루지 않는다)."""
        comp_id, section, rest = parse_address(address)
        if section != "params":
            raise HarnessSpecError("with_param handles params addresses only")
        self.check_unlocked(address)
        comps = []
        for c in self.components:
            if c.id != comp_id:
                comps.append(c)
                continue
            params = copy.deepcopy(dict(c.params))
            node = params
            for key in rest[:-1]:
                node = node.setdefault(key, {})
            node[rest[-1]] = value
            comps.append(ComponentSpec(c.id, c.kind, c.impl, params, c.files, c.enabled, c.description))
        return HarnessManifest(
            name=self.name,
            components=tuple(comps),
            description=self.description,
            parent=self.version_id(),
            exploration_policy=self.exploration_policy,
            enabled_kinds=self.enabled_kinds,
            locked=self.locked,
            root=self.root,
        )

    def check_unlocked(self, address: str) -> None:
        """잠금 패턴(fnmatch, 예 ``*.params.model``)에 걸리면 거부한다 — 정책 π·커널 한도는 하네스가 못 바꾼다."""
        for pattern in self.locked:
            if fnmatch.fnmatchcase(address, pattern) or fnmatch.fnmatchcase(address, pattern + ".*"):
                raise HarnessSpecError(f"address {address!r} is locked by {pattern!r}")

    def kinds_touched(self, addresses: Iterable[str]) -> List[str]:
        """편집 주소들이 건드린 구성요소 kind — RRSI comp(H') (Eq.16)."""
        out: List[str] = []
        for a in addresses:
            comp_id, _, _ = parse_address(a)
            kind = self.kind_of(comp_id)
            if kind not in out:
                out.append(kind)
        return out


def parse_address(address: str) -> Tuple[str, str, List[str]]:
    """``<id>.params.<a>.<b>`` → (id, "params", [a, b]); ``<id>.files.<path>`` → (id, "files", [path])."""
    for section in ("params", "files"):
        marker = f".{section}."
        idx = address.find(marker)
        if idx > 0:
            comp_id = address[:idx]
            rest = address[idx + len(marker) :]
            if not rest:
                break
            return comp_id, section, ([rest] if section == "files" else rest.split("."))
    raise HarnessSpecError(f"bad edit address {address!r} (want <id>.params.<key> or <id>.files.<path>)")


def load_manifest(root: os.PathLike[str] | str) -> HarnessManifest:
    """디렉터리에서 하네스를 읽고 검증한다."""
    root_path = Path(root).resolve()
    raw = json.loads((root_path / "manifest.json").read_text(encoding="utf-8"))
    manifest = manifest_from_json(raw, root=root_path)
    validate(manifest)
    return manifest


def manifest_from_json(raw: Mapping[str, Any], *, root: Optional[Path] = None) -> HarnessManifest:
    if raw.get("schema") != SCHEMA:
        raise HarnessSpecError(f"unsupported harness schema {raw.get('schema')!r} (want {SCHEMA})")
    comps = tuple(
        ComponentSpec(
            id=str(c["id"]),
            kind=str(c["kind"]),
            impl=str(c["impl"]),
            params=dict(c.get("params") or {}),
            files=tuple(str(f) for f in (c.get("files") or ())),
            enabled=bool(c.get("enabled", True)),
            description=str(c.get("description") or ""),
        )
        for c in raw.get("components") or ()
    )
    return HarnessManifest(
        name=str(raw.get("name") or "harness"),
        components=comps,
        description=str(raw.get("description") or ""),
        parent=raw.get("parent"),
        exploration_policy=raw.get("exploration_policy"),
        enabled_kinds=tuple(raw.get("enabled_kinds") or K),
        locked=tuple(raw.get("locked") or ()),
        root=root,
    )


def validate(m: HarnessManifest) -> None:
    """형식·규칙 검사. 어기면 :class:`HarnessSpecError`."""
    ids = [c.id for c in m.components]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise HarnessSpecError(f"duplicate component ids: {sorted(dup)}")
    for kind in m.enabled_kinds:
        if kind not in K:
            raise HarnessSpecError(f"enabled_kinds has unknown kind {kind!r}")
    for c in m.components:
        if "." in c.id and (".params." in c.id or ".files." in c.id):
            raise HarnessSpecError(f"component id {c.id!r} may not contain '.params.' or '.files.'")
        if c.kind not in K:
            raise HarnessSpecError(f"component {c.id!r}: kind {c.kind!r} not in 𝒦 {list(K)}")
        if c.enabled and c.kind not in m.enabled_kinds:
            raise HarnessSpecError(
                f"component {c.id!r}: kind {c.kind!r} is disabled for this harness"
            )
        if not c.impl.startswith(ALLOWED_IMPL_PREFIXES) or ":" not in c.impl:
            raise HarnessSpecError(
                f"component {c.id!r}: impl {c.impl!r} must be 'module:Class' under {ALLOWED_IMPL_PREFIXES}"
            )
        for rel in c.files:
            if m.root is not None:
                _safe_join(m.root, rel)  # 경로 탈출 검사
    for kind in REQUIRED_KINDS:
        if not m.by_kind(kind):
            raise HarnessSpecError(f"harness has no enabled {kind!r} component")
    for kind in ("prompt", "control_flow", "output_plumbing"):
        if len(m.by_kind(kind)) > 1:
            raise HarnessSpecError(f"harness may enable at most one {kind!r} component")


def save_manifest(m: HarnessManifest, root: os.PathLike[str] | str) -> Path:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    path = root_path / "manifest.json"
    path.write_text(json.dumps(m.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ── 계보(lineage) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class LineageTable:
    """정책 계열 → 하네스 버전 디렉터리. RRSI 는 정책별로 진화한다(논문 Table 3).

    키는 ``provider`` 또는 ``provider:model_prefix``. 가장 긴 접두가 이긴다. 없으면 ``default``.
    """

    entries: Mapping[str, str]

    def resolve(self, provider: str, model: str) -> str:
        provider = (provider or "").strip().lower()
        model = (model or "").strip().lower()
        best_key = ""
        for key in self.entries:
            if key == "default":
                continue
            prov, _, prefix = key.partition(":")
            if prov != provider:
                continue
            if prefix and not model.startswith(prefix):
                continue
            if len(key) > len(best_key):
                best_key = key
        if best_key:
            return self.entries[best_key]
        if "default" in self.entries:
            return self.entries["default"]
        raise KeyError(f"no lineage for provider={provider!r} model={model!r} and no default")

    @classmethod
    def load(cls, path: os.PathLike[str] | str) -> "LineageTable":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(entries=dict(raw.get("lineages") or {}))


# ── 내부 ────────────────────────────────────────────────────────────────


def _safe_join(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if p != root and not str(p).startswith(str(root) + os.sep):
        raise HarnessSpecError(f"file path escapes the harness directory: {rel!r}")
    return p


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def addresses_for(m: HarnessManifest) -> Sequence[str]:
    """manifest 의 모든 params 잎 주소 — proposer 에게 편집 가능한 손잡이 목록으로 보여 준다."""
    out: List[str] = []

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, Mapping) and node:
            for k, v in node.items():
                walk(f"{prefix}.{k}", v)
        else:
            out.append(prefix)

    for c in m.components:
        for k, v in dict(c.params).items():
            walk(f"{c.id}.params.{k}", v)
        for f in c.files:
            out.append(f"{c.id}.files.{f}")
    return out
