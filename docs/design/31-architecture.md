# 31. 아키텍처 — 커널 · 하네스 · 탐색 계층 · 진화 엔진

> 원칙은 [30-fusion-philosophy.md](30-fusion-philosophy.md). 이 문서는 **모양**(패키지·모듈·인터페이스·데이터·실행 흐름)을 정한다. 시그니처는 설계안이며 구현 단계(PLAN)에서 테스트와 함께 확정한다.

---

## 1. 배치 결정 — 어디에 무엇을 만드는가

### 1.1 결정: 새 저장소 + 자체 진입점 (기존 런타임 무변경)

```
xgen-agent-runtime (기존, 그대로 — geny-rsi 를 모른다)    xgen-agent-runtime-rsi (이 저장소)
 ├ host/turn_executor.py  AgentTurnExecutor.run  (geny)     xgen_rsi.turn_executor.GenyRSITurnExecutor.run (geny-rsi, 같은 계약)
 │                                                           ├ xgen_rsi.assembly       턴 조립 — 호스트 계약의 자체 사본
 │                                                           └ xgen_rsi.kernel.executor RSITurnExecutor(실행 코어)
 ├ host/host.py           HostServices 프로토콜   ◄──  (그대로 import)
 ├ host/runner.py         build_client 등 심볼    ◄──  (공급자 클라이언트 빌더 그대로 사용)
 ├ host/cancel_context.py 취소 레지스트리          ◄──  (같은 모듈 상태 공유)
 ├ tools/base.py          도구 ABI                ◄──  (그대로)
 ├ memory/provider.py     MemoryProvider          ◄──  (그대로)
 └ llm_client/*           15 provider             ◄──  Provider Gateway 가 감쌈
```

- **경계 A**(11 문서 §0): `AgentTurnExecutor().run(host, **kwargs)` 아래 전부를 새 엔진이 대체한다. 그 자리에 **같은 계약의
  자체 진입점** `GenyRSITurnExecutor().run(host, **kwargs)` 를 두고, 어느 쪽을 쓸지는 **호스트가** 고른다.
- **기존 런타임은 고치지 않는다.** RSI 코드는 이 저장소에만 둔다. 계약 모듈(host 프로토콜, 도구 ABI, 메모리 프로토콜,
  cancel_context, runner 의 공개 심볼, `_constants` 프롬프트 심볼)과 공급자 계층은 런타임을 **라이브러리로 import** 한다.
- 턴 조립(호스트 계약 26단계)은 `xgen_rsi.assembly` 에 자체 사본으로 둔다. 런타임의 조립이 바뀌어 갈라지면 동등성 테스트
  (각본 모델)와 재생 실험(실제 모델 응답 → 요청 바이트 비교)이 실패해서 알 수 있다.
- 이력: 한때 런타임에 엔진 선택점(설정 `XGEN_HARNESS_ENGINE`)을 넣었으나(4.76.0·4.79.0), "RSI 는 별개 저장소에만" 원칙에 따라
  4.80.0 에서 걷어 냈다.

### 1.2 대안과 기각 이유

| 대안 | 장점 | 기각 이유 |
|---|---|---|
| 기존 런타임 안에 하위 패키지 `xgen_agent_runtime.rsi` | wheel 하나 | 21-stage 코드와 섞여 "처음부터 새로" 원칙이 흐려지고, 진화 엔진(L1/L2)의 무거운 의존성이 런타임에 들어감 |
| 기존 런타임에 엔진 선택점(설정 하나로 위임) | 호스트 코드 무변경 | 기존 런타임이 RSI 를 알게 된다 — RSI 와 기본 버전을 분리한다는 원칙에 어긋나 4.80.0 에서 제거 |
| 경계 B/C(파이프라인 층만 교체) | 작은 범위 | 21칸 구조·`PipelineState` 를 유지해야 해서 새 철학 적용 불가(11 문서 §0 표) |

> 패키지 이름(`xgen_rsi`)과 저장소 위치(GitHub PlateerLab 공개 여부)는 **결정 항목**(PLAN §결정 D-1).

---

## 2. 패키지 구조

