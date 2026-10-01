"""Information hiding: a malicious policy cannot read unrevealed scores (04 §7.1, R-10)."""

from __future__ import annotations

import textwrap

import pytest

from tests.explore.synthetic import toy_world
from xgen_rsi.dream.replay import ReplayQuestion, run_episode
from xgen_rsi.dream.sandbox import PolicyRejected, load_policy
from xgen_rsi.explore.api import LLMDesignedMethod

HEADER = textwrap.dedent('''
    from xgen_rsi.explore.api import LLMDesignedMethod, SimResult
    NAME = "OptimalPolicy"
''')


def _policy(body: str, extra: str = "") -> str:
    return HEADER + extra + textwrap.dedent('''
    class OptimalPolicy(LLMDesignedMethod):
        def solve(self, question, budget=None):
            question.reset()
    ''') + textwrap.indent(textwrap.dedent(body), "        ") + "\n        return SimResult()\n"


ATTACKS = {
    "closure": ("leak = question.observed.__closure__[0].cell_contents", "dunder-attribute"),
    "private slot": ("leak = question._fns", "private-attribute"),
    "vars": ("leak = vars(question)", "forbidden-name"),
    "dict": ("leak = question.__dict__", "dunder-attribute"),
    "getattr": ("leak = getattr(question, '_fns')", "forbidden-name"),
    "type": ("leak = type(question).__mro__", "forbidden-name"),
    "globals": ("leak = question.probe_batch.__globals__", "dunder-attribute"),
    "builtins": ("leak = __builtins__", "forbidden-name"),
    "format walk": ("leak = '{0.observed.__closure__}'.format(question)", "format-string"),
    "frames": ("g = (x for x in [1])\nleak = g.gi_frame.f_back.f_locals", "introspection"),
    "best_so_far": ("leak = question.best_so_far", "prefix-only"),
    "budget_spent": ("leak = question.budget_spent", "prefix-only"),
    "inspect": ("import inspect\nleak = inspect.currentframe()", "import"),
    "gc": ("import gc\nleak = gc.get_objects()", "import"),
    "eval": ("leak = eval('question._fns')", "forbidden-name"),
    "swallow timeout": ("try:\n    pass\nexcept BaseException:\n    pass", "forbidden-name"),
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_introspection_attacks_are_rejected(name: str) -> None:
    body, rule = ATTACKS[name]
    with pytest.raises(PolicyRejected) as exc:
        load_policy(_policy(body))
    assert rule in exc.value.report.rules


def test_hardcoded_cell_ids_and_scores_are_rejected() -> None:
    w = toy_world()
    src = _policy("question.probe_batch(['b1a0'])\nquestion.probe_batch(['b1a1'])")
    with pytest.raises(PolicyRejected) as exc:
        load_policy(src, cell_ids=w.cell_ids)
    assert "hardcoded-cell-id" in exc.value.report.rules
    doc = '"""b1a0"""\n' + _policy("x = 1")                  # a docstring is not code
    assert load_policy(doc, cell_ids=w.cell_ids)
    with pytest.raises(PolicyRejected):
        load_policy(_policy("x = 1\n'b1a0'"), cell_ids=w.cell_ids)   # a bare expression is
    src2 = _policy("target = 0.7333\nwanted = 0.5")
    with pytest.raises(PolicyRejected) as exc2:
        load_policy(src2, score_values=[0.7333, 0.5])
    assert exc2.value.report.rules == {"hardcoded-score"}      # 0.5 is too round to flag


def test_facade_exposes_no_world_through_attributes() -> None:
    q = ReplayQuestion(toy_world(), 2)
    with pytest.raises(TypeError):
        vars(q)
    assert not hasattr(q, "__dict__")
    public = {n for n in dir(q) if not n.startswith("_")}
    assert public == {"reset", "observed", "legal_actions", "legal_roots", "opened_branches",
                      "meta", "probe_batch", "baseline_score", "max_parallelism", "best_so_far",
                      "budget_spent"}
    with pytest.raises(AttributeError):
        q.world = None
    with pytest.raises(AttributeError):
        q.anything = 1
    # the only private slot holds API closures, not the world
    assert all(callable(f) for f in object.__getattribute__(q, "_fns"))
    q.probe_batch(["b1a0"])
    assert set(q.observed()) == {"b1a0"}
    m = q.meta("b1a1")
    assert not hasattr(m, "score") and m.seq == -1


def test_aliased_private_access_yields_only_the_api() -> None:
    """``self._x`` passes the static check; aliasing the question into ``self`` only reaches
    the API closures, which reveal nothing beyond the prefix."""
    src = _policy('''
    def peek(self):
        return self._fns
    fns = peek(question)
    seen = fns[1]()
    question.probe_batch(question.legal_roots()[:1])
    after = fns[1]()
    self.config["leak"] = (len(seen), len(after))
    ''')
    loaded = load_policy(src)
    run = run_episode(loaded, toy_world(), 0.5, 2)
    assert run.error is None and run.episode.n_revealed == 1


def test_no_state_survives_between_episodes() -> None:
    """Module globals / class attributes are rebuilt for every episode (fresh namespace)."""
    src = _policy('''
    if STASH:
        question.probe_batch(question.legal_roots()[:1])
    else:
        STASH.append(question.legal_roots())
        question.probe_batch(question.legal_roots()[:2])
    ''', extra="STASH = []\n")
    loaded = load_policy(src)
    first = run_episode(loaded, toy_world(), 1.0, 2)
    second = run_episode(loaded, toy_world(), 0.0, 2)
    assert first.episode.batch_sizes == second.episode.batch_sizes == (2,)


def test_mutating_shared_classes_fails_the_episode() -> None:
    src = _policy("base = LLMDesignedMethod\nbase.leak = question.legal_roots()")
    loaded = load_policy(src)
    try:
        run = run_episode(loaded, toy_world(), 0.5, 2)
        assert run.error and "PolicyIntegrityError" in run.error
    finally:
        if hasattr(LLMDesignedMethod, "leak"):
            del LLMDesignedMethod.leak


def test_module_proxies_hide_submodules() -> None:
    src = _policy("import dataclasses\nleak = dataclasses.sys.modules")
    run = run_episode(load_policy(src), toy_world(), 0.5, 2)
    assert run.error and "AttributeError" in run.error
