"""xgen_rsi.base 를 xgen-agent-runtime 사본으로 맞추고, 사본이 그대로인지 확인한다.

geny-rsi 와 geny 의 차이는 **harness pipeline 하나**여야 한다. 기억·도구·작업·앱·스토리지·자기 진화 같은 요소 계층은
xgen-agent-runtime 의 코드를 그대로 복사해 쓴다(import 하지 않는다). 이 스크립트가 그 사본을 만들고, ``COPY.json`` 에
원본 버전과 파일별 해시를 남긴다. 테스트(``tests/test_base_copy.py``)는 사본이 그 기록과 같은지 본다 — 사본을 손으로
고치면 실패하고, 고친 파일은 ``LOCAL_EDITS`` 에 이유와 함께 적어야 한다.

    python tools/sync_base.py /path/to/xgen-agent-runtime/src/xgen_agent_runtime 4.80.0   # 복사 + COPY.json
    python tools/sync_base.py --check                                                      # 사본이 기록과 같은가

복사는 모듈 경로만 바꾼다(``xgen_agent_runtime`` → ``xgen_rsi.base``). 바꾼 뒤 ``LOCAL_EDITS`` 를 다시 적용한다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "src" / "xgen_rsi" / "base"
MANIFEST = BASE / "COPY.json"

#: 사본에서 의도적으로 고친 파일 — (파일, 이유, 바꾸기 전 문자열, 바꾼 뒤 문자열).
LOCAL_EDITS = [
    (
        "__init__.py",
        "버전: 원본은 배포본 xgen-agent-runtime 의 메타데이터에서 버전을 읽는다. 사본은 그 배포본이 없어도 자신을 밝힌다.",
        '''# Single source of truth: read the installed distribution version so
# ``__version__`` can never drift from ``pyproject.toml`` again.
try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("xgen-agent-runtime")
except Exception:  # noqa: BLE001 — not installed (e.g. source checkout)
    __version__ = "0.0.0+local"
''',
        '''#: 복사한 원본 버전 — 이 사본은 xgen-agent-runtime 배포본과 무관하게 이 값으로 자신을 밝힌다.
COPIED_FROM = "xgen-agent-runtime {version}"
__version__ = "{version}"
''',
    ),
    (
        "__init__.py",
        "머리말: 이 패키지가 사본이라는 것과 의존하지 않는다는 것을 밝힌다.",
        '''"""xgen-agent-runtime: Harness-engineered agent pipeline library.
''',
        '''"""xgen_rsi.base — geny-rsi 의 바탕 런타임. xgen-agent-runtime {version} 을 복사해 이 패키지가 소유한다.

geny-rsi 는 xgen-agent-runtime 을 import 하지도, 의존성으로 두지도 않는다. 공급자 계층·도구·기억·호스트 계약·
21-stage 엔진(geny 기준선)까지 필요한 것은 전부 이 사본에서 온다. 원본이 바뀌어도 여기는 따로 갱신한다.

(원본 docstring) xgen-agent-runtime: Harness-engineered agent pipeline library.
''',
    ),
    (
        "skills/bundled_skills.py",
        "docstring 의 파일 경로 — 모듈 경로 치환이 디렉터리 경로까지 바꾼 것을 되돌린다.",
        "``xgen_rsi.base/skills/bundled/<id>/SKILL.md``",
        "``xgen_rsi/base/skills/bundled/<id>/SKILL.md``",
    ),
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(root: Path):
    for f in sorted(root.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts and f.name != "COPY.json":
            yield f.relative_to(root).as_posix(), f


def sync(source: Path, version: str) -> None:
    if BASE.exists():
        shutil.rmtree(BASE)
    originals = {}
    for rel, f in _files(source):
        data = f.read_bytes()
        originals[rel] = _sha(data)
        out = BASE / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data.replace(b"xgen_agent_runtime", b"xgen_rsi.base"))
    for rel, _why, old, new in LOCAL_EDITS:
        path = BASE / rel
        text = path.read_text(encoding="utf-8")
        old = old.replace("xgen_agent_runtime", "xgen_rsi.base")
        if text.count(old) != 1:
            raise SystemExit(f"{rel}: local edit anchor not found once — update LOCAL_EDITS for this runtime version")
        path.write_text(text.replace(old, new.format(version=version)), encoding="utf-8")
    manifest = {
        "source": "xgen-agent-runtime",
        "version": version,
        "rename": ["xgen_agent_runtime", "xgen_rsi.base"],
        "local_edits": [{"file": rel, "why": why} for rel, why, _o, _n in LOCAL_EDITS],
        "files": {rel: _sha(f.read_bytes()) for rel, f in _files(BASE)},
        "original_files": originals,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"copied {len(manifest['files'])} files from {source} (xgen-agent-runtime {version})")


def check() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    recorded = manifest["files"]
    actual = {rel: _sha(f.read_bytes()) for rel, f in _files(BASE)}
    changed = sorted(r for r in recorded if r in actual and actual[r] != recorded[r])
    missing = sorted(set(recorded) - set(actual))
    extra = sorted(set(actual) - set(recorded))
    if changed or missing or extra:
        print(json.dumps({"changed": changed, "missing": missing, "extra": extra}, ensure_ascii=False, indent=1))
        return 1
    print(f"base == xgen-agent-runtime {manifest['version']} copy ({len(recorded)} files, "
          f"{len(manifest['local_edits'])} recorded local edits)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    if sys.argv[1:2] == ["--check"]:
        raise SystemExit(check())
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sync(Path(sys.argv[1]).resolve(), sys.argv[2])
