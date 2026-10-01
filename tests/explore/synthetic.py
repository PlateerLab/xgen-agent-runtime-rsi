"""Synthetic replay worlds shared by the explore and dream tests (deterministic)."""

from __future__ import annotations

import random

from xgen_rsi.dream.world import World

TOY_ROOT = 0.40
TOY_BRANCHES = {0: [0.50, 0.62, 0.60], 1: [0.30, 0.70], 2: [0.55]}


def toy_world() -> World:
    """The 05 §8.8 toy world: s_r = 0.40, b0 [0.50, 0.62, 0.60], b1 [0.30, 0.70], b2 [0.55]."""
    return World.from_branches(TOY_BRANCHES, root_score=TOY_ROOT, world_id="toy",
                               meta={"task_id": "toy"})


def synth_world(seed: int, B: int = 8, R: int = 5) -> World:
    """One good, steadily improving branch among flat/declining noisy ones, with some failures."""
    rng = random.Random(seed)
    branches: dict[int, list[object]] = {}
    good = rng.randrange(B)
    for b in range(B):
        depth = rng.randint(2, R + 1)
        base = rng.uniform(0.2, 0.5)
        slope = 0.08 if b == good else rng.uniform(-0.05, 0.02)
        cells: list[object] = []
        for a in range(depth):
            if rng.random() < 0.12:
                cells.append({"score": 0.0, "error": "boom",
                              "fail_class": rng.choice(["wrong_output", "runtime_error",
                                                        "env_failure"])})
            else:
                cells.append(round(base + slope * a + rng.uniform(-0.03, 0.03), 4))
        branches[b] = cells
    return World.from_branches(branches, root_score=0.3, world_id=f"s{seed}",
                               meta={"task_id": f"t{seed}"})


def synth_worlds(n: int = 12) -> list[World]:
    return [synth_world(s) for s in range(n)]
