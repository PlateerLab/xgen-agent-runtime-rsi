# 10. xgen-agent-runtime 21-stage 파이프라인 코어와 설계 철학

- 조사 대상: `PlateerLab/xgen-agent-runtime@2e015ae` (main, commit `2e015ae6`, `pyproject.toml` version `4.75.0`)
- 목적: RRSI / Dream-RSI 기반 신규 하네스로 코어를 교체할 때 "무엇을 버리고, 무엇을 외부 계약으로 지켜야 하는가"를 판단하기 위한 사실 조사.
- 표기: 코드 근거는 레포 루트 기준 `src/xgen_agent_runtime/...:line`. 문서 근거는 `docs/...:line`, `README.md:line`, `PLAN.md:line`. 직접 확인하지 못한 것은 **미확인**으로 표시했다.
- 검증 실험: 스크래치 스크립트로 두 가지를 실제 실행해 확인했다(레포 무변경, `.venv/bin/python`). 결과는 부록 A.

---

## 0. 요약

1. **실행 모델은 문서와 다르다.** `docs/architecture.md:19-30`와 `README.md:67-78`은 "Phase A(1-5) 턴당 1회, Phase B(6-16) 루프"라고 쓰지만, 코드는 Stage 1만 1회 돌고 **Stage 2-16 전체가 루프 본문**이다(`src/xgen_agent_runtime/core/pipeline.py:832-835`, `:3051-3058`). Stage 3(System)·4(Guard)·5(Cache)는 매 반복 다시 실행되고, Stage 2는 내부에서 `iteration == 0`일 때만 검색한다(`src/xgen_agent_runtime/stages/s02_context/artifact/default/stage.py:415-425`). 실험으로 확인했다(부록 A-2). 원 설계서도 "Stage 2 재진입"이다(`PLAN.md:140`).
2. **루프 재진입은 Stage 16 혼자 정하지 않는다.** 공유 가변 필드 `state.loop_decision`에 s10(`continue`), s14(평가 결과 매핑), s15(HITL 거부/취소), s16(최종), 파이프라인 하드 리밋(`suspend`/`escalate`)이 차례로 쓴다. s16은 상류가 써 둔 terminal 값을 그대로 존중한다(`src/xgen_agent_runtime/stages/s16_loop/artifact/default/stage.py:156-162`). 규칙이 stage 순서와 문자열 값에 숨어 있다.
3. **완료 판정은 프롬프트 텍스트 프로토콜에 묶여 있다.** s09가 응답 텍스트에서 `[CONTINUE]`, `[COMPLETE]`/`[TASK_COMPLETE]`, `[BLOCKED]`, `[ERROR]`를 정규식으로 찾고(`src/xgen_agent_runtime/stages/s09_parse/artifact/default/signals.py:15-22`), 기본 controller는 "도구 결과가 있으면 continue, 마커가 없고 도구 호출도 없으면 complete"로 정한다(`src/xgen_agent_runtime/stages/s16_loop/artifact/default/controllers.py:107-133`). 마커를 쓰라는 지시는 일부 preset 프롬프트에만 있다(`src/xgen_agent_runtime/memory/presets.py:392`).
4. **`PipelineState`가 실제 stage 간 버스다.** 필드가 약 70개이고 수명은 3종류(per-turn / sticky / session-cumulative)다(`src/xgen_agent_runtime/core/state.py:72-142`). 거기에 자유 형식 dict `shared`, `metadata`가 붙는다. stage 간 계약은 문자열 키 관례로 유지된다. core가 stage와 host의 키 이름까지 알고 있어 층이 뒤집혀 있다(`src/xgen_agent_runtime/core/state.py:413-438`, `src/xgen_agent_runtime/core/compaction.py:34-43`).
5. **설정 채널이 여럿이고, 이 레포의 운영 host는 manifest를 쓰지 않는다.** 문서상 채널은 manifest, `PipelineConfig`, `attach_runtime`, `refresh_runtime`/`PipelineMutator`, 실행별 `ModelOverrides` 5단계다(`docs/architecture.md:126-136`). 여기에 에이전트가 직접 고치는 `env` 도구가 더해진다. 그런데 레포 안의 운영 host 경로(`host/runner.py::build_pipeline`)는 `PipelineBuilder`로 조립한 뒤 `get_stage(10)`, `get_stage(16)`, 비공개 속성을 직접 만진다(`src/xgen_agent_runtime/host/runner.py:615-805`). "manifest가 단일 진실 원천"이라는 원칙이 실제 운영 경로에는 적용되지 않는다.
6. **교체해도 지켜야 할 외부 계약** (상세는 §7.1):
   - `Pipeline.run(input, state=None, *, overrides=None) -> PipelineResult`
   - `Pipeline.run_stream(...) -> AsyncIterator[PipelineEvent]`
   - `PipelineEvent` envelope(type/stage/iteration/timestamp/data/session_id/run_id/seq)
   - `EventTypes` 140개 이름(append-only, `src/xgen_agent_runtime/events/catalog.py:67-70`)
   - `CONTINUE_RUN`과 `RunStatus` slice 의미
   - host bridge가 실제로 소비하는 이벤트 약 9종(`src/xgen_agent_runtime/host/runner.py:1149-1510`)
7. **stage에서 난 오류는 원래 오류 코드를 잃는다.** `_run_stage`가 모든 예외를 `code` 없이 `StageError`로 감싼다(`src/xgen_agent_runtime/core/pipeline.py:3613`). 그래서 stage에서 난 오류의 `pipeline.error.code`는 항상 `exec.stage.failed`가 된다. 원래 코드는 그 직전 `stage.error`에만 남는다. 실험으로 확인했다(부록 A-1). `README.md:279-290`의 예시(`pipeline.error`에 `exec.cli.auth_failed`)와 다르다.
8. **자기 수정 환경이 이미 있다.** `PipelineEnvironment`가 에이전트의 `env` 도구를 통해 prompt, 도구 활성화, 도구 제작(forge), skill, 모델 tunable을 실행 중에 바꾸고 다음 턴부터 반영한다. model/provider/credential은 잠겨 있다(`src/xgen_agent_runtime/core/environment_control.py:52-68`, `:299-412`, `:730-763`). RSI식 컴포넌트 편집의 원형이지만, 세션 범위에서만 유효하고 정규화나 평가 장치는 없다.
9. **번호 체계는 21이지만 살아 있는 stage는 19개다.** 12 `agent`와 13 `task_registry`는 4.71.0에서 은퇴했지만 번호는 그대로 예약되어 있다(`src/xgen_agent_runtime/core/artifact.py:115-118`, `docs/architecture.md:60-69`). 저장된 manifest, snapshot, UI가 번호 21칸에 묶여 있다.
10. **문서와 코드가 어긋난 곳이 많다(§1.5).** active=False의 의미, `pipeline.snapshot()` 부재, slot 이름 `loader`, 자동 이어가기 기본값 20 대 2, minimal preset의 Token stage 포함, attach_runtime docstring의 "Stage 15 (Memory)" 등이다. 재설계 문서는 코드를 기준으로 삼아야 한다.

---

## 1. 설계 철학 (문서상 주장)

### 1.1 "harness"라는 자기 정의
- README 첫 줄: "**A harness-engineered agent pipeline library — 21 stages, 5 LLM providers, MCP-native, fully introspectable.**" (`README.md:8`). "No LangChain. No LangGraph. Just an explicit, modular pipeline where every step is observable, mutatable, and swappable." (`README.md:10`)
- architecture.md: "a **harness** — a deliberately explicit pipeline that exposes every step of agent execution rather than hiding it behind framework magic" (`docs/architecture.md:7`). 여기서 두 가지 약속이 나온다(`docs/architecture.md:9-10`).
  1. "**Configuration is artifact.**" 파이프라인은 `EnvironmentManifest`(JSON) 하나로 완전히 기술되고, 로드하면 결정적으로 재구성된다.
  2. "**Dual abstraction.**" stage 통째 교체(Level 1)와 stage 안 strategy 교체(Level 2)가 서로 직교한다. 둘 다 manifest 편집만으로 가능하다.
- 귀결로 제시된 것(`docs/architecture.md:12-15`): 모든 행동 변경에 diff 가능한 산출물 변경이 대응하고, 테스트가 manifest를 고정해 end-to-end를 검증하며, host 하나가 여러 environment를 운용한다.
- 원 설계서(`PLAN.md:17-26`)의 원칙: "Harness as Architecture — 모든 실행은 반드시 파이프라인을 통과", "Modular Bypass — 불필요 시 bypass 가능하나 구조적 위치는 항상 존재", "WebUI-Ready — 단계는 고정, 개별 Stage의 구현체를 교체하는 형식".

### 1.2 Dual abstraction의 코드 실체
- Level 1: `Stage(ABC, Generic[T_In, T_Out])`(`src/xgen_agent_runtime/core/stage.py:85`). Pipeline은 `Dict[int, Stage]`에 `stage.order`를 키로 등록한다(`src/xgen_agent_runtime/core/pipeline.py:1692-1695`).
- Level 2: `Strategy(ABC)`(`src/xgen_agent_runtime/core/stage.py:39`). stage는 `StrategySlot`(전략 1개 + 이름→클래스 registry, `src/xgen_agent_runtime/core/slot.py:21-83`)이나 `SlotChain`(순서 있는 전략 리스트, `src/xgen_agent_runtime/core/slot.py:87-205`)을 소유하고, `get_strategy_slots()`/`get_strategy_chains()`로 노출한다(`src/xgen_agent_runtime/core/stage.py:176-191`).
- 교체 경로: `Stage.set_strategy(slot, impl, config)` → `StrategySlot.swap()`. registry에 있는 클래스를 **인자 없이** 만든 뒤 `configure(config)`를 부른다(`src/xgen_agent_runtime/core/slot.py:48-71`). 따라서 생성자 인자가 필요한 전략(예: host가 만든 retriever)은 manifest로 선택할 수 없고, `attach_runtime`으로 인스턴스를 직접 넣어야 한다(§2.8).

### 1.3 Manifest = 단일 진실 원천, 설정 우선순위
- "Provider selection is pinned at `stages[6].config["provider"]`… each lives in exactly one manifest field." (`docs/architecture.md:116`). strict 로드는 legacy `strategies["provider"]`를 거부한다(`src/xgen_agent_runtime/core/pipeline.py:251`, `docs/manifest.md:108-114`).
- 우선순위 5단계(`docs/architecture.md:128-134`), 높은 것부터:
  1. 실행별 `ModelOverrides`
  2. `PipelineMutator`/`refresh_runtime`
  3. `attach_runtime` 런타임 객체
  4. Manifest
  5. `PipelineConfig` 기본값

  실행 시작 시 읽는 순서는 `apply_to_state`로 state를 덮어쓴 뒤(4/5, 2가 변경한 값 포함) override(1)를 얹는 것이다(`docs/architecture.md:136`).

### 1.4 Environment와 Preset
- Preset: 빌더 단축(`PipelinePresets.minimal/chat/agent/evaluator/geny_vtuber`, `src/xgen_agent_runtime/core/presets.py:208-331`). 스크립트와 테스트용이다.
- Manifest: 운영 host, UI 편집, 멀티테넌트용(`docs/manifest.md:153-158`). 라이브러리 소유 팩토리는 `build_manifest(preset, provider=..)`이고 `worker_adaptive`/`vtuber`/`default` 세 가지다(`src/xgen_agent_runtime/core/manifest_factory.py:76-80`, `:546-660`).

