"""평가 과제와 스위트(evolve / heldout / smoke 분할).

과제 하나 = 작업 공간 초기 파일 + 사용자 요청 + 결정적 검사 목록(+ 에이전트 설정: 시스템 프롬프트·출력 스키마·
도구 묶음). 에이전트 설정은 **태스크 입력 x 의 일부**다 — 진화 대상 하네스 H 가 아니다(설계 30 문서 P3).

스위트 디렉터리 형식::

    suite.json        {"name": ..., "splits": {"evolve": [ids], "heldout": [ids], "smoke": [ids]}}
    tasks/<id>.json   TaskSpec 의 JSON
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class TaskSpec:
    id: str
    prompt: str
    checks: Tuple[Mapping[str, Any], ...]
    files: Mapping[str, str] = field(default_factory=dict)
    toolset: str = "workspace"
    output_schema: Optional[Dict[str, Any]] = None
    system_prompt: Optional[str] = None
    max_iterations: int = 20
    tags: Tuple[str, ...] = ()
    description: str = ""

    @property
    def weight(self) -> float:
        """시행 가중치 = 검사 수(RRSI workspace 의 criteria 가중과 같은 규칙)."""
        return float(len(self.checks)) or 1.0

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "TaskSpec":
        return cls(
            id=str(raw["id"]),
            prompt=str(raw["prompt"]),
            checks=tuple(dict(c) for c in raw.get("checks") or ()),
            files=dict(raw.get("files") or {}),
            toolset=str(raw.get("toolset") or "workspace"),
            output_schema=raw.get("output_schema"),
            system_prompt=raw.get("system_prompt"),
            max_iterations=int(raw.get("max_iterations", 20)),
            tags=tuple(raw.get("tags") or ()),
            description=str(raw.get("description") or ""),
        )

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "prompt": self.prompt, "checks": [dict(c) for c in self.checks]}
        if self.files:
            out["files"] = dict(self.files)
        if self.toolset != "workspace":
            out["toolset"] = self.toolset
        if self.output_schema is not None:
            out["output_schema"] = self.output_schema
        if self.system_prompt is not None:
            out["system_prompt"] = self.system_prompt
        if self.max_iterations != 20:
            out["max_iterations"] = self.max_iterations
        if self.tags:
            out["tags"] = list(self.tags)
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class Suite:
    name: str
    tasks: Mapping[str, TaskSpec]
    splits: Mapping[str, Tuple[str, ...]]
    root: Optional[Path] = None

    def split(self, name: str) -> List[TaskSpec]:
        return [self.tasks[i] for i in self.splits.get(name, ())]

    def ids(self, name: str) -> List[str]:
        return list(self.splits.get(name, ()))


def load_suite(root: os.PathLike[str] | str) -> Suite:
    root_path = Path(root)
    meta = json.loads((root_path / "suite.json").read_text(encoding="utf-8"))
    tasks: Dict[str, TaskSpec] = {}
    for path in sorted((root_path / "tasks").glob("*.json")):
        spec = TaskSpec.from_json(json.loads(path.read_text(encoding="utf-8")))
        if spec.id in tasks:
            raise ValueError(f"duplicate task id {spec.id!r}")
        tasks[spec.id] = spec
    splits = {k: tuple(v) for k, v in (meta.get("splits") or {}).items()}
    for name, ids in splits.items():
        unknown = [i for i in ids if i not in tasks]
        if unknown:
            raise ValueError(f"split {name!r} names unknown tasks {unknown}")
    overlap = set(splits.get("evolve", ())) & set(splits.get("heldout", ()))
    if overlap:
        raise ValueError(f"evolve and heldout overlap: {sorted(overlap)}")
    return Suite(name=str(meta.get("name") or root_path.name), tasks=tasks, splits=splits, root=root_path)


def write_suite(suite_name: str, tasks: Sequence[TaskSpec], splits: Mapping[str, Sequence[str]], root: os.PathLike[str] | str) -> Path:
    root_path = Path(root)
    (root_path / "tasks").mkdir(parents=True, exist_ok=True)
    for t in tasks:
        (root_path / "tasks" / f"{t.id}.json").write_text(json.dumps(t.to_json(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (root_path / "suite.json").write_text(
        json.dumps({"name": suite_name, "splits": {k: list(v) for k, v in splits.items()}}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return root_path
