"""geny-rsi — XGEN 에이전트의 실행 코어를 갈아끼우는 자기 개선 하네스(RRSI + Dream-RSI).

가장 쉬운 사용::

    from xgen_rsi import GenyRSI

    agent = GenyRSI.minimal(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-...")
    print(agent.run_sync("What is the capital of France?").text)

호스트(xgen-workflow 등)에서는 ``AgentTurnExecutor().run(host, **kwargs)`` 자리를 ``GenyRSITurnExecutor().run(host, **kwargs)``
로 바꾼다 — 같은 계약이고, xgen-agent-runtime 과 이 패키지는 서로 의존하지 않는다.
"""

from typing import Any

__version__ = "0.3.0"

__all__ = ["GenyRSI", "GenyRSITurnExecutor", "LocalHost", "RunResult", "__version__"]


def __getattr__(name: str) -> Any:  # 무거운 런타임 import 는 쓸 때만
    if name in ("GenyRSI", "RunResult"):
        from xgen_rsi import agent

        return getattr(agent, name)
    if name == "GenyRSITurnExecutor":
        from xgen_rsi.turn_executor import GenyRSITurnExecutor

        return GenyRSITurnExecutor
    if name == "LocalHost":
        from xgen_rsi.host import LocalHost

        return LocalHost
    raise AttributeError(name)
