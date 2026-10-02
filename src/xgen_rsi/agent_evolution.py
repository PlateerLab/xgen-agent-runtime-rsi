"""에이전트 하나의 하네스 진화 — 호스트(XGEN)가 에이전트마다 부르는 진입점(설계 40 §2.4).

    from xgen_rsi.agent_evolution import AgentEvolution
    from xgen_rsi.usage import UsageItem, AgentSettings

    evo = AgentEvolution(work_dir, policy=PolicySpec(...), roles={"proposer": ..., "critic": ..., "analyst": ...,
                         "digester": ..., "judge": ...}, agent=AgentSettings(system_prompt=...))
    result = evo.run(usage_items, current=<그 에이전트의 현재 하네스 페이로드 또는 None(H0)>)
    if result.adopted:
        save(result.payload)            # 그 에이전트의 다음 하네스 — 다음 턴부터 rsi_agent_harness() 로 돌려준다

하는 일: 사용 기록 → 과제(:mod:`xgen_rsi.usage`) → 그 에이전트의 현재 하네스에서 시작하는 RRSI 라운드(δ 보정, 잡음 바닥, 비용 규칙,
띠 안 규칙, 읽히지 않은 편집 가드, 환경 단정 금지 — :mod:`xgen_rsi.evolve` 그대로) → 채택된 하네스를 페이로드로. 채택은 RRSI 가 정하고,
이 모듈은 판정을 바꾸지 않는다. 판정에 쓰지 않은 heldout 과제가 있으면 시작 하네스와 채택 하네스를 거기서도 재서 함께 돌려준다.

평가는 과제마다 독립 작업 공간에서 돈다(``AgentSettings.toolset`` — 기본 파일 도구). 사용자의 기억·파일·외부 시스템을 건드리지 않는다.
자격증명은 객체로만 받고 디스크에 쓰지 않는다(정책·역할 JSON 파일을 만들지 않는다).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from xgen_rsi.evolve.config import OPTIONAL_ROLES, ROLES, EvolveConfig
from xgen_rsi.evolve.runner import PolicySpec
from xgen_rsi.roles.llm import RoleModel
from xgen_rsi.usage import AgentSettings, NotEnoughUsage, UsageItem, build_suite


@dataclass
class RoundSummary:
    t: int
    winner: Optional[str]
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    """후보마다 ``{variant, outcome, reason, S, delta_S, delta_C, components}``."""


@dataclass
class AgentEvolutionResult:
    status: str
    """``done`` · ``stopped`` · ``infra_failures`` · ``not_enough_usage``."""
    adopted: bool = False
    payload: Optional[Dict[str, Any]] = None
    """채택된 하네스(:mod:`xgen_rsi.harness.payload`). 채택이 없으면 None."""
    start_version: str = ""
    version: str = ""
    baseline: Dict[str, Any] = field(default_factory=dict)
    """시작 하네스의 evolve 점수 ``{S, C}``."""
    final: Dict[str, Any] = field(default_factory=dict)
    """채택 하네스(없으면 시작 하네스)의 evolve 점수 ``{S, C, t}``."""
    delta: Optional[float] = None
    rounds: List[RoundSummary] = field(default_factory=list)
    heldout: Dict[str, Any] = field(default_factory=dict)
    """판정에 쓰지 않은 heldout 측정 ``{start: {S, C}, adopted: {S, C}, tasks: n}`` — 과제가 없거나 채택이 없으면 빈 dict."""
    tasks: Dict[str, int] = field(default_factory=dict)
    """``{evolve, heldout, skipped}`` — 과제 수와 신호가 없어 빠진 턴 수."""
    message: str = ""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


class AgentEvolution:
    """에이전트 하나의 RRSI 진화 실행. ``work_dir`` 하나가 실행 하나다(같은 디렉터리로 다시 부르면 이어서 돈다)."""

    def __init__(
        self,
        work_dir: Path | str,
        *,
        policy: PolicySpec,
        roles: Mapping[str, RoleModel | Mapping[str, Any]],
        agent: AgentSettings = AgentSettings(),
        T: int = 3,
        k: int = 2,
        min_evolve: int = 4,
        heldout_k: int = 1,
        overrides: Optional[Mapping[str, Any]] = None,
        client_factory: Optional[Callable[[Any], Any]] = None,
        log: Optional[Callable[[str], None]] = None,
        role_llms: Any = None,
        judge: Optional[Callable[..., Any]] = None,
    ) -> None:
        """``role_llms``(:class:`~xgen_rsi.evolve.round.Roles`)·``judge`` 를 주면 ``roles`` 의 해당 모델 대신 쓴다(호스트 맞춤·테스트)."""
        need = ([] if role_llms is not None else list(ROLES)) + ([] if judge is not None else ["judge"])
        missing = [r for r in need if r not in roles]
        if missing:
            raise ValueError(f"roles missing: {missing} (judge grades the criteria made from usage)")
        self.work_dir = Path(work_dir).resolve()
        self.policy = policy
        self.roles = {r: roles[r] for r in (*ROLES, *OPTIONAL_ROLES) if r in roles}
        self.agent = agent
        self.T, self.k, self.min_evolve, self.heldout_k = int(T), int(k), int(min_evolve), int(heldout_k)
        self.overrides = dict(overrides or {})
        self.client_factory = client_factory
        self._log = log
        self._role_llms = role_llms
        self._judge = judge

    @property
    def stop_path(self) -> Path:
        """이 파일을 만들면 다음 라운드 전에 멈춘다(채택된 것까지는 남는다)."""
        return self.work_dir / "run" / "STOP"

    def stop(self) -> None:
        self.stop_path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_path.write_text("stop\n", encoding="utf-8")

    def run(self, usage: Sequence[UsageItem], current: Optional[Mapping[str, Any]] = None) -> AgentEvolutionResult:
        from xgen_rsi.evolve.domain import EvolveDomain
        from xgen_rsi.evolve.driver import drive
        from xgen_rsi.evolve.judge import CriteriaJudge
        from xgen_rsi.evolve.round import EvolveRun
        from xgen_rsi.evolve.tasks import load_suite
        from xgen_rsi.harness.payload import materialize
        from xgen_rsi.harness.spec import load_manifest
        from xgen_rsi.kernel.executor import BUILTIN_H0

        self.work_dir.mkdir(parents=True, exist_ok=True)
        try:
            suite = build_suite(usage, self.work_dir / "suite", agent=self.agent, min_evolve=self.min_evolve)
        except NotEnoughUsage as exc:
            return AgentEvolutionResult(status="not_enough_usage", message=str(exc))
        counts = {"evolve": len(suite.splits["evolve"]), "heldout": len(suite.splits["heldout"]), "skipped": suite.skipped}
        start_dir = materialize(current, self.work_dir / "harness-cache") if current else BUILTIN_H0
        start_version = load_manifest(start_dir).version_id()

        cfg = EvolveConfig.from_dict({**self.overrides, "T": self.T, "k": self.k, "roles": dict(self.roles)})
        judge = self._judge
        if judge is None:
            assert cfg.judge is not None
            judge = CriteriaJudge(cfg.judge)
        domain = EvolveDomain(name="agent", suite=load_suite(suite.root), policy=self.policy,
                              client_factory=self.client_factory, judge=judge,
                              max_valid_rate_drop=cfg.max_valid_rate_drop, max_nosub_rise=cfg.max_nosub_rise)
        run = EvolveRun(domain, cfg, self.work_dir / "run", roles=self._role_llms, start_harness=start_dir,
                        name="agent", log=self._log)
        out = drive(run, T=self.T)

        frontier = run.frontier()
        traj = frontier.get("trajectory") or []
        base = traj[0] if traj else {}
        inc = frontier["incumbent"]
        result = AgentEvolutionResult(
            status=str(out["status"]),
            start_version=start_version,
            version=str(inc.get("harness_version") or start_version),
            baseline={"S": base.get("S"), "C": base.get("C")},
            final={"S": inc.get("S"), "C": inc.get("C"), "t": inc.get("t")},
            delta=_delta(run),
            rounds=_rounds(run.run_dir, self.T),
            tasks=counts,
        )
        result.adopted = bool(inc.get("harness_version")) and inc.get("harness_version") != start_version
        if result.adopted:
            from xgen_rsi.evolve.cli import export_harness
            from xgen_rsi.harness.payload import to_payload

            with tempfile.TemporaryDirectory(prefix="rsi-adopted-") as tmp:
                export_harness(run, str(Path(tmp) / "h"))
                result.payload = to_payload(Path(tmp) / "h")
                result.version = str(result.payload["version"])
                if self.heldout_k > 0 and counts["heldout"]:
                    result.heldout = self._heldout(domain, start_dir, Path(tmp) / "h")
        return result

    def _heldout(self, domain: Any, start_dir: Path, adopted_dir: Path) -> Dict[str, Any]:
        from xgen_rsi.evolve.runner import evaluate

        tasks = domain.split_tasks("heldout")
        out: Dict[str, Any] = {"tasks": len(tasks), "k": self.heldout_k}
        for label, hdir in (("start", start_dir), ("adopted", adopted_dir)):
            ev = evaluate(str(hdir), tasks, self.heldout_k, policy=self.policy,
                          out_dir=str(self.work_dir / "heldout" / label), job=f"heldout-{label}",
                          client_factory=self.client_factory, judge=domain.judge)
            out[label] = {"S": ev.S, "C": ev.C, "missing": ev.missing}
        return out


def _delta(run: Any) -> Optional[float]:
    if run.cfg.delta is not None:
        return float(run.cfg.delta)
    try:
        return float(json.loads(run.calibration_path.read_text(encoding="utf-8"))["delta"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _rounds(run_dir: Path, T: int) -> List[RoundSummary]:
    out: List[RoundSummary] = []
    for t in range(T):
        path = run_dir / f"r{t}" / "decisions.json"
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cands = [{k: d.get(k) for k in ("variant", "outcome", "reason", "S", "delta_S", "delta_C", "components")}
                 for d in raw.get("decisions") or []]
        out.append(RoundSummary(t=t, winner=raw.get("winner"), candidates=cands))
    return out


__all__ = ["AgentEvolution", "AgentEvolutionResult", "RoundSummary"]