### 1.5 왜 21 stage인가 (역사)
- 시작은 16단계였다: "Claude Code 11단계 + Geny 철학 반영 5단계"(`PLAN.md:109`). 추가된 5개는 Cache, Think, Agent, Evaluate, Memory다.
- Sub-phase 9a(S9a.3)에서 21칸으로 늘었다. 새로 생긴 칸은 tool_review(11), task_registry(13), hitl(15), summarize(19), persist(20)다(`src/xgen_agent_runtime/core/pipeline.py:827-835`, `src/xgen_agent_runtime/core/manifest_factory.py:93-99`). 처음에는 pass-through scaffold로 들어왔다(`src/xgen_agent_runtime/core/builder.py:111-117`).
- 4.71.0에서 12 agent와 13 task_registry가 은퇴했다. 번호를 다시 매기지 않은 이유는 "stored manifests, snapshots and UIs line up"(`docs/architecture.md:60-62`).
- 결론: "21"에는 기능적 필연성이 없다. 역사적으로 칸을 덧붙여 온 결과다. 21칸을 고정한 것은 UI 렌더링과 저장 포맷 호환 때문이다(`src/xgen_agent_runtime/core/pipeline.py:2993-3015`, `src/xgen_agent_runtime/core/environment.py:124-131`).

### 1.6 문서와 코드가 어긋나는 곳 (재설계 시 코드를 정답으로 볼 것)

| 문서 주장 | 코드 실제 |
|---|---|
| Phase A = Stage 1-5 턴당 1회 (`docs/architecture.md:20-21`, `README.md:68-69`) | Stage 1만 1회. 2-16이 매 반복 실행된다(`src/xgen_agent_runtime/core/pipeline.py:832-833`, `:3052-3058`). 클래스 docstring은 코드와 맞다(`:815-818`) |
| manifest `active:false`면 "registered but `should_bypass()` returns True" (`docs/manifest.md:88`) | inactive 항목은 아예 생성·등록되지 않는다(`src/xgen_agent_runtime/core/pipeline.py:1354-1356`). 빈 칸은 `stage.bypass` 이벤트만 낸다(`:3537-3548`) |
| `snap = pipeline.snapshot(); snap.to_manifest()` (`docs/manifest.md:143-145`) | `Pipeline`에 `snapshot()`이 없다. `PipelineMutator.snapshot()`(`src/xgen_agent_runtime/core/mutation.py:609`)과 `EnvironmentManifest.from_snapshot()`(`src/xgen_agent_runtime/core/environment.py:752`)뿐이다 |
| `await mut.swap_strategy(stage_order=2, slot_name="loader", impl=...)` (`docs/manifest.md:134`) | 동기 함수이고 인자 이름은 `impl_name`이다(`src/xgen_agent_runtime/core/mutation.py:156-162`). s02의 slot 이름은 `strategy/compactor/retriever`라 `loader`는 없다(`src/xgen_agent_runtime/stages/s02_context/artifact/default/stage.py:99-130`). `vector_search` 전략도 없다(`docs/architecture.md:39` 대비) |
| minimal = Input→API→Parse→Yield (`README.md:384`) | builder가 Token(7)도 항상 등록한다(`src/xgen_agent_runtime/core/builder.py:149-159`) |
| `pipeline.error` payload에 원래 코드(`exec.cli.auth_failed`)가 실린다 (`README.md:279-290`) | stage 예외는 `StageError`로 감싸져 `exec.stage.failed`가 된다(`src/xgen_agent_runtime/core/pipeline.py:3613`, 부록 A-1) |
| `memory_strategy → Stage 15 (Memory)` (`src/xgen_agent_runtime/core/pipeline.py:1755-1756` docstring) | Memory는 18번이다. 코드는 이름(`"memory"`)으로 찾으므로 동작은 정상이다(`:1972-1980`) |
| host 자동 이어가기 기본 20 슬라이스 (`docs/long_running_execution.md:204-205`) | `DEFAULT_MAX_CONTINUATION_SLICES = 2`(`src/xgen_agent_runtime/host/runner.py:1306`) |
| stage 클래스 docstring 번호 | s16 "Stage 13: Loop"(`src/xgen_agent_runtime/stages/s16_loop/artifact/default/stage.py:1`, `:23`), s14 "Stage 12", s17 "Stage 14", s18 "Stage 15", s21 "Stage 16". 16-stage 시절 번호가 남아 있다 |

### 1.7 내부 철학 감사의 결론 (2026-06-09)
- "**골격은 진짜고, 의미는 아직 구호다.**"(`docs/reviews/2026-06-09-environment-philosophy-audit.md:13`)
  - config fidelity가 세 층으로 갈라져 있다. stage 선택은 완벽하게 동작하지만 stage-level config는 21개 중 9개만 소비되고, strategy-level config는 조용히 버려지는 일이 많았다.
  - 설정 채널이 약 10개였다.
  - host 보상 코드가 약 3,800줄이었다(`docs/reviews/2026-06-09-environment-philosophy-audit.md:15-27`).
- 그 뒤 2.2.0에서 `validate_manifest`, `ModelOverrides`, `refresh_runtime`, `EventTypes` 카탈로그, `aclose()`, run-lock이 추가되어 일부가 메워졌다(`docs/architecture.md:149-169`). 그래도 §7.2의 구조적 문제는 남아 있다.

---

## 2. `core/pipeline.py` — 엔진

### 2.1 클래스 상수와 내부 상태
- 루프 경계: `LOOP_START = 2`, `LOOP_END = 16`(포함), `FINALIZE_START = 17`, `FINALIZE_END = 21`, `EVENT_DATA_TRUNCATE = 500`(`src/xgen_agent_runtime/core/pipeline.py:832-836`). 번호→이름 기본표 `_DEFAULT_STAGE_NAMES`에는 은퇴한 12/13도 들어 있다(`:848-870`).
- 생성자 `Pipeline(config: PipelineConfig | None = None, *, event_journal_size: int = 2048)`(`:872-877`)의 주요 내부 필드(`:878-1002`):

  | 범주 | 필드 |
  |---|---|
  | 구성 | `_stages: Dict[int, Stage]`, `_config` |
  | 이벤트 | `_event_bus`, `_event_seq`, `_event_journal`(ring), `_event_taps` |
  | 도구·외부 연결 | `_mcp_manager`, `_tool_registry`, `_cli_mcp_passthrough`, `_memory_provider`, `_tool_providers`, `_adhoc_providers` |
  | 자기 수정 환경 | `_env_persistence`, `_pack_persistence`, `_environment` |
  | 클라이언트 | `_attached_llm_client`, `_attached_sandbox`, `_credentials`, `_attached_session_runtime`, `_client_generation`, `_warm_llm_client`, `_manifest_provider` |
  | rollout 기록 | `_run_rollout_recorders` |
  | HITL | `_pending_hitl: token→Future` |
  | hook | `_hook_runner`, `_lifecycle_tail` |
  | 실행·수명 | `_runs_in_flight`(카운터), `_active_run_tasks`, `_closed`, `_has_started`, `_pending_runtime_events`, `dropped_stages` |

### 2.2 구성 경로
- `from_manifest(manifest, *, credentials, api_key, strict=True, adhoc_providers, tool_registry, satisfied_config)`(`src/xgen_agent_runtime/core/pipeline.py:1203-1213`)의 단계:
  1. 은퇴 선언 제거(`:1283`)
  2. provider 위치 검증(`:1289`)
  3. `validate_manifest` — strict면 error 수준 항목이 있을 때 `ConfigError`(`:1299-1318`)
  4. `PipelineConfig` 조립(`:1334`, `:91-110`)
  5. **active인 항목만** `create_stage(name, artifact, **kwargs)`로 생성·등록. lenient 모드의 생성 실패는 `dropped_stages`에 기록(`:1354-1383`)
  6. `PipelineMutator.restore(manifest.to_snapshot())`로 slot·config·chain·binding·model_override 적용. strict에서 skip이 생겨도 경고만 남긴다(`:1385-1401`)
  7. stage config schema 검증(`:1403-1417`)
  8. built-in → external 도구 등록, `ToolResolutionReport` 작성(`:1430-1446`)
  9. 이미 생성된 stage의 registry 참조를 사후에 재바인딩(`:1473-1478`). 생성 순서 문제를 덮는 패치다
  10. `manifest.memory`가 있으면 provider를 만들고 `_apply_runtime` 경로로 배선(`:1486-1517`)
- `from_manifest_async(...)`(`:1522-1688`): 위에 더해 ToolProvider 기동, MCP 연결과 도구 등록(CLI provider면 subprocess의 `--mcp-config`로 넘김, `:1601-1664`), 자기 수정 환경 컨트롤러 초기화(`:1677`), 백그라운드 `warmup()`(`:1683-1687`)을 한다.
- 수동 경로: `register_stage`/`replace_stage`/`remove_stage`/`get_stage`/`stages`(`:1692-1714`). 같은 order로 등록하면 기존 stage를 대체한다.

### 2.3 `run` / `run_stream`
```python
async def run(self, input: Any, state: Optional[PipelineState] = None, *,
              overrides: Optional[ModelOverrides] = None) -> PipelineResult          # pipeline.py:2426-2432
async def run_stream(self, input: Any, state: Optional[PipelineState] = None, *,
                     overrides: Optional[ModelOverrides] = None) -> AsyncIterator[PipelineEvent]  # :2535-2541
```
- **공통 흐름**
  1. `_ensure_not_closed()`
  2. `_init_state(state, overrides, continuation=isinstance(input, ContinuationInput))`
  3. `_runs_in_flight += 1`과 활성 task 등록. 첫 await 이전에 lock을 건다
  4. `pipeline.start` 발행
  5. `PIPELINE_START` hook(대기하지 않음)
  6. `_run_phases(input, state)`
  7. `pipeline.complete`(정상) 또는 `pipeline.error`(예외)
  8. finally: 카운터 감소, `_end_turn`, hook flush, `PIPELINE_END`(대기함), rollout flush
- **`run` 특이점** (`src/xgen_agent_runtime/core/pipeline.py:2462-2533`)
  - 예외를 잡으면 `state.mark_failed` 뒤 `PipelineResult.error_result()`를 **반환**한다. 예외는 밖으로 나가지 않는다(`:2507-2517`).
  - `pipeline.complete` payload에는 `result`와 `total_cost_usd`가 없다(`:2493-2504`).
- **`run_stream` 특이점** (`:2581-2729`)
  - bus에 `"*"` 구독 하나를 걸고 `run_id`로 다른 실행의 이벤트를 걸러낸다(`:2592-2597`).
  - phase는 백그라운드 task에서 돈다(`:2690`). 큐에서 sentinel이 나올 때까지 yield한다(`:2696-2700`).
  - 소비자가 generator를 버려도 **백그라운드 task는 계속 돈다**. 문서에 명시된 동작이다(`:2614-2619`).
  - `pipeline.complete` payload에 **절단 없는 `result`(final_text)**, `total_cost_usd`, status 등이 실린다(`:2630-2647`).
  - state를 돌려줄 객체가 없으므로 호출자가 state를 직접 만들어 쥐고 있어야 한다(`:2548-2556`).

