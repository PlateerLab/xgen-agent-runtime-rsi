"""역할 LLM — 호출마다 그 루프에서 클라이언트를 만들고 닫는다(루프에 묶인 클라이언트 재사용 장애의 회귀 테스트)."""

from __future__ import annotations

import asyncio
import concurrent.futures as cf

from xgen_agent_runtime.core.state import TokenUsage
from xgen_agent_runtime.host import runner as runner_mod
from xgen_agent_runtime.llm_client.types import APIResponse, ContentBlock

from xgen_rsi.roles.llm import RoleLLM, RoleModel


class _LoopBoundClient:
    """처음 쓴 이벤트 루프에서만 동작하는 클라이언트(httpx AsyncClient 처럼)."""

    provider = "fake"
    created = 0
    closed = 0

    def __init__(self) -> None:
        type(self).created += 1
        self._loop = None

    async def create_message(self, **kwargs):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise RuntimeError("Connection error.")
        return APIResponse(content=[ContentBlock(type="text", text="ok")], stop_reason="end_turn",
                           usage=TokenUsage(input_tokens=3, output_tokens=1), model="m")

    async def aclose(self):
        type(self).closed += 1


def test_parallel_generate_never_reuses_a_loop_bound_client(monkeypatch):
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: _LoopBoundClient())
    llm = RoleLLM(RoleModel(provider="openai", model="m", api_key="k"), role="digester")
    with cf.ThreadPoolExecutor(4) as ex:
        outs = list(ex.map(lambda i: llm.generate(f"q{i}", max_retries=1), range(8)))
    assert outs == ["ok"] * 8
    assert _LoopBoundClient.created == 8 and _LoopBoundClient.closed == 8
    assert len(llm.ledger.records) == 8
