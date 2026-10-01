"""Batch legality (04 §3.1/§3.3) and the helper signals' semantics."""

from __future__ import annotations

import pytest

from xgen_rsi.dream.replay import ReplayQuestion
from xgen_rsi.dream.world import World
from xgen_rsi.explore.api import ROOT, Observation, check_batch
from xgen_rsi.explore.signals import (
    branch_failed_hard,
    branch_promising,
    failure_kind,
    is_success,
    probe_improved_vs_baseline,
    probe_improved_vs_parent,
)


def _world() -> World:
    return World.from_branches({0: [0.5, 0.6, 0.7], 1: [0.4, 0.45], 2: [0.3], 3: [0.2, 0.1]},
                               root_score=0.35, world_id="legal")


def _pos(c: str) -> tuple[int, int]:
    b, a = c[1:].split("a")
    return int(b), int(a)


def _check(cells, legal, W=4, root_multi=True):
    return check_batch(cells, legal=legal, W=W, root_multi=root_multi,
                       parent_of=lambda c: f"b{_pos(c)[0]}a{_pos(c)[1] - 1}" if _pos(c)[1] else None,
                       branch_of=lambda c: _pos(c)[0])


def test_check_batch_rules() -> None:
    legal = ["b0a0", "b1a0", "b2a3"]
    assert _check(["b0a0", "b2a3"], legal) == ["b0a0", "b2a3"]
    with pytest.raises(ValueError, match="duplicate"):
        _check(["b0a0", "b0a0"], legal)
    with pytest.raises(ValueError, match="max_parallelism"):
        _check(["b0a0", "b1a0", "b2a3"], legal, W=2)
    with pytest.raises(ValueError, match="not legal"):
        _check(["b0a1"], legal)
    with pytest.raises(ValueError, match="same branch"):
        _check(["b2a3", "b2a4"], ["b2a3", "b2a4"])
    with pytest.raises(ValueError, match="parent"):
        check_batch(["x", "y"], legal=["x", "y"], W=4,
                    parent_of=lambda c: "x" if c == "y" else None,
                    branch_of=lambda c: {"x": 0, "y": 1}[c])
    with pytest.raises(ValueError, match="sequence"):
        _check("b0a0", legal)
    # the symbolic root may repeat up to its multiplicity (E1), but only when root_multi
    assert _check([ROOT, ROOT], [ROOT, ROOT, "b2a3"]) == [ROOT, ROOT]
    with pytest.raises(ValueError, match="unopened"):
        _check([ROOT, ROOT, ROOT], [ROOT, ROOT])
    with pytest.raises(ValueError, match="at most once"):
        _check([ROOT, ROOT], [ROOT, ROOT], root_multi=False)


def test_replay_question_enforces_legality() -> None:
    q = ReplayQuestion(_world(), 2)
    assert q.legal_roots() == ["b0a0", "b1a0", "b2a0", "b3a0"]
    with pytest.raises(ValueError, match="not legal"):
        q.probe_batch(["b0a1"])            # deeper cell before its parent is revealed
    with pytest.raises(ValueError, match="max_parallelism"):
        q.probe_batch(["b0a0", "b1a0", "b2a0"])
    with pytest.raises(ValueError, match="duplicate"):
        q.probe_batch(["b0a0", "b0a0"])
    assert q.observed() == {}              # failed calls reveal nothing
    q.probe_batch(["b0a0", "b2a0"])
    assert q.legal_actions() == ["b1a0", "b3a0", "b0a1"]   # b2 has no recorded child
    with pytest.raises(ValueError, match="not legal"):
        q.probe_batch(["b2a1"])
    with pytest.raises(ValueError, match="not legal"):
        q.probe_batch(["b0a1", "b0a2"])    # parent and child together: child not legal yet
    assert q.probe_batch([]) == []
    assert q.budget_spent == 2


def _obs(**kw) -> Observation:
    base = dict(branch=0, attempt=1, score=0.5, evaluated=True, valid=True, fail_class="ok",
                error=None, delta_vs_baseline=0.1, delta_vs_parent=0.05, n_valid=3, n_total=3)
    base.update(kw)
    return Observation(**base)


def test_success_semantics() -> None:
    assert is_success(_obs())
    assert is_success(_obs(valid=False, n_valid=None, n_total=None))   # valid=False is not failure
    assert not is_success(_obs(evaluated=False))
    assert not is_success(_obs(error="boom"))
    assert not is_success(_obs(fail_class="wrong_output"))


def test_failure_kinds_and_signals() -> None:
    assert failure_kind(_obs()) == "ok"
    assert failure_kind(_obs(fail_class="env_failure", error="docker down")) == "hard"
    assert failure_kind(_obs(fail_class="env_failure", error="shape mismatch")) == "repairable"
    assert failure_kind(_obs(fail_class="compile_other", error="x")) == "repairable"
    assert failure_kind(_obs(fail_class="wrong_output", n_valid=0)) == "repairable"
    assert failure_kind(_obs(fail_class="weird_new_class")) == "repairable"
    # branch_failed_hard is a signal (zero valid / not evaluated / hard class), not closure
    assert branch_failed_hard(_obs(fail_class="wrong_output", n_valid=0))
    assert branch_failed_hard(_obs(evaluated=False, fail_class="timeout"))
    assert not branch_failed_hard(_obs(fail_class="wrong_output", n_valid=2))
    assert not branch_failed_hard(_obs(valid=False, n_valid=0))      # a success is never hard
    assert probe_improved_vs_parent(_obs())
    assert not probe_improved_vs_parent(_obs(delta_vs_parent=0.0))
    assert not probe_improved_vs_parent(_obs(fail_class="wrong_output"))
    assert probe_improved_vs_baseline(_obs())
    assert not probe_improved_vs_baseline(_obs(delta_vs_baseline=None))
    assert branch_promising(_obs(delta_vs_baseline=-0.1, delta_vs_parent=0.2))
    traj = [_obs(attempt=0), _obs(fail_class="wrong_output", score=0.0, delta_vs_baseline=-0.4)]
    assert branch_promising(traj)                 # repairable latest failure keeps the anchor
    assert not branch_promising(traj[:1] + [_obs(fail_class="env_failure", error="dns")])
    assert not branch_promising([])