### 2.4 `_init_state` — 턴 경계 소유자 (`src/xgen_agent_runtime/core/pipeline.py:3146-3326`)
1. 이미 실행한 적 있는 pipeline에 `state=None`이 오면 1회 경고한다(대화 기억 상실 방지, `:3179-3188`).
2. 같은 state의 동시 실행을 거부한다(`_turn_in_flight`, `:3195-3200`).
3. `CONTINUE_RUN`이면 `SUSPENDED` 상태가 아닐 때 `ValueError`를 내고, 맞으면 `begin_continuation_slice()`를 부른다. 재사용 state(`_run_count>0` 또는 기존 messages 있음)면 `begin_turn()`을 부른다(`:3210-3219`).
4. 실행마다 `run_id`를 새로 만들고 `state._bus_emitter` 브리지를 설치한다(`:3228-3229`).
5. `self._config.apply_to_state(state)`로 덮어쓴 뒤 `overrides.apply_to_state`를 적용하고, `config.override_applied` 이벤트는 `_run_phases`까지 미룬다(`:3231-3248`).
6. credentials를 주입하고 llm_client를 해석한다. generation이 맞지 않으면 다시 해석한다(`:3249-3263`). session_runtime도 주입한다(`:3264-3265`).
7. **stage 이름으로 Tool stage를 찾아** `state.tool_dispatcher = ToolDispatcher(tool_stage)`를 단다(`:3274-3283`).
8. **order 2와 4를 숫자로 찍어** Context stage의 compactor·provider를 state와 Guard stage에 자동 배선한다(`:3291-3319`). 비공개 속성 `_compactor`, `_provider`, `_budget_compactor`, `_compaction_enabled`를 직접 읽고 쓴다.
9. rollout recorder를 붙이고 `_turn_in_flight = True`로 만든다(`:3321-3326`).

### 2.5 `_run_phases` — 실행 순서와 루프 의미 (`src/xgen_agent_runtime/core/pipeline.py:3019-3142`)
```
flush deferred events (pipeline-scoped, run-scoped)                       :3042-3049
Phase A : current = _run_stage(1, input)                                   :3052
Phase B : while True:
            for order in 2..16: current = _try_run_stage(order, current)   :3056-3058
            if no stage@16 and decision=="continue": decision="complete"   :3061-3062
            if state.single_turn and decision=="continue": "complete"      :3065-3066
            LOOP_ITERATION_END hook (nowait)                               :3071-3079
            if decision != "continue": break                               :3081-3082
            state.iteration += 1                                           :3084
            if is_over_iterations: decision="suspend", mark_suspended,
                                   add_event("loop.suspended"); break      :3087-3106
            if is_over_budget:     decision="escalate", mark_blocked,
                                   add_event("loop.blocked"); break        :3107-3122
map loop_decision → run_status (if still RUNNING)                          :3127-3138
Phase C : for order in 17..21: current = _try_run_stage(order, current)    :3141-3142
```
- **재진입 조건**: 루프 본문 한 바퀴가 끝났을 때 `state.loop_decision == "continue"`이면 다시 돈다. 그 값을 마지막으로 쓰는 것은 보통 s16이지만, 실제로는 §4.3의 여러 stage가 함께 결정한다.
- **iteration 의미**: 0부터 시작하고 "계속"할 때만 증가한다. 두 바퀴를 돈 턴의 `PipelineResult.iterations`는 1이다(부록 A-2). 하드 리밋 검사(`iteration >= max_iterations`)는 증가 직후에 한다(`src/xgen_agent_runtime/core/state.py:628-631`).
- **status 매핑**(`src/xgen_agent_runtime/core/pipeline.py:3127-3138`)

  | `loop_decision` | `run_status` | 비고 |
  |---|---|---|
  | `complete` | `completed` | |
  | `suspend` | `suspended` | 재개 가능 |
  | `escalate` | `blocked` | `USER_INPUT_REQUIRED` |
  | `error` | `failed` | |

  하드 리밋에서 미리 찍은 mark가 이 매핑보다 우선한다.
- **Phase C는 루프가 어떻게 끝났든 항상 돈다**(suspend/escalate/error 포함). 예외로 루프를 빠져나간 경우에는 돌지 않는다.
- `_run_phases`의 반환값(s21의 `current`)은 **버려진다**. 결과는 `PipelineResult.from_state(state)`로 state에서 다시 만든다(`src/xgen_agent_runtime/core/pipeline.py:2491`).

### 2.6 bypass와 오류 처리
- `_try_run_stage`(`src/xgen_agent_runtime/core/pipeline.py:3535-3558`): 칸이 비어 있거나 `stage.should_bypass(state)`가 True면 `stage.bypass` 이벤트를 내고 `current`를 그대로 넘긴다.
- `_run_stage`(`:3560-3613`)의 순서:
  1. `state.current_stage`를 설정하고 `stage_history`에 추가
  2. `stage.enter` 이벤트와 `STAGE_ENTER` hook
  3. `on_enter`
  4. `execute`
  5. `on_exit`
  6. `stage.exit` 이벤트와 `STAGE_EXIT` hook
- 예외가 나면 `stage.error`(원래 코드 포함, `_error_event_data`, `:64-88`)를 낸 뒤 `stage.on_error(e, state)`를 부른다. 값이 돌아오면 그 값으로 복구하고, `None`이면 `StageError(str(e), stage_name, stage_order, cause=e)`를 던진다. 이때 `code`를 넘기지 않아 **기본 코드 `EXEC_STAGE_FAILED`가 붙는다**(`src/xgen_agent_runtime/core/errors.py:227-243`).
- `run()`은 `Exception`만 잡는다(`src/xgen_agent_runtime/core/pipeline.py:2507`). `GuardRejectError`(Stage 4)도 같은 경로로 failed 결과가 된다.

### 2.7 이벤트 발행 지점

| 지점 | 이벤트 | 채널 |
|---|---|---|
| `run`/`run_stream` 시작 | `pipeline.start {input(500자 절단)}` | `_emit` → journal → bus (`src/xgen_agent_runtime/core/pipeline.py:2478-2483`, `:2682-2687`) |
| 각 stage | `stage.enter`, `stage.exit`, `stage.bypass`, `stage.error` | `_emit` (`:3541-3609`) |
| 루프 하드 리밋 | `loop.suspended`, `loop.blocked` | `state.add_event` → 브리지 (`:3097-3121`) |
| 턴 시작 시 지연 발행 | `runtime.llm_client_override`, `config.override_applied` | `state.add_event` (`:3042-3049`) |
| 종료 | `pipeline.complete` / `pipeline.error` | `_emit_rollout_terminal`. rollout flush 후 bus 발행 (`:2881-2900`) |
| stage 내부 | 도메인 이벤트 약 120종 | `state.add_event` → `_bus_emitter` → `_record_event` → `emit_sync` (`src/xgen_agent_runtime/core/state.py:476-514`, `src/xgen_agent_runtime/core/pipeline.py:2902-2926`) |

모든 이벤트는 `_record_event`를 지나면서 `seq`를 부여받고 journal(ring)과 tap에 들어간다(`src/xgen_agent_runtime/core/pipeline.py:2813-2838`). host가 bus에 직접 발행한 이벤트는 journal에 남지 않는다(`:2766-2767`).

### 2.8 런타임 주입: `attach_runtime` / `refresh_runtime` / `_apply_runtime`
- `attach_runtime(...)`는 첫 실행 전에만 허용되고, 이후에 부르면 `RuntimeError`다(`src/xgen_agent_runtime/core/pipeline.py:1872-1881`). `refresh_runtime(**kw)`는 실행 중에만 거부된다(`:1931-1939`). 둘 다 `_apply_runtime`을 공유한다(`:1941-2111`).

| kwarg | 꽂히는 곳 |
|---|---|
| `memory_retriever` | stage `"context"`의 slot `retriever`(`:1967-1970`) |
| `memory_strategy` / `memory_persistence` | stage `"memory"`의 slot `strategy` / `persistence`(`:1972-1980`) |
| `system_builder` | stage `"system"`의 slot `builder`와 env 컨트롤러(`:1982-1989`) |
| `tool_context` | Tool stage의 `_context`를 통째로 교체(`:2010-2015`, `:2300-2319`) |
| `llm_client` | `_attached_llm_client`. manifest provider와 다르면 `ConfigError`, `override_manifest=True`면 `runtime.llm_client_override` 이벤트를 예약. generation을 올린다(`:2017-2059`) |
| `sandbox` | Tool `ctx.sandbox`. generation을 올린다(`:2061-2070`) |
| `session_runtime` | 다음 실행 때 `state.session_runtime`(`:2072-2073`, `:3264-3265`) |
| `hook_runner` | pipeline 자신의 lifecycle hook과 Tool ctx(`:2075-2084`) |
| `permission_rules` / `permission_mode` | Tool ctx(`:2086-2094`) |
| `mcp_manager` | 교체 후 registry 재시드(`:2096-2111`) |
| `env_persistence` / `pack_persistence` / `env_settings_schemas` | env 컨트롤러(`:1991-2008`) |

- **대상 stage가 없으면 조용히 아무것도 하지 않는다**(`src/xgen_agent_runtime/core/pipeline.py:2281-2298`). 운영 host 주석이 이 함정 때문에 실제로 STM 기록이 죽은 사고를 기록하고 있다(`src/xgen_agent_runtime/host/runner.py:678-681`).

### 2.9 취소와 정리
- `aclose()`(`src/xgen_agent_runtime/core/pipeline.py:1060-1177`)의 순서:
  1. 대기 중 HITL을 CANCEL
  2. 활성 run task를 cancel하고 gather
  3. s02 백그라운드 압축 취소
  4. `events()` tap에 종료 sentinel 투입
  5. MCP `disconnect_all`
  6. ToolProvider shutdown
  7. LLM client `aclose`

  각 단계는 best-effort이고 aclose 자체는 idempotent다. 닫힌 pipeline에서 `run`을 부르면 `RuntimeError`다(`:2323-2337`).
- `asyncio.CancelledError`는 `except Exception`에 잡히지 않는다. 그래서 task를 취소하면 finally만 실행되고 예외는 그대로 전파된다. `RunStatus.CANCELLED`와 `TerminationReason.CANCELLED`는 정의만 있고(`src/xgen_agent_runtime/core/run_status.py:22`, `:38`) **값을 설정하는 코드가 src 어디에도 없다**(grep 확인). 취소된 턴의 state는 `running`으로 남는다.
- host 수준 취소는 이벤트 사이에서 협조적으로 확인하는 방식이다(`stream_turn(cancel_check=...)`, `src/xgen_agent_runtime/host/runner.py:1340-1354`).

### 2.10 기타 공개 API
- `on(event_type, handler)`(`src/xgen_agent_runtime/core/pipeline.py:2733`). `events(replay_from=-1)` 멀티 tap은 seq cursor로 재생한다(`:2750-2811`).
- HITL: `list_pending_hitl()`, `resume(token, decision)`, `cancel_pending_hitl(token)`(`:2930-2989`). future는 s15의 `PipelineResumeRequester`가 `_pending_hitl`에 등록한다(`:937-943`).
- `describe() -> List[StageDescription]`은 1..21 전부를 내고, 빈 칸은 `category="unregistered"`로 표시한다(`:2993-3015`).
- `invalidate_client()`, `warmup()`, `run_in_progress`(`:2340-2424`).

---

## 3. core 데이터 타입과 보조 모듈

### 3.1 `PipelineState` 필드 (`src/xgen_agent_runtime/core/state.py:72-333`)
수명 표기: **T** = per-turn(`begin_turn`에서 리셋), **S** = sticky, **C** = session-cumulative, **R** = 실행마다 pipeline이 다시 설정, **P** = private.

