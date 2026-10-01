"""Evaluate(H', D, k) — 후보 하네스를 과제 집합에서 k 번씩 실제로 돌려 Ŝ, Ĉ 를 잰다(RRSI Eq.3).

* 시행 하나 = 독립 작업 공간 + :class:`EvalHost` + 진입점 ``GenyRSITurnExecutor().run`` (후보 하네스 고정)
* 보상 r = 검증기 통과 비율, 가중치 w = 검사 수, 비용 c(τ) = 원장의 정책 토큰(모든 purpose)
* 누락(인프라 실패) = r 0, 분모 포함(05 §2.1) — ``missing`` 으로 센다
* 재개 안전: 시행 결과 파일이 있으면 다시 돌리지 않는다
* 정확 경계 조기 종료(설계 33 §4): 바닥에 못 미침이 확정되면 남은 시행을 돌리지 않는다(판정 불변)
"""

from __future__ import annotations

import json
import os
import random
import shutil
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from xgen_rsi.evolve.evalhost import EvalHost
from xgen_rsi.evolve.tasks import TaskSpec
from xgen_rsi.evolve.verifiers import verify
from xgen_rsi.rsi_math import EvalResult, TaskResult, aggregate, can_stop_exactly, upper_lower


@dataclass(frozen=True)
class PolicySpec:
    """정책 π — 진화 동안 고정된다. 자격증명은 XGEN 에 등록된 LLM 의 것(호출자가 넘긴다)."""

    provider: str
    model: str
    api_key: str = ""
    base_url: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    temperature: float = 0.7
    thinking: Optional[str] = None

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> "PolicySpec":
        return cls(
            provider=str(raw["provider"]),
            model=str(raw["model"]),
            api_key=str(raw.get("api_key") or ""),
            base_url=raw.get("base_url"),
            credentials=raw.get("credentials"),
            temperature=float(raw.get("temperature", 0.7)),
            thinking=raw.get("thinking"),
        )


@dataclass
class TrialOutcome:
    task_id: str
    trial: int
    reward: float
    weight: float
    tokens: Optional[int]
    missing: bool
    valid_output: Optional[bool]
    no_submission: bool
    status: str
    answer: str
    record: Optional[str]
    verifier: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    #: 외부 usage(입력+출력) — 두 엔진이 같은 정의로 내는 값(엔진 비교의 공통 비용 잣대)
    usage_tokens: Optional[int] = None
    duration_s: Optional[float] = None
    engine: str = "geny-rsi"
    #: 이 시행에서 구성요소가 읽은 하네스 파라미터 주소(궤적 기록의 합). None = 모름(기존 엔진·옛 결과)
    params_read: Optional[List[str]] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


_INFRA_PREFIXES = ("[ERROR] geny agent could not start",)

#: 엔진 이름 — 기존 21-stage 엔진은 ``geny``, 이 패키지의 엔진은 ``geny-rsi``.
ENGINES = ("geny-rsi", "geny")


def turn_executor(engine: str) -> Any:
    """엔진 이름 → 진입점 객체. 두 진입점은 같은 ``run(host, **kwargs)`` 계약이다(기존 런타임은 이 패키지를 모른다)."""
    if engine == "geny-rsi":
        from xgen_rsi.turn_executor import GenyRSITurnExecutor

        return GenyRSITurnExecutor()
    if engine == "geny":
        from xgen_agent_runtime.host.turn_executor import AgentTurnExecutor

        return AgentTurnExecutor()
    raise ValueError(f"engine must be one of {ENGINES}, got {engine!r}")


def run_trial(
    task: TaskSpec,
    trial: int,
    *,
    harness_dir: str,
    policy: PolicySpec,
    out_dir: str,
    client_factory: Optional[Callable[[Any], Any]] = None,
    keep_content: bool = True,
    engine: str = "geny-rsi",
) -> TrialOutcome:
    """시행 하나를 돌리고 검증한다. 결과는 ``out_dir/<task>__<trial>/outcome.json`` 에 남는다."""
    return run_attempt(
        task,
        trial,
        Path(out_dir) / f"{task.id}__{trial}",
        harness_dir=harness_dir,
        policy=policy,
        client_factory=client_factory,
        keep_content=keep_content,
        engine=engine,
    )


