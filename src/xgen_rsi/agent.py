"""GenyRSI — xgen-agent-runtime 의 ``PipelinePresets`` 와 같은 사용감의 진입점.

.. code-block:: python

    from xgen_rsi import GenyRSI

    agent = GenyRSI.minimal(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-...")
    result = await agent.run("What is the capital of France?")
    print(result.text, result.input_tokens, result.output_tokens)

    agent = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...",
                          workspace="./ws", tools=[MyTool()])          # 내장 파일 도구 + 내 도구, 대화 이력 유지
    for chunk in agent.stream_sync("Summarize report.md into summary.json"):
        print(chunk, end="")

    baseline = GenyRSI.agent(..., engine="geny")                       # 같은 호출로 기존 21-stage 엔진(A/B 비교)

턴 하나는 운영과 **같은 계약**으로 돈다 — ``geny-rsi`` 는 ``GenyRSITurnExecutor().run(host, **kwargs)``(턴 조립 26단계 →
이 패키지의 커널 + 하네스), ``geny`` 는 기존 런타임의 ``AgentTurnExecutor().run(host, **kwargs)``(21-stage). 호스트는
:class:`LocalHost`(이 프로세스 안의 작업 공간·내장 도구·자격증명).
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Mapping, Optional, Sequence

from xgen_rsi.host import WORKSPACE_TOOLS, LocalHost

ENGINES = ("geny-rsi", "geny")

#: 공급자별 기본 모델 — 이 저장소의 비교 실험에 쓴 모델(2026-10). ``model=`` 로 바꾼다.
DEFAULT_MODELS: Dict[str, str] = {"anthropic": "claude-sonnet-5", "openai": "gpt-6-sol"}


@dataclass
class RunResult:
    """턴 하나의 결과."""

    text: str
    engine: str
    usage: Dict[str, Any] = field(default_factory=dict)
    harness: Optional[str] = None
    record: Optional[Dict[str, Any]] = None

    @property
    def input_tokens(self) -> int:
        return int(self.usage.get("input_tokens") or 0)

    @property
    def output_tokens(self) -> int:
        return int(self.usage.get("output_tokens") or 0)

    @property
    def total_cost_usd(self) -> Optional[float]:
        cost = self.usage.get("total_cost_usd")
        return float(cost) if cost is not None else None

    @property
    def failed(self) -> bool:
        return self.text.startswith("[ERROR]")


class GenyRSI:
    """geny-rsi 에이전트 하나. 같은 객체로 여러 턴을 돌리면(``keep_history``) 대화가 이어진다."""

    def __init__(
        self,
        *,
        provider: str = "anthropic",
        model: Optional[str] = None,
        api_key: str = "",
        base_url: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[Sequence[Any]] = None,
        builtin_tools: Sequence[str] = (),
        workspace: Optional[str] = None,
        harness: Optional[str] = None,
        engine: str = "geny-rsi",
        keep_history: bool = False,
        max_iterations: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        thinking: Optional[str] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        record_dir: Optional[str] = None,
        settings: Optional[Mapping[str, str]] = None,
        client_factory: Optional[Callable[[Any], Any]] = None,
        **turn_kwargs: Any,
    ) -> None:
        if engine not in ENGINES:
            raise ValueError(f"engine must be one of {ENGINES}, got {engine!r}")
        resolved = model or DEFAULT_MODELS.get(provider)
        if not resolved:
            raise ValueError(f"model is required for provider {provider!r}")
        self.provider = provider
        self.model = resolved
        self.engine = engine
        self.system_prompt = system_prompt
        self.tools = list(tools or ())
        self.keep_history = keep_history
        self.output_schema = output_schema
        self.record_dir = record_dir
        self._turn_kwargs: Dict[str, Any] = dict(turn_kwargs)
        for key, value in (("max_iterations", max_iterations), ("max_tokens", max_tokens),
                           ("temperature", temperature), ("thinking", thinking)):
            if value is not None:
                self._turn_kwargs[key] = value
        host_settings = dict(settings or {})
        self.harness_dir = str(Path(harness).resolve()) if harness else None
        if self.harness_dir:
            host_settings["XGEN_RSI_HARNESS_DIR"] = self.harness_dir
        if record_dir:
            host_settings["XGEN_RSI_RECORD_DIR"] = str(Path(record_dir).resolve())
        self.host = LocalHost(provider=provider, model=resolved, api_key=api_key, base_url=base_url,
                              credentials=credentials, workspace=workspace, builtin_tools=builtin_tools,
                              settings=host_settings, client_factory=client_factory)
        self._history: List[Dict[str, Any]] = []
        self._turns = 0

    # ── 프리셋 (xgen-agent-runtime ``PipelinePresets`` 와 같은 이름) ─────────
    @classmethod
    def minimal(cls, *, api_key: str = "", provider: str = "anthropic", model: Optional[str] = None,
                **kw: Any) -> "GenyRSI":
        """도구 없음, 대화 이력 없음 — 질문 하나에 답 하나."""
        return cls(provider=provider, model=model, api_key=api_key, **kw)

    @classmethod
    def chat(cls, *, api_key: str = "", system_prompt: Optional[str] = None, provider: str = "anthropic",
             model: Optional[str] = None, tools: Optional[Sequence[Any]] = None, **kw: Any) -> "GenyRSI":
        """대화 이력 유지 + 시스템 프롬프트(+ 선택 도구)."""
        return cls(provider=provider, model=model, api_key=api_key, system_prompt=system_prompt, tools=tools,
                   keep_history=True, **kw)

    @classmethod
    def agent(cls, *, api_key: str = "", system_prompt: Optional[str] = None, provider: str = "anthropic",
              model: Optional[str] = None, tools: Optional[Sequence[Any]] = None, workspace: Optional[str] = None,
              builtin_tools: Sequence[str] = WORKSPACE_TOOLS, max_iterations: int = 20, **kw: Any) -> "GenyRSI":
        """작업 공간 + 내장 파일 도구(Read·Write·Edit·Glob·Grep) + 내 도구 + 대화 이력."""
        return cls(provider=provider, model=model, api_key=api_key, system_prompt=system_prompt, tools=tools,
                   workspace=workspace, builtin_tools=builtin_tools, max_iterations=max_iterations,
                   keep_history=True, **kw)

    # ── 상태 ────────────────────────────────────────────────────────────
    @property
    def workspace(self) -> str:
        return self.host.workspace

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def reset(self) -> None:
        """대화 이력을 비운다(작업 공간은 그대로)."""
        self._history.clear()

    # ── 실행 ────────────────────────────────────────────────────────────
    def _params(self, text: str, *, streaming: bool, usage_sink: Dict[str, Any]) -> Dict[str, Any]:
        self._turns += 1
        params: Dict[str, Any] = dict(
            text=text,
            provider=self.provider,
            streaming=streaming,
            interaction_id=f"geny-rsi-{id(self):x}-{self._turns}",
            workflow_id=f"geny-rsi-{id(self):x}",
            workflow_name="geny-rsi",
            user_id="local",
            enable_memory=False,
            memory_distill=False,
            enable_self_evolution=False,
            usage_sink=usage_sink,
        )
        params.update(self._turn_kwargs)
        if self.system_prompt is not None:
            params["system_prompt"] = self.system_prompt
        if self.tools:
            params["tools"] = list(self.tools)
        if self.output_schema is not None:
            params["output_schema"] = self.output_schema
        if self.keep_history and self._history:
            params["memory"] = list(self._history)
        return params

    def _execute(self, text: str, *, streaming: bool, usage_sink: Dict[str, Any]) -> Any:
        from xgen_rsi.evolve.runner import turn_executor

        return turn_executor(self.engine).run(self.host, **self._params(text, streaming=streaming, usage_sink=usage_sink))

    def _finish(self, text: str, answer: str, usage: Dict[str, Any], before: set) -> RunResult:
        record = None
        harness = None
        if self.engine == "geny-rsi":
            from xgen_rsi.harness.spec import load_manifest
            from xgen_rsi.kernel.executor import resolve_harness_dir

            m = load_manifest(resolve_harness_dir(self.host, self.provider, self.model)[0])
            harness = f"{m.name}@{m.version_id()}"
            if self.record_dir:
                new = sorted(set(Path(self.record_dir).glob("*.json")) - before, key=os.path.getmtime)
                if new:
                    try:
                        record = json.loads(new[-1].read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        record = None
        if self.keep_history and not answer.startswith("[ERROR]"):
            self._history += [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
        return RunResult(text=answer, engine=self.engine, usage=dict(usage), harness=harness, record=record)

    def _records(self) -> set:
        if self.engine != "geny-rsi" or not self.record_dir or not Path(self.record_dir).exists():
            return set()
        return set(Path(self.record_dir).glob("*.json"))

    def run_sync(self, text: str) -> RunResult:
        """턴 하나를 돌리고 결과를 돌려준다(동기)."""
        usage: Dict[str, Any] = {}
        before = self._records()
        out = self._execute(text, streaming=False, usage_sink=usage)
        answer = out if isinstance(out, str) else "".join(c for c in out if isinstance(c, str))
        return self._finish(text, answer, usage, before)

    async def run(self, text: str) -> RunResult:
        """턴 하나(비동기 — 엔진은 자기 이벤트 루프를 쓰므로 작업 스레드에서 돈다)."""
        return await asyncio.to_thread(self.run_sync, text)

    def stream_sync(self, text: str) -> Iterator[str]:
        """글 조각을 차례로 내놓는다. 끝나면 :attr:`last_result` 에 결과가 남는다."""
        usage: Dict[str, Any] = {}
        before = self._records()
        parts: List[str] = []
        for chunk in self._execute(text, streaming=True, usage_sink=usage):
            if isinstance(chunk, str):
                parts.append(chunk)
                yield chunk
        self.last_result = self._finish(text, "".join(parts), usage, before)

    async def stream(self, text: str) -> AsyncIterator[str]:
        """비동기 스트림 — 작업 스레드의 동기 스트림을 큐로 옮긴다."""
        q: "queue.Queue[Any]" = queue.Queue()
        done = object()

        def _pump() -> None:
            try:
                for chunk in self.stream_sync(text):
                    q.put(chunk)
            except BaseException as exc:  # noqa: BLE001 — 호출자에게 그대로 전한다
                q.put(exc)
            finally:
                q.put(done)

        threading.Thread(target=_pump, name="geny-rsi-stream", daemon=True).start()
        while True:
            item = await asyncio.to_thread(q.get)
            if item is done:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    last_result: Optional[RunResult] = None


__all__ = ["DEFAULT_MODELS", "ENGINES", "GenyRSI", "RunResult"]
