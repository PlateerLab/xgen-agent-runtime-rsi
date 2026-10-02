"""geny-rsi 는 독립 패키지다 — xgen-agent-runtime 을 import 하지도, 의존성으로 두지도 않는다.

바탕 런타임은 xgen-agent-runtime 4.81.0 을 복사한 ``xgen_rsi.base`` 다. 어느 경로로든 원본 패키지가 끼어들면 이 테스트가 잡는다.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_source_mentions_the_runtime_module():
    hits = [str(p.relative_to(ROOT)) for p in (ROOT / "src").rglob("*.py")
            if "xgen_agent_runtime" in p.read_text(encoding="utf-8")]
    assert hits == []


def test_not_a_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    deps = list(project["dependencies"]) + [d for ds in project.get("optional-dependencies", {}).values() for d in ds]
    assert not [d for d in deps if re.match(r"\s*xgen-agent-runtime(\s|\[|@|>|=|<|$)", d)]


def test_a_turn_never_loads_the_runtime_even_if_it_were_installed():
    # 원본 이름으로 import 하려는 순간 실패하게 막고, 두 엔진(geny-rsi·base 의 geny)으로 한 턴씩 돈다.
    code = textwrap.dedent('''
        import sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name == "xgen_agent_runtime" or name.startswith("xgen_agent_runtime."):
                    raise ImportError("blocked: " + name)
                return None
        sys.meta_path.insert(0, Block())
        from xgen_rsi.base.host import runner as runner_mod
        sys.path.insert(0, %r)
        from tests.kernel.fakes import ScriptedClient, text_step
        runner_mod.build_client = lambda *a, **k: ScriptedClient([text_step("ok")])
        from xgen_rsi import GenyRSI
        for engine in ("geny-rsi", "geny"):
            r = GenyRSI.minimal(provider="openai", model="m", api_key="k", engine=engine).run_sync("hi")
            assert r.text == "ok", (engine, r.text)
        loaded = [m for m in sys.modules if m.split(".")[0] == "xgen_agent_runtime"]
        assert not loaded, loaded
        print("independent")
    ''') % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    assert "independent" in out.stdout
