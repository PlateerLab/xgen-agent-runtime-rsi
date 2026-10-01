"""LocalHost — XGEN 서버 없이 엔진을 쓰는 HostServices 구현(라이브러리 사용·평가 공용).

운영 호스트(xgen-workflow 의 ServerHostServices)와 같은 호출 순서를 받되, 인프라는 이 프로세스 안이다.

* 자격증명: 생성자로 받은 provider·model·키(환경변수를 읽지 않는다)
* 작업 공간: ``workspace`` 디렉터리 하나 — 내장 파일 도구는 그 안으로만(``allowed_paths``)
* 내장 도구: ``builtin_tools`` 로 고른 것만(Read·Write·Edit·Glob·Grep·Bash …, 런타임의 내장 도구 그대로)
* 기억·작업·커넥터·자기진화: 없음 — 대화 이력은 호출자가 ``memory`` 로 넘긴다(:class:`xgen_rsi.agent.GenyRSI`)
* ``rsi_client_factory``: geny-rsi 엔진 전용 선택 훅 — 테스트·재현에서 각본 클라이언트를 끼운다
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from xgen_agent_runtime.tools.base import ToolContext

WORKSPACE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")


class LocalHost:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str = "",
        base_url: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
        builtin_tools: Sequence[str] = (),
        settings: Optional[Mapping[str, str]] = None,
        client_factory: Optional[Callable[[Any], Any]] = None,
        environment_note: Optional[str] = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._credentials = credentials
        self.workspace = os.path.realpath(workspace or tempfile.mkdtemp(prefix="geny-rsi-ws-"))
        os.makedirs(self.workspace, exist_ok=True)
        self.builtin_tools = tuple(builtin_tools)
        self._settings = dict(settings or {})
        self._environment_note = environment_note
        if client_factory is not None:
            self.rsi_client_factory = client_factory

    # ── 설정 ────────────────────────────────────────────────────────────
    def setting(self, name: str, default: str = "") -> str:
        return self._settings.get(name, default)

    def setting_truthy(self, name: str) -> bool:
        return str(self._settings.get(name, "")).strip().lower() in ("1", "true", "yes", "on")

    # ── 자격증명 ────────────────────────────────────────────────────────
    def resolve_model(self, provider: str, params: Dict[str, Any]) -> str:
        return self._model

    def resolve_api_key(self, provider: str, params: Dict[str, Any]) -> str:
        return self._api_key

    def resolve_base_url(self, provider: str, params: Dict[str, Any]) -> Optional[str]:
        return self._base_url

    def resolve_credentials(self, provider: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self._credentials

    # ── 작업 공간·샌드박스 ──────────────────────────────────────────────
    def probe_connector_workspace(self, *a: Any, **k: Any) -> None:
        return None

    def make_sandbox(self, *a: Any, **k: Any) -> None:
        return None

    def agent_workspace_dir(self, workflow_id: str, *, create: bool = True) -> str:
        return self.workspace

    def workspace_storage_root(self, workflow_id: str) -> str:
        path = os.path.join(os.path.dirname(self.workspace), ".geny-rsi-storage")
        os.makedirs(path, exist_ok=True)
        return path

    def hydrate_workspace(self, workflow_id: str, run_dir: str) -> None:
        return None

    def publish_workspace(self, *a: Any, **k: Any) -> None:
        return None

    def environment_prompt(self, sandbox: Any, provider: str) -> str:
        if self._environment_note is not None:
            return self._environment_note
        if not self.builtin_tools:
            return ""
        return f"Your working directory is {self.workspace}. Use absolute paths inside it for file tools."

    # ── 기억·작업·커넥터(없음) ──────────────────────────────────────────
    def build_memory_provider(self, workflow_id: str, interaction_id: str) -> None:
        return None

    def jobs_prompt_block(self) -> str:
        return ""

    def build_connector_mcp_tools(self, *a: Any, **k: Any) -> List[Any]:
        return []

    def build_job_tools(self, *a: Any, **k: Any) -> List[Any]:
        return []

    def build_host_skill_tools(self, **k: Any) -> List[Any]:
        return []

    def register_workflow_self_tools(self, registry: Any, **k: Any) -> None:
        return None

    def register_forged_tools(self, *a: Any, **k: Any) -> None:
        return None

    def load_ssh_servers(self) -> List[Any]:
        return []

    def rag_context_builder(self, text: str, item: Any) -> None:
        return None

    def fetch_vllm_max_model_len(self, base_url: str, model: str) -> None:
        return None

    def agent_vault_root(self, workflow_id: str) -> str:
        return os.path.join(os.path.dirname(self.workspace), ".geny-rsi-vault")

    def build_turn_memory_llm(self, *a: Any, **k: Any) -> None:
        return None

    # ── 도구 ────────────────────────────────────────────────────────────
    def register_builtin_tools(self, registry: Any, **k: Any) -> Dict[str, Any]:
        from xgen_agent_runtime.tools.built_in import BUILT_IN_TOOL_CLASSES

        names: List[str] = []
        for name in self.builtin_tools:
            cls = BUILT_IN_TOOL_CLASSES.get(name)
            if cls is None:
                raise ValueError(f"unknown built-in tool {name!r}; known: {sorted(BUILT_IN_TOOL_CLASSES)}")
            registry.register(cls(), core=True)
            names.append(name)
        return {"tools": names, "extras": {}, "families": []}

    def build_run_tool_context(self, **k: Any) -> ToolContext:
        return ToolContext(working_dir=self.workspace, allowed_paths=[self.workspace])

    # ── 턴 끝 ───────────────────────────────────────────────────────────
    def finalize_turn(self, **k: Any) -> None:
        return None

    def build_cli_runtime(self, provider: str, params: Dict[str, Any]) -> Any:
        raise RuntimeError("LocalHost does not provide CLI runtimes; use an SDK provider (anthropic, openai, ...)")


__all__ = ["LocalHost", "WORKSPACE_TOOLS"]
