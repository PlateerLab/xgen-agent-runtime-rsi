"""턴 세계 기록 — 재생(설계 41)에 필요한 것을 턴이 도는 동안 모은다.

Dream-RSI 의 세계는 "완료된 실행의 결정과 그 결과"다. 에이전트 턴에서 그것은 **모델이 본 입력**과 **환경이 돌려준 응답**이다:

* 턴 계획 — 시스템 조각(하네스가 짓기 전의 재료), 출력 스키마, 노드 손잡이(비밀 없는 것만), 앞 대화, 입력, 조립이 상태에 남긴 값
* 도구 — 그 턴의 도구 목록(이름·설명·스키마·노출 상태)과 **호출마다 실제 결과**(호스트 결과 필터를 지난 것 = 모델이 본 것)
* 기억 — 첫 반복의 기억 검색 결과(조각)
* 결과 — 최종 답, 상태, 종료 사유, 정책 토큰, 스텝, 그 턴의 전사(분석·제안이 읽는다)

하네스 구성요소가 만드는 것(시스템 프롬프트 완성본, 노출 도구 선택, 압축)은 기록하지 않는다 — 재생에서 후보 하네스가 다시
만든다. 자격증명·클라이언트·레지스트리 객체는 기록하지 않는다. 이미지는 자리 표시로 바꾼다.

기록은 호스트가 켠다(``XGEN_RSI_RECORD_WORLD``). 켜면 :class:`~xgen_rsi.kernel.recorder.TrajectoryRecord` 의 ``world`` 로 나간다.
"""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

WORLD_SCHEMA = "xgen-rsi-world/1"
#: 도구 결과 하나의 상한(글자). 넘으면 앞·뒤를 남기고 자른다(세계에 ``truncated`` 표시).
MAX_RESULT_CHARS = 32_000
#: 세계 하나의 상한(직렬화 바이트). 넘으면 재생할 수 없는 세계로 표시한다(``replayable=False``).
MAX_WORLD_BYTES = 2_000_000
#: 레지스트리를 읽기만 하는 메타 도구 — 재생에서 실제 인스턴스로 돈다(기록 결과가 아니라).
LIVE_META_TOOLS = ("ToolSearch", "SelfExtendGuide")
IMAGE_PLACEHOLDER = "[image omitted from the replay world]"

#: 기록하는 노드 손잡이(비밀·객체 없는 것만).
SAFE_PIPELINE_KEYS = (
    "name", "max_iterations", "temperature", "max_tokens", "thinking_level", "stream", "output_schema",
    "context_window_budget", "enable_compaction", "enable_prompt_cache", "repeat_stop_after", "prune_over_tokens",
    "turn_input_budget_tokens",
)