#: 시작 파일의 고정 수정 시각(2026-07 무렵) — 파일마다 1초씩 다르게.
SEED_MTIME = 1_785_000_000


def seed_workspace(task: TaskSpec, ws: Path) -> None:
    """과제의 시작 파일을 빈 작업 공간에 쓴다.

    수정 시각을 경로 순서대로 고정한다 — 수정 시각으로 정렬하는 도구(Glob)의 결과 순서가 실행마다 달라지면
    같은 하네스·같은 응답에서도 다음 요청이 갈라진다(엔진 동등성 재생 검사에서 실측).
    """
    ws.mkdir(parents=True, exist_ok=True)
    for i, rel in enumerate(sorted(task.files)):
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(task.files[rel], encoding="utf-8")
        os.utime(path, (SEED_MTIME + i, SEED_MTIME + i))


def run_attempt(
    task: TaskSpec,
    trial: int,
    tdir: Path,
    *,
    harness_dir: str,
    policy: PolicySpec,
    client_factory: Optional[Callable[[Any], Any]] = None,
    keep_content: bool = True,
    prompt: Optional[str] = None,
    start_from: Optional[Path] = None,
    interaction_id: Optional[str] = None,
    engine: str = "geny-rsi",
) -> TrialOutcome:
    """시도 하나 — 시행(평가)과 탐색 시도(Dream-RSI 의 셀)가 같은 함수를 쓴다.

    ``prompt`` 가 없으면 과제 지시문, ``start_from`` 이 있으면 그 작업 공간을 복사해 이어서 한다(정제 시도).
    결과는 ``tdir/outcome.json`` — 있으면 다시 돌리지 않는다(재개 안전). ``engine="geny"`` 면 같은 호스트·같은
    과제로 기존 21-stage 엔진을 돌린다(비교용 — 하네스 디렉터리는 쓰이지 않고 궤적 기록도 없다).
    """
    done = tdir / "outcome.json"
    prior = _read_outcome(done)
    if prior is not None:
        return prior
    if tdir.exists():
        shutil.rmtree(tdir)
    ws = tdir / "workspace"
    if start_from is not None:
        tdir.mkdir(parents=True)
        shutil.copytree(start_from, ws)
    else:
        seed_workspace(task, ws)
    rec_dir = tdir / "records"
    host = EvalHost(
        provider=policy.provider,
        model=policy.model,
        api_key=policy.api_key,
        base_url=policy.base_url,
        credentials=policy.credentials,
        workspace=str(ws),
        toolset=task.toolset,
        settings={
            "XGEN_RSI_HARNESS_DIR": str(harness_dir),
            "XGEN_RSI_RECORD_DIR": str(rec_dir),
            "XGEN_RSI_RECORD_CONTENT": "1" if keep_content else "0",
            "GENY_PREFETCH_REFERENCED_FILES": "1",
        },
        client_factory=client_factory,
    )
    usage_sink: Dict[str, Any] = {}
    params: Dict[str, Any] = dict(
        text=task.prompt if prompt is None else prompt,
        provider=policy.provider,
        streaming=False,
        interaction_id=interaction_id or f"rsi-eval-{task.id}-{trial}",
        workflow_id="rsi-eval",
        workflow_name="rsi-eval",
        user_id="rsi-eval",
        enable_memory=False,
        memory_distill=False,
        enable_self_evolution=False,
        temperature=policy.temperature,
        max_iterations=task.max_iterations,
        usage_sink=usage_sink,
    )
    if policy.thinking:
        params["thinking"] = policy.thinking
    if task.system_prompt is not None:
        params["system_prompt"] = task.system_prompt
    if task.output_schema is not None:
        params["output_schema"] = task.output_schema
    error = ""
    started = time.monotonic()
    try:
        answer = turn_executor(engine).run(host, **params)
        answer = answer if isinstance(answer, str) else "".join(c for c in answer if isinstance(c, str))
    except Exception as exc:  # noqa: BLE001 — 인프라 실패는 누락으로
        answer = ""
        error = f"{type(exc).__name__}: {exc}"[:500]
    record_path: Optional[str] = None
    tokens: Optional[int] = None
    status = "unknown"
    records = sorted(rec_dir.glob("*.json")) if rec_dir.exists() else []
    # 기록이 없는 geny-rsi 시행(시작 전에 실패)은 아무것도 읽지 않은 것이다. 기존 엔진은 하네스가 없다 → 모름
    params_read: Optional[List[str]] = [] if engine == "geny-rsi" else None
    if records:
        record_path = str(records[-1])
        try:
            rec = json.loads(records[-1].read_text(encoding="utf-8"))
            tokens = int(rec.get("policy_tokens") or 0) or None
            status = str(rec.get("status") or "unknown")
        except (OSError, ValueError):
            pass
        read: set = set()
        for path in records:  # 한 시행이 여러 턴이면 그 합
            try:
                read |= set(json.loads(path.read_text(encoding="utf-8")).get("params_read") or [])
            except (OSError, ValueError):
                read = set()
                break
        else:
            params_read = sorted(read)
    duration = round(time.monotonic() - started, 3)
    usage_tokens: Optional[int] = None
    if usage_sink.get("input_tokens") is not None or usage_sink.get("output_tokens") is not None:
        usage_tokens = int(usage_sink.get("input_tokens") or 0) + int(usage_sink.get("output_tokens") or 0)
    if tokens is None and engine == "geny":
        tokens = usage_tokens  # 기존 엔진은 궤적 원장이 없다 — 외부 usage 로 잰다
    if status == "unknown" and not error:
        status = "failed" if answer.startswith("[ERROR]") else "complete"
    # 누락(인프라) = 예외, 시작 실패, 또는 재시도를 다 쓴 공급자 오류로 끝난 턴("[ERROR] …") — r 0·분모 포함(05 §2.1),
    # 유효성 게이트와 재측정이 이것을 본다. 누락 시행의 토큰은 Ĉ 에 넣지 않는다(05 §5).
    missing = bool(error) or answer.startswith(_INFRA_PREFIXES) or answer.lstrip().startswith("[ERROR]")
    if missing:
        tokens = None
    vr = verify(task.checks, workspace=str(ws), answer=answer)
    outcome = TrialOutcome(
        task_id=task.id,
        trial=trial,
        reward=0.0 if missing else vr.reward,
        weight=task.weight,
        tokens=tokens,
        missing=missing,
        valid_output=vr.valid_output,
        no_submission=vr.no_submission,
        status=status,
        answer=answer[:20_000],
        record=record_path,
        verifier=vr.to_json(),
        error=error or (answer if missing else ""),
        usage_tokens=usage_tokens,
        duration_s=duration,
        engine=engine,
        params_read=params_read,
    )
    tmp = done.with_name(done.name + ".tmp")
    tmp.write_text(json.dumps(outcome.to_json(), ensure_ascii=True, indent=1), encoding="utf-8")
    os.replace(tmp, done)  # 원자적 — 반쯤 쓰인 결과가 재개를 막지 않게
    return outcome