```
xgen_rsi/
  kernel/                         ── L0 고정 커널 (진화 금지) ─────────────────────────────
    executor.py      RSITurnExecutor.run(host, **kwargs)        I/O 계약 어댑터(경계 A)
    assembly.py      턴 조립: HostServices 호출 26단계 순서 재현    11 문서 §2.1.3
    gateway.py       ProviderGateway: llm_client 감싸기, 호출마다 usage 기록
    ledger.py        UsageLedger: 호출 레코드, c(τ), 외부 usage 페이로드 생성(캐시 비대칭 보존)
    limits.py        KernelLimits: max_iterations, 턴 예산, 반복 종료, 벽시계, 슬라이스
    tools.py         ToolRunner: 도구 ABI·state_view·권한·HITL·병렬도
    stream.py        내부 이벤트 → 외부 청크(str / agent_event / canvas_command / usage)
    recorder.py      TrajectoryRecord, ReplayNode, LLM tape
    loader.py        HarnessLoader: lineage 해석, manifest 검증, kind 확인, overlay 합성
    explore.py       ExploreExecutor: GridPlan 적용, 워커 풀, LiveQuestion
    measure.py       스텝·유효 출력·미제출·종료 사유
    errors.py        오류 코드 보존(StageError 래핑 금지)
  harness/                        ── H 의 형식과 실행 엔진 ───────────────────────────────
    spec.py          HarnessManifest, ComponentSpec, version_id(트리 해시), lineage 표
    kinds.py         𝒦 9종 Protocol (§4)
    engine.py        TurnEngine: ControlProgram 을 구성요소와 함께 구동
    overlay.py       세션 overlay(env 식 자기 수정) 합성
  harnesses/                      ── 하네스 패키지(데이터+메커니즘, 버전 관리 대상) ───────
    h0/              H0 = 기존 운영 동작을 구성요소로 옮긴 것(§7)
  explore/                        ── Dream-RSI API (B.2 그대로) ──────────────────────────
    api.py           Observation, CellMeta, Question, GridPlan, GridPlanningContext, LLMDesignedMethod, SimResult
    signals.py       branch_promising, branch_failed_hard, probe_improved_vs_parent, probe_improved_vs_baseline
    policies/        π_1(parallel refine baseline) + 진화된 버전들
  rsi_math/                       ── 수식 정본 구현(순수 함수) — 33 문서 ─────────────────
    rrsi.py  calibrate.py  dream.py  bounds.py  units.py
  evolve/                         ── L1 RRSI 엔진 ───────────────────────────────────────
    domain.py  evaluate.py  history.py  frontier.py  propose.py  critic.py
    analyst.py  digester.py  round.py  driver.py  gitops.py  constitution/
  dream/                          ── L2 Dream 엔진 ──────────────────────────────────────
    world.py  replay.py  evaluator.py  develop.py  cycle.py  sandbox.py
  promote/                        ── 승격 파이프라인 ────────────────────────────────────
    gate.py  lineage.py  rollout.py
  cli.py                          rsi {baseline,calibrate,round,run,readjudicate,reevaluate,heldout,
                                       dream,promote,status,replay}
```

---

## 3. L0 — 커널

### 3.1 실행 흐름 (한 턴)

```
RSITurnExecutor.run(host, **kwargs)                       # 동기, 루프 없는 워커 스레드 (기존과 동일)
 ├─ ① 조기 검증(validate_agent_params) → 실패 시 기존과 같은 "[ERROR104: …]"
 ├─ ② assembly: HostServices 호출 26단계를 기존 순서 그대로 (11 문서 §2.1.3)
 │      kwargs["_sandbox_session"], kwargs["_tool_surface"] 역방향 키도 같은 시점에 기록
 ├─ ③ loader: lineage(provider, model) → H 버전 → manifest 검증 → 구성요소 인스턴스화
 │      ⊕ 에이전트 설정(kwargs: system_prompt, tools, memory, …) ⊕ 세션 overlay
 ├─ ④ recorder.begin(trajectory) : harness_id, lineage, policy, π_E 버전 기록
 ├─ ⑤ TurnEngine.run(ctx) ── private event loop ───────────────────────────────┐
 │      while not done:                                                         │
 │        limits.check(ctx)               # 커널 한도 → 기존 안내 문장·상태      │
 │        action = control.next_action(ctx)   # 루프 결정은 여기 한 곳         │
 │        kernel.execute(action)          # CallModel / RunTools / Compact /     │
 │                                        # Verify / Explore / Finish            │
 │        ledger.record(...) ; recorder.node(...) ; stream.emit(...)             │
 │      ─────────────────────────────────────────────────────────────────────────┘
 ├─ ⑥ 이어가기(resumable 슬라이스, max_continuation_slices) — 기존 의미 유지
 ├─ ⑦ stream: usage 청크 정확히 1회(마지막), usage_sink 동일 객체 update, partial
 └─ ⑧ teardown: host.finalize_turn → CLI·run_dir 정리, close() 전파 (기존 순서)
```