| 필드 | 타입 | 수명 | 의미 / 주 writer → reader |
|---|---|---|---|
| `session_id` | str | S | host가 부여. 이벤트·Tool ctx·persist 키 |
| `pipeline_id` | str | S | `_init_state`가 비어 있으면 생성(`src/xgen_agent_runtime/core/pipeline.py:3221-3222`) |
| `system` | str \| List[block] | S | s03 작성(`src/xgen_agent_runtime/stages/s03_system/artifact/default/stage.py:318`), s05 블록화, s06 송신 |
| `messages` | List[dict] (Anthropic 형식) | S | s01·s06·s10·s16(note)·s02(압축, window prepend) 작성. **s05가 cache_control을 history에 직접 박는다** |
| `iteration` | int | T | pipeline이 증가(`src/xgen_agent_runtime/core/pipeline.py:3084`) |
| `max_iterations` | int=50 | R | `apply_to_state` |
| `current_stage` / `stage_history` | str / List[str] | T | `_run_stage` |
| `stream` / `single_turn` | bool | R | config에서 옴. s06 `_resolve_stream`, 루프 강제 종료 |
| `model` `max_tokens` `temperature` `top_p` `top_k` `stop_sequences` | 모델 파라미터 | R | `apply_to_state`와 overrides. `Stage.resolve_model_config` |
| `tools` | List[dict] | S | s03이 registry version 변경 시 재구성(`src/xgen_agent_runtime/stages/s03_system/artifact/default/stage.py:335-358`). s05가 cache_control 표시 |
| `tool_choice` | dict? | S | **어떤 stage도 쓰지 않는다**(host 전용), s06이 읽음 |
| `tools_version` | int=-1 | S | s03 |
| `thinking_enabled` `thinking_budget_tokens` `thinking_type` `thinking_display` `thinking_level` | thinking 설정 | R | config. s08 bypass 판정 |
| `thinking_history` | List[dict] | S(2000개 상한) | s09가 추가 |
| `token_usage` | TokenUsage | C | s07 tracker(`src/xgen_agent_runtime/stages/s07_token/artifact/default/trackers.py:25`) |
| `turn_token_usage` | List[TokenUsage] | T | s07. s16 turn_budget·repeat_stop가 호출 수로 사용 |
| `total_cost_usd` | float | T | s07 `accumulate_cost`. 예산 guard 3곳이 비교 |
| `session_cost_usd` | float | C | `_end_turn`이 delta를 합산(`src/xgen_agent_runtime/core/pipeline.py:3340-3342`) |
| `cost_budget_usd` | float? | R | config |
| `cache_metrics` | CacheMetrics | S | s07이 writes/reads 카운트. `estimated_savings_usd`/`cache_hit_rate`는 **쓰는 곳 없음** |
| `memory_refs` | List[dict] | S(2000) | s02 |
| `context_window_budget` | int=200000 | R | s02·s04·compaction 임계 |
| `loop_decision` | str | T | s10/s14/s15/s16/pipeline (§4.3) |
| `completion_signal` / `completion_detail` | str? | T | s09 리셋·설정, s15, s16 보조 장치, pipeline |
| `pending_tool_calls` | List[dict] | T | s09 작성 → s10 소비 후 비움 |
| `tool_results` | List[dict] | T | s10 작성 → s11/s14/s16 판단, s16 끝에서 비움(`src/xgen_agent_runtime/stages/s16_loop/artifact/default/stage.py:197`) |
| `tool_dispatcher` | ToolDispatcher? | R | `_init_state`. s06 internal loop |
| `evaluation_score` / `evaluation_feedback` | float? / str? | T | s14 |
| `final_text` / `final_output` | str / Any | T | s09 / s21 formatter |
| `last_api_response` | APIResponse? | T | s06 → s07, s08, s09 |
| `created_at` / `updated_at` | datetime | S | `add_event`가 갱신 |
| `metadata` | Dict | S | 레거시 신호, stage-local 저장(`Stage.local_state`, `src/xgen_agent_runtime/core/stage.py:405-421`), 메모리 주입(`memory_context`, `memory_pinned`), 워터마크 |
| `shared` | Dict | S(일부 키만 턴마다 pop) | stage 간 통신. 키 관례는 `SharedKeys`(`src/xgen_agent_runtime/core/shared_keys.py:27-149`) |
| `events` | List[dict] | T | `add_event` 로그 |
| `_event_listener`, `_bus_emitter`, `_run_id`, `_pending_run_events`, `_run_count`, `_turn_in_flight` | | P/R | 이벤트 브리지, 동시 실행 guard (`src/xgen_agent_runtime/core/state.py:250-289`) |
| `_run_status`, `_termination_reason`, `_resumable`, `_checkpoint_id`, `_is_continuation_slice`, `_accounted_turn_cost_usd` | | P/T | slice 결과. 공개는 read-only property(`:528-546`) |
| `_turn_event_counts` | Dict[str,int] | P/T | 하네스 장치 작동 횟수(`src/xgen_agent_runtime/host/harness_components.py:36-51`) |
| `_context_compactor`, `_context_memory_provider` | | P/R | `_init_state`의 s02 자동 배선 |
| `_client_generation` | int? | P | client 재해석 판정 |
| `llm_client` / `credentials` / `session_runtime` | Any | S | 런타임 객체. `session_runtime`은 형태 자유(free-shape) 플러그인 컨테이너 |

주요 메서드:
- `begin_turn()`(`src/xgen_agent_runtime/core/state.py:335-438`): T 필드를 리셋하고, sticky 리스트를 2000개로 자르고, `shared`의 턴 단위 키 15개를 pop한다. 이 키 목록에는 s10·s16 모듈 소유 키가 하드코딩되어 있다.
- `begin_continuation_slice()`(`:440-474`): 비용과 토큰은 유지하고 루프 관련 값만 리셋한다.
- `add_event()`(`:476-514`), `mark_completed/suspended/blocked/failed()`(`:548-586`), `add_message()`, `add_tool_result()`, `accumulate_cost()`, `is_over_budget`, `is_over_iterations`, `has_fresh_tool_results`(`:516-526`).

### 3.2 `ModelConfig` / `ModelOverrides` / `PipelineConfig` (`src/xgen_agent_runtime/core/config.py`)

| 클래스 | 필드 (기본값) |
|---|---|
| `ModelConfig` (`:13-42`) | `model="claude-sonnet-4-6"`, `max_tokens=8192`, `temperature=0.0`, `top_p=None`, `top_k=None`, `stop_sequences=None`, `thinking_enabled=False`, `thinking_budget_tokens=10000`, `thinking_type="enabled"`, `thinking_display=None`, `thinking_level=None` |
| `ModelOverrides` (frozen, `:89-128`) | `model`, `max_tokens`, `temperature`, `top_p`, `thinking_enabled`, `thinking_budget_tokens`, `thinking_level` (모두 Optional) |
| `PipelineConfig` (`:161-188`) | `name="default"`, `model: ModelConfig`, `api_key=""`, `base_url=None`, `max_iterations=50`, `cost_budget_usd=None`, `context_window_budget=200_000`, `stream=True`, `single_turn=False`, `artifacts: Dict[str,str]`(빌더의 s06 artifact 선택에만 쓰임, `src/xgen_agent_runtime/core/builder.py:266`), `metadata: Dict` |

- `PipelineConfig.apply_to_state`(`src/xgen_agent_runtime/core/config.py:241-265`)는 모델, 샘플링, thinking, stream, single_turn, limit 값을 **매 실행 state에 덮어쓴다**. state를 직접 고쳐도 다음 실행에 사라지는 것은 이 덮어쓰기 때문이다.
- **버그**: `ModelOverrides.thinking_level`은 필드로 선언되어 있지만 `non_none_fields()`의 키 목록에 없다(`src/xgen_agent_runtime/core/config.py:139-146`). 그래서 실행별 override로 넘겨도 적용되지 않는다(부록 A-3).

### 3.3 `Stage` 기반 API (`src/xgen_agent_runtime/core/stage.py:85-421`)
- 추상 멤버: `name`, `order`(1-21), `execute(input, state) -> T_Out`.
- 선택 멤버:
  - `category`(ingress / pre_flight / execution / decision / egress. 실제로는 review / gate / finalize도 쓰인다)
  - `should_bypass(state)=False`
  - `on_enter`, `on_exit`, `on_error(→복구값 | None)`(`:113-145`)
- 설정 표면: `get_config_schema()`, `get_config()`, `update_config()`. 기본 구현은 no-op이다(`:246-260`).
- 전략 표면: `get_strategy_slots()`, `get_strategy_chains()`, `set_strategy()`, `add_to_chain`/`remove_from_chain`/`reorder_chain`/`clear_chain`, `list_strategies()`, `describe()`(`:147-244`).
- stage별 override:
  - `tool_binding`(lazy `StageToolBinding`, `:264-278`)
  - `model_override`(`:280-287`)
  - `artifact_name`, `stage_module`(`:289-309`)
- 모델 해석:
  - `resolve_model_config(state)`: override가 있으면 그것을 그대로 쓰고, 없으면 state 필드로 `ModelConfig`를 새로 만든다(`:360-394`).
  - `resolve_local_client(state)`: `config["provider_override"]`가 있으면 stage 전용 client를 만든다(`:311-358`).
- `local_state(state)`는 `state.metadata[self.name]`을 개인 저장소로 쓴다(`:405-421`).

### 3.4 Builder / Presets / Manifest factory
- `PipelineBuilder(name, *, api_key, model)`(`src/xgen_agent_runtime/core/builder.py:33-41`). 메서드는 `with_artifact`, `with_model(model, **kw)`(ModelConfig 필드는 모델 쪽으로, 나머지는 PipelineConfig로 보냄, `:53-68`), `with_system/tools/guard/cache/context/memory/loop/think/evaluate/emit/tool_review/hitl/summarize/persist`(`:70-133`).
- `build()`(`:135-261`)의 동작:
  - Input, API, **Token**, Parse, Yield는 항상 등록한다(`:149-159`).
  - 나머지는 `with_*`를 불렀을 때만 등록한다.
  - API artifact는 모델명 접두어로 추론한다. `gpt-`/`o1`/`o3`/`o4` → openai, `gemini-` → google(`:282-290`). 하드코딩된 정책이다.
- `PipelinePresets`(`src/xgen_agent_runtime/core/presets.py:208-331`):
  - `agent`·`geny_vtuber`는 Context, System, Guard, Cache(aggressive), Think, ToolReview, HITL, Evaluate, Loop, Memory, Summarize, Persist를 켠다.
  - `PresetRegistry`는 entry-point `xgen_agent_runtime.presets` 플러그인을 발견한다(`:22`, `:117-183`).
- `build_manifest()`(`src/xgen_agent_runtime/core/manifest_factory.py:546-660`)는 worker/vtuber stage 목록(`:265-408`, `:411-529`)과 scaffold(11/15/19/20, 기본 inactive, `:100-139`)를 합친다. preset별 override는 `:146-220`이다. 알려진 한계로 `chain_order`는 기존 chain을 **재정렬만** 할 수 있다. 그래서 worker preset의 guard chain 선언(`:298-300`)은 기본 chain이 비어 있으면 적용되지 않는다(`:47-55`).

