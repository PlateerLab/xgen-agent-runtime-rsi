"""탐색 역할(proposer · critic · analyst · digester · policy-development agent · judge)의 LLM 호출.

공식 RRSI 구현은 Claude Opus 4.8 on Vertex 를 직접 불렀다(``rrsi/llm.py``). 여기서는 기존 런타임의 다중 공급자
계층(``llm_client``)을 그대로 쓴다 — XGEN 에 등록된 모델이면 무엇이든 역할에 쓸 수 있다(운영 규범: 등록된 LLM 그대로,
키는 XGEN 설정에서). 역할 호출도 원장에 남겨 **진화 비용**(정책 비용 c(τ)와 별도)을 잰다.

설정은 :class:`RoleModel` 하나다. 자격증명은 호출자가 넘긴다(환경변수에서 읽지 않는다).

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/llm.py``: the JSON-only
instruction and the JSON extraction helper), Copyright 2026 The rrsi Authors / Google LLC,
Apache License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xgen_rsi.kernel.ledger import LedgerClient, UsageLedger


@dataclass(frozen=True)
class RoleModel:
    """역할 하나에 쓸 모델."""

    provider: str
    model: str
    api_key: str = ""
    base_url: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    max_tokens: int = 16000
    temperature: float = 0.2
    thinking_level: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "RoleModel":
        return cls(
            provider=str(raw["provider"]),
            model=str(raw["model"]),
            api_key=str(raw.get("api_key") or ""),
            base_url=raw.get("base_url"),
            credentials=raw.get("credentials"),
            max_tokens=int(raw.get("max_tokens", 16000)),
            temperature=float(raw.get("temperature", 0.2)),
            thinking_level=raw.get("thinking_level"),
        )


_JSON_SUFFIX = "\n\nOutput ONLY a single valid JSON object. No prose before or after, no markdown fences."


def extract_json(text: str) -> str:
    """응답에서 JSON 객체/배열을 잘라 낸다(공식 구현 ``rrsi.llm.extract_json`` 과 같은 규칙)."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith("{") and not t.startswith("["):
        starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
        if starts:
            t = t[min(starts) :]
    if t and t[0] == "{" and not t.endswith("}"):
        end = t.rfind("}")
        if end != -1:
            t = t[: end + 1]
    if t and t[0] == "[" and not t.endswith("]"):
        end = t.rfind("]")
        if end != -1:
            t = t[: end + 1]
    return t


class RoleLLM:
    """역할 LLM — 동기 ``generate``(스레드 안전).

    호출마다 **그 호출의 이벤트 루프 안에서** 공급자 클라이언트를 만들고 닫는다. 비동기 HTTP 클라이언트는 만든 루프에
    묶이므로, 하나를 캐시해 여러 ``asyncio.run``·여러 스레드에서 같이 쓰면 두 번째 호출부터 "Connection error" 로
    실패한다(실측 — digester 를 병렬로 돌릴 때). 테스트가 넘긴 ``client`` 는 그대로 쓴다.
    """

    def __init__(self, model: RoleModel, *, role: str, ledger: Optional[UsageLedger] = None, client: Any = None) -> None:
        self.model = model
        self.role = role
        self.ledger = ledger or UsageLedger(provider=model.provider)
        self._client = client
        self._lock = threading.Lock()

    def _new_client(self) -> Any:
        from xgen_agent_runtime.host.runner import build_client

        m = self.model
        if m.credentials:
            return build_client(m.provider, m.api_key, m.base_url, credentials=m.credentials)
        return build_client(m.provider, m.api_key, m.base_url)

    async def _call(self, **kwargs: Any) -> Any:
        injected = self._client is not None
        raw = self._client if injected else self._new_client()
        try:
            return await LedgerClient(raw, self.ledger).create_message(**kwargs)
        finally:
            if not injected:
                aclose = getattr(raw, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:  # noqa: BLE001 — 닫기 실패는 결과를 바꾸지 않는다
                        pass

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        json_only: bool = False,
        cache_prefix: Optional[str] = None,
        max_retries: int = 3,
    ) -> str:
        """프롬프트 하나 → 응답 텍스트. ``json_only`` 면 JSON 부분만 돌려준다."""
        from xgen_agent_runtime.core.config import ModelConfig

        cfg = ModelConfig(
            model=self.model.model,
            max_tokens=self.model.max_tokens,
            temperature=self.model.temperature,
            thinking_level=self.model.thinking_level,
        )
        content: Any
        if cache_prefix:
            content = [
                {"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": prompt},
            ]
        else:
            content = prompt
        messages: List[Dict[str, Any]] = [{"role": "user", "content": content}]
        sys_prompt = (system or "") + (_JSON_SUFFIX if json_only else "")
        last: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                response = _run(self._call(model_config=cfg, messages=messages, system=sys_prompt, purpose=f"role:{self.role}"))
                text = response.text
                if text:
                    return extract_json(text) if json_only else text
                last = RuntimeError("empty response")
            except Exception as exc:  # noqa: BLE001 — 재시도
                last = exc
        raise RuntimeError(f"{self.role}: generate failed after {max_retries} tries: {last}")

    def generate_json(self, prompt: str, *, system: str = "", cache_prefix: Optional[str] = None) -> Any:
        raw = self.generate(prompt, system=system, json_only=True, cache_prefix=cache_prefix)
        return json.loads(raw)


def _run(coro: Any) -> Any:
    """동기 맥락에서 코루틴을 돌린다(이미 루프가 도는 스레드면 새 스레드에서)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


@dataclass
class ScriptedRoleLLM:
    """테스트·오프라인 재현용 — 응답을 차례로 돌려준다(``RoleLLM`` 과 같은 표면)."""

    responses: List[str]
    role: str = "scripted"
    calls: List[Dict[str, Any]] = field(default_factory=list)

    def generate(self, prompt: str, *, system: str = "", json_only: bool = False, cache_prefix: Optional[str] = None, max_retries: int = 3) -> str:
        self.calls.append({"prompt": prompt, "system": system, "cache_prefix": cache_prefix})
        if not self.responses:
            raise RuntimeError("scripted role LLM exhausted")
        out = self.responses.pop(0)
        return extract_json(out) if json_only else out

    def generate_json(self, prompt: str, *, system: str = "", cache_prefix: Optional[str] = None) -> Any:
        return json.loads(self.generate(prompt, system=system, json_only=True, cache_prefix=cache_prefix))