### 3.2 Provider Gateway 와 Usage Ledger

```python
class ProviderGateway:
    """정책 π 에 대한 모든 호출의 단일 관문. 하네스 구성요소는 이것만 쓴다."""
    def __init__(self, client: BaseClient, ledger: UsageLedger, policy: PolicyId): ...
    async def call(self, req: ModelCall, *, purpose: Purpose) -> APIResponse: ...
    def stream(self, req: ModelCall, *, purpose: Purpose) -> AsyncIterator[dict]: ...

Purpose = Literal["main", "compact", "distill", "subagent", "verify", "explore_attempt", "parse_repair"]

@dataclass(frozen=True)
class CallRecord:
    seq: int; purpose: Purpose; provider: str; model: str          # 실제 호출 모델(라우팅 후)
    input_tokens: int; output_tokens: int
    cache_read: int; cache_write: int; reasoning: int | None
    cli_num_turns: int | None                                      # CLI provider 내부 왕복(있으면)
    cost_usd_reported: float | None                                # 공급자 보고값
    duration_ms: int; stop_reason: str | None; error: str | None

class UsageLedger:
    def record(self, rec: CallRecord) -> None: ...
    def policy_tokens(self) -> int: ...            # c(τ) = Σ (input + output), purpose 무관(전부 포함)
    def external_usage_payload(self) -> dict: ...  # 11 문서 §3.5 형식 그대로 (anthropic·bedrock 캐시 분리)
```

- 12·13·14 문서의 누락(압축·증류·fork·재시도·내부 도구 루프·CLI 내부 턴·reasoning)은 "모든 호출이 Gateway 를 지난다"는 구조로 사라진다. Gateway 를 우회하는 정책 호출은 **커널이 금지**(하네스 구성요소는 `BaseClient` 에 직접 접근 불가).
- 가격 계산은 원장 밖 오프라인 계산(가격표 버전 기록). 외부 `total_cost_usd` 는 기존 규칙 유지.
- **평가 모드 불변식**: 진화 평가 중에는 라우터·페일오버 금지(π 고정). 실제 호출 모델이 lineage 정책과 다르면 시행을 무효 처리.

### 3.3 KernelLimits

노드 kwargs 로 주입되는 한도(`max_iterations`, `turn_input_budget_tokens`, `repeat_stop_after`, `max_continuation_slices`, 벽시계)와 기존 안내 문장(`BUDGET_NOTICE`, `REPEAT_NOTICE`, `SUSPEND_NOTICE`)을 소유한다. **하네스는 한도를 늘릴 수 없고 더 일찍 멈출 수만 있다**(RRSI hard rule 5 의 구조화).

### 3.4 ToolRunner

기존 도구 ABI(`Tool`, `ToolResult`, `ToolContext(state_view)`)를 그대로 실행한다. `state_view` 의 `add_event`(canvas_command 통과), `shared`, `pending_tool_calls` 계약을 커널이 제공. 권한 매트릭스·사용자 거부 문구(dex-core 교차 계약)·병렬도는 커널 소유. 하네스의 output_plumbing 은 결과가 **모델에 들어가기 전** 후처리만 하고, 외부 agent_event 의 4000자(머리 3200 + 꼬리 800) 규칙은 커널 stream 이 지킨다.

### 3.5 Recorder

```python
@dataclass
class TrajectoryRecord:            # τ 하나 (14 문서 §7.2 초안 + 확장)
    trajectory_id: str; task_id: str | None; trial_idx: int | None
    harness_id: str; lineage: str; explore_policy_id: str | None
    policy: PolicyId; thinking_level: str | None
    status: str; termination_reason: str
    steps: Steps                    # model_calls, iterations, tool_calls, tool_errors, slices
    usage: UsageSummary             # ledger 집계(purpose 별)
    measure: Measure                # valid_output, no_submission (커널 측정)
    verifier: VerifierResult | None # 오프라인 평가에서만 채움
    components_fired: dict[str, int]   # usage.harness 승계
    tree_id: str                    # 이 τ 의 발견 트리
    llm_tape: str | None            # 요청 해시→응답 녹화(옵트인)

@dataclass
class ReplayNode:                  # Dream-RSI 노드
    node_id: str; tree_id: str; parent_id: str | None   # None = 루트
    branch: int; attempt: int; created_seq: int
    start_snapshot: str | None; end_snapshot: str | None   # 샌드박스 스냅샷 참조
    artifact: ArtifactRef | None
    score: float | None; evaluated: bool; valid: bool | None
    fail_class: str; error: str | None
    n_valid: int | None; n_total: int | None
    tags: dict                      # direction 등 구조 메타
    cost: NodeCost                  # policy_tokens, model_calls
```