### 3.5 `environment.py` (manifest 포맷, `src/xgen_agent_runtime/core/environment.py`)
- `MANIFEST_VERSION = "3.0"`이고 1.0/2.0은 자동 마이그레이션한다(`:124-131`, `:682-739`).
- `EnvironmentManifest` 필드(`:660-667`): `version`, `metadata`, `model`(dict, 모델 선택의 단일 위치), `pipeline`(dict), `stages`(List[dict]), `tools: ToolsSnapshot`, `host_selections`, `memory`.
- `StageManifestEntry`(`:234-279`): `order`, `name`, `active=True`, `artifact="default"`, `strategies: slot→impl`, `strategy_configs: slot→dict`, `config`, `tool_binding`, `model_override`, `chain_order`.
- `HostSelections`(`:135-230`): hooks/skills/permissions 선택. 라이브러리는 이것을 적용하지 않는다고 docstring이 직접 인정한다(`:154-170`).
- `validate_manifest(manifest) -> List[ManifestIssue]`(`:1040-1131`): 저장 시점에 거는 계약 검사다. unknown stage/strategy, no-op `configure`에 대한 strategy_configs, 필수 stage(`s01_input`, `s06_api`, `s09_parse`, `s21_yield`, `src/xgen_agent_runtime/core/introspection.py:141`), provider 위치 등을 본다.
- 그 밖에 `EnvironmentManager`(파일 저장소, `src/xgen_agent_runtime/core/environment.py:1698`), `EnvironmentResolver`(변수 치환, `:1636`), `EnvironmentSanitizer`(`:1850`)가 있다.

### 3.6 `environment_control.py` — 자기 수정 환경 (RSI 관점에서 핵심)
- 모듈 docstring: "the live controller a session uses to edit its OWN operating environment (system prompt, active tools, active skills) at runtime"(`src/xgen_agent_runtime/core/environment_control.py:1-3`). 변경은 **다음 턴부터** 반영된다(`:7-12`). 범위는 "AVAILABLE environment"로 제한된다(`:20-23`).
- 편집 가능 표면:

  | 대상 | 메서드 | 근거 |
  |---|---|---|
  | prompt | `set_prompt`, `append_prompt`. `MutablePromptBuilder`가 설치되어 있어야 함 | `:291-317` |
  | 도구 | `enable_tool` / `disable_tool`. env 도구 자신은 끌 수 없음 | `:320-349` |
  | 도구 제작 | `forge_tool`. sandbox 스크립트를 `SandboxExecTool`로 즉시 등록 | `:351-412` |
  | 도구 팩 | `save_pack` | `:414` |
  | skill | `enable_skill` / `disable_skill` / `create_skill` / `edit_skill` | `:484-610` |
  | 도구 설정 | `set_setting`. secret은 마스킹 | `:661-703` |
  | 모델 설정 | `set_config`. 허용: temperature/max_tokens/top_p/top_k/thinking_*, max_iterations/cost_budget_usd/context_window_budget/single_turn. 잠금: model/provider/api_key/base_url/name/credentials | `:52-68`, `:730-763` |

- 모든 변경은 `EnvChangeEntry` change-log에 남는다(`:103-120`). `overlay()` → `save()`로 host 콜백에 영속화한다(`:778-809`).
- 주의: `set_config`는 pipeline이 공유하는 `PipelineConfig` 객체를 직접 고친다(`:744-752`). 그래서 같은 pipeline을 쓰는 이후 모든 실행에 영향을 준다.

### 3.7 `mutation.py` / `snapshot.py` / `introspection.py`
- `PipelineMutator(pipeline)`(`src/xgen_agent_runtime/core/mutation.py:128-152`)가 하는 일:
  - 메서드: `swap_strategy`, `update_stage_config`, `update_model_config`, `update_pipeline_config`, `set_stage_active`, `update_strategy_config`, `replace_stage`, chain 조작, `register_hook`, `bind_tool_to_stage`, `set_stage_model`, `batch()`, `snapshot()`, `restore()`(`:156-839`)
  - 변경 종류는 `MutationKind` 19가지로 기록한다(`:39-59`)
  - 실행 중에는 `MutationLocked`(`:917-933`). `restore()`만 이 lock에서 면제다(`:709-713`)
  - `set_stage_active(False)`는 stage를 **pipeline에서 제거**하고 mutator 인스턴스의 `_removed_stages`에 보관한다(`:253-286`). 다른 mutator 인스턴스는 그 stage를 되살릴 수 없다
- `PipelineSnapshot` / `StageSnapshot`(`src/xgen_agent_runtime/core/snapshot.py:20-125`): 구성 표면만 직렬화한다(stages의 slot·config·artifact·binding·override·chain, pipeline_config, model_config).
- introspection(`src/xgen_agent_runtime/core/introspection.py`):
  - live pipeline 없이 `introspect_stage(stage, artifact)`, `introspect_all()`(`:309`, `:361`)로 UI용 slot·schema 정보를 얻는다.
  - stage별 capability 맵(`:101-106`)과 필수 stage 집합(`:141`)이 있다.

### 3.8 `result.py` / `run_status.py` / `continuation.py`
- `PipelineResult`(`src/xgen_agent_runtime/core/result.py:12-52`): `text`, `output`, `success`, `error`, `iterations`, `token_usage`, `turn_token_usage`, `total_cost_usd`, `cache_metrics`, `thinking_history`, `events`, `session_id`, `pipeline_id`, `model`, `metadata`, `state`(handle). read-only property는 `status`, `termination_reason`, `resumable`, `checkpoint_id`다(`:54-76`). `success`는 `run_status == completed`일 때만 True다(`:79-87`).
- `RunStatus`: running / completed / suspended / blocked / failed / cancelled(`src/xgen_agent_runtime/core/run_status.py:14-22`).
- `TerminationReason`: 11개(`:25-38`).
- `CONTINUE_RUN = ContinuationInput()`(`src/xgen_agent_runtime/core/continuation.py:8-13`). Stage 1이 이 입력을 받으면 user 메시지를 붙이지 않고 통과시킨다(`src/xgen_agent_runtime/stages/s01_input/artifact/default/stage.py:87-101`).

### 3.9 `compaction.py` / `context_prune.py`
- `run_compaction(state, compactor, *, trigger, provider, target_tokens)`(`src/xgen_agent_runtime/core/compaction.py:122-234`)의 흐름:
  1. 결정론적 prune
  2. `compactor.compact(state)`
  3. 워터마크 재조정
  4. `context.compacted` / `context.compaction_target_missed` 이벤트
  5. provider에 스냅샷 기록

  예외를 던지지 않는다.
- `RECORDED_INDEX_KEYS`(`:34-43`)는 압축으로 `messages`가 줄어들 때 같이 변환해야 하는 인덱스 키 4개를 문자열로 하드코딩한다. 그중 하나는 host 키(`geny_bridge.conversation_archived_idx`)다. `reconcile_recorded_index`(`:46-96`)가 메시지 객체 identity 기반으로 suffix를 찾아 인덱스를 다시 계산한다.
- `prune_messages()`(`src/xgen_agent_runtime/core/context_prune.py:178`): LLM 없는 정리다. 중복 도구 출력 제거, 오래된 이미지 제거, 거대 출력 절단을 한다. 메시지 수와 순서, `tool_use_id`는 보존한다(`:15-24`). 비용 트리거 기본값은 `DEFAULT_PRUNE_OVER_TOKENS = 30_000`(`:68`)이다.

---

## 4. 21개 stage 표

### 4.1 stage별 요약
경로 약어: `sNN/…` = `src/xgen_agent_runtime/stages/sNN_*/artifact/default/stage.py` (별도 표기가 없을 때). "빈도"는 §2.5 기준으로 Phase A(1회) / B(루프 매 반복) / C(1회)다.

