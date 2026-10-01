"""하네스 고르기 — 고정 설정 → 계열 표 설정 → 패키지에 든 계열 표 → 내장 H0.

RRSI 로 채택한 하네스는 패키지(``harnesses/`` + ``lineages.json``)에 담아 릴리스한다. 호스트(XGEN)는 설정 없이도
패키지 버전을 올리면 그 하네스를 쓰고, 관리자는 ``builtin:<이름>`` 으로 패키지 하네스 하나를 고정할 수 있다.
"""

from __future__ import annotations

import json
import shutil

import pytest

from xgen_rsi.harness.spec import HarnessSpecError
from xgen_rsi.kernel import executor as ex


class _Host:
    def __init__(self, **settings):
        self._s = settings

    def setting(self, name, default=""):
        return self._s.get(name, default)


def test_default_uses_the_bundled_table_and_falls_back_to_h0():
    path, label = ex.resolve_harness_dir(_Host(), "openai", "gpt-6-luna")
    assert path == ex.BUILTIN_H0.resolve() and label == "builtin:h0"
    assert json.loads(ex.BUILTIN_LINEAGES.read_text())["lineages"]["default"] == "h0"


def test_builtin_name_in_the_fixed_setting():
    path, label = ex.resolve_harness_dir(_Host(XGEN_RSI_HARNESS_DIR="builtin:h0"), "anthropic", "claude-haiku-4-5")
    assert path == ex.BUILTIN_H0.resolve() and label == "builtin:h0"
    with pytest.raises(HarnessSpecError):
        ex.resolve_harness_dir(_Host(XGEN_RSI_HARNESS_DIR="builtin:../h0"), "openai", "m")
    with pytest.raises(HarnessSpecError):
        ex.resolve_harness_dir(_Host(XGEN_RSI_HARNESS_DIR="builtin:nope"), "openai", "m")


def test_fixed_directory_and_lineage_file_still_win(tmp_path):
    h = tmp_path / "hx"
    shutil.copytree(ex.BUILTIN_H0, h)
    assert ex.resolve_harness_dir(_Host(XGEN_RSI_HARNESS_DIR=str(h)), "openai", "m") == (h, "fixed")
    table = tmp_path / "lineages.json"
    table.write_text(json.dumps({"lineages": {"default": "builtin:h0", "openai:gpt-6": "hx"}}))
    host = _Host(XGEN_RSI_LINEAGE_FILE=str(table))
    assert ex.resolve_harness_dir(host, "openai", "gpt-6-luna") == (tmp_path / "hx", "hx")
    assert ex.resolve_harness_dir(host, "anthropic", "claude") == (ex.BUILTIN_H0.resolve(), "builtin:h0")


def test_bundled_table_picks_a_model_lineage(tmp_path, monkeypatch):
    root = tmp_path / "harnesses"
    root.mkdir()
    shutil.copytree(ex.BUILTIN_H0, root / "h0")
    shutil.copytree(ex.BUILTIN_H0, root / "luna-h1")
    (root / "lineages.json").write_text(json.dumps({"lineages": {"default": "h0", "openai:gpt-6-luna": "luna-h1"}}))
    monkeypatch.setattr(ex, "BUILTIN_DIR", root)
    monkeypatch.setattr(ex, "BUILTIN_H0", root / "h0")
    monkeypatch.setattr(ex, "BUILTIN_LINEAGES", root / "lineages.json")
    assert ex.resolve_harness_dir(_Host(), "openai", "gpt-6-luna-2026") == ((root / "luna-h1").resolve(), "builtin:luna-h1")
    assert ex.resolve_harness_dir(_Host(), "anthropic", "claude-haiku") == ((root / "h0").resolve(), "builtin:h0")