- 일반 대화 턴 = 루트 + 사슬 1개(퇴화 트리). 탐색이 일어난 하위 작업 = branch × attempt 격자.
- 저장: 운영 L0 는 **구조·점수·비용만**(내용 비보존) 기본, 평가(L1) 실행은 전체(trace 렌더링·digester 입력용).

### 3.6 Stream 어댑터

내부 이벤트를 기존 청크 문법(11 문서 §3.1)으로만 내보낸다: 텍스트 `str`, `agent_event`(tool_call/tool_result/tool_error/task_*), `canvas_command`, `usage`. thinking·내부 이벤트는 외부로 내보내지 않는다(현재 동작). 새 RSI 내부 이벤트(`harness.loaded`, `explore.batch`, `explore.node`, `ledger.call`)는 **내부 버스와 recorder 에만** 간다.

---

## 4. H — 구성요소 kind 별 인터페이스 (𝒦 9종)

모든 구성요소는 `ComponentSpec{id, kind, files, entry?, params, condition?, order?}` 로 manifest 에 선언되고, 커널이 kind 에 맞는 Protocol 로 인스턴스화한다. 구성요소는 커널이 주는 **불변 `TurnContext`** 만 보고, 상태 변경은 반환값(액션·패치)으로만 한다.

```python
@dataclass(frozen=True)
class TurnContext:
    step: int
    messages: tuple[Message, ...]            # 정규형(Anthropic 모양)
    exposed_tools: tuple[ToolSpec, ...]
    last_response: APIResponse | None
    last_tool_results: tuple[ToolResultView, ...]
    usage: UsageView                         # 원장 읽기 전용 요약(정책 토큰·호출 수)
    limits: LimitsView                       # 남은 한도(읽기 전용)
    agent: AgentSettingsView                 # 에이전트 설정(입력 x) 읽기 전용
    notes: Mapping[str, Any]                 # 구성요소가 선언한 키만(타입 있는 scratch)

# control_flow — 루프 결정의 유일한 자리
class ControlProgram(Protocol):
    def next_action(self, ctx: TurnContext) -> "Action": ...
Action = CallModel | RunTools | Compact | Verify | Explore | Finish

# prompt — 블록 목록(데이터)
class PromptBlocks(Protocol):
    def blocks(self, ctx: TurnContext) -> list["PromptBlock"]   # {id, text, order, volatile, cache_hint}

# config — 타입 있는 노브(잠금 키 편집 불가)
class HarnessConfig(Protocol):
    def get(self, key: str) -> Any: ...

# output_plumbing — 모델 입력 전 도구 결과 후처리, 응답 파싱, 구조화 출력 정착
class OutputPlumbing(Protocol):
    def shape_tool_result(self, call: ToolCallView, result: ToolResultView) -> ToolResultForModel: ...
    def parse_response(self, resp: APIResponse) -> ParsedResponse: ...
    def settle_structured(self, text: str, schema: dict | None) -> str: ...

# context_mgmt — 호출 전 메시지 구성
class ContextPolicy(Protocol):
    def prepare(self, ctx: TurnContext, gw: "BudgetedGateway") -> tuple[Message, ...]   # prune/compact (요약 호출도 gw 경유)

# client_tool — 노출 정책 + 하네스 측 도구
class ToolExposure(Protocol):
    def exposed(self, ctx: TurnContext, registry: "ToolRegistryView") -> list[ToolSpec]: ...
class HarnessTool(Protocol):     # 등록 전 사전 실행 게이트(ForgeTool 식) 필수
    spec: ToolSpec
    def run(self, args: dict, tctx: ToolContext) -> ToolResult: ...

# skill — Guide 문 + 가족 + SKILL.md 를 하나로
@dataclass(frozen=True)
class SkillSpec: gate_name: str; gate_description: str; family: tuple[str, ...]; instructions_md: str | None

# memory — 정책만(내용은 사용자 데이터, H 아님)
@dataclass(frozen=True)
class MemoryPolicy: inject_budget_chars: int; layer_ratios: dict; tools_exposed: tuple[str, ...]; distill: "DistillPolicy"

# subagent — 같은 정책에 대한 제한된 추가 호출 (활성 여부 = 결정 D-3)
@dataclass(frozen=True)
class SubagentSpec: id: str; brief_template: str; max_output_tokens: int; trigger: str   # gw purpose="subagent"
```

