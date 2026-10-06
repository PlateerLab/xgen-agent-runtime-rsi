"""XGR, the harness checkpoint file (``.xgr``).

One file holds one harness of one agent: everything needed to restore it and where it came from. The content is JSON
(UTF-8, readable as is); the extension is used only by XGEN and geny-rsi::

    {
      "format": "xgr",
      "format_version": 1,
      "version": "sha256:...",      harness content version (manifest canonical form + referenced files)
      "parent": "sha256:..." | "",  the harness this one was drafted from ("" = H0)
      "manifest": {...},            manifest.json
      "files": {"path": "text"},    every file the manifest's components reference
      "lineage": {...},             why the host adopted it (consolidation run, round, variant, components, scores)
      "created_at": "...",
      "created_by": "geny-rsi 0.9.0"
    }

``version`` is :meth:`HarnessManifest.version_id`, computed from ``manifest`` and ``files`` only, so the same harness
reached by two different paths has the same version; ``parent`` and ``lineage`` do not change it. :func:`loads`
recomputes it and refuses a file whose content does not match (a checkpoint edited by hand or truncated never reaches
a turn). :func:`restore` unpacks a checkpoint into a harness directory, :func:`pack` and :func:`from_dir` go the other
way. The payload exchanged with hosts (:mod:`xgen_rsi.harness.payload`) is the ``version``/``manifest``/``files``
subset.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from xgen_rsi.harness.payload import materialize, payload_version, to_payload
from xgen_rsi.harness.spec import HarnessSpecError

FORMAT = "xgr"
FORMAT_VERSION = 1
EXTENSION = ".xgr"


class XgrError(HarnessSpecError):
    """Not a readable harness checkpoint (format, version or content mismatch)."""


def _created_by() -> str:
    from xgen_rsi import __version__

    return f"geny-rsi {__version__}"


def pack(
    payload: Mapping[str, Any],
    *,
    parent: str = "",
    lineage: Optional[Mapping[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Harness payload (``manifest``, ``files``) -> checkpoint. The version is computed from the content."""
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping):
        raise XgrError("harness payload needs a 'manifest' object")
    files = payload.get("files") or {}
    if not isinstance(files, Mapping):
        raise XgrError("harness payload 'files' must be an object")
    version = payload_version({"manifest": manifest, "files": files})
    claimed = payload.get("version")
    if claimed and str(claimed) != version:
        raise XgrError(f"payload version mismatch: claimed {claimed}, content is {version}")
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "version": version,
        "parent": str(parent or ""),
        "manifest": json.loads(json.dumps(manifest)),
        "files": {str(k): str(v) for k, v in sorted(files.items())},
        "lineage": json.loads(json.dumps(dict(lineage or {}), default=str)),
        "created_at": created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": _created_by(),
    }


def from_dir(root: os.PathLike[str] | str, **kw: Any) -> Dict[str, Any]:
    """Harness directory -> checkpoint (``parent``, ``lineage``, ``created_at`` as in :func:`pack`)."""
    return pack(to_payload(root), **kw)


def dumps(checkpoint: Mapping[str, Any]) -> bytes:
    """Checkpoint -> file bytes (UTF-8 JSON, two-space indent, trailing newline)."""
    return (json.dumps(dict(checkpoint), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def loads(data: Union[bytes, str]) -> Dict[str, Any]:
    """File bytes -> checkpoint, after checking the format and that ``version`` matches the content."""
    try:
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        raw = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XgrError(f"not an xgr file: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise XgrError("not an xgr file: missing format 'xgr'")
    fv = raw.get("format_version")
    if not isinstance(fv, int) or fv < 1 or fv > FORMAT_VERSION:
        raise XgrError(f"unsupported xgr format_version {fv!r} (this geny-rsi reads up to {FORMAT_VERSION})")
    actual = payload_version({"manifest": raw.get("manifest"), "files": raw.get("files") or {}})
    if str(raw.get("version") or "") != actual:
        raise XgrError(f"xgr version mismatch: file says {raw.get('version')}, content is {actual}")
    return raw


def read(path: os.PathLike[str] | str) -> Dict[str, Any]:
    return loads(Path(path).read_bytes())


def write(checkpoint: Mapping[str, Any], path: os.PathLike[str] | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(dumps(checkpoint))
    return target


def to_payload_of(checkpoint: Mapping[str, Any]) -> Dict[str, Any]:
    """Checkpoint -> the payload hosts hand to a turn (``manifest``, ``files``, ``version``)."""
    return {"manifest": checkpoint["manifest"], "files": dict(checkpoint.get("files") or {}),
            "version": checkpoint["version"]}


def restore(checkpoint: Mapping[str, Any], cache_root: Optional[os.PathLike[str] | str] = None) -> Path:
    """Checkpoint -> harness directory (``<cache_root>/<version>/``, see :func:`materialize`)."""
    return materialize(to_payload_of(checkpoint), cache_root)


def file_name(version: str) -> str:
    """File name for a harness version: ``sha256-<hex>.xgr``."""
    return str(version).replace(":", "-") + EXTENSION


__all__ = ["EXTENSION", "FORMAT", "FORMAT_VERSION", "XgrError", "dumps", "file_name", "from_dir", "loads", "pack",
           "read", "restore", "to_payload_of", "write"]
