"""테스트 대역 — 호스트(HostServices 최소면), 각본 클라이언트, 간단한 도구."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from xgen_rsi.base.core.state import TokenUsage
from xgen_rsi.base.llm_client.base import BaseClient, ClientCapabilities
from xgen_rsi.base.llm_client.types import APIResponse, ContentBlock
from xgen_rsi.base.tools.base import Tool, ToolCapabilities, ToolContext, ToolResult


class FakeMemoryProvider:
    async def close(self) -> None:
        return None


class FakeHost:
    """기존 런타임 테스트의 ``_FakeHost`` 와 같은 표면 + 설정 주입·도구 등록."""

    def __init__(
        self,
        *,
        settings: Optional[Dict[str, str]] = None,
        tools: Sequence[Tool] = (),
        storage_root: str = "/tmp/rsi-ws-storage",
    ) -> None:
        self._settings = dict(settings or {})
        self._tools = list(tools)
        self._storage_root = storage_root
        self.finalized = 0

    def setting(self, name: str, default: str = "") -> str:
        return self._settings.get(name, default)

    def setting_truthy(self, name: str) -> bool:
        return str(self._settings.get(name, "")).lower() in ("1", "true", "on", "yes")

    def resolve_model(self, provider, params):
        return "fake-model"

    def resolve_api_key(self, provider, params):
        return "k"

    def resolve_base_url(self, provider, params):
        return None

    def resolve_credentials(self, provider, params):
        return None

    def probe_connector_workspace(self, *a, **k):
        return None

    def make_sandbox(self, *a, **k):
        return None

    def agent_workspace_dir(self, workflow_id, *, create=True):
        return "/tmp/rsi-ws"

    def workspace_storage_root(self, workflow_id):
        return self._storage_root

    def hydrate_workspace(self, workflow_id, run_dir):
        return None

    def publish_workspace(self, *a, **k):
        return None

    def environment_prompt(self, *a, **k):
        return ""

    def build_memory_provider(self, workflow_id, interaction_id):
        return None

    def jobs_prompt_block(self):
        return ""

    def build_connector_mcp_tools(self, *a, **k):
        return []

    def build_job_tools(self, *a, **k):
        return []

    def build_host_skill_tools(self, **k):
        return []

    def register_workflow_self_tools(self, registry, **k):
        return None

    def register_forged_tools(self, *a, **k):
        return None

    def register_builtin_tools(self, registry, **k):
        for tool in self._tools:
            registry.register(tool, core=True)
        return {"tools": [t.name for t in self._tools], "extras": {}, "families": []}

    def build_run_tool_context(self, **k):
        return ToolContext(working_dir="/tmp/rsi-ws")

    def load_ssh_servers(self):
        return []

    def rag_context_builder(self, text, item):
        return None

    def fetch_vllm_max_model_len(self, base_url, model):
        return None

    def agent_vault_root(self, workflow_id):
        return "/tmp/rsi-vault"

    def build_turn_memory_llm(self, *a, **k):
        return None

    def finalize_turn(self, **k):
        self.finalized += 1
        return None

    def build_cli_runtime(self, provider, params):
        return object(), None


class EchoTool(Tool):
    def __init__(self, name: str = "echo", fail: bool = False) -> None:
        self._name = name
        self._fail = fail
        self.calls: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Echo the given text back."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    def capabilities(self, input: Dict[str, Any]) -> ToolCapabilities:
        return ToolCapabilities(concurrency_safe=True)

    async def execute(self, input: Dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls.append(dict(input))
        if self._fail:
            return ToolResult(content="boom", is_error=True)
        return ToolResult(content=f"echo:{input.get('text', '')}")


def text_step(text: str, *, usage: tuple = (10, 5)) -> Dict[str, Any]:
    return {"text": text, "tools": [], "usage": usage}


def tool_step(text: str, calls: Sequence[tuple], *, usage: tuple = (10, 5)) -> Dict[str, Any]:
    return {"text": text, "tools": list(calls), "usage": usage}


class ScriptedClient(BaseClient):
    """각본대로 응답하는 클라이언트. 스트림은 글을 두 조각 text_delta 로 흘린 뒤 끝 프레임을 낸다."""

    provider = "fake"
    capabilities = ClientCapabilities()

    def __init__(self, script: Sequence[Dict[str, Any]], **kw: Any) -> None:
        super().__init__(api_key="k", **kw)
        self.script = list(script)
        self.requests: List[Dict[str, Any]] = []
        self.purposes: List[str] = []

    def _next(self) -> Dict[str, Any]:
        idx = min(len(self.requests) - 1, len(self.script) - 1)
        return self.script[idx]

    def _response(self, step: Dict[str, Any]) -> APIResponse:
        if step.get("raise"):
            raise RuntimeError(step["raise"])
        content: List[ContentBlock] = []
        if step["text"]:
            content.append(ContentBlock(type="text", text=step["text"]))
        for tid, name, inp in step["tools"]:
            content.append(ContentBlock(type="tool_use", tool_use_id=tid, tool_name=name, tool_input=dict(inp)))
        inp_tok, out_tok = step["usage"]
        return APIResponse(
            content=content,
            stop_reason="tool_use" if step["tools"] else "end_turn",
            usage=TokenUsage(input_tokens=inp_tok, output_tokens=out_tok),
            model="fake-model",
        )

    async def _send(self, request: Any, *, purpose: str = "") -> APIResponse:
        self.requests.append({"system": request.system, "messages": request.messages, "tools": request.tools})
        self.purposes.append(purpose)
        return self._response(self._next())

    async def create_message_stream(self, **kwargs: Any):  # type: ignore[override]
        self.requests.append(
            {"system": kwargs.get("system"), "messages": kwargs.get("messages"), "tools": kwargs.get("tools")}
        )
        self.purposes.append(str(kwargs.get("purpose") or ""))
        step = self._next()
        if step.get("raise"):
            raise RuntimeError(step["raise"])
        text = step["text"]
        if text:
            half = max(1, len(text) // 2)
            yield {"type": "text_delta", "text": text[:half]}
            if text[half:]:
                yield {"type": "text_delta", "text": text[half:]}
        for tid, name, inp in step["tools"]:
            yield {"type": "tool_use", "id": tid, "name": name, "input": dict(inp)}
        yield {"type": "message_complete", "response": self._response(step)}


def error_step(message: str) -> Dict[str, Any]:
    """이 호출에서 공급자 오류를 낸다(턴이 오류로 끝나는 경로)."""
    return {"raise": message, "text": "", "tools": [], "usage": (0, 0)}


def tool_context_factory(**k: Any) -> Any:
    return SimpleNamespace(extras=dict(k.get("extras") or {}))