- **원자 편집 주소**(13 문서 §8 승계): `ℓ:<component_id>[.<field>]` + op ∈ {replace_text, set, insert, remove, reorder, add, register, unregister, swap}. 하네스 diff 는 이 주소 목록으로 정규화되어 history 에 들어간다.
- **결정성**: 같은 TurnContext 에 같은 결정(난수·시간 사용 금지, 필요한 경우 커널이 seed 제공).

---

## 5. 탐색 계층 (Dream-RSI, 턴 안)

### 5.1 언제 여는가

`ControlProgram` 이 `Explore(task)` 를 내면 커널 ExploreExecutor 가 처리한다. `task: VerifiableTask{goal, verifier_id, start_snapshot, budget_hint}`. 검증기는 커널 레지스트리의 결정적 검증기만 허용(테스트 실행, 스키마/린트, 질의 실행 결과 비교 등).

### 5.2 실행

```
plan = π_E.plan_grid(GridPlanningContext(history=live_manifests, hard caps, worker cap))   # B, R
grid  = LiveGrid(B, R, start_snapshot, directions=direction_provider(B))                    # meta.tags
question = LiveQuestion(grid, W = kernel.explore_workers, baseline_score = verifier(start))
π_E.solve(question)    # B.2 API 그대로. probe_batch → 워커 풀에서 시도 실행:
                       #   시도 = 부모 스냅샷에서 재개한 하위 궤적(Gateway purpose="explore_attempt")
                       #         → 검증기 → Observation(score, evaluated, valid, fail_class, error, …)
best = argmax score over revealed cells (성공 의미론 준수) → 메인 궤적에 병합(산출물·요약)
recorder: 격자 = 발견 트리 (세계)
```

- `LiveQuestion` 과 재생용 `ReplayQuestion` 은 **같은 Question 인터페이스**다(Dream 의 "같은 결정 인터페이스, 다른 전이"). 같은 정책 코드가 온라인·재생 양쪽에서 돈다.
- 의존성: 시도마다 **샌드박스 스냅샷 분기**(copy-on-write)가 필요하다. 기존 샌드박스 영속 계층(색인/스냅샷 2층 구조)이 분기를 지원하는지 확인 필요 → 위험 R-5(35 문서).

### 5.3 비용 연결

모든 시도 토큰은 같은 원장에 `purpose="explore_attempt"` 로 기록되어 c(τ) 에 포함된다. 따라서 탐색을 더 쓰는 π_E 는 RRSI 비용 규칙에서 그만큼 정당화되어야 하고(L1), 재생 목적함수의 −β1·N 이 같은 방향으로 작용한다(L2).

---

## 6. 진화 엔진

### 6.1 L1 — RRSI (`evolve/`)

| 모듈 | 역할 | 공식 구현 대응 |
|---|---|---|
| `domain.py` | `EvalDomain`: evolve/heldout/smoke ids, run/score, load_trial/render_trace/task_row, smoke, critic_patterns, guards, briefs, constitution | `rrsi/domain.py` |
| `evaluate.py` | `Evaluate(H', D, k)`: 격리 평가 호스트(EvalHost)에서 `RSITurnExecutor.run` 을 k 회 × |D| 실행, 검증기 → `EvalResult`. **정확 경계 조기 종료** 옵션 | `rrsi/evaluate.py` |
| `history.py` / `frontier.py` | 𝓛_t JSONL, frontier(incumbent·S★·trajectory) | `rrsi/history.py`, loop 의 frontier |
| `propose.py` | strict-JSON 행동 프로토콜 proposer(Gateway 로 XGEN 등록 LLM 호출), done() 검증(b_t, 필드, K, 예약 슬롯) | `rrsi/propose.py` |
| `critic.py` | precheck(XGEN 패턴: 평가 태스크 id, 검증기 경로, 자격증명) + LLM 6규칙 + 수리 | `rrsi/critic.py` |
| `analyst.py` / `digester.py` | 3-렌즈 보고서 / 읽기 전용 trace 조사 | 동명 |
| `round.py` | Algorithm 1+2, 재판정, 재평가, attribution | `rrsi/loop.py` |
| `driver.py` | 재개 가능한 라운드 드라이버, STOP, 연속 인프라 실패 중단 | `rrsi/driver.py` |
| `gitops.py` | 하네스 패키지 저장소의 후보 worktree/브랜치/fast-forward/tree hash | `rrsi/gitops.py` |
| `constitution/` | XGEN 인스턴스별 SKILL.md(헌법)·PATTERNS.md·briefs | `domains/*/SKILL.md` |

