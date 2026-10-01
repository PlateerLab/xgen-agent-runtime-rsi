"""L2 — Dream-RSI: replay-based improvement of the exploration policy π_E.

- :mod:`.world` — frozen replay worlds (branch × attempt trees) and the world pool.
- :mod:`.replay` — ``ReplayQuestion`` (Child semantics, stop conditions,
  information hiding) and ``run_episode``.
- :mod:`.sandbox` — static checks and restricted, time-limited execution of policy code.
- :mod:`.evaluator` — Eq.1 V^m, the β sweep, pareto.reward, archive files.
- :mod:`.develop` — the policy-development agent (rules after arXiv:2609.14858 App. B).
- :mod:`.cycle` — ``DreamCycle``: versions → selection → online confirmation → β reference.
- :mod:`.manifest` — ``live_cycle_manifest.json`` sidecars.
"""

from .cycle import ConfirmationRequest, ConfirmationResult, CycleConfig, CycleResult, DreamCycle
from .develop import PolicyDeveloper, VersionRecord, build_dev_prompt, validate_policy_source
from .evaluator import DEFAULT_BETA_GRID, EvalConfig, PolicyEvaluation, evaluate_policy
from .manifest import live_cycles, load_live_manifests, write_live_manifest
from .replay import ReplayQuestion, ReplayRun, replay_state, run_episode
from .sandbox import LoadedPolicy, PolicyRejected, PolicyTimeout, load_policy, static_check
from .world import (
    PoolSplit,
    World,
    WorldCell,
    WorldPool,
    worlds_from_record,
    worlds_from_trial_outcomes,
)

__all__ = [
    "DEFAULT_BETA_GRID", "ConfirmationRequest", "ConfirmationResult", "CycleConfig", "CycleResult",
    "DreamCycle", "EvalConfig", "LoadedPolicy", "PolicyDeveloper", "PolicyEvaluation",
    "PolicyRejected", "PolicyTimeout", "PoolSplit", "ReplayQuestion", "ReplayRun", "VersionRecord",
    "World", "WorldCell", "WorldPool", "build_dev_prompt", "evaluate_policy", "live_cycles",
    "load_live_manifests", "load_policy", "replay_state", "run_episode", "static_check",
    "validate_policy_source", "worlds_from_record", "worlds_from_trial_outcomes",
    "write_live_manifest",
]