def _json_safe(value: Any) -> Any:
    """JSON 으로 왕복할 수 있는 값만 남긴다(나머지는 버린다)."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return None


def _safe_mapping(raw: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (raw or {}).items():
        if str(key).startswith("_prompt_tokens"):
            continue
        safe = _json_safe(value)
        if safe is not None or value is None:
            out[str(key)] = safe
    return out


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit * 3 // 4
    tail = limit - head
    return f"{text[:head]}\n…[{len(text) - limit} characters cut from the replay world]…\n{text[-tail:]}", True


class WorldCapture:
    """턴 하나의 세계. 커널이 준비·도구·기억·끝에서 먹인다."""

    def __init__(self, *, max_result_chars: int = MAX_RESULT_CHARS, max_world_bytes: int = MAX_WORLD_BYTES) -> None:
        self.max_result_chars = int(max_result_chars)
        self.max_world_bytes = int(max_world_bytes)
        self.world: Dict[str, Any] = {"schema": WORLD_SCHEMA, "replayable": True, "truncated": False, "images_dropped": False}
        self.calls: List[Dict[str, Any]] = []
        self._history_len = 0

    # ── 값 정리 ─────────────────────────────────────────────────────────
    def _content(self, content: Any, *, limit: Optional[int] = None) -> Any:
        """메시지·도구 결과의 내용 → JSON 안전, 이미지 → 자리 표시, 긴 글 → 자르기."""
        lim = self.max_result_chars if limit is None else limit
        if isinstance(content, str):
            text, cut = _clip(content, lim) if lim else (content, False)
            self.world["truncated"] = self.world["truncated"] or cut
            return text
        if isinstance(content, list):
            return [self._block(b, lim) for b in content]
        if isinstance(content, dict):
            return self._block(content, lim)
        safe = _json_safe(content)
        return safe if safe is not None else str(content)

    def _block(self, block: Any, lim: int) -> Any:
        if not isinstance(block, dict):
            return self._content(block, limit=lim)
        btype = str(block.get("type") or "")
        if btype == "image" or btype == "input_image":
            self.world["images_dropped"] = True
            return {"type": "text", "text": IMAGE_PLACEHOLDER}
        out: Dict[str, Any] = {}
        for key, value in block.items():
            if key in ("text", "content") and isinstance(value, (str, list, dict)):
                out[key] = self._content(value, limit=lim)
            else:
                safe = _json_safe(value)
                out[key] = safe if safe is not None else (None if value is None else str(value))
        return out

    def _messages(self, messages: Sequence[Any], *, limit: int = 0) -> List[Dict[str, Any]]:
        """``limit`` 0 = 자르지 않는다(앞 대화 — 모델이 본 그대로여야 재생이 같다)."""
        out: List[Dict[str, Any]] = []
        for msg in messages or ():
            if not isinstance(msg, dict):
                continue
            out.append({"role": str(msg.get("role") or ""), "content": self._content(copy.deepcopy(msg.get("content")), limit=limit)})
        return out

    # ── 커널이 부른다 ───────────────────────────────────────────────────
    def on_prepare(self, plan: Any, *, registry: Any, contributed: Sequence[str], harness_version: str, lineage: str,
                   provider: str) -> None:
        state = plan.state
        kw = dict(getattr(plan, "pipeline_kwargs", None) or {})
        messages = list(getattr(state, "messages", None) or [])
        self._history_len = len(messages)
        now = time.time()
        self.world.update(
            created_at=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            clock=now,
            interaction_id=str(getattr(plan, "interaction_id", "") or ""),
            harness_version=str(harness_version or ""),
            lineage=str(lineage or ""),
            provider=str(provider or getattr(plan, "provider", "") or ""),
            model=str(getattr(plan, "model", "") or ""),
            plan={
                "node_name": str(getattr(plan, "node_name", "") or ""),
                "system_prompt": str(getattr(plan, "system_prompt", "") or ""),
                "system_parts": [[str(pid), str(text)] for pid, text in (getattr(plan, "system_parts", None) or [])],
                "schema": _json_safe(getattr(plan, "schema", None)),
                "budget_window": int(getattr(plan, "budget_window", 0) or 0),
                "enable_compaction": bool(getattr(plan, "enable_compaction", True)),
                "max_tokens": int(getattr(plan, "max_tokens", 0) or 0),
                "pipeline_kwargs": {k: _json_safe(kw[k]) for k in SAFE_PIPELINE_KEYS if k in kw},
                "kwargs": {k: _json_safe(v) for k, v in (getattr(plan, "kwargs", None) or {}).items()
                           if k in ("max_continuation_slices",)},
            },
            state={
                "session_id": str(getattr(state, "session_id", "") or ""),
                "messages": self._messages(messages),
                "metadata": _safe_mapping(getattr(state, "metadata", None) or {}),
                "shared": _safe_mapping(getattr(state, "shared", None) or {}),
            },
            input=self._content(copy.deepcopy(getattr(plan, "pipeline_input", "")), limit=0),
            memory={"present": getattr(plan, "memory_provider", None) is not None, "chunks": None},
            tools=self._tool_defs(registry, contributed),
        )

    @staticmethod
    def _tool_defs(registry: Any, contributed: Sequence[str]) -> Dict[str, Any]:
        defs: List[Dict[str, Any]] = []
        if registry is not None:
            from xgen_rsi.base.tools.definition import api_definition

            activated = set(getattr(registry, "activated_names", lambda: [])() or [])
            for tool in registry.list_all():
                name = str(getattr(tool, "name", "") or "")
                if not name or name in contributed:
                    continue
                try:
                    d = api_definition(tool)
                except Exception:  # noqa: BLE001 — 정의를 못 만드는 도구는 재생에서도 못 쓴다
                    continue
                defs.append({
                    "name": name,
                    "description": str(d.get("description") or ""),
                    "input_schema": _json_safe(d.get("input_schema")) or {"type": "object", "properties": {}},
                    "core": bool(registry.is_core(name)),
                    "activated": name in activated,
                    "live": name in LIVE_META_TOOLS,
                })
        return {"defs": defs, "contributed": sorted(str(n) for n in contributed)}

    def on_tool_results(self, tool_calls: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]) -> None:
        by_id = {str(tc.get("tool_use_id") or ""): tc for tc in tool_calls or ()}
        for res in results or ():
            call = by_id.get(str(res.get("tool_use_id") or ""), {})
            self.calls.append({
                "name": str(call.get("tool_name") or res.get("tool_name") or ""),
                "input": _json_safe(call.get("tool_input")) or {},
                "content": self._content(copy.deepcopy(res.get("content"))),
                "is_error": bool(res.get("is_error")),
            })

    def on_memory(self, chunks: Sequence[Any]) -> None:
        out = []
        for c in chunks or ():
            out.append({
                "key": str(getattr(c, "key", "") or ""),
                "source": str(getattr(c, "source", "") or ""),
                "content": str(getattr(c, "content", "") or ""),
                "relevance_score": float(getattr(c, "relevance_score", 0.0) or 0.0),
                "metadata": _safe_mapping(getattr(c, "metadata", None) or {}),
            })
        self.world.setdefault("memory", {"present": True, "chunks": None})["chunks"] = out

    def finish(self, *, state: Any, final_text: str, status: str, termination_reason: str, policy_tokens: int,
               steps: Mapping[str, int]) -> Dict[str, Any]:
        messages = list(getattr(state, "messages", None) or [])
        self.world["tools"] = dict(self.world.get("tools") or {}, calls=list(self.calls))
        self.world["outcome"] = {
            "final_text": str(final_text or ""),
            "status": str(status or ""),
            "termination_reason": str(termination_reason or ""),
            "policy_tokens": int(policy_tokens or 0),
            "steps": dict(steps or {}),
            "transcript": self._messages(messages[self._history_len:], limit=self.max_result_chars),
        }
        try:
            size = len(json.dumps(self.world, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            size = self.max_world_bytes + 1
        self.world["bytes"] = size
        if size > self.max_world_bytes:
            # 재생할 수 없는 세계 — 앞 대화·전사를 비워 기록만 남긴다(호스트 저장소를 지킨다).
            self.world["replayable"] = False
            self.world["state"] = dict(self.world.get("state") or {}, messages=[])
            self.world["outcome"]["transcript"] = []
            self.world["tools"]["calls"] = [dict(c, content="") for c in self.calls]
        return self.world


__all__ = ["IMAGE_PLACEHOLDER", "LIVE_META_TOOLS", "MAX_RESULT_CHARS", "MAX_WORLD_BYTES", "SAFE_PIPELINE_KEYS",
           "WORLD_SCHEMA", "WorldCapture"]