π_E 파일 경로는 proposer 워크스페이스에서 **읽기 전용**(P5).

### 6.2 L2 — Dream (`dream/`)

| 모듈 | 역할 |
|---|---|
| `world.py` | recorder 의 트리 → 불변 `World`(격자 + 노드 관측). 세계 풀 관리(태스크·출처·정책 버전 메타) |
| `replay.py` | `ReplayQuestion`(정보 은닉·결정적·지원 밖 처리), 에피소드 실행, `policy_execution_traces.jsonl` |
| `evaluator.py` | Eq.1(V_i^m, V^m), pareto.reward(β 격자, λ), anytime AUC, out-of-support 판정 |
| `develop.py` | policy-development agent(B.2 의 규칙을 우리 말로 재서술한 프롬프트, Gateway 경유), 산출 정책 정적 검사(prefix-only 위반 탐지: 금지 API·하드코딩 id) |
| `cycle.py` | π^0..π^{M−1} 개발·평가 → argmax V(동점 π^0 우선) → **온라인 확인**(RRSI 바닥+비용 규칙) → β 기본값 규칙 |
| `sandbox.py` | 정책 코드 실행 격리(파일·네트워크·시간·난수 차단) |

### 6.3 승격 (`promote/`)

```
후보(H 또는 π_E) ──► RRSI 선택 규칙 통과 기록 ──► held-out·OOD 평가(보고 전용) ──► 승격 보고서
     ──► MR/PR 생성(하네스 저장소) ──► 사람 승인·머지 ──► lineage 갱신 ──► shadow → canary → 기본
```

- shadow: 운영 턴을 새 H 로 *기록만* 하지 않는다(사용자에게 두 번 실행은 비용·부작용 위험). 대신 **평가 호스트에서 운영 분포를 흉내 낸 재연 세트**로 검증하고, canary 는 호스트가 일부 워크플로·사용자에만 `GenyRSITurnExecutor` 를 쓰고 lineage 버전을 고르는 방식으로 적용.
- 즉시 롤백: lineage 표를 이전 버전으로(커널이 버전별 manifest 를 불변 보관).

---

## 7. H0 — 시작 하네스는 "지금의 운영 동작"

RRSI 는 H_0 에서 출발해 H_0 대비로 모든 것을 잰다. H_0 를 *새로 상상한 하네스*로 두면 "교체 자체의 회귀"와 "진화의 이득"이 섞인다. 그래서:

- **H0 = 기존 운영 경로(13 문서 §1: 실제 등록되는 s01–s07·s09·s10·s16·s18·s21)의 동작을 구성요소로 옮긴 것.** 프롬프트 블록(`_constants.py`), 도구 노출(`TURN_ONE_TOOLS`, `GATES`, ToolSearch), 컨텍스트 6장치(13 문서 요약 6), 루프 가드(repeat/denial/second machine, DeliverableReviewer, RepeatStop, TurnInputBudget), 구조화 출력, 메모리 주입·도구·증류를 각각 kind 에 맞춰 이전.
- **동등성 기준**: 같은 시나리오 + 가짜 클라이언트(14 문서 §5 의 `_send` 패턴)에서 청크 스트림·도구 호출 순서·usage 형태가 기존과 같고, 실제 공급자 소규모 회귀 세트에서 Ŝ(H0_new) 와 Ŝ(pipeline21) 의 차이가 δ 안(PLAN Phase 2 종료 기준).
- 기존 동작의 알려진 결함(10·12·14 문서: 오류 코드 손실, s08 생각 블록, Gemini stop_reason, 원장 누락)은 H0 에서 **고친다** — 단 외부 계약에 보이는 변화는 32 문서의 결정 표를 따른다.
