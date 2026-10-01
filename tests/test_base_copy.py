"""xgen_rsi.base 는 xgen-agent-runtime 의 사본 그대로다 — geny 와 geny-rsi 의 차이가 harness pipeline 하나로 남게.

기억·도구·작업·앱·스토리지·자기 진화 같은 요소 계층은 사본의 코드이고, ``COPY.json`` 에 원본 버전과 파일별 해시가
있다. 사본을 고치면 이 테스트가 실패한다 — 고칠 이유가 있으면 ``tools/sync_base.py`` 의 ``LOCAL_EDITS`` 에 적고 다시 만든다.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _sync():
    spec = importlib.util.spec_from_file_location("sync_base", ROOT / "tools" / "sync_base.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_base_matches_the_recorded_runtime_copy(capsys):
    assert _sync().check() == 0, capsys.readouterr().out


def test_recorded_version_is_the_package_identity():
    import xgen_rsi.base as base

    manifest = json.loads((ROOT / "src" / "xgen_rsi" / "base" / "COPY.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "xgen-agent-runtime"
    assert base.__version__ == manifest["version"] and base.COPIED_FROM == f"xgen-agent-runtime {manifest['version']}"
    # 사본에서 고친 파일은 이유와 함께 기록된 것뿐이다
    assert {e["file"] for e in manifest["local_edits"]} == {"__init__.py", "skills/bundled_skills.py"}