| # | name (class) | 역할 | slot·chain (impl) | 읽는 state | 쓰는 state | 빈도 · bypass |
|---|---|---|---|---|---|---|
| 1 | `input` (`InputStage`, s01:27) | 입력 검증·정규화, 끊긴 tool_use 이력 복구, user 메시지 추가. `CONTINUE_RUN`이면 추가 없이 통과 (:86-129) | validator{default, passthrough, strict, schema}, normalizer{default, multimodal} (:40-61) | messages, session_id | messages, events | A · 없음 (필수) |
| 2 | `context` (`ContextStage`, s02:70) | 컨텍스트 전략(기본 no-op, `src/xgen_agent_runtime/stages/s02_context/artifact/default/strategies.py:23-25`), STM 창 preload(i=0, :353-388), 메모리 검색(i=0만, :415-425), 비용 트리거 prune(:490-510), 80% 압축 동기/백그라운드(:512-547), s16이 요청한 압축(:517-530) | strategy{simple_load, hybrid, progressive_disclosure}, compactor{truncate, summary, llm_summary, sliding_window}, retriever{null, static} (:99-130) | messages, iteration, `_is_continuation_slice`, context_window_budget, `shared[context.compaction_requested]`, metadata 워터마크 | messages, memory_refs, `metadata[memory_context, memory_pinned, memory.*]`, `shared[_prompt_tokens_memo]` | B · `stateless` (:294-295) |
| 3 | `system` (`SystemStage`, s03:24) | 시스템 프롬프트 빌드(stable/volatile 분리, :193-261), deferred 도구 카탈로그 추가(:288-316), registry version이 바뀌면 `state.tools` 재구성(:335-358) | builder{static, mutable, composable, dynamic_persona} (:43-59) | metadata(블록이 메모리 렌더), messages(도구 복원), registry | system, tools, tools_version, `shared[turn_context_text, system_parts]` | B · 없음 |
| 4 | `guard` (`GuardStage`, s04:23) | 사전 검사 chain. `compact` 신호가 오면 압축 후 1회 재검사(:187-205, :252-276). 실패 시 `GuardRejectError` | chain guards{token_budget, cost_budget, iteration, permission}, **기본 빈 chain** (:37-49) | 프롬프트 토큰 추정, total_cost_usd, iteration, pending_tool_calls | (압축 시) messages, metadata | B · 없음 |
| 5 | `cache` (`CacheStage`, s05:20) | Anthropic cache_control 마커(도구·system·history prefix), cache_key 계산(:120-135) | strategy{no_cache, system_cache, aggressive_cache} (:33-44) | model, system, tools, messages | **tools·system·messages를 제자리 변경** (`src/xgen_agent_runtime/stages/s05_cache/artifact/default/strategies.py:208-246`), `shared[cache_key]` | B · `NoCacheStrategy` (:87-88) |
| 6 | `api` (`APIStage`, s06:165) | 모델 라우팅(:369-398), client 해석(:420-459), retry 감싼 호출, 스트림 청크 → 이벤트(:955-1013), assistant 메시지 추가(:561-570). turn_context를 user 메시지 사본에 주입(:606-641) | retry{exponential_backoff, no_retry, rate_limit_aware}, router{passthrough, adaptive}, tool_loop{pipeline, internal} (:222-259). provider는 `config["provider"]` | system, messages, tools, tool_choice, model 필드, stream, `shared[turn_context_text, executor.retired_tool_calls]`, llm_client, tool_dispatcher | last_api_response, messages(+assistant, internal loop의 교환 내역), llm_client(fallback), `shared[_api_call_t0, _api_ttft_emitted]` | B · 없음 (필수) |
| 7 | `token` (`TokenStage`, s07:23) | 사용량 누적, 비용 계산(:81-112) | tracker{default, detailed}, calculator{anthropic_pricing, custom_pricing, unified_pricing} (:36-56) | last_api_response, model | token_usage, turn_token_usage, total_cost_usd, cache_metrics | B · 없음 (응답이 없으면 그냥 통과) |
| 8 | `think` (`ThinkStage`, s08:27) | thinking 블록 분리·처리. **실행되면 `ThinkingResult`를 반환해 다음 stage의 입력 타입이 바뀐다** (:119-161). budget planner는 자동 호출되지 않는다(:38-42) | processor{passthrough, extract_and_store, filter}, budget_planner{static, adaptive} (:50-73) | thinking_enabled, last_api_response | (processor에 따라) thinking_history | B · thinking off이거나 thinking 블록 없음 (:108-117) |
| 9 | `parse` (`ParseStage`, s09:28) | 응답 파싱, 완료 마커 탐지, tool call 큐잉, final_text 설정(:86-146) | parser{default, structured_output}, signal_detector{regex, structured, hybrid} (:41-61) | input 또는 last_api_response | completion_signal/detail(**매번 리셋 후 설정**), pending_tool_calls(리셋 후 설정), thinking_history, final_text | B · 없음 (필수) |
| 10 | `tool` (`ToolStage`, s10:31) | tool_binding 검사, dispatch ctx 구성(:167-276), 거부·반복 guard, executor 실행(:278-399), tool_result를 user 메시지로 추가(:423) | executor{sequential, parallel, partition, streaming}, router{registry} (:60-87). 권한 사다리(matrix → posture → ASK→HITL → hooks)는 router가 담당(`src/xgen_agent_runtime/stages/s10_tool/artifact/default/routers.py:625-650`) | pending_tool_calls, shared(guard 키들), session_id | messages, tool_results, `shared[executor.tool_calls_total, tool.*]`, pending_tool_calls=[], **loop_decision="continue"** (:432) | B · pending 없음 (:164-165) |
| 11 | `tool_review` (`ToolReviewStage`, `src/xgen_agent_runtime/stages/s11_tool_review/artifact/default/stage.py:53`) | reviewer chain이 플래그를 누적한다. 장애는 reviewer 단위로 격리(:100-135) | chain reviewers{schema, sensitive, destructive, network, size} (:65-76) | pending_tool_calls(이미 비어 있음), tool_results | `shared["tool_review_flags"]` (**`SharedKeys.TOOL_REVIEW_FLAGS = "executor.tool_review_flags"`와 키가 다름**, `src/xgen_agent_runtime/core/shared_keys.py:63`) | B · 둘 다 비어 있음 (:96-98) |
| 12 | `agent` (은퇴) | 4.71.0에서 sub-agent 오케스트레이션과 함께 제거 | — | — | — | 빈 칸 → `stage.bypass` |
| 13 | `task_registry` (은퇴) | 백그라운드 task registry는 `runtime/tasks`로 이동(`docs/architecture.md:66-69`) | — | — | — | 빈 칸 → `stage.bypass` |
| 14 | `evaluate` (`EvaluateStage`, s14:19) | 평가 전략 결과를 `loop_decision`으로 매핑(:78-110). 기본 `SignalBasedEvaluation`(`src/xgen_agent_runtime/stages/s14_evaluate/artifact/default/strategies.py:16-67`) | strategy{signal_based, criteria_based, binary_classify, evaluation_chain} (`EVALUATOR_REGISTRY`), scorer{no_scorer, weighted} (:32-53). 별도 artifact `adaptive` | completion_signal/detail, tool_results, pending_tool_calls | evaluation_score/feedback, **loop_decision** | B · 없음 |
| 15 | `hitl` (`HITLStage`, `src/xgen_agent_runtime/stages/s15_hitl/artifact/default/stage.py`) | `shared["hitl_request"]`가 있으면 requester에 결정을 요청하고 timeout 정책을 적용. REJECT → `complete`, CANCEL → `escalate` (:133-164) | requester{null, callback, pipeline_resume}, timeout{indefinite, auto_approve, auto_reject} | `shared[hitl_request]` | `shared[hitl_request=None, hitl_last_decision, hitl_history]`, loop_decision, completion_signal/detail | B · 요청 없음 (:129-131) |
| 16 | `loop` (`LoopStage`, s16:22) | 최종 루프 결정. 상류 terminal 값 존중 → early_stop → controller → completion reviewer(note 주입) → repeat_stop → turn_budget (:155-198) | controller{standard, single_turn, budget_aware, multi_dim_budget} (:38-54). slot 밖의 생성자·setter 장치: `completion_reviewers`, `repeat_stop`, `turn_input_budget` (:57-74) | loop_decision, completion_signal, tool_results, pending_tool_calls, iteration, max_iterations, turn_token_usage, shared | loop_decision, messages(note), completion_signal/detail, `shared[context.compaction_requested, loop.*]`, mark_suspended, **tool_results=[]** | B · 없음 |
| 17 | `emit` (`EmitStage`, s17:21) | emitter chain으로 외부 채널에 출력(:86-115) | chain emitters{text, callback, vtuber, tts}, 기본 빈 chain (:28-40) | 전체 state(emitter별) | events | C · 빈 chain (:83-84) |
| 18 | `memory` (`MemoryStage`, s18:70) | 전략 갱신, provider에 STM turn 기록(워터마크 2개), terminal이면 execution 기록·reflect·promote, 파일 persistence(:200-277) | strategy{append_only, no_memory, reflective, structured_reflective}, persistence{null, in_memory, file} (:97-119) | messages, loop_decision, metadata 워터마크, session_id | `metadata[memory.last_recorded_idx, memory.provider_strategy_recorded_idx]`, events | C · stateless 또는 NoMemoryStrategy (:197-198) |
| 19 | `summarize` (`SummarizeStage`, `src/xgen_agent_runtime/stages/s19_summarize/artifact/default/stage.py`) | 턴 요약과 중요도 산정. terminal이면 세션 요약 md를 `provider.stm().write_summary`로 기록(:120-167) | summarizer{no_summary, rule_based}, importance{fixed, heuristic} (:75-86) | 전체 state, loop_decision, `session_runtime.memory_provider`, `shared[tool_review_flags]` | `shared[turn_summary, summary_history]` (**턴마다 누적되고 상한 없음**) | C · NoSummarizer (:115-118) |
| 20 | `persist` (`PersistStage`, `src/xgen_agent_runtime/stages/s20_persist/artifact/default/stage.py`) | frequency 정책에 따라 checkpoint 기록. resumable slice면 무조건 기록(:168-219) | persister{no_persist, file}, frequency{every_turn, every_n_turns, on_significant} (:118-137) | resumable, iteration, messages/shared/metadata 등 payload | `shared[last_checkpoint, checkpoint_history]`, `state._checkpoint_id` | C · NoPersister (:163-166) |
| 21 | `yield` (`YieldStage`, s21:21) | 최종 출력 포맷(:62-72) | formatter{default, structured, streaming, multi_format} (:29-41) | final_text, iteration, total_cost_usd | final_output | C · 없음 (필수) |

### 4.2 stage 사이의 `current` 데이터 흐름 (숨은 두 번째 채널)
- `current` 값의 변화:
  - s01이 `NormalizedInput`을 반환한다.
  - s02–s05는 받은 값을 그대로 넘긴다.
  - s06이 `APIResponse`를 반환하고, s07은 그대로 넘긴다.
  - s08은 실행되면 `ThinkingResult`, bypass되면 `APIResponse`를 넘긴다.
  - s09가 `ParsedResponse`를 반환하고, s10/s11/s14/s15/s16은 그대로 넘긴다.
  - 그래서 **두 번째 반복의 s02는 이전 반복의 `ParsedResponse`를 입력으로 받는다**.
  - s17–s20은 그대로 넘기고, s21은 `final_output`이나 `final_text`를 반환하지만 그 값은 버려진다(§2.5).
- s09는 `input`이 `APIResponse`가 아니면 `state.last_api_response`로 대신한다(`src/xgen_agent_runtime/stages/s09_parse/artifact/default/stage.py:88-93`). 결국 `current` 채널은 거의 쓰이지 않고 **실제 계약은 state다**. 다만 이것이 타입으로 보장되지 않는다.

### 4.3 `loop_decision`을 쓰는 주체 (한 반복 안에서 쓰는 순서)

| 순서 | 주체 | 값 | 근거 |
|---|---|---|---|
| 1 | s10 Tool (도구가 실행되면) | `continue` | `src/xgen_agent_runtime/stages/s10_tool/artifact/default/stage.py:432` |
| 2 | s14 Evaluate (등록되어 있으면 매 반복) | complete / continue / escalate / error | `src/xgen_agent_runtime/stages/s14_evaluate/artifact/default/stage.py:90-97` |
| 3 | s15 HITL (요청이 있을 때) | complete(REJECT) / escalate(CANCEL) | `src/xgen_agent_runtime/stages/s15_hitl/artifact/default/stage.py:155-162` |
| 4 | s16 Loop | 상류 terminal 존중 → controller → 보조 장치 | `src/xgen_agent_runtime/stages/s16_loop/artifact/default/stage.py:156-184` |
| 5 | Pipeline | s16 미등록·`single_turn`이면 complete. 하드 리밋은 suspend/escalate | `src/xgen_agent_runtime/core/pipeline.py:3061-3122` |

- 반복 상한 검사는 **최소 3곳**에 있다. s04 `IterationGuard`(실패 시 reject라서 failed가 된다, `src/xgen_agent_runtime/stages/s04_guard/artifact/default/guards.py:184-193`), s16 controller의 `max_turns`(suspend, `src/xgen_agent_runtime/stages/s16_loop/artifact/default/controllers.py:124-131`), pipeline 하드 리밋(suspend)이다. 같은 반복 상한이 걸려도 어디서 걸리느냐에 따라 status가 다르게 나올 수 있다.
- 비용 상한도 s04 `CostBudgetGuard`, s16 budget controller, pipeline 하드 리밋 세 곳에 있다.

---

## 5. 이벤트 카탈로그

### 5.1 envelope와 채널
- `PipelineEvent`(`src/xgen_agent_runtime/events/types.py:11-40`): `type: str`, `stage: str=""`, `iteration: int=0`, `timestamp: ISO str`, `data: dict`, `session_id`, `run_id`(실행마다 uuid), `seq`(pipeline 단위 단조 증가).
- `EventBus`(`src/xgen_agent_runtime/events/bus.py:21-140`): exact / `"*"` / prefix(`stage.*`) 매칭을 하고 handler 중복을 제거한다. `emit`은 async handler를 inline으로 await한다. `emit_sync`는 state 브리지용이고, async handler는 task로 띄우므로 순서를 보장하지 않는다. handler 예외는 로그만 남긴다.
- 카탈로그: `EVENT_CATALOG_VERSION = 16`, 이벤트 140개(`docs/events.md:6-7`, `src/xgen_agent_runtime/events/catalog.py:67-70`). 값이 곧 wire 문자열이고 append-only다. 필드 설명 `PAYLOADS`(`:316`)는 import 시점에 완전성을 검사한다(`:1027-1041`). 은퇴한 이벤트는 agent/subagent/task/task_registry 15개다(`:988-1006`).

### 5.2 이벤트 계열과 주요 payload
(전체 필드는 `docs/events.md`. 아래는 재설계에 필요한 골격만.)

