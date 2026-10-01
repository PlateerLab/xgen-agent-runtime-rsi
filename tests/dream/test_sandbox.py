"""Sandbox: import allow-list, forbidden names, determinism blockers, wall-clock limit."""

from __future__ import annotations

import time

import pytest

from tests.dream.test_hiding import _policy
from tests.explore.synthetic import toy_world
from xgen_rsi.dream.replay import run_episode
from xgen_rsi.dream.sandbox import (
    PolicyRejected,
    PolicyTimeout,
    check_structure,
    load_policy,
    run_limited,
    static_check,
)


@pytest.mark.parametrize("mod", ["os", "sys", "subprocess", "socket", "time", "random",
                                 "importlib", "pathlib", "threading", "builtins"])
def test_forbidden_imports(mod: str) -> None:
    for stmt in (f"import {mod}", f"from {mod} import x"):
        report = static_check(_policy(stmt))
        assert "import" in report.rules, stmt


@pytest.mark.parametrize("name", ["open", "eval", "exec", "compile", "__import__", "globals",
                                  "locals", "id", "hash", "breakpoint", "input"])
def test_forbidden_names(name: str) -> None:
    assert "forbidden-name" in static_check(_policy(f"x = {name}")).rules


def test_other_static_rules() -> None:
    assert "import" in static_check(_policy("from . import x")).rules
    assert "import" in static_check(_policy("from math import *")).rules
    assert "catch-all" in static_check(_policy("try:\n    pass\nexcept:\n    pass")).rules
    assert "finally" in static_check(_policy("try:\n    pass\nfinally:\n    pass")).rules
    assert "mutate-import" in static_check(_policy("SimResult.x = 1")).rules
    assert "forbidden-method" in static_check(
        _policy("pass", extra="class C:\n    def __exit__(self, *a):\n        return True\n")).rules
    assert "async" in static_check(
        _policy("pass", extra="async def f():\n    pass\n")).rules
    assert "format-string" in static_check(_policy("fmt = 'x'\ny = fmt.format(1)")).rules
    assert static_check(_policy("y = '{:.3f} {name}'.format(1.0, name='a')")).ok
    assert static_check(_policy("y = f'{question.max_parallelism:d}'")).ok
    assert "syntax" in static_check("def (:").rules


def test_allowed_imports_load_and_run() -> None:
    src = _policy('''
    s = sorted(question.legal_roots(), key=lambda c: (bisect.bisect([1], 0), c))
    dq = deque(s)
    total = math.fsum([1.0, 2.0]) + statistics.mean([1, 2]) + heapq.nsmallest(1, [3, 1])[0]
    question.probe_batch([dq.popleft()])
    ''', extra=("import math, statistics, heapq, bisect, itertools, functools, typing\n"
                "import collections.abc\nfrom collections import deque\n"
                "from dataclasses import dataclass\nfrom see.policy.api import finalize_result\n"
                "from xgen_rsi.explore import signals\n"
                "@dataclass\nclass Box:\n    v: int = 0\n"))
    run = run_episode(load_policy(src), toy_world(), 0.5, 2)
    assert run.error is None and run.episode.n_revealed == 1


def test_structure_check() -> None:
    good = _policy("pass").replace("def solve", "def plan_grid(self, context):\n        return None\n    def solve")
    assert check_structure(good).ok
    report = check_structure(_policy("pass"))
    assert not report.ok and "plan_grid" in report.summary()
    assert not check_structure("class OptimalPolicy: pass").ok      # NAME missing
    with pytest.raises(PolicyRejected, match="plan_grid"):
        load_policy(_policy("pass"), require=("solve", "plan_grid"))
    with pytest.raises(PolicyRejected, match="subclass"):
        load_policy('NAME = "OptimalPolicy"\nclass OptimalPolicy:\n    pass\n')
    with pytest.raises(PolicyRejected, match="load"):
        load_policy('NAME = "OptimalPolicy"\nraise ValueError("at import")\n')


def test_wall_clock_limit_stops_runaway_policies() -> None:
    spin = load_policy(_policy("while True:\n    x = 1"), timeout=0.3)
    t0 = time.monotonic()
    run = run_episode(spin, toy_world(), 0.5, 2)
    assert run.error and run.error.startswith("timeout")
    assert run.episode.stop_reason == "timeout" and time.monotonic() - t0 < 3.0
    # catching Exception does not swallow the timeout raised by question methods
    stubborn = load_policy(_policy(
        "while True:\n    try:\n        question.legal_actions()\n    except Exception:\n        pass"),
        timeout=0.3)
    assert run_episode(stubborn, toy_world(), 0.5, 2).episode.stop_reason == "timeout"
    with pytest.raises(PolicyTimeout, match="abandoned"):
        run_limited(lambda: time.sleep(0.5), timeout=0.05, grace=0.05)


def test_import_time_loops_are_rejected() -> None:
    with pytest.raises(PolicyRejected, match="load"):
        load_policy(_policy("pass", extra="while True:\n    pass\n"), timeout=0.2)


def test_print_is_a_noop_and_run_limited_inline() -> None:
    run = run_episode(load_policy(_policy("print('hello', question.max_parallelism)")),
                      toy_world(), 0.5, 2)
    assert run.error is None
    assert run_limited(lambda: 41 + 1, timeout=None) == 42
    with pytest.raises(ZeroDivisionError):
        run_limited(lambda: 1 / 0, timeout=1.0)


# ── 프로세스 격리(검토 P1-3): 긴 C 호출·메모리 폭탄이 평가기를 멈추거나 죽이지 못한다 ──────────────


def _bomb_policy(body: str) -> str:
    return (
        "from xgen_rsi.explore.api import LLMDesignedMethod, SimResult\n"
        'NAME = "OptimalPolicy"\n'
        "class OptimalPolicy(LLMDesignedMethod):\n"
        "    def solve(self, question, budget=None):\n"
        f"        {body}\n"
        "        return SimResult()\n"
    )


def test_long_c_call_is_killed_by_the_cpu_limit() -> None:
    import time

    from xgen_rsi.dream.replay import run_episode
    from xgen_rsi.dream.sandbox import load_policy
    from xgen_rsi.dream.world import World

    world = World.from_branches({0: [0.5, 0.6], 1: [0.4]}, root_score=0.1)
    lp = load_policy(_bomb_policy("x = sum(range(10**11))"), timeout=0.5)
    t = time.monotonic()
    run = run_episode(lp, world, None, 2)
    assert time.monotonic() - t < 15 and run.error and run.episode.n_revealed == 0


def test_memory_bomb_is_contained() -> None:
    from xgen_rsi.dream.replay import run_episode
    from xgen_rsi.dream.sandbox import load_policy
    from xgen_rsi.dream.world import World

    world = World.from_branches({0: [0.5]}, root_score=0.1)
    run = run_episode(load_policy(_bomb_policy("x = [0] * (10**10)"), timeout=1.0), world, None, 1)
    assert run.error and "MemoryError" in run.error


def test_isolated_episode_equals_in_process_episode() -> None:
    from xgen_rsi.dream.replay import run_episode
    from xgen_rsi.dream.sandbox import load_policy
    from xgen_rsi.dream.world import World
    from xgen_rsi.explore.policies import builtin_policy_source

    world = World.from_branches({0: [0.2, 0.5, 0.9], 1: [0.4, 0.3], 2: [0.1]}, root_score=0.1)
    lp = load_policy(builtin_policy_source("portfolio"), timeout=5.0)
    assert run_episode(lp, world, 0.6, 2).episode == run_episode(lp, world, 0.6, 2, isolate=False).episode
