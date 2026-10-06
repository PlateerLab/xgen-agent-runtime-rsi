"""XGR 하네스 체크포인트 — 하네스 하나를 JSON 파일 하나로 담고, 읽을 때 내용과 버전을 맞춰 본다."""

from __future__ import annotations

import json

import pytest

from xgen_rsi.harness import xgr
from xgen_rsi.harness.payload import to_payload
from xgen_rsi.harness.spec import load_manifest, save_manifest
from xgen_rsi.kernel.executor import BUILTIN_H0


def _harness(tmp_path, text="Write prose, no tables."):
    m = load_manifest(BUILTIN_H0).with_param("prompt.system.params.extra_blocks", [{"id": "agent", "text": text}])
    root = tmp_path / "h"
    save_manifest(m, root)
    return root


def test_pack_dump_load_restore_round_trip(tmp_path):
    root = _harness(tmp_path)
    payload = to_payload(root)
    cp = xgr.pack(payload, parent="", lineage={"run_id": 7, "variant": "A", "components": ["prompt"]})
    data = xgr.dumps(cp)
    assert data.startswith(b"{\n") and data.endswith(b"\n")  # 그대로 읽히는 JSON
    back = xgr.loads(data)
    assert back["format"] == "xgr" and back["format_version"] == 1
    assert back["version"] == payload["version"] and back["lineage"]["run_id"] == 7
    assert xgr.to_payload_of(back) == {"manifest": payload["manifest"], "files": payload["files"],
                                       "version": payload["version"]}
    restored = xgr.restore(back, tmp_path / "cache")
    assert load_manifest(restored).version_id() == payload["version"]


def test_lineage_and_parent_do_not_change_the_version(tmp_path):
    payload = to_payload(_harness(tmp_path))
    a = xgr.pack(payload, parent="", lineage={"run_id": 1})
    b = xgr.pack(payload, parent="sha256:" + "0" * 64, lineage={"run_id": 2})
    assert a["version"] == b["version"] == payload["version"]


def test_a_tampered_file_is_refused(tmp_path):
    cp = xgr.from_dir(_harness(tmp_path))
    raw = json.loads(xgr.dumps(cp))
    raw["manifest"]["components"][0]["params"] = {"tampered": True}
    with pytest.raises(xgr.XgrError, match="version mismatch"):
        xgr.loads(json.dumps(raw))
    with pytest.raises(xgr.XgrError, match="not an xgr file"):
        xgr.loads(b'{"manifest": {}}')
    raw = json.loads(xgr.dumps(cp))
    raw["format_version"] = 99
    with pytest.raises(xgr.XgrError, match="format_version"):
        xgr.loads(json.dumps(raw))


def test_file_name_and_write_read(tmp_path):
    cp = xgr.from_dir(_harness(tmp_path))
    path = xgr.write(cp, tmp_path / "out" / xgr.file_name(cp["version"]))
    assert path.name.startswith("sha256-") and path.suffix == ".xgr"
    assert xgr.read(path)["version"] == cp["version"]