| 계열 | 이벤트 | 대표 payload |
|---|---|---|
| Pipeline | `pipeline.start` / `pipeline.complete` / `pipeline.error` | start `{input}`. complete `{iterations, status, termination_reason, resumable, checkpoint_id, result?, total_cost_usd?}` (`result`·`total_cost_usd`는 run_stream에만). error `{error, code, exception_type, total_cost_usd?}` |
| Stage | `stage.enter` / `exit` / `bypass` / `error` | payload 없음(envelope만). error `{error, code, exception_type}` |
| 설정 | `config.override_applied`, `runtime.llm_client_override` | `{field, value, source}`, `{manifest_provider, client_provider}` |
| Loop | `loop.{continue, complete, error, escalate, suspend}` (f-string), `loop.suspended`, `loop.blocked`, `loop.completion_review`, `loop.repeat_stop`, `loop.turn_budget`, `loop.budget_exceeded`, `loop.force_complete` | `{iteration, signal, pending_tools, has_tool_results, upstream_decision}` (`src/xgen_agent_runtime/stages/s16_loop/artifact/default/stage.py:186-195`) |
| s01 | `input.normalized`, `input.continuation`, `input.tool_calls_repaired` | `{text_length}` 등 |
| s02 | `context.built`, `context.compacted`, `context.pruned`, `context.compaction_{failed, record_failed, scheduled, requested, target_missed}`, `context.retrieval_timeout`, `context.short_term_window`, `memory.compaction.*` | compacted `{strategy, trigger, messages_before/after, saved_tokens_estimate, tokens_after_estimate, target_tokens, target_met}` |
| s03–s05 | `system.built`, `guard.check/warn/compacting`, `cache.applied` | system `{prompt_type, prompt_length, tools_count, volatile_placement, turn_context_chars}` |
| s06 | `api.request`, `api.response`, `api.ttft`, `api.retry`, `api.stream_restart`, `api.error`, `api.router.error`, `api.model_routed`, `api.internal_loop_capped`, `api.timeout_unsupported` | request `{model, provider, message_count, has_tools, has_thinking, stream}`. response `{stop_reason, text_length, tool_calls, input_tokens, output_tokens, cache_*}`. error `{code, category, provider, message, cli_version?}` |
| 스트리밍 청크 | `text.delta {text, source, granularity}`, `thinking.delta {text}`, `api.tool_use {id, name, input, source}`, `api.cli_tool_call`, `api.input_json_delta {delta}`, `api.content_block_stop`, `api.tool_result {tool_use_id, content, is_error, source}` | `src/xgen_agent_runtime/stages/s06_api/artifact/default/stage.py:972-1013` |
| s07–s09 | `token.tracked`, `think.processed`, `think.budget_applied`, `parse.complete` | token `{input_tokens, output_tokens, cache_write, cache_read, cost_usd, total_cost_usd}` |
| s10 | `tool.execute_start/complete`, `tool.call_start {tool_use_id, name, input}`, `tool.call_complete {tool_use_id, name, is_error, duration_ms}`, `tool.repeat_failure/repeat_blocked/same_result`, `tool.user_denied`, `tool.surface_restored`, `tool.gate_reachability_repaired`, `tool.not_in_sandbox` | call 쌍은 executor가 `on_event` 콜백으로 발행 |
| s11, s14, s15 | `tool_review.*`, `evaluate.start/complete`, `hitl.request/decision/no_decision/timeout/requester_error` | evaluate.complete `{passed, score, decision, loop_decision, feedback}` |
| s17–s21 | `emit.*`, `memory.*`(16종, MEMORY_SPEC 포함), `summary.*`, `checkpoint.written/skipped/persister_error`, `yield.complete/summary` | yield `{text_length, iterations, total_cost_usd}` |
| llm_client | `llm_client.feature_unsupported` 외 5종 | client의 `event_sink`로만 전달(state를 거치지 않음) |

### 5.3 한 턴의 전형적 이벤트 흐름
1. `pipeline.start`
2. `stage.enter(input)` → `input.normalized` → `stage.exit`
3. [반복 i]
   1. context: `context.*`
   2. system: `system.built`
   3. guard·cache: `guard.check`, `cache.applied` (또는 bypass)
   4. api: `api.request` → `api.ttft` → `text.delta`* / `api.tool_use`* → `api.response`
   5. token: `token.tracked`
   6. think: bypass
   7. parse: `parse.complete`
   8. tool: `tool.execute_start` → `tool.call_start` / `tool.call_complete`* → `tool.execute_complete`
   9. evaluate: `evaluate.*`
   10. loop: `loop.continue` 또는 `loop.complete`
4. finalize stages
5. `pipeline.complete`

### 5.4 host가 실제로 의존하는 이벤트 (레포 안 운영 bridge)
- `stream_turn`이 분기하는 이벤트: `text.delta`, `pipeline.complete`, `pipeline.error`, `tool.call_start`, `tool.call_complete`, `api.cli_tool_call`, `api.tool_result`(`src/xgen_agent_runtime/host/runner.py:1434-1510`).
- 실행 통계에 쓰는 이벤트: `tool.execute_complete`, `tool.repeat_blocked`(`:1149-1152`).
- usage는 이벤트가 아니라 state에서 직접 계산한다(`turn_usage`, `:1038-1124`). 하네스 장치 요약(`harness_components.COMPONENTS`)은 `_turn_event_counts`로 이벤트 이름 10여 개를 센다(`src/xgen_agent_runtime/host/harness_components.py:19-33`).

### 5.5 불일치
- 정의만 있고 발행하는 곳이 없는데 은퇴 목록에도 없는 이벤트가 둘 있다. `loop.force_complete`(`src/xgen_agent_runtime/events/catalog.py:95`), `api.timeout_unsupported`(`:169`). src를 grep해서 확인했다.
- `pipeline.complete`의 payload가 `run`과 `run_stream`에서 다르다(§2.3).

---

## 6. "harness" 대 "policy": 행동은 어디서 결정되는가

