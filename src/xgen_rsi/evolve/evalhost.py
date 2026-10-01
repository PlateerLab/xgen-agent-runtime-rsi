"""평가 호스트 — 오프라인 평가가 **실제 엔진·실제 호스트 프로토콜**로 돌게 하는 HostServices 구현.

:class:`xgen_rsi.host.LocalHost` 에 평가용 도구 묶음 이름을 붙인 것이다.

* 작업 공간: 시행마다 독립 임시 디렉터리(파일 도구는 그 안으로만 — ``allowed_paths``)
* 도구 묶음: ``none`` | ``workspace``(Read/Write/Edit/Glob/Grep) | ``workspace+bash``(+Bash, 로컬 실행 — 신뢰된 환경에서만)
* 기억·작업·커넥터·자기진화: 없음(평가는 기억 없이, 하네스만 잰다)
* 정책 모델: 호출자가 넘긴 자격증명(XGEN 에 등록된 LLM) — 환경변수에서 읽지 않는다
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from xgen_rsi.host import WORKSPACE_TOOLS, LocalHost

TOOLSETS: Dict[str, tuple] = {
    "none": (),
    "workspace": WORKSPACE_TOOLS,
    "workspace+bash": WORKSPACE_TOOLS + ("Bash",),
}


class EvalHost(LocalHost):
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str = "",
        base_url: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
        workspace: str,
        toolset: str = "workspace",
        settings: Optional[Mapping[str, str]] = None,
        client_factory: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        if toolset not in TOOLSETS:
            raise ValueError(f"toolset must be one of {sorted(TOOLSETS)}, got {toolset!r}")
        super().__init__(provider=provider, model=model, api_key=api_key, base_url=base_url,
                         credentials=credentials, workspace=workspace, builtin_tools=TOOLSETS[toolset],
                         settings=settings, client_factory=client_factory)
        self.toolset = toolset

    def build_cli_runtime(self, provider: str, params: Dict[str, Any]) -> Any:
        raise RuntimeError("EvalHost does not provide CLI runtimes; evaluate SDK providers")
