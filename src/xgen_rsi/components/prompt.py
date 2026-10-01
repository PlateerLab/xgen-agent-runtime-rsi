"""prompt 구성요소 — 시스템 프롬프트를 조각 단위로 짓는다.

턴 조립(호스트 계약)이 만든 조각(``plan.system_parts``: base·jobs·environment·efficiency·memory·
self_evolution·tool_catalog·cli_naming …)을 받아, 하네스 파라미터로 조각을 교체·삭제·재배열·추가한 뒤
기억 블록·현재 시각·턴 안내를 붙인다. 원자 편집 주소 예:

* ``prompt.system.params.part_overrides.efficiency`` — 효율 원칙 블록의 글 교체(``null`` 이면 삭제)
* ``prompt.system.params.extra_blocks`` — 새 지침 블록 추가 ``[{"id": ..., "text": ...}]``
* ``prompt.system.params.part_order`` — 조각 순서

H0 파라미터는 비어 있어 기존 운영 문자열과 같은 프롬프트를 낸다.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from xgen_rsi.harness.kinds import SystemPrompt
from xgen_rsi.harness.runtime import Component


def schema_instruction(schema: Dict[str, Any]) -> str:
    """구조화 출력 지시(기존 ``runner._schema_instruction`` 과 같은 글)."""
    return (
        "\n\n# Output format\n"
        "Respond with a single JSON object that conforms to the JSON Schema below. "
        "Output ONLY the JSON object — no explanations, no markdown fences.\n"
        + json.dumps(schema, ensure_ascii=False)
    )


def compose_parts(
    parts: Sequence[Tuple[str, str]],
    *,
    overrides: Optional[Dict[str, Optional[str]]] = None,
    order: Optional[Sequence[str]] = None,
    extra: Optional[Sequence[Any]] = None,
) -> List[Tuple[str, str]]:
    """조각 목록에 교체·삭제·순서·추가를 적용한다. 같은 id 의 조각이 여럿이면(예: memory) 모두 같이 다룬다.

    ``extra`` 의 원소는 글(``str``) 또는 ``{"id": ..., "text": ...}`` — 제안자가 어느 쪽으로 써도 같은 조각이 된다.
    """
    overrides = dict(overrides or {})
    out: List[Tuple[str, str]] = []
    for part_id, text in parts:
        if part_id in overrides:
            replacement = overrides[part_id]
            if replacement is None:
                continue
            out.append((part_id, str(replacement)))
        else:
            out.append((part_id, text))
    for block in extra or ():
        if isinstance(block, dict):
            block_id, text = str(block.get("id") or "extra"), str(block.get("text") or "")
        else:
            block_id, text = "extra", ("" if block is None else str(block))
        if text:
            # 추가 블록은 앞 조각과 붙지 않게 빈 줄로 띄운다(기존 블록들이 쓰는 관례).
            out.append((block_id, text if text.startswith("\n") else "\n\n" + text))
    if order:
        rank = {pid: i for i, pid in enumerate(order)}
        indexed = list(enumerate(out))
        indexed.sort(key=lambda it: (rank.get(it[1][0], len(rank) + it[0]), it[0]))
        out = [p for _, p in indexed]
    return out


class SystemPromptComponent(Component):
    """Builds the system prompt from the turn's named parts plus harness blocks.

    The host assembles named parts (persona, environment, tool notes, ...). This component can
    replace a part (``part_overrides`` {name: text}), reorder parts (``part_order`` [names]) and append
    harness text (``extra_blocks`` — a list of strings, or of {"id", "text"} objects); other components (e.g. the skill library) contribute
    blocks too. Builder blocks: pinned memory and retrieved knowledge (``memory_blocks``), current
    date/time (``datetime``), turn notes (``turn_notes``), the output-schema instruction
    (``schema_instruction``). Stable text goes first so provider prompt caches keep hitting; volatile
    text goes to the turn context unless ``volatile_placement`` is "system". Harness text must be
    English and must implement a mechanism, never task-specific content.

    Params: part_overrides (dict), part_order (list), extra_blocks (list), memory_blocks (bool, True),
    datetime (bool, True), turn_notes (bool, True), schema_instruction (bool, True),
    volatile_placement (str, "turn_context").
    """
    kind = "prompt"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._builder: Any = None
        self._catalog_version: Any = object()
        self._catalog = ""

    def _make_builder(self, base: str, with_memory: bool) -> Any:
        from xgen_rsi.base.stages.s03_system.artifact.default.builders import (
            ComposablePromptBuilder,
            CustomBlock,
            DateTimeBlock,
            PinnedFactsBlock,
            RetrievedMemoryBlock,
            TurnNotesBlock,
        )

        blocks: List[Any] = [CustomBlock("base", base)]
        if with_memory and self.param("memory_blocks", True):
            blocks.append(PinnedFactsBlock())
        if self.param("datetime", True):
            blocks.append(DateTimeBlock())
        if with_memory and self.param("memory_blocks", True):
            blocks.append(RetrievedMemoryBlock())
        if self.param("turn_notes", True):
            blocks.append(TurnNotesBlock())
        return ComposablePromptBuilder(blocks=blocks)

    def base_text(self, rt: Any) -> Tuple[str, List[Tuple[str, str]]]:
        extra = list(self.param("extra_blocks") or ())
        # 다른 구성요소가 기여하는 조각(skill 카탈로그 등) — 같은 편집 규칙(교체·순서)을 받는다.
        for comp in rt.harness.components.values():
            hook = getattr(comp, "prompt_blocks", None)
            if callable(hook) and comp is not self:
                for block_id, text in hook(rt) or ():
                    extra.append({"id": block_id, "text": text})
        parts = compose_parts(
            list(getattr(rt.plan, "system_parts", None) or [("base", rt.plan.system_prompt)]),
            overrides=self.param("part_overrides") or {},
            order=self.param("part_order") or None,
            extra=extra,
        )
        base = "".join(t for _, t in parts)
        schema = getattr(rt.plan, "schema", None)
        if schema and self.param("schema_instruction", True):
            base += schema_instruction(schema)
        return base, parts

    def build(self, rt: Any) -> SystemPrompt:
        state = rt.state
        if self._builder is None:
            base, parts = self.base_text(rt)
            self._parts = tuple(parts)
            self._builder = self._make_builder(base, rt.memory_provider is not None)
        system, volatile_text = self._assemble(state)

        # 숨긴 도구 카탈로그 — SDK 경로에서만(CLI 는 조립 단계가 이미 붙였다). 레지스트리 버전이 바뀔 때만 다시 만든다.
        registry = rt.registry
        if registry is not None:
            version = getattr(registry, "version", None)
            if self._catalog_version != version:
                from xgen_rsi.base.tools.catalog import deferred_catalog_text

                self._catalog = deferred_catalog_text(registry)
                self._catalog_version = version
            if self._catalog and isinstance(system, str):
                system = (system + "\n\n" + self._catalog) if system else self._catalog
        return SystemPrompt(system=system, turn_context=volatile_text, parts=self._parts)

    def _assemble(self, state: Any) -> Tuple[Any, str]:
        """기존 SystemStage 의 안정/휘발 분리와 같은 규칙(첫 휘발 블록부터 끝까지가 이번 턴 맥락)."""
        parts = None
        build_parts = getattr(self._builder, "build_parts", None)
        if callable(build_parts):
            try:
                parts = build_parts(state)
            except Exception:  # noqa: BLE001 — 구조는 최적화일 뿐 실패 사유가 아니다
                parts = None
        if not parts:
            return self._builder.build(state), ""
        texts = [str(p.get("text", "")) for p in parts]
        first_volatile = next((i for i, p in enumerate(parts) if p.get("volatile")), len(parts))
        stable_text = "\n\n".join(t for t in texts[:first_volatile] if t)
        volatile_text = "\n\n".join(t for t in texts[first_volatile:] if t)
        if not volatile_text:
            return stable_text, ""
        if self.param("volatile_placement", "turn_context") == "system":
            return "\n\n".join(t for t in (stable_text, volatile_text) if t), ""
        return stable_text, volatile_text