def _params_read(outcomes: Sequence[TrialOutcome]) -> Optional[List[str]]:
    """평가 전체에서 읽힌 하네스 파라미터 주소의 합. 한 시행이라도 모르면(None) 전체를 모른다고 본다."""
    out: set = set()
    for o in outcomes:
        if o.params_read is None:
            return None
        out |= set(o.params_read)
    return sorted(out)


def _read_outcome(path: Path) -> Optional[TrialOutcome]:
    """끝난 시행의 결과. 없거나 읽을 수 없으면(쓰다 끊김) None — 그 시행은 다시 돈다."""
    if not path.exists():
        return None
    try:
        return TrialOutcome(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class EarlyStop:
    """정확 경계 조기 종료 기준(설계 33 §4)."""

    S_star: float
    delta: float
    S_inc: float


def evaluate(
    harness_dir: str,
    tasks: Sequence[TaskSpec],
    k: int,
    *,
    policy: PolicySpec,
    out_dir: str,
    job: str = "",
    parallel: int = 4,
    client_factory: Optional[Callable[[Any], Any]] = None,
    early_stop: Optional[EarlyStop] = None,
    seed: int = 7,
    engine: str = "geny-rsi",
) -> EvalResult:
    """후보 하나를 평가한다. ``extra`` 에 valid_rate·no_submission_rate·early_stopped 등을 싣는다."""
    order: List[Tuple[TaskSpec, int]] = [(t, j) for t in tasks for j in range(k)]
    random.Random(seed).shuffle(order)  # 고정 무작위 순서 — 부분 평균 편향 방지(33 §4)
    total_weight = sum(t.weight for t, _ in order)
    results: Dict[Tuple[str, int], TrialOutcome] = {}
    lock = threading.Lock()
    stopped = {"flag": False, "A": 0.0, "B": 0.0}

    def _accumulate(outcome: TrialOutcome) -> bool:
        with lock:
            results[(outcome.task_id, outcome.trial)] = outcome
            A = sum(o.reward * o.weight for o in results.values())
            B = sum(o.weight for o in results.values())
            R = max(0.0, total_weight - B)
            if early_stop is not None and R > 0 and can_stop_exactly(A, B, R, early_stop.S_star, early_stop.delta, early_stop.S_inc):
                stopped.update(flag=True, A=A, B=B)
                return True
            return False

    pending = list(order)
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {}
        while pending or futures:
            while pending and len(futures) < max(1, parallel) and not stopped["flag"]:
                task, trial = pending.pop(0)
                fut = pool.submit(
                    run_trial,
                    task,
                    trial,
                    harness_dir=harness_dir,
                    policy=policy,
                    out_dir=out_dir,
                    client_factory=client_factory,
                    engine=engine,
                )
                futures[fut] = (task.id, trial)
            if not futures:
                break
            done, _ = wait(list(futures), return_when=FIRST_COMPLETED)
            for fut in done:
                futures.pop(fut)
                if _accumulate(fut.result()):
                    pending.clear()

    per_task: Dict[str, TaskResult] = {}
    for task in tasks:
        rewards, weights, tokens = [], [], []
        miss = 0
        for j in range(k):
            o = results.get((task.id, j))
            if o is None:
                continue  # 조기 종료로 돌지 않은 시행
            rewards.append(o.reward)
            weights.append(o.weight)
            tokens.append(o.tokens)
            miss += int(o.missing)
        if rewards:
            per_task[task.id] = TaskResult(rewards=rewards, weights=weights, tokens=tokens, missing=miss)
    outcomes = list(results.values())
    valid = [o.valid_output for o in outcomes if o.valid_output is not None]
    extra: Dict[str, Any] = {
        "valid_rate": (sum(1 for v in valid if v) / len(valid)) if valid else None,
        "no_submission_rate": (sum(1 for o in outcomes if o.no_submission) / len(outcomes)) if outcomes else 0.0,
        "trials_run": len(outcomes),
        "trials_planned": len(order),
        "early_stopped": bool(stopped["flag"]),
        "engine": engine,
        "usage_tokens_mean": _mean([o.usage_tokens for o in outcomes if o.usage_tokens and not o.missing]),
        "duration_s_mean": _mean([o.duration_s for o in outcomes if o.duration_s is not None and not o.missing]),
        "params_read": _params_read(outcomes),
    }
    if stopped["flag"]:
        upper, _ = upper_lower(stopped["A"], stopped["B"], max(0.0, total_weight - stopped["B"]))
        extra["S_upper_bound"] = upper
    ev = aggregate(per_task, k, job=job, extra=extra)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "eval.json").write_text(json.dumps(ev.to_json(), ensure_ascii=True, indent=1), encoding="utf-8")
    return ev


def _mean(xs: List[Any]) -> Optional[float]:
    return (sum(xs) / len(xs)) if xs else None


def load_outcomes(out_dir: str) -> List[TrialOutcome]:
    out = []
    for path in sorted(Path(out_dir).glob("*__*/outcome.json")):
        o = _read_outcome(path)
        if o is not None:
            out.append(o)
    return out
