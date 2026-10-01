"""내장 스위트 xgen-core — 검증기 자체를 검증한다(오라클은 만점, 아무것도 안 한 답은 낮은 점수)."""

from __future__ import annotations

import os

import pytest

from xgen_rsi.evolve.tasks import load_suite
from xgen_rsi.evolve.verifiers import verify
from xgen_rsi.suites.build_xgen_core import build, build_with_oracles, main

BUILT, SPLITS = build_with_oracles()


def _materialize(task, root):
    for rel, content in task.files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)


@pytest.mark.parametrize("item", BUILT, ids=[b.spec.id for b in BUILT])
def test_oracle_scores_full_marks(item, tmp_path):
    _materialize(item.spec, str(tmp_path))
    answer = item.oracle(str(tmp_path))
    result = verify(item.spec.checks, workspace=str(tmp_path), answer=answer)
    failed = [c.name + ": " + c.detail for c in result.checks if not c.passed]
    assert result.reward == 1.0, failed
    assert result.valid_output in (True, None)
    assert result.no_submission is False


@pytest.mark.parametrize("item", BUILT, ids=[b.spec.id for b in BUILT])
def test_doing_nothing_scores_low(item, tmp_path):
    _materialize(item.spec, str(tmp_path))
    result = verify(item.spec.checks, workspace=str(tmp_path), answer="")
    assert result.reward <= 0.5


def test_splits_are_disjoint_and_cover_every_category():
    evolve, heldout = set(SPLITS["evolve"]), set(SPLITS["heldout"])
    assert not evolve & heldout
    cats = {b.spec.tags[0] for b in BUILT}
    assert {next(b.spec.tags[0] for b in BUILT if b.spec.id == i) for i in heldout} == cats
    assert set(SPLITS["smoke"]) <= evolve


def test_build_is_deterministic_and_round_trips(tmp_path):
    a, sa = build()
    b, sb = build()
    assert [t.to_json() for t in a] == [t.to_json() for t in b] and sa == sb
    main(["x", str(tmp_path / "suite")])
    suite = load_suite(tmp_path / "suite")
    assert len(suite.tasks) == len(a) and suite.ids("evolve") == sa["evolve"]
