"""하네스 고르기 — 고정 설정 → 계열 표 설정 → 패키지에 든 계열 표(H0 만) → 내장 H0."""

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
    path, label = ex.resolve_harness_dir(_Host(), "openai", "gpt-4o-mini")
    assert path == ex.BUILTIN_H0.resolve() and label == "builtin:h0"
    assert json.loads(ex.BUILTIN_LINEAGES.read_text())["lineages"]["default"] == "h0"


def test_every_model_starts_from_h0():
    """패키지는 RSI 파이프라인의 기본값(H0)만 싣는다 — 어떤 모델의 에이전트든 H0 에서 시작해 그 에이전트의 사용으로 진화한다.

    실험에서 진화시킨 하네스(특정 스위트·모델에 맞춘 것)는 패키지에 넣지 않는다.
    """
    assert json.loads(ex.BUILTIN_LINEAGES.read_text()) == {"lineages": {"default": "h0"}}
    assert sorted(p.name for p in ex.BUILTIN_DIR.iterdir() if p.is_dir() and (p / "manifest.json").exists()) == ["h0"]
    for provider, model in [("openai", "gpt-6-luna"), ("openai", "gpt-6-sol"), ("anthropic", "claude-haiku-4-5-20251001"),
                            ("anthropic", "claude-sonnet-5"), ("google", "gemini-3-pro")]:
        assert ex.resolve_harness_dir(_Host(), provider, model) == (ex.BUILTIN_H0.resolve(), "builtin:h0")


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
