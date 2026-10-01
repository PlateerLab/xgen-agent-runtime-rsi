"""skill 구성요소 — 하네스가 가진 절차 문서(SKILL.md)를 점진 공개로 쓴다(구조 레버, 𝒦_str).

RRSI 공식 구현의 기질(``upload_skills`` + 카탈로그 광고 + 정책이 필요할 때 전문을 읽음)과 같은 모양이다.

* 카탈로그(이름 + 한 줄 설명)만 시스템 프롬프트에 싣는다.
* 전문은 하네스 도구 ``ReadSkill`` 로 그때그때 읽는다 — 모든 호출에 절차 전문을 싣지 않는다.

스킬 파일은 하네스 디렉터리의 데이터 파일(``files``)이다. 진화(RRSI proposer)는 파일을 추가·편집하고
manifest 의 ``files`` 에 올린다. 스킬 본문은 **엔티티 없는 일반 절차**여야 한다(누설 심사 대상).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from xgen_agent_runtime.tools.base import Tool, ToolCapabilities, ToolContext, ToolResult

from xgen_rsi.harness.runtime import Component

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def parse_skill(text: str, fallback_name: str) -> Tuple[str, str, str]:
    """SKILL.md → (name, description, body). 머리말이 없으면 첫 줄이 설명."""
    name, desc = fallback_name, ""
    body = text
    match = _FRONT.match(text)
    if match:
        body = text[match.end() :]
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            if key == "name" and value:
                name = value
            elif key == "description" and value:
                desc = value
    if not desc:
        first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "")
        desc = first[:160]
    return name, desc, body.strip()


class ReadSkillTool(Tool):
    """Returns the full text of a harness skill."""

    def __init__(self, skills: Dict[str, Tuple[str, str]]) -> None:
        self._skills = skills

    @property
    def name(self) -> str:
        return "ReadSkill"

    @property
    def description(self) -> str:
        return "Load the full step-by-step procedure of a skill listed under 'Skills' in the system prompt."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name from the catalog."}},
            "required": ["name"],
        }

    def capabilities(self, input: Dict[str, Any]) -> ToolCapabilities:
        return ToolCapabilities(concurrency_safe=True)

    async def execute(self, input: Dict[str, Any], context: ToolContext) -> ToolResult:
        name = str(input.get("name") or "").strip()
        hit = self._skills.get(name)
        if hit is None:
            known = ", ".join(sorted(self._skills)) or "(none)"
            return ToolResult(content=f"Unknown skill {name!r}. Available: {known}", is_error=True)
        return ToolResult(content=hit[1])


class SkillLibraryComponent(Component):
    """Structural lever: a library of procedures (``skills/<name>/SKILL.md``) with progressive disclosure.

    Each SKILL.md has front matter ``name`` and ``description``; the system prompt lists only names and
    descriptions under ``header``, and the model loads a full procedure with the ``ReadSkill`` tool
    when a task matches. Add a skill by writing a new SKILL.md and listing it in this component's
    ``files``. Skills must describe general procedures, never evaluation-specific content.

    Params: header (str).
    """
    kind = "skill"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._loaded: Optional[Dict[str, Tuple[str, str]]] = None

    def _load(self, rt: Any) -> Dict[str, Tuple[str, str]]:
        if self._loaded is not None:
            return self._loaded
        out: Dict[str, Tuple[str, str]] = {}
        manifest = rt.harness.manifest
        for rel in self.spec.files:
            text = manifest.read_file(rel)
            stem = rel.rsplit("/", 2)[-2] if rel.endswith("SKILL.md") and "/" in rel else rel.rsplit("/", 1)[-1]
            name, desc, body = parse_skill(text, stem)
            out[name] = (desc, body)
        self._loaded = out
        return out

    # 커널 훅: 하네스가 기여하는 도구
    def tools(self, rt: Any) -> List[Tool]:
        skills = self._load(rt)
        return [ReadSkillTool(skills)] if skills else []

    # 커널 훅: 시스템 프롬프트에 붙일 조각
    def prompt_blocks(self, rt: Any) -> List[Tuple[str, str]]:
        skills = self._load(rt)
        if not skills:
            return []
        lines = [f"- {name}: {desc}" for name, (desc, _) in sorted(skills.items())]
        header = str(self.param("header", "# Skills\nProcedures you can load with ReadSkill(name) when the task matches:"))
        return [("skills", "\n\n" + header + "\n" + "\n".join(lines))]
