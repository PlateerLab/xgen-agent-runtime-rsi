"""EvolveConfig — the RRSI hyperparameters plus the engineering knobs a run needs.

The method's symbols live in :class:`xgen_rsi.rsi_math.RRSIParams` (T, k, m, b_min, b_max, w,
m_draft, δ, z, β0, β1, w_s, w_c, w_n, n_prune, φ). Everything else here is engineering that the
paper does not define (reference ``rrsi/config.py``): repair rounds, trace counts, parallelism,
the exact early-stop switch, the role models and the optional domain guard thresholds.

``rrsi.json`` is a flat object, the same layout as the reference instance files. Role models go
under ``"roles": {"proposer": {...}, ...}`` (or ``"<role>_model": {...}``). Unknown keys are kept in
``notes`` so an instance file can carry documentation (``_doc``) and domain settings.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/config.py``: the instance-file
layout and the engineering knobs), Copyright 2026 The rrsi Authors / Google LLC, Apache License 2.0;
modified by PlateerLab.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from xgen_rsi.roles.llm import RoleModel
from xgen_rsi.rsi_math import ModeConfig, RRSIParams

ROLES = ("proposer", "critic", "analyst", "digester")
#: 있어도 되고 없어도 되는 역할 — judge 는 기준 판정 검사(``answer_criteria``)를 채점한다(사용 기록에서 만든 과제).
OPTIONAL_ROLES = ("judge",)
_ALL_ROLES = ROLES + OPTIONAL_ROLES
_PARAM_FIELDS = {f.name for f in fields(RRSIParams)}


@dataclass(frozen=True)
class EvolveConfig:
    """One evolution run's configuration (immutable; use :meth:`with_overrides`)."""

    params: RRSIParams = field(default_factory=RRSIParams)
    # ---- engineering knobs (not part of the method) ----------------------
    repair_rounds: int = 5
    n_fail_traces: int = 22
    n_success_traces: int = 6
    eval_parallel: int = 1
    """Candidates evaluated concurrently (reference ``eval_parallel``)."""
    trial_parallel: int = 4
    """Trials run concurrently inside one evaluation."""
    digest_parallel: int = 6
    early_stop: Optional[bool] = None
    """Exact-bound early stopping (33 §4). ``None`` = the mode's default (xgen on, paper off)."""
    mode: str = "xgen"
    smoke_n: int = 2
    seed: int = 7
    """Seed of the fixed random trial order inside an evaluation (33 §4)."""
    max_valid_rate_drop: Optional[float] = None
    max_nosub_rise: Optional[float] = None
    proposer: Optional[RoleModel] = None
    critic: Optional[RoleModel] = None
    analyst: Optional[RoleModel] = None
    digester: Optional[RoleModel] = None
    judge: Optional[RoleModel] = None
    notes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in ("paper", "xgen"):
            raise ValueError(f"mode must be 'paper' or 'xgen', got {self.mode!r}")
        if self.repair_rounds < 0 or self.smoke_n < 0:
            raise ValueError("repair_rounds and smoke_n must be >= 0")
        if self.n_fail_traces < 0 or self.n_success_traces < 0:
            raise ValueError("trace counts must be >= 0")
        if min(self.eval_parallel, self.trial_parallel, self.digest_parallel) < 1:
            raise ValueError("parallelism knobs must be >= 1")
        for name in ("max_valid_rate_drop", "max_nosub_rise"):
            v = getattr(self, name)
            if v is not None and v < 0:
                raise ValueError(f"{name} must be >= 0")
        object.__setattr__(self, "notes", dict(self.notes))

    # ---- views ------------------------------------------------------------
    @property
    def T(self) -> int:
        return self.params.T

    @property
    def k(self) -> int:
        return self.params.k

    @property
    def m(self) -> int:
        return self.params.m

    @property
    def delta(self) -> Optional[float]:
        return self.params.delta

    @property
    def invalid_missing_frac(self) -> float:
        return self.params.invalid_missing_frac

    @property
    def mode_config(self) -> ModeConfig:
        return ModeConfig.for_mode("paper" if self.mode == "paper" else "xgen")

    @property
    def use_early_stop(self) -> bool:
        return self.mode_config.early_stop if self.early_stop is None else bool(self.early_stop)

    def role_model(self, role: str) -> Optional[RoleModel]:
        if role not in _ALL_ROLES:
            raise KeyError(role)
        model: Optional[RoleModel] = getattr(self, role)
        return model

    # ---- construction -----------------------------------------------------
    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], **overrides: Any) -> "EvolveConfig":
        merged: Dict[str, Any] = dict(raw)
        merged.update({k: v for k, v in overrides.items() if v is not None})
        knob_names = {f.name for f in fields(cls)} - {"params", "notes", *_ALL_ROLES}
        params_kw: Dict[str, Any] = {}
        knobs: Dict[str, Any] = {}
        roles: Dict[str, Any] = {}
        notes: Dict[str, Any] = dict(merged.pop("notes", None) or {})
        role_block = merged.pop("roles", None)
        if isinstance(role_block, Mapping):
            for role, spec in role_block.items():
                if role in _ALL_ROLES and spec is not None:
                    roles[role] = _role(spec)
                else:
                    notes.setdefault("roles", {})[role] = spec
        for key, value in merged.items():
            if key in _PARAM_FIELDS:
                params_kw[key] = value
            elif key in knob_names:
                knobs[key] = value
            elif key in _ALL_ROLES and value is not None and not isinstance(value, str):
                roles[key] = _role(value)
            elif key.endswith("_model") and key[: -len("_model")] in _ALL_ROLES and isinstance(value, (Mapping, RoleModel)):
                roles[key[: -len("_model")]] = _role(value)
            else:
                notes[key] = value
        return cls(params=RRSIParams(**params_kw), notes=notes, **knobs, **roles)

    @classmethod
    def load(cls, path: Path | str, **overrides: Any) -> "EvolveConfig":
        """Read ``rrsi.json``; ``overrides`` (None values ignored) win over the file."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError(f"{path}: expected a JSON object")
        return cls.from_dict(raw, **overrides)

    def with_overrides(self, **overrides: Any) -> "EvolveConfig":
        """A copy with RRSIParams fields and/or knobs replaced (None values are applied as-is
        for ``delta``/guards so they can be cleared)."""
        p_kw = {k: v for k, v in overrides.items() if k in _PARAM_FIELDS}
        k_kw = {k: v for k, v in overrides.items() if k not in _PARAM_FIELDS}
        unknown = set(k_kw) - {f.name for f in fields(self)}
        if unknown:
            raise KeyError(f"unknown config keys {sorted(unknown)}")
        params = replace(self.params, **p_kw) if p_kw else self.params
        return replace(self, params=params, **k_kw)

    def dump(self) -> Dict[str, Any]:
        """Flat JSON view for the frontier (role credentials are never written)."""
        out: Dict[str, Any] = {f.name: getattr(self.params, f.name) for f in fields(RRSIParams)}
        for f in fields(self):
            if f.name in ("params", "notes", *_ALL_ROLES):
                continue
            out[f.name] = getattr(self, f.name)
        out["early_stop_effective"] = self.use_early_stop
        out["roles"] = {r: _redacted(getattr(self, r)) for r in _ALL_ROLES if getattr(self, r) is not None}
        if self.notes:
            out["notes"] = _redact(dict(self.notes))
        return out


_SECRET_HINTS = ("key", "token", "secret", "password", "credential", "auth")


def _redact(value: Any) -> Any:
    """설정 덤프(frontier.json 등 디스크)에 자격증명이 남지 않게 — 이름이 비밀처럼 보이는 값은 가린다."""
    if isinstance(value, Mapping):
        return {k: ("***" if any(h in str(k).lower() for h in _SECRET_HINTS) and v not in (None, "") else _redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def _role(spec: Any) -> RoleModel:
    if isinstance(spec, RoleModel):
        return spec
    if not isinstance(spec, Mapping):
        raise ValueError(f"role model must be an object with provider and model, got {spec!r}")
    return RoleModel.from_json(dict(spec))


def _redacted(m: Optional[RoleModel]) -> Optional[Dict[str, Any]]:
    if m is None:
        return None
    return {"provider": m.provider, "model": m.model, "base_url": m.base_url,
            "max_tokens": m.max_tokens, "temperature": m.temperature,
            "thinking_level": m.thinking_level}
