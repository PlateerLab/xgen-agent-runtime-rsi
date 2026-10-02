"""하네스 ↔ 페이로드 — 호스트(XGEN)가 에이전트마다 하네스를 저장하고 턴에 넘기는 형식.

에이전트의 하네스는 디렉터리가 아니라 호스트의 저장소(DB 등)에 산다. 그래서 주고받는 형식은 JSON 하나다::

    {"manifest": {... manifest.json ...}, "files": {"skills/x/SKILL.md": "...", ...}, "version": "sha256:..."}

``files`` 는 manifest 의 구성요소가 참조하는 파일 전부다. ``version`` 은 :meth:`HarnessManifest.version_id` 와 같은 값이고
(manifest 정규형 + 참조 파일 내용), 받는 쪽은 다시 계산해 맞는지 본다. geny-rsi 는 페이로드를 버전별 디렉터리에 한 번 풀어
두고 쓴다(:func:`materialize`).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from xgen_rsi.harness.spec import (
    HarnessSpecError,
    _safe_join,
    load_manifest,
    manifest_from_json,
    validate,
)

_LOCK = threading.Lock()


def to_payload(root: os.PathLike[str] | str) -> Dict[str, Any]:
    """하네스 디렉터리 → 페이로드."""
    m = load_manifest(root)
    files = {rel: m.read_file(rel) for rel in sorted({f for c in m.components for f in c.files})}
    manifest = json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))
    return {"manifest": manifest, "files": files, "version": m.version_id()}


def payload_version(payload: Mapping[str, Any]) -> str:
    """페이로드의 하네스 버전(``sha256:...``) — 디렉터리에 풀지 않고 계산한다."""
    with tempfile.TemporaryDirectory(prefix="rsi-harness-") as tmp:
        root = _write(payload, Path(tmp))
        return load_manifest(root).version_id()


def materialize(payload: Mapping[str, Any], cache_root: Optional[os.PathLike[str] | str] = None) -> Path:
    """페이로드를 ``<cache_root>/<버전>/`` 에 풀고 그 디렉터리를 돌려준다(같은 버전이면 다시 쓰지 않는다).

    ``version`` 이 주어졌는데 내용과 다르면 :class:`HarnessSpecError` — 저장소가 깨진 하네스를 턴에 넘기지 않게.
    """
    base = Path(cache_root) if cache_root else Path(tempfile.gettempdir()) / "xgen-rsi-agent-harness"
    with tempfile.TemporaryDirectory(prefix="rsi-harness-") as tmp:
        staged = _write(payload, Path(tmp) / "h")
        version = load_manifest(staged).version_id()
        claimed = payload.get("version")
        if claimed and str(claimed) != version:
            raise HarnessSpecError(f"harness payload version mismatch: claimed {claimed}, content is {version}")
        target = base / version.replace(":", "-")
        with _LOCK:
            if not (target / "manifest.json").is_file():
                base.mkdir(parents=True, exist_ok=True)
                tmp_target = base / f".{target.name}.{os.getpid()}.{threading.get_ident()}"
                if tmp_target.exists():
                    shutil.rmtree(tmp_target)
                shutil.copytree(staged, tmp_target)
                try:
                    os.replace(tmp_target, target)
                except OSError:  # 다른 프로세스가 먼저 풀었다
                    shutil.rmtree(tmp_target, ignore_errors=True)
    return target


def _write(payload: Mapping[str, Any], root: Path) -> Path:
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping):
        raise HarnessSpecError("harness payload needs a 'manifest' object")
    validate(manifest_from_json(manifest))
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = payload.get("files") or {}
    if not isinstance(files, Mapping):
        raise HarnessSpecError("harness payload 'files' must be an object")
    for rel, text in files.items():
        path = _safe_join(root, str(rel))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text), encoding="utf-8")
    return root


__all__ = ["materialize", "payload_version", "to_payload"]