RRSI는 하네스를 "the system and task prompts, the control flow that decides when the agent plans, acts, reflects or stops, the tool interfaces and their descriptions, the memory and skill files …, and the context management that decides what the policy sees at each step"으로 정의한다(RRSI 논문 §2, [arXiv:2609.24972](https://arxiv.org/abs/2609.24972)). 이 정의에 맞춰, 현 런타임에서 각 컴포넌트의 행동이 **코드(control flow 하드코딩)**, **프롬프트 텍스트**, **설정(manifest·config·slot 선택)** 중 어디서 정해지는지 분류했다.

| RRSI 컴포넌트 | 현 런타임 위치 | 결정 주체 | 편집 표면 / 비고 |
|---|---|---|---|
| **prompt** | s03 builder 4종(`src/xgen_agent_runtime/stages/s03_system/artifact/default/stage.py:43-59`), Composable 블록(Persona/Rules/DateTime/PinnedFacts/RetrievedMemory/MemoryContext/ToolInstructions/TurnNotes/Custom, `src/xgen_agent_runtime/stages/s03_system/artifact/default/builders.py:145-441`), `template_vars`(`src/xgen_agent_runtime/stages/s03_system/artifact/default/stage.py:159-191`) | 설정(문자열과 블록 조합) + 런타임 객체(attach) | `env` 도구 `set_prompt`/`append_prompt`(`src/xgen_agent_runtime/core/environment_control.py:299-317`). 이 밖에 **코드에 박힌 프롬프트**가 흩어져 있다: deferred 카탈로그 문구(`src/xgen_agent_runtime/tools/catalog.py`), `<session-context>` 래퍼(`src/xgen_agent_runtime/stages/s06_api/artifact/default/stage.py:635`), repeat_stop 종료 note(`src/xgen_agent_runtime/stages/s16_loop/repeat_stop.py:46-52`), turn_budget note, DeliverableReviewer note(`src/xgen_agent_runtime/stages/s16_loop/completion_review.py`), second_machine 안내(`src/xgen_agent_runtime/stages/s10_tool/second_machine.py`) |
| **control_flow** | `_run_phases`의 21칸 순서와 루프(`src/xgen_agent_runtime/core/pipeline.py:3019-3142`), 각 stage의 `should_bypass`, `loop_decision` 다중 writer(§4.3), s16 controller와 보조 장치, s14 평가 매핑 | **코드**(순서, 루프 경계, status 매핑) + slot 선택(controller/evaluator 종류) + **프롬프트 프로토콜**(`[COMPLETE]` 등의 마커, `src/xgen_agent_runtime/stages/s09_parse/artifact/default/signals.py:15-22`, 지시는 `src/xgen_agent_runtime/memory/presets.py:392`) | 루프 구조 자체는 바꿀 수 없다(`LOOP_START`/`LOOP_END` 클래스 상수). 바꿀 수 있는 것은 slot 교체, `max_turns`/`early_stop_on`, mutator 정도다. 보조 장치(repeat_stop/turn_budget/completion_reviewer)는 manifest로 도달할 수 없고 host 코드가 setter로 붙인다(`src/xgen_agent_runtime/host/runner.py:711-748`) |
| **config** | `PipelineConfig`/`ModelConfig`(§3.2), stage `config` dict와 `update_config`, `strategy_configs` → `Strategy.configure`, `ModelOverrides`, `env set_config` | 설정 | 5개 이상 채널(§1.3). `apply_to_state`가 매 실행 덮어쓴다. 일부 knob은 생성자로만 지정할 수 있다(감사 §1-1, 1-5) |
| **output_plumbing** | s06 스트림 청크 → 이벤트(`src/xgen_agent_runtime/stages/s06_api/artifact/default/stage.py:955-1013`), s09 parser(structured_output 포함), s17 emitter chain, s21 formatter, `pipeline.complete`/`PipelineResult`, host bridge(`src/xgen_agent_runtime/host/runner.py:1340`) | 코드 + slot 선택 | 이벤트 이름과 payload는 공표된 계약(EventTypes)이다. host bridge가 이벤트를 xgen 청크(str, `agent_event` dict, `usage`)로 번역한다 |
| **context_mgmt** | s02(검색 i=0, STM 창, prune, 압축 80%/90%/요청), s04 token guard compact, `core/compaction.py`, `core/context_prune.py`, s03 volatile 분리, s06 turn_context 주입과 retired tool 평문화(`src/xgen_agent_runtime/stages/s06_api/artifact/default/stage.py:653-660`) | **코드 임계값**(0.8/0.9/0.7 하드코딩, `src/xgen_agent_runtime/stages/s02_context/artifact/default/stage.py:531-545`) + 설정(`prune_over_tokens`, `compaction_enabled`, compactor slot) | 압축 소유권 규칙: SDK provider는 런타임이, CLI provider는 native thread가 소유한다(`docs/long_running_execution.md:221-227`) |
| **client_tool** | `ToolRegistry`(core/deferred 노출), s10 executor/router/권한 사다리, s06 `tool_loop=internal`, `ToolDispatcher`, MCP(`from_manifest_async`), manifest `tools.{built_in, external, mcp_servers, core_overrides}`(`docs/manifest.md:93-100`), 도구 설명(description) | 설정(manifest tools) + 코드(권한·반복 guard·second_machine) + 런타임 객체(registry·provider) | `env` 도구 `enable/disable/forge_tool`, `set_setting`. 도구 노출 상태는 registry version을 거쳐 s03이 다음 반복에 반영한다 |
| **skill** | `SkillToolProvider` registry(파이프라인이 duck-typing으로 찾음, `src/xgen_agent_runtime/core/pipeline.py:2216-2225`), manifest `host_selections.skills`(라이브러리는 적용하지 않음) | 런타임 객체 + 설정 | `env` 도구 `enable/disable/create/edit_skill`(`src/xgen_agent_runtime/core/environment_control.py:484-610`). pipeline stage에는 skill 전용 칸이 없다(도구로 노출됨) |
| **memory** | s02 retriever + provider 검색, s03 메모리 블록, s18 strategy·persistence·provider(record_turn, execution, reflect, promote), s19 요약 provider 전달, manifest `memory` 블록 | 런타임 객체(대개 host가 attach) + 설정 | 배선이 세 stage에 나뉘어 있어 하나만 빠져도 기억이 조용히 죽는다(`src/xgen_agent_runtime/host/runner.py:752-766`) |
| **subagent** | 4.71.0에서 제거. Stage 12/13 칸과 manifest `subagents` 절은 로드할 때 버려진다(`src/xgen_agent_runtime/core/environment.py:359`, `:704-726`) | 없음 | 백그라운드 task(`runtime/tasks`)와 skill fork runner(`_fork_runner`)만 남았다(`src/xgen_agent_runtime/core/pipeline.py:2236`) |

관찰:
- **policy가 코드에 박혀 있는 대표 사례**:
  - 모델명으로 provider 추론(`src/xgen_agent_runtime/core/builder.py:282-290`)
  - 압축 임계값 0.8/0.9/0.7
  - 반복 차단·거부 존중·second-machine 안내(s10 미들웨어)
  - 완료 직전 산출물 대조 규칙(CSV/JSON 결정론 판정)
  - turn_budget 기본값(soft 100만 / hard 300만 토큰, `src/xgen_agent_runtime/stages/s16_loop/turn_budget.py:1-22`)
  - `begin_turn`의 턴 단위 키 목록
  - 이들 다수가 실측 근거(Harness-Bench, dev 28일)를 docstring에 적은 경험적 장치이고, `harness_components.py`가 이것들을 "middleware / recovery / verification / context" 층으로 이미 분류하고 있다(`src/xgen_agent_runtime/host/harness_components.py:19-33`). **RRSI 편집 단위의 초기 카탈로그로 그대로 쓸 수 있다.**
- **prompt와 control_flow의 결합**: 루프 종료가 모델의 텍스트 마커(regex) 또는 "도구 호출 없음"에 달려 있다. 마커 규약은 pipeline 설정에 선언되어 있지 않고 host 프롬프트에 암묵적으로 들어 있다. 신규 설계는 이 결합을 명시적 계약으로 올리거나 구조화된 신호로 바꿔야 한다.
- **기존 자기 수정 표면(`PipelineEnvironment`)**: 에이전트가 직접 바꿀 수 있는 것은 {prompt, tools, skills, tool settings, 모델 tunable}이다. control_flow, context_mgmt 임계값, output_plumbing은 바꿀 수 없다. 변경 단위는 세션이고 평가나 롤백 루프는 없다(change-log만 있음).

---

## 7. Pain points와 재설계 제약

### 7.1 반드시 보존해야 할 것 (외부 계약)
1. **실행 진입점과 결과**:
   - `await pipeline.run(input, state, overrides=) -> PipelineResult`. 예외를 던지지 않고 실패 결과를 반환한다.
   - `pipeline.run_stream(...) -> AsyncIterator[PipelineEvent]`. 종료 이벤트가 반드시 하나 온다.
   - `PipelineResult`의 필드와 property 4개(`src/xgen_agent_runtime/core/result.py:12-76`).
2. **slice 의미론**: `RunStatus`/`TerminationReason`, `resumable`, `CONTINUE_RUN`(user 메시지 추가 없음), 연속 slice에서 비용과 토큰 보존(`docs/long_running_execution.md:168-208`). host는 `while result.resumable: run(CONTINUE_RUN, state)` 형태를 쓴다(`src/xgen_agent_runtime/host/runner.py:1677-1682`).
3. **이벤트 계약**: `PipelineEvent` envelope, `EventTypes` 140개 이름(append-only), 그리고 최소한 §5.4의 소비 집합과 그 payload(`text.delta.text/source/granularity`, `tool.call_*`, `api.cli_tool_call`, `api.tool_result`, `pipeline.complete.result/status`). `seq` 기반 `events(replay_from)`, `run_id`/`session_id` 상관 키.
4. **상태 연속성**: `PipelineState`를 턴 사이에 넘겨 대화를 잇는 모델(fresh-per-turn과 long-lived 둘 다), `messages`의 Anthropic 형식, `session_id`. 다만 내부 필드 대부분은 바꿔도 된다. host가 직접 읽는 필드는 `turn_token_usage`, `total_cost_usd`, `events`, `shared`(fast_path 등), `_turn_event_counts`, `final_text`, `run_status` 정도다(**정확한 전체 목록은 미확인**: host/와 외부 xgen-workflow 소비처를 따로 조사해야 함).
5. **런타임 주입 API의 의미**: `attach_runtime(llm_client, tool_context, sandbox, memory_*, system_builder, hook_runner, permission_*, session_runtime(+rollout_recorder))`, `refresh_runtime`, `aclose()`. 레포 안 host는 그중 `llm_client(override_manifest=True)`, `tool_context`, `memory_*`, `system_builder`, `session_runtime`을 쓴다(`src/xgen_agent_runtime/host/runner.py:704-786`, `:1265`).
6. **HITL resume**(`resume(token, decision)`), **오류 코드 체계**(`ExecutorErrorCode`의 안정 문자열, `docs/error_codes.md`).
7. **(선택)** manifest 포맷과 21칸 번호를 쓰는 외부 UI·저장소가 있다면 그 호환성. 이 레포 안 host는 manifest를 쓰지 않는다(§0-5). Geny나 xgen-workflow가 쓰는지는 **미확인**.

### 7.2 피해야 할 것 (구조적 부채)
1. **공유 가변 state를 버스로 쓰는 구조**:
   - 70개 필드와 자유 dict 2개(`shared`, `metadata`)를 모든 stage가 읽고 쓴다.
   - 키 계약이 문자열 관례뿐이고, `SharedKeys` 상수가 실제 키와 다른 경우도 있다: `TOOL_REVIEW_FLAGS`의 `executor.tool_review_flags` 대 실제 `tool_review_flags`, `HITL_REQUEST`의 `executor.hitl_request` 대 실제 `hitl_request`(`src/xgen_agent_runtime/core/shared_keys.py:63`, `:74`, `src/xgen_agent_runtime/stages/s15_hitl/interface.py:24`).
   - 턴 경계에서 지울 키를 core가 하드코딩한다(`src/xgen_agent_runtime/core/state.py:413-438`). `summary_history`, `checkpoint_history`는 그 목록에 없어 무한히 자란다.
2. **루프 결정의 분산**: `loop_decision`을 5개 주체가 순서 의존적으로 쓰고, 반복·비용 상한이 각각 3곳에서 서로 다른 status로 끝난다(§4.3). 신규 설계는 단일 decision 함수에 신호를 모으는 구조가 바람직하다.
3. **stage 사이의 숨은 결합**:
   - `_init_state`가 order 2/4와 이름 `"tool"`/`"api"`로 stage를 찾아 비공개 속성을 배선한다(`src/xgen_agent_runtime/core/pipeline.py:3274-3319`, `:3482`).
   - `from_manifest`가 이미 생성된 stage의 registry 참조를 사후에 바꿔 끼운다(`:1473-1478`).
   - s10이 s16 모듈 함수(`note_refused`)를 직접 import한다(`src/xgen_agent_runtime/stages/s10_tool/artifact/default/stage.py:349-351`).
   - core `compaction.py`가 host 키를 안다.
4. **"매 반복 재실행 + 내부 iteration 가드"**: s02–s05가 매 반복 돌면서 비용 있는 일을 `iteration==0` 조건으로 스스로 피한다. 문서는 이것을 "once"로 오해하게 쓴다. s03은 system prompt를 매 반복 다시 빌드하고 s05는 history에 cache_control을 매번 다시 찍는다. 전송 계층의 관심사가 정규 history를 오염시키는 구조다.
5. **조용한 no-op과 decoy**:
   - attach 대상 stage가 없으면 무음(§2.8).
   - inert 필드: `cache_metrics.estimated_savings_usd/cache_hit_rate`, `tool_choice` writer 없음, `ModelOverrides.thinking_level`(부록 A-3).
   - 발행 주체 없는 이벤트 2개(§5.5).
   - `RunStatus.CANCELLED`는 설정되지 않는다.
   - `HostSelections`는 적용되지 않는다.
6. **오류 코드 손실**(§2.6). 재설계에서는 원래 예외의 코드를 terminal 이벤트에 그대로 실어야 한다.
7. **설정 채널 난립과 실제 운영 경로의 이탈**: 운영 host가 builder와 비공개 속성으로 조립한다(`pipeline._memory_provider = ...`, `_tool_stage._context.result_filter = ...`, `src/xgen_agent_runtime/host/runner.py:724-729`, `:778`). 신규 설계는 "조립 = 선언 하나" 경로를 단 하나만 두고, 하네스 컴포넌트 편집도 그 선언(RRSI의 편집 대상)으로 표현하는 편이 정합적이다.
8. **21칸 고정 번호**: 기능과 무관한 위치 고정이다(은퇴 칸 2개 포함). Phase 경계는 클래스 상수이고 manifest는 order를 키로 쓴다. 컴포넌트 단위 편집과 맞지 않는다.

### 7.3 재사용 가치가 높은 것 (지킬 자산)
- 이벤트 카탈로그 체계(값=wire, append-only, PAYLOADS 완전성 검사, seq/journal/replay).
- slice 상태 모델(completed / suspended / blocked / failed)과 continuation 계약.
- 압축 관련 장치: 워터마크 재조정(identity 기반), 결정론 prune(쌍 보존 불변식), 압축 소유권 규칙.
- `ToolDispatcher` 원칙: "permission 결정 경로는 하나"(`src/xgen_agent_runtime/stages/s10_tool/dispatcher.py:1-28`).
- `harness_components` 같은 장치별 작동 계측. RRSI 평가 지표와 credit assignment에 바로 쓸 수 있다.
- `PipelineEnvironment`의 "AVAILABLE 집합 안에서만 편집, core 잠금, change-log" 원칙.

---

## 부록 A. 검증 실험 (스크래치 스크립트, 레포 무변경)

**A-1. stage 예외의 오류 코드 전파.** `InputStage` 다음에 order 6 stub이 `APIError(code=EXEC_CLI_AUTH_FAILED)`를 던지게 했다. 관측 결과:
```
stage.error api exec.cli.auth_failed
pipeline.error  exec.stage.failed
status failed iterations 0
```
→ 원래 코드는 `stage.error`에만 남고 `pipeline.error`는 `exec.stage.failed`다(`src/xgen_agent_runtime/core/pipeline.py:3613`). Stage 2–5는 미등록이라 `stage.bypass`로 기록됐다.

**A-2. 루프 본문 범위.** 등록: Input(1), stub System(3), stub Cache(5), stub API(6), Tool stub(10, iteration 0에서만 tool_results 설정), 실제 `LoopStage`(16), stub Memory(18). `stage.enter` 관측:
```
[(0,'input'),(0,'system'),(0,'cache'),(0,'api'),(0,'tool'),(0,'loop'),
 (1,'system'),(1,'cache'),(1,'api'),(1,'tool'),(1,'loop'),(1,'memory')]
status completed iterations 1 ['loop.continue', 'loop.complete']
```
→ Stage 3·5가 두 번째 반복에서도 실행된다(Phase A는 Stage 1뿐). 두 바퀴를 돌았지만 `iterations=1`로 보고된다.

**A-3. `ModelOverrides.thinking_level`.** `ModelOverrides(thinking_level='high', model='m').non_none_fields()`의 결과가 `{'model': 'm'}`이다. thinking_level이 빠진다(`src/xgen_agent_runtime/core/config.py:139-146`).

## 부록 B. 미확인 항목
- 외부 소비자(xgen-workflow, Geny, xgen-agent-runtime-web)가 `PipelineState`나 manifest의 어떤 필드를 직접 쓰는지. 이 문서는 레포 안 `host/`만 확인했다.
- `PipelineState`에서 host가 직접 읽는 필드의 전체 목록(§7.1-4).
- s06 `InternalAgenticLoop`의 세부 상한·실패 처리(`src/xgen_agent_runtime/stages/s06_api/artifact/default/tool_loop.py:112-456`)와 provider별 client 동작. 이 문서는 docstring 수준만 확인했다.
- s14 `adaptive` artifact, s16 `MultiDimensionalBudgetController`의 dimension별 세부 동작(`src/xgen_agent_runtime/stages/s16_loop/artifact/default/controllers.py:484-832`). 개요만 확인했다.
