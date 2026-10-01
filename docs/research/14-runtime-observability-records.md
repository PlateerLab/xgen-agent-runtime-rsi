# 14. 기존 런타임의 관측·기록 현황 — τ 단위 신호와 재생 기록 관점 조사

> 대상: `PlateerLab/xgen-agent-runtime@2e015ae` (패키지 `xgen_agent_runtime` 4.75.0, HEAD `2e015ae`). 조사일 2026-10-01.
> 범위: `events/`, `docs/events.md`·`docs/rollouts.md`·`docs/long_running_execution.md`, `core/`(rollout_recorder·snapshot·continuation·run_status·result·diff·artifact·state·pipeline·environment_control), `host/`(rollouts·execution_record·conversation_archive·runner·turn_executor·harness_components), `history/`, `session/`, `stages/s07_token`·`s14_evaluate`·`s16_loop`·`s17_emit`·`s20_persist`·`s21_yield`, `notifications/`, `cron/`, `runtime/`, `tests/`, `scripts/`.
> 방법: 정적 읽기만 했다(테스트·스크립트 실행 없음, 레포 수정 없음). 확인하지 못했거나 레포 밖에 있는 것은 **미확인**으로 적는다.
> 경로 표기: `src/xgen_agent_runtime/` 아래는 접두를 생략한다(예: `core/state.py:12`). `tests/`·`docs/`·`CHANGELOG.md` 는 레포 루트 기준이다.
> 용어: r(x,τ)·c(τ) 정의는 [01-rrsi-paper.md](01-rrsi-paper.md) §1.2(c(τ)=정책 토큰 수, 달러 아님), 재생 트리 노드는 [03-dream-rsi-paper.md](03-dream-rsi-paper.md)를 따른다. 프로바이더별 토큰 파싱의 세부 결함은 [12-runtime-provider-layer.md](12-runtime-provider-layer.md) §5 에 더 자세하다. 이 문서는 그와 겹치지 않게 "무엇이 기록·집계되는가"에 집중한다.

---

## 요약

- **이벤트 스트림은 잘 정돈돼 있다.** 카탈로그 v16에 이벤트 140종(그중 15종은 은퇴)이 있고(`events/catalog.py:67-308`, `docs/events.md`), 엔진의 모든 이벤트는 `Pipeline._record_event` 한 깔때기를 지나며 `seq`·`run_id`·`session_id` 를 받는다(`core/pipeline.py:2813-2838`). 사용량은 `api.response`(호출별)와 `token.tracked`(호출별 비용+턴 누적)에, 시간은 `api.ttft`·`tool.call_complete.duration_ms`·봉투의 `timestamp` 에 실린다.
- **턴 전체의 호출별 토큰 원장이 이미 있다.** `state.turn_token_usage`(호출당 `TokenUsage` 1건)는 새 턴에서만 비워지고 연속 슬라이스(`CONTINUE_RUN`)에서는 유지된다(`core/state.py:389`, `core/state.py:440-474`). 호스트의 `turn_usage()` 가 이것을 합쳐 턴 끝에 `usage` 페이로드를 낸다(`host/runner.py:1038-1122`). 다만 reasoning 토큰 칸은 없다(`core/state.py:12-21`).
- **그러나 c(τ)를 그대로 읽어 쓸 수는 없다.** (1) Stage 6 internal loop 는 여러 왕복의 사용량을 응답 1건으로 접고(`stages/s06_api/artifact/default/tool_loop.py:419-454`), CLI 백엔드도 CLI 내부 왕복 전체가 1건이다(`num_turns` 미수집). (2) 압축·skill fork·턴 후 증류의 LLM 호출 사용량은 턴 원장에 들어가지 않는다(`stages/s02_context/artifact/default/compactors.py:257-262`, `skills/fork.py:176-180`, `host/distill.py:146`). (3) USD 비용은 기본 계산기가 Anthropic 표만 쓰므로 다른 모델은 0.0 이 된다(`core/builder.py:157`, `stages/s07_token/artifact/default/pricing.py:206-210`).
- **기록 장치는 "관측 이벤트 녹화"까지만 한다.** opt-in `RolloutRecorder` 는 순서·내구성(fsync)이 보장된 JSONL 을 쓴다(`core/rollout_recorder.py:118-305`). 하지만 모델 요청 본문(system·tools·실제 전송 messages), 응답 원문 블록, 전체 도구 결과(8000자 절단), `llm_client.*` 이벤트가 빠져 있어 **기록만으로 결정론적 재생은 불가능하다**(§3.11).
- **재생 트리의 노드로 쓸 만한 스냅샷이 없다.** Stage 20 checkpoint 는 부모 포인터가 없는 평면 상태 사본이고(`stages/s20_persist/types.py:19-45`), 호스트 기본 조립은 Stage 20을 등록하지도 않는다(`host/runner.py:615-683`). 워크스페이스(파일시스템) 스냅샷은 런타임 밖 호스트 책임이다(미확인).
- **평가기는 사실상 없다.** `s14_evaluate` 의 `score` 는 루프 계속/종료 판정의 부산물(1.0/0.8/0.0 고정값, `NoScorer` 는 늘 1.0)이며(`stages/s14_evaluate/artifact/default/strategies.py:16-67,166-174`), LLM 판정기(judge)는 코드에 없다. 호스트 조립은 Stage 14도 등록하지 않는다. 결정론 검증 장치는 `DeliverableReviewer`(산출물 파일 형식 점검), Bash `artifact_contracts`, `StructuredOutputParser` 세 가지다.
- **결정론 테스트 기반은 재사용 가치가 크다.** `BaseClient._send` 하나만 구현한 가짜 클라이언트를 `runner.build_client` 자리에 끼우고 `_FakeHost` 로 `AgentTurnExecutor().run()` 전 경로를 네트워크 없이 돌리는 패턴이 이미 테스트에 있다(`tests/test_host_turn_executor_gates.py:644-695`). `MockProvider`·`ScriptedClient`·가짜 CLI 바이너리·실녹화 golden 도 있다.
- **`history/` 패키지(SQLite 실행 기록·`ExecutionReplayer`·`ABTestRunner`)는 파이프라인에 연결되지 않은 고아다.** 테스트에서만 쓰이고, 재생기는 실제 이벤트 이름과 다른 `stage_start`/`stage_complete` 를 찾는다(`history/replay.py:59,85,116`).
- **편집 이력은 자기수정 환경의 changelog 정도다.** `PipelineEnvironment` 가 `{seq, action, target, detail, ok}` 를 메모리에 쌓을 뿐 전후 내용·부모·평가 결과와 연결되지 않는다(`core/environment_control.py:102-119,205-215`).
- **결론:** step 수·종료 사유·턴 토큰 합은 기존 원장에서 파생할 수 있지만 누락 경로를 메워야 한다. r(x,τ), valid-output/no-submission 판정, k-trial 배치, 하네스 버전 식별자, 요청→응답 녹화, 재생 트리 노드는 새로 만들어야 한다. 런타임을 고치지 않고 붙일 이음매(seam)는 충분하다(§7).

---

## 0. 용어 대응 — 런타임 단위와 RSI 개념

같은 단어가 다른 뜻으로 쓰이는 곳이 있어 먼저 맞춘다.

| RSI 개념 | 런타임에서 가장 가까운 것 | 주의 | 근거 |
|---|---|---|---|
| 궤적 τ (한 과제 1회 실행) | 호스트 턴 1회 = `stream_turn`/`run_turn` 1회 | 한 호스트 턴은 연속 슬라이스 여러 개(=`Pipeline.run` 여러 번)일 수 있다. 슬라이스마다 `run_id` 가 새로 발급된다 | `host/runner.py:1416-1578`, `core/pipeline.py:3228` |
| 단계(step) | ① loop iteration(Stage 2~16 1회전) ② 모델 호출(`api.request`) ③ 도구 호출(`tool.call_start`) | 셋은 서로 다른 카운터다. 한 iteration 에 도구 0~N개, internal loop 는 한 iteration 에 모델 호출 여러 번 | `docs/long_running_execution.md:96-105`, `core/pipeline.py:3056-3084` |
| 산출물(artifact) | 과제 결과 파일·최종 텍스트 | 런타임의 "artifact" 는 **스테이지 구현 변형**(예: `s14_evaluate/artifact/adaptive`)이다 | `core/artifact.py:1-24,58-78` |
| 스냅샷(snapshot) | 노드의 파일시스템 상태 | 런타임의 `PipelineSnapshot` 은 **파이프라인 설정** 스냅샷이다 | `core/snapshot.py:1-6,45-56` |
| 점수 s_v / r(x,τ) | 과제 채점 결과 | 런타임의 `evaluation_score` 는 루프 판정 부산물 | `core/state.py:224-226`, §4 |
| 하네스 H | 파이프라인 조립(스테이지·전략 슬롯·프롬프트·도구 표면·한도) | 호스트에서는 `build_pipeline(...)` 키워드 인자 + 시스템 프롬프트 + 도구 레지스트리가 사실상의 H | `host/runner.py:495-806` |

---

## 1. 이벤트 스트림

### 1.1 봉투(envelope)와 전달 경로

`PipelineEvent` 필드는 `type, stage, iteration, timestamp, data, session_id, run_id, seq` 여덟 개다(`events/types.py:33-40`). `timestamp` 는 생성 시각 ISO 문자열(UTC), `seq` 는 파이프라인 단위 단조 증가 번호다.

이벤트가 만들어지는 길은 셋이다.

1. **버스 직접 방출** — `Pipeline._emit` 이 `pipeline.start`, `stage.*` 등을 만든다. 기록(`_record_event`) 후 비동기 핸들러를 await 한다(`core/pipeline.py:3615-3626`).
2. **상태 이벤트 다리** — 스테이지가 `state.add_event(type, data)` 를 부르면 `state.events` 에 dict 로 쌓이고, `_init_state` 가 설치한 다리가 `PipelineEvent` 로 감싸 기록한 뒤 `emit_sync` 한다(`core/state.py:476-514`, `core/pipeline.py:2902-2926`). 다리 실패는 경고만 남기고 삼킨다(`core/state.py:502-510`).
3. **종료 이벤트** — `pipeline.complete`/`pipeline.error` 는 `_emit_rollout_terminal` 로 기록 → 녹화기 flush(내구성 장벽) → 버스 순서로 나간다(`core/pipeline.py:2881-2900`).

기록 깔때기 `_record_event` 는 `seq` 부여 → 링 저널(기본 2048개, `core/pipeline.py:876,894`) → `events()` 탭 → 녹화기 순으로 넘긴다(`core/pipeline.py:2813-2838`). `pipeline.events(replay_from=seq)` 는 저널 범위 안에서 늦게 붙은 구독자에게 이어 보기를 준다(`core/pipeline.py:2750-2811`).

**기록되지 않는 것**: 호스트가 날 버스에 직접 쏜 이벤트(`core/pipeline.py:2766-2767`), `llm_client.*` 이벤트(클라이언트 `event_sink` 콜백으로만 감, `events/catalog.py:39-42`, `llm_client/base.py:146-151`), 호스트 층의 `agent_event`(`task_progress`·`task_suspended`·`task_blocked`)와 `usage` 청크(`host/runner.py:1535-1587`). 이들은 PipelineEvent 가 아니라 롤아웃 파일에 남지 않는다.

`state.events` 는 턴마다(`core/state.py:394`) 그리고 **연속 슬라이스마다**(`core/state.py:465`) 비워진다. 그래서 `PipelineResult.events` 는 마지막 슬라이스의 것만 담는다(`core/result.py:94`). 반면 `state._turn_event_counts`(타입별 횟수)는 슬라이스를 넘어 턴 전체를 센다(`core/state.py:301-303,498`; `begin_continuation_slice` 는 이것을 건드리지 않음).

### 1.2 이벤트 전체 목록

카탈로그는 이름과 페이로드 설명을 한곳에 둔다(`events/catalog.py:70-308` 이름, `:316-981` 페이로드). 완전성은 AST 테스트(`tests/unit/test_event_catalog.py`, 카탈로그 docstring `events/catalog.py:26-29`)와 import 시점 검사(`events/catalog.py:1027-1041`)가 지킨다. 아래 "U/C/T" 열은 사용량(Usage)·비용(Cost)·시간(Time) 정보를 싣는지 표시한다.

| 계열 (카탈로그 줄) | 이벤트 | 주요 페이로드 | U/C/T | 주 방출 위치 |
|---|---|---|---|---|
| 파이프라인 수명 (82-88) | `pipeline.start` | `input`(500자 절단) | — | `core/pipeline.py:2478,2682` |
| | `pipeline.complete` | `iterations, status, termination_reason, resumable, checkpoint_id` + run_stream 에서만 `result`(전문), `total_cost_usd` | C(스트림만) | `core/pipeline.py:2493-2504`(run), `2630-2647`(run_stream) |
| | `pipeline.error` | `error, code, exception_type` + 스트림에서 `total_cost_usd` | C(스트림만) | `core/pipeline.py:2510,2651` |
| | `stage.enter` / `stage.exit` / `stage.bypass` | 페이로드 없음(봉투의 `stage`·`iteration`) | T(타임스탬프 차) | `core/pipeline.py:3541-3592` |
| | `stage.error` | `error, code, exception_type` | — | `core/pipeline.py:3602-3609` |
| 실행 시작 알림 (91-92) | `config.override_applied`, `runtime.llm_client_override` | 필드명·값 / provider | — | `core/pipeline.py:3042-3049,3243-3248` |
| 루프 제어 (95-110) | `loop.continue/complete/error/escalate/suspend` | `iteration, signal, pending_tools, has_tool_results, upstream_decision` | — | `stages/s16_loop/artifact/default/stage.py:186-195` |
| | `loop.suspended` | `reason, iteration, max_iterations, resumable` | — | `core/pipeline.py:3097-3105` |
| | `loop.blocked` | `reason, total_cost_usd, budget_usd` | C | `core/pipeline.py:3114-3121` |
| | `loop.force_complete`, `loop.budget_exceeded` | `reason`/`dimension`(iteration·cost·tokens·wall_clock·tool_calls) | C(일부) | 카탈로그 352-357, 404-407 |
| | `loop.completion_review` | `reviewer, mode, files, missing, problems, paths` | — | `stages/s16_loop/completion_review.py:389-401` |
| | `loop.repeat_stop` | `phase, refused, calls, iteration` | U(호출 수) | 카탈로그 416-421 |
| | `loop.turn_budget` | `phase, used`(프롬프트 토큰), `soft, hard, calls` | U | 카탈로그 422-429 |
| Stage 1 (113-117) | `input.normalized`, `input.continuation`, `input.tool_calls_repaired` | 길이/메시지 수/합성 결과 수 | — | 카탈로그 430-438 |
| Stage 2 (120-138) | `context.built`, `context.compacted`, `context.pruned`, `context.compaction_failed`, `context.compaction_record_failed`, `context.retrieval_timeout`, `context.compaction_scheduled`, `context.compaction_requested`, `context.compaction_target_missed`, `context.short_term_window`, `memory.compaction.summarized`, `memory.compaction.llm_failed` | 메시지 수, 추정 토큰(`estimated_tokens`, `tokens_before/after`, `saved_tokens_estimate`) | U(추정치) | 카탈로그 439-505 |
| Stage 3·4·5 (141-149) | `system.built`, `guard.check`, `guard.warn`, `guard.compacting`, `cache.applied` | 프롬프트 길이, 가드 결과, 캐시 전략 | — | 카탈로그 506-527 |
| Stage 6 (152-185) | `api.request` | `model, provider, message_count, has_tools, has_thinking, stream` | — | `stages/s06_api/artifact/default/stage.py:478-488` |
| | `api.response` | `stop_reason, text_length, tool_calls, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens` | **U** | 같은 파일 `:541-560` |
| | `api.ttft` | `ttft_ms, provider, model, stream, iteration, first_visible` | **T** | 같은 파일 `:506-516`(비스트림=전체 지연), `:957-969`(스트림=첫 청크) |
| | `api.retry`, `api.stream_restart`, `api.error`, `api.router.error`, `api.model_routed`, `api.timeout_unsupported`, `api.internal_loop_capped` | 시도·지연(`delay`)·오류 분류·라우팅 전후 모델 | T(`delay`) | 같은 파일 `:769-793,826-851,861-872,527-539,383-397`; `tool_loop.py:397-407` |
| | `text.delta`, `thinking.delta`, `api.tool_use`, `api.cli_tool_call`, `api.input_json_delta`, `api.content_block_stop`, `api.tool_result` | 스트림 청크 본문(`text`, 도구 `id/name/input`, CLI 도구 결과 `content` 전문) | — | 같은 파일 `:970-1013` |
| Stage 7 (188) | `token.tracked` | `input_tokens, output_tokens, cache_write, cache_read, cost_usd`(이번 호출), `total_cost_usd`(턴 누적) | **U·C** | `stages/s07_token/artifact/default/stage.py:100-110` |
| Stage 8 (191-192) | `think.processed`, `think.budget_applied` | `thinking_block_count, total_thinking_tokens` / 예산 전후 | U(사실상 0, §1.4) | `stages/s08_think/artifact/default/stage.py:149-155` |
| Stage 9 (195) | `parse.complete` | `text_length, tool_calls, signal, stop_reason` | — | `stages/s09_parse/artifact/default/stage.py:136-144` |
| Stage 10 (198-217) | `tool.execute_start` / `tool.execute_complete` | `count, tools` / `count, errors` | — | `stages/s10_tool/artifact/default/stage.py:413-440` |
| | `tool.call_start` | `tool_use_id, name, input`(전체) | — | `stages/s10_tool/artifact/default/executors.py:73-83` |
| | `tool.call_complete` | `tool_use_id, name, is_error, duration_ms` + 실제로는 `error`(≤2000자) 또는 `result`(≤8000자) | **T** | 같은 파일 `:120-155` |
| | `tool.repeat_failure`, `tool.repeat_blocked`, `tool.same_result`, `tool.gate_reachability_repaired`, `tool.user_denied`, `tool.surface_restored`, `tool.not_in_sandbox` | 하네스 가드 작동 내역 | — | `stages/s10_tool/artifact/default/stage.py:360-398` |
| Stage 11 (220-222) | `tool_review.flag`, `tool_review.completed`, `tool_review.reviewer_error` | 검토자·심각도 | — | 카탈로그 683-697 |
| Stage 14 (246-247) | `evaluate.start`, `evaluate.complete` | `strategy` / `passed, score, decision, loop_decision, feedback`(200자) | — | `stages/s14_evaluate/artifact/default/stage.py:79,99-108` |
| Stage 15 (250-254) | `hitl.request`, `hitl.decision`, `hitl.no_decision`, `hitl.timeout`, `hitl.requester_error` | 토큰·판정·`timeout_seconds` | T(설정값) | 카탈로그 778-801 |
| Stage 17 (257-263) | `emit.start`, `emit.complete`, `emit.timeout`, `emit.skipped_backpressure`, `emit.skipped_dep_failed`, `emit.cycle_detected`, `emit.unknown_dependency` | 출력 채널(텍스트·콜백·TTS 등) 전송 결과 | — | 카탈로그 802-831 |
| Stage 18 (266-281) | `memory.updated`, `memory.persisted`, `memory.turn_recorded`, `memory.execution_recorded`, `memory.insight`, `memory.promoted`, `memory.reindexed`, `memory.cost`, `memory.snapshot`, `memory.insight_recorded`, `memory.insight_invalid`, `memory.reflection_queued`, `memory.structured_reflection_done`, `memory.provider_recorded`, `memory.retrieve_breakdown`, `memory.retrieved_empty` | 메모리 기록·검색 내역 | — (`memory.cost` 는 방출자 없음) | 카탈로그 832-895 |
| Stage 19 (284-291) | `summary.written`, `summary.skipped`, `summary.session_closed`, `summary.session_close_error`, `summary.importance_error`, `summary.provider_recorded`, `summary.provider_error`, `summary.summarizer_error` | 요약 기록 | — | 카탈로그 896-925 |
| Stage 20 (294-296) | `checkpoint.written`, `checkpoint.skipped`, `checkpoint.persister_error` | `checkpoint_id, session_id, iteration, persister` | — | `stages/s20_persist/artifact/default/stage.py:173-218` |
| Stage 21 (299-300) | `yield.complete`, `yield.summary` | `text_length, iterations, total_cost_usd` | C | `stages/s21_yield/artifact/default/stage.py:64-71`, `formatters.py:61-69` |
| llm_client (303-308) | `llm_client.feature_unsupported`, `llm_client.parameter_dropped`, `llm_client.drift_healed`, `llm_client.unknown_wire_shape`, `llm_client.tool_args_repaired`, `llm_client.tool_args_unparsed` | 요청 필드 협상·와이어 이상 | — | `llm_client/base.py:461-487,544-585` |
| 은퇴 (225-243) | `agent.*`(3), `subagent.*`(5), `task.*`(4), `task_registry.*`(3) | 4.71.0 이후 방출 없음 | — | `events/catalog.py:988-1006` |

카탈로그 밖에서 호스트 브리지가 받아 주는 `canvas_command` 이벤트도 있다(`host/runner.py:1494-1495`). 방출자는 미확인(호스트 측으로 보인다).

### 1.3 사용량·비용·시간을 싣는 이벤트만 추린 표

| 신호 | 이벤트·필드 | 단위 | 비고 |
|---|---|---|---|
| 호출별 토큰 | `api.response.{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}` | 호출 1회 | internal loop 의 내부 호출도 각각 방출된다(`call_once` 가 호출마다 실행, `stage.py:475-561`). CLI 는 CLI 실행 1회 = 1건 |
| 호출별 토큰+비용 | `token.tracked.{input_tokens, output_tokens, cache_write, cache_read, cost_usd}` | Stage 7 1회 | internal loop 에서는 여러 호출의 합(§2.4) |
| 턴 누적 비용 | `token.tracked.total_cost_usd`, `yield.complete.total_cost_usd`, `pipeline.complete.total_cost_usd`(스트림만), `loop.blocked.total_cost_usd` | 턴 | 계산기 기준 USD |
| 턴 프롬프트 토큰 | `loop.turn_budget.used`, `.calls` | 턴(슬라이스 누적) | 캐시 합산 방식이 provider 무관(§2.6) |
| TTFT | `api.ttft.ttft_ms` | 호출 | 비스트림에서는 전체 지연 |
| 도구 지연 | `tool.call_complete.duration_ms` | 도구 호출 | CLI 내부 도구는 호스트가 따로 잰다(`host/runner.py:1500-1526`) |
| 백오프 | `api.retry.delay` | 재시도 | |
| 단계 소요 | `stage.enter`/`stage.exit` 의 `timestamp` 차 | 스테이지 | 직접 필드 없음 |
| 전체 API 지연(스트림) | 없음 | — | `api.request`→`api.response` 타임스탬프 차로 파생해야 함 |

### 1.4 카탈로그와 실제 방출의 차이·함정

- `tool.call_complete` 의 카탈로그 설명(`events/catalog.py:677-682`)에는 `result`/`error` 가 없지만, 실제 방출은 실패 사유(≤2000자)나 성공 결과(≤8000자)를 싣는다(`stages/s10_tool/artifact/default/executors.py:87-91,135-154`). 기록에 남는 도구 결과는 **잘린 것**이다.
- `think.processed.total_thinking_tokens` 는 thinking 블록 dict 의 `budget_tokens_used` 키를 더하는데(`stages/s08_think/artifact/default/stage.py:137,144`), 어떤 클라이언트도 그 키를 채우지 않는다(`llm_client/` grep 0건). 사실상 늘 0이다. [12번 문서](12-runtime-provider-layer.md)는 본문까지 None 이 되는 버그를 실측으로 보고했다. 호스트 조립은 Stage 8을 등록하지도 않는다(`host/runner.py:615-683` 에 `with_think` 없음).
- `memory.reindexed`·`memory.cost`·`memory.snapshot` 은 예약만 돼 있고 방출자가 없다(`events/catalog.py:855-863`).
- `run()` 경로의 `pipeline.complete` 는 `result` 와 `total_cost_usd` 를 싣지 않는다(`core/pipeline.py:2493-2504`). `run_stream()` 경로만 싣는다(`:2630-2647`). 롤아웃 파일만 보고 최종 답과 비용을 얻으려면 스트림 경로여야 한다.
- 봉투의 `iteration` 은 기록 시점의 `state.iteration` 이고 슬라이스마다 0부터 다시 센다(`core/state.py:447`).
- 실행되지 않은 스테이지(호스트 조립에서 빠진 s08·s11·s14·s15·s17·s19·s20)는 매 iteration 마다 `stage.bypass` 를 낸다(`core/pipeline.py:3538-3548`). 기록량이 늘지만, 어떤 스테이지가 꺼져 있었는지의 증거도 된다.

---

## 2. 토큰·비용 계측

### 2.1 저장소와 수명

| 단위 | 저장소 | 리셋 | 위치 |
|---|---|---|---|
| 호출 1회 | `APIResponse.usage: TokenUsage` | — | `llm_client/types.py:78-90` |
| 호출 1회(턴 원장) | `state.turn_token_usage: List[TokenUsage]` 원소 | 새 턴(`begin_turn`)에서만. 연속 슬라이스에서는 유지 | `core/state.py:193,389`; `begin_continuation_slice` 는 미접촉(`:440-474`) |
| iteration 1회 | 별도 칸 없음. pipeline 모드에서는 Stage 7이 iteration 당 1번 돌므로 원장 원소 ≈ iteration. `DetailedTracker` 를 고르면 `metadata["token_breakdown"]` 에 `iteration`·`stage` 와 함께 기록 | — | `stages/s07_token/artifact/default/trackers.py:42-63` |
| 턴 비용 | `state.total_cost_usd` | 새 턴에서 0, 슬라이스에서는 유지 | `core/state.py:194,390`, `core/state.py:607-614` |
| 세션 누적 | `state.token_usage`(모든 호출 합), `state.session_cost_usd`(턴 끝에 증분만 더함) | 없음 | `core/state.py:192,195`, `core/pipeline.py:3337-3342` |
| 캐시 지표 | `state.cache_metrics.total_cache_writes/reads`(호출 횟수) | 없음 | `stages/s07_token/artifact/default/stage.py:94-98` |

`TokenUsage` 칸은 `input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, cost_usd, duration_ms` 여섯 개다(`core/state.py:12-21`). `total_tokens` 는 의도적으로 input+output 만 더한다(벤더마다 input 의 캐시 포함 여부가 달라서, `core/state.py:23-31`). **reasoning(생각) 토큰 칸이 없다.** OpenAI 계열 `completion_tokens_details.reasoning_tokens` 와 Gemini `thoughts_token_count` 는 읽히지 않는다(`llm_client/openai.py:550-574`, `llm_client/google.py:423-429`). 출력 토큰에 섞여 있는지는 벤더 정의에 달렸다(미확인, 벤더별 상이).

### 2.2 프로바이더별 채우는 방식(요약)

| 경로 | input 의미 | 캐시 | cost_usd | 위치 |
|---|---|---|---|---|
| Anthropic SDK | 캐시 제외 | 생성·읽기 별도 칸 | 없음 | `llm_client/anthropic.py:777-790` |
| OpenAI·vLLM·호환 | 캐시 포함(`prompt_tokens`) | `cached_tokens`→`cache_read` | 없음 | `llm_client/openai.py:550-574` |
| Google | `prompt_token_count` | 매핑 없음 | 없음 | `llm_client/google.py:423-429` |
| Claude Code CLI | envelope `usage` | 별도 칸 | 최상위 `total_cost_usd`, `duration_ms` 도 채움 | `llm_client/translators/_cli.py:697-721` |
| Codex CLI | 캐시 포함 | `cached_input_tokens`→`cache_read` | 없음 | `llm_client/translators/_codex.py:323-338` |

CLI envelope 의 `num_turns`(CLI 내부 모델 왕복 수)는 어디서도 읽지 않는다(`grep num_turns` 결과 docstring 한 줄, `llm_client/translators/_cli.py:748`).

### 2.3 Stage 7(Token) — 원장 적재와 USD 계산

`TokenStage.execute` 는 `state.last_api_response` 의 usage 를 tracker 로 적재하고(`turn_token_usage.append`, `token_usage +=`), calculator 로 비용을 계산해 `accumulate_cost` 한 뒤 `token.tracked` 를 낸다(`stages/s07_token/artifact/default/stage.py:81-112`, `trackers.py:21-28`).

USD 비용의 함정:

- **기본 계산기가 Anthropic 전용이다.** `PipelineBuilder.build()` 는 `TokenStage()` 를 인자 없이 등록하고(`core/builder.py:157`), 기본 calculator 는 `AnthropicPricingCalculator`(`stages/s07_token/artifact/default/stage.py:46-55`)다. 표에 없는 모델은 `0.0` 을 돌려준다(`pricing.py:206-210`). gpt·gemini·qwen(vLLM)·신규 Claude 별칭은 모두 0이다. 다중 provider 표인 `UnifiedPricingCalculator`(`pricing.py:276-305`)는 레지스트리에만 있고 아무도 기본으로 쓰지 않는다(src grep 결과 정의 외 사용 0건).
- **가격은 `state.model` 로 매긴다**(`stage.py:91`). 라우터가 호출 단위로 모델을 바꿔도 상태를 바꾸지 않으므로(`stages/s06_api/artifact/default/stage.py:369-398`, 특히 376-377) 라우팅된 호출은 기준 모델 가격으로 계산된다.
- **provider 가 보고한 `usage.cost_usd` 를 Stage 7은 무시한다.** 그래서 `state.total_cost_usd`(계산기 값, 예산 가드가 읽음)와 호스트 `turn_usage()` 의 `total_cost_usd`(provider 보고값 우선, `host/runner.py:1071-1074`)가 서로 다른 숫자가 될 수 있다.
- `_sum_optional` 은 `None + x = x` 다(`core/state.py:55-58`). 일부 호출만 cost 를 보고하면 합계가 조용히 과소 집계된다.
- 가격 계산 자체는 provider 의미를 구분한다(Anthropic 은 세 버킷 독립, OpenAI/Google 은 캐시 읽기를 input 에서 할인, `pricing.py:44-88`). 표 자체는 2026-04 기준 정적 값이다(`pricing.py:91-176`).

### 2.4 iteration 안의 여러 호출 — internal loop 와 CLI

- Stage 6 `tool_loop="internal"` 은 한 iteration 안에서 도구 왕복을 여러 번 돈다. 내부 호출마다 `api.request`/`api.response` 는 나가지만, 소비된 응답의 usage 는 마지막 응답 하나에 합쳐져 Stage 7에는 **1건**으로 들어간다(`stages/s06_api/artifact/default/tool_loop.py:419-426,453-454`). 따라서 `turn_token_usage` 길이(=호스트 `usage.calls`)는 실제 모델 호출 수보다 작다. 중간 실패 시에는 이미 소비한 usage 를 `state.token_usage` 에만 더하고 `turn_token_usage`·`total_cost_usd` 에는 넣지 않는다(`tool_loop.py:443-451`).
- 호스트 기본 조립은 `tool_loop` 를 바꾸지 않으므로 기본값 `PipelineToolLoop` 이다(`stages/s06_api/artifact/default/stage.py:246-258`, `host/runner.py:615-629`). 즉 호스트 SDK 경로에서는 호출 1회 = 원장 1건이 성립한다.
- CLI 백엔드(`claude_code_cli`, `codex_cli`)는 CLI 프로세스 1회가 원장 1건이다. CLI 안의 모델 왕복 수는 기록되지 않는다(§2.2).

### 2.5 턴 끝에 얻을 수 있는 합계

- `PipelineResult.turn_token_usage`, `.total_cost_usd`, `.token_usage`(세션 누적!)가 결과 객체에 실린다(`core/result.py:26-29,89-91`).
- 호스트 `turn_usage(pipeline, state)` 가 크로스-레포 계약 페이로드를 만든다(`host/runner.py:1038-1122`): `input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, total_cost_usd, model, provider, calls, first_call_prompt_tokens, max_call_prompt_tokens` + 선택적 `harness`(장치별 작동 횟수·빠른 경로 판정, `host/harness_components.py:19-51`). 호출별 프롬프트 크기는 provider 가 anthropic/bedrock 일 때만 캐시를 더한다(`host/runner.py:1086-1100`). 사용량 0이면 `None`.
- `stream_turn` 은 이 페이로드를 `{"type":"usage"}` 청크로 정확히 한 번 내고, 취소된 턴에는 `partial: True` 를 붙인다(`host/runner.py:1579-1596`). `run_turn` 은 `usage_sink` dict 로 넘긴다(`host/runner.py:1684-1690`).
- `StructuredFormatter` 와 `MultiFormatFormatter` 는 **세션 누적** `state.token_usage` 를 결과에 싣는다(`stages/s21_yield/artifact/default/formatters.py:41-45`, `multi_format.py:37-40`). 턴 비용으로 오인하기 쉽다.

### 2.6 턴 원장에서 빠지는 LLM 사용량

| 경로 | 무엇 | 현재 처리 | 위치 |
|---|---|---|---|
| 맥락 압축 | `LLMSummaryCompactor` 의 요약 호출(`purpose="s02.compact"`) | 응답 텍스트만 쓰고 usage 폐기. 이벤트에도 토큰 없음 | `stages/s02_context/artifact/default/compactors.py:257-290` |
| skill fork | 서브 호출 | `ForkResult.metadata` 에 input/output 만. 턴 원장 미반영 | `skills/fork.py:145-180,286-296` |
| 턴 후 증류 | 사실 추출·롤업(`purpose="memory.rollup"`) | 별도 데몬 스레드, 턴과 무관 | `host/distill.py:121-159`, `host/runner.py:1220-1230` |
| internal loop 실패 경로 | 소비된 내부 호출 | 세션 누적에만 | `tool_loop.py:443-451` |
| 스트림 재시도 | 실패한 시도의 부분 사용량 | 미집계(재시도 시 새 응답만 남음) | `stages/s06_api/artifact/default/stage.py:802-858` |
| CLI 내부 왕복 | 왕복 수 | 미수집 | §2.2 |

또 하나의 불일치: 턴 입력 예산이 쓰는 `turn_input_tokens()` 는 provider 와 무관하게 `input + cache_creation + cache_read` 를 더한다(`stages/s16_loop/turn_budget.py:59-68`). OpenAI 계열에서는 캐시 읽기가 input 에 이미 들어 있어 이중 집계다. 호스트 `turn_usage()` 는 provider 를 보고 구분한다(`host/runner.py:1086-1100`). 같은 "프롬프트 토큰"이 두 군데에서 다르게 계산된다.

### 2.7 c(τ) 정의에 대한 함의

c(τ)를 "τ가 소비한 정책 토큰 수"로 정확히 정의하려면 다음이 필요하다.

1. 원장의 단위를 "Stage 7 1회"가 아니라 "벤더 호출 1회"로 고정한다. 현 런타임에서 그 단위에 가장 가까운 정본은 `api.response` 이벤트 열이다(internal loop 의 내부 호출까지 각각 나옴).
2. provider 의미를 정규화한 네 버킷(uncached_input, cache_write, cache_read, output)과, 따로 보고되는 경우 reasoning 을 기록한다.
3. 정책(policy) 호출과 부수 호출(압축·judge·증류)을 `purpose` 로 나눈다. 압축이 정책 컨텍스트를 바꾸는 하네스 구성요소라면 c(τ)에 넣을지 규칙을 정해야 한다(논문 정의와 대조 필요, 미확인).
4. CLI 백엔드는 왕복 수를 알 수 없으므로 c(τ)는 envelope 합계만 가능하다. step 수는 CLI 에서 정확하지 않다는 점을 레코드에 표시한다.

---

## 3. 기록·영속화

### 3.1 `RolloutRecorder` — 형식과 보장

- **형식**: 한 줄 = `dataclasses.asdict(PipelineEvent)` 의 compact JSON(`ensure_ascii=False`)(`core/rollout_recorder.py:307-320`). 즉 키는 `type, stage, iteration, timestamp, data, session_id, run_id, seq`. 직렬화 불가 값은 dataclass→dict, Enum→value, Path→str, 그 외 `str()` 로 바뀐다(`:323-330`). 예:
  ```json
  {"type":"api.response","stage":"api","iteration":0,"timestamp":"2026-…Z","data":{"stop_reason":"end_turn","text_length":4,"tool_calls":0,"input_tokens":2,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation_input_tokens":0},"session_id":"…","run_id":"…","seq":17}
  ```
- **순서·내구성**: 파일 핸들은 백그라운드 작성 태스크 하나가 소유하고, 생산자는 직렬화 후 큐에 넣기만 한다(`:1-10,118-148`). 큐 크기 기본 256, 가득 차면 `record_nowait` 가 `RolloutBackpressureError` 를 던진다(조용히 버리지 않음, `:160-169`). `flush`/`shutdown` 은 fsync 장벽이다(`:171-208,254-275`). 쓰기 실패 시 재오픈 1회 재시도, 이미 쓴 접두부는 중복 기록하지 않는다(`:60-91`). 쓰이지 않은 녹화기는 빈 파일을 만들지 않는다(`:262-266`).
- **파이프라인 연결**: 공개 타입을 바꾸지 않으려고 `state.session_runtime.rollout_recorder` 자유 슬롯을 덕 타이핑으로 찾는다(`core/pipeline.py:2840-2853`). 기록 실패가 한 번 나면 그 run 의 이후 기록을 멈추고(틈 있는 감사 기록 방지) 종료 시 flush 에서 실패를 표면화해 `pipeline.error` 경로로 보낸다(`:2826-2837,2855-2874,2881-2900`). 종료 이벤트는 fsync 이후에야 버스로 나간다(`docs/rollouts.md:37-41`).
- **주의(잠재 위험, 실측 안 함)**: 동기 `record_nowait` 는 텍스트 델타가 몰릴 때 큐 256을 넘기면 실패한다. 그러면 그 턴이 `pipeline.error` 로 끝날 수 있다(`core/rollout_recorder.py:160-169`, `core/pipeline.py:2826-2837`). 실제 발생 빈도는 미확인.

### 3.2 호스트 연결·저장 위치

- 설정 `GENY_ROLLOUT_RECORDING_ENABLED` 가 참이고 `workflow_id` 가 있을 때만 켜진다. 없으면 경고 후 기록하지 않는다(`host/turn_executor.py:1018-1042`, `host/rollouts.py:22-23`).
- 경로: `<workspace_storage_root>/executor/rollouts/rollout-<UTC시각>-<sha256(interaction_id)[:16]>-<uuid>.jsonl`(`host/rollouts.py:28-47`). 최신 100개만 남기고 지운다(`host/rollouts.py:50-88`, `host/runner.py:1275-1296`).
- 녹화기는 턴 전용 이벤트 루프 안에서 만들어지고, 기존 `session_runtime` 위에 오버레이로 얹었다가 턴 끝에 되돌린다(`host/runner.py:1233-1272,1607-1609`).
- **파일 1개 = 호스트 턴 1회**다. 연속 슬라이스가 있으면 한 파일에 `run_id` 가 여러 개 들어간다(같은 오버레이로 슬라이스마다 `_init_state` 가 다시 붙임, `core/pipeline.py:3321`).

### 3.3 롤아웃만으로 할 수 있는 것과 없는 것

| 질문 | 롤아웃으로 가능? | 근거 |
|---|---|---|
| 모델 호출 수, 호출별 토큰 | 가능(`api.request`/`api.response` 개수·필드) | §1.3 |
| iteration 수, 슬라이스 수 | 가능(`pipeline.complete.iterations` 합, `run_id` 개수) | `core/pipeline.py:2493-2504` |
| 도구 호출 수·실패 수·지연 | 가능(`tool.call_*`) | `executors.py:73-155` |
| 종료 상태·사유 | 가능(`pipeline.complete.status/termination_reason`) | |
| 최종 답 텍스트 | 스트림 경로만(`pipeline.complete.result`) 또는 `text.delta` 이어 붙이기 | `core/pipeline.py:2637` |
| 모델에 실제로 보낸 요청(system·tools·messages·turn context) | **불가** — `api.request` 는 개수·플래그만 | `stages/s06_api/artifact/default/stage.py:478-488,643-675` |
| 응답 원문 블록(텍스트+tool_use+thinking 구조) | 부분 — 스트림 청크로 재구성 가능하나 비스트림·internal loop 내부 응답은 블록 없음 | `stage.py:970-1013`, `tool_loop.py:416-417` |
| 도구 결과 전문 | 불가(≤8000자 절단). 단 CLI 내부 도구 결과는 `api.tool_result.content` 에 전문 | `executors.py:91,154`, `stage.py:1004-1013` |
| 과제 식별자·하네스 버전·트라이얼 번호 | 불가(필드 없음) | `events/types.py:33-40` |
| 점수·검증 진단 | 불가(평가기 없음, §4) | |
| `llm_client.*` 협상 이벤트 | 불가(저널 밖) | §1.1 |
| 파일시스템 상태 | 불가 | §3.10 |

### 3.4 Stage 20 checkpoint

- 페이로드: `session_id, iteration, model, messages(role·content 사본), shared, metadata, loop_decision, completion_signal, completion_detail, run_status, termination_reason, resumable, total_cost_usd, token_usage{input, output, total}, final_text`(`stages/s20_persist/artifact/default/stage.py:79-100`). **system 프롬프트·tools·turn_token_usage·캐시 토큰·cost_usd 출처는 없다.**
- 레코드: `CheckpointRecord{checkpoint_id="ckpt_…", session_id, iteration, created_at, payload}`(`stages/s20_persist/types.py:19-45`). 부모 checkpoint 를 가리키는 칸이 없다.
- 저장: 기본 `NoPersister`(쓰기 없음, 스테이지 자체를 bypass, `stage.py:163-166`). `FilePersister` 는 `base_dir/<session_id>/<checkpoint_id>.json` 에 원자적 쓰기(tempfile+fsync+replace), 세션당 최신 100개 유지(`persisters.py:38-179`, `KEEP_LAST` `:154`).
- 빈도: `every_turn`/`every_n_turns`/`on_significant`. 재개 가능(suspended) 경계는 정책과 무관하게 항상 쓴다(`stage.py:168-177`).
- 복원: `state_from_payload`/`restore_state_from_checkpoint`(`stages/s20_persist/restore.py:33-99`). 토큰은 input/output 만 복원한다(`restore.py:72-77`). 런타임에 `Pipeline.resume_from_checkpoint` 는 없다(`stage.py:12-18`, `types.py:23-29`).
- **호스트 기본 조립은 Stage 20을 등록하지 않는다**(`host/runner.py:615-683` 에 `with_persist` 없음, 등록 조건 `core/builder.py:259`). 프리셋 `agent`/`geny_vtuber` 만 쓴다(`core/presets.py:274,325`).

### 3.5 `FileSessionPersistence`

세션당 파일 하나 `{root}/{session_id}/.pipeline_state.json` 을 원자적으로 **덮어쓴다**(`session/persistence.py:24-129`). v2 형식은 system·messages·턴/세션 비용·상태·캐시 포함 세션 누적 토큰·memory_refs·model·metadata 를 담는다(`:60-100`). 이력이 아니라 최신 상태 1개다. `shared`·`tools`·`turn_token_usage` 는 없다. 호스트 경로에서 쓰이는 곳은 찾지 못했다(host grep 0건).

### 3.6 연속 실행(continuation)과 상태 계약

- `CONTINUE_RUN` 은 가짜 사용자 메시지 없이 이전 슬라이스를 잇는 표지다(`core/continuation.py:1-16`). 상태가 `suspended` 일 때만 허용된다(`core/pipeline.py:3210-3216`).
- `begin_continuation_slice` 는 iteration·이벤트·도구 상태·슬라이스 결과를 비우되 **턴 토큰·비용·메시지는 유지**한다(`core/state.py:440-474`). 단 `executor.tool_calls_total` 은 비운다(`:466-474`) → 도구 호출 예산과 누적 카운터가 슬라이스 단위가 된다.
- 상태 열거: `RunStatus{running, completed, suspended, blocked, failed, cancelled}`, `TerminationReason{model_completed, max_iterations_per_slice, max_tool_calls_per_slice, wall_clock_budget, cost_budget, token_budget, context_limit, context_compaction_failed, user_input_required, error, cancelled}`(`core/run_status.py:14-38`). 루프 종료 → 상태 매핑은 `core/pipeline.py:3124-3138`.
- 호스트는 재개 가능한 슬라이스를 **기본 2번까지** 자동으로 잇는다(`host/runner.py:1306,1533-1546,1680-1682`). 문서는 아직 20번이라고 적고 있다(`docs/long_running_execution.md:42-46`). 문서가 코드보다 뒤처져 있다.

### 3.7 실행 기록(execution record)

`record_turn_execution` 은 턴마다 메모리 provider 에 두 가지를 쓴다(`host/execution_record.py:116-228`): ① daily 카드 1장(frontmatter: `session_id, execution_number, success, outcome, tool_calls, tool_failures, duration_ms`, 본문: 입력 200자·결과 1500자·오류 500자 절단, `:181-200`), ② `executions-<날짜>.md` 저널 한 줄(`:204-226`). `classify_outcome` 은 `ok`(완주+도구 실패 0+차단 0)/`partial`(완주했지만 실패나 차단 있음)/`failed`(오류·취소·미완) 셋으로 나눈다(`:52-65`).

한계:
- 토큰·비용·모델 호출 수가 없다. provider/model 은 증류 spec 의 값이라 실제 대화 모델과 다를 수 있다(`host/runner.py:1191-1192`).
- 도구 통계를 `state.events` 에서 세는데(`host/runner.py:1141-1154,1178`) `state.events` 는 슬라이스마다 비워지므로(`core/state.py:465`) **연속 슬라이스가 있으면 마지막 슬라이스의 도구만 센다.** `_turn_event_counts` 를 쓰면 턴 전체가 된다.
- 메모리 provider 가 없으면 아무것도 쓰지 않는다(`host/runner.py:1174-1176`). 저장 위치는 provider 백엔드에 달렸다(파일/SQL, 미확인).

### 3.8 대화 아카이브

`ConversationArchivingStrategy` 는 STM 기록 직후 사용자·어시스턴트 **텍스트만** 세션 노트에 이어 붙인다. 도구 왕복은 제외하고 발화당 2000자로 자른다(`host/conversation_archive.py:41,48-66,157-241`). 사람이 읽는 기록이지 재생용 기록이 아니다.

### 3.9 `history/` 패키지 — 연결되지 않은 기록·재생·A/B 도구

- `HistoryService`: SQLite 테이블 `executions`(토큰·캐시·비용·iterations·tool_calls·thinking_tokens·오류·소요), `stage_timings`, `tool_calls`(입력 10000자·출력 5000자 절단), `execution_tags` + 실행별 `events.jsonl` blob(`history/service.py:14-82,201-281`).
- `ExecutionReplayer`: blob 이벤트를 시간 간격대로 다시 흘려 주고 스테이지 브레이크포인트를 건다(`history/replay.py:12-71`). 그러나 `stage_start`/`stage_complete` 이벤트와 `data.stage_order` 를 찾는데(`:58-59,84-87,116`), 실제 엔진은 `stage.enter`/`stage.exit` 를 페이로드 없이 낸다(`events/catalog.py:335-337`). 브레이크포인트·스냅샷 기능은 실제 기록과 맞지 않는다. 또 이것은 "이벤트 재표시"이지 정책을 바꿔 다시 평가하는 재생이 아니다.
- `ABTestRunner`: 두 환경의 실행 ID 를 만들고 결과를 나란히 비교한다. 실행 자체는 외부 몫이다(`history/ab_test.py:11-95`). 반복(k) 개념이 없다.
- `CostAnalyzer.PRICING` 은 2025년 모델 3개뿐이다(`history/cost.py:15-34`).
- src 안에서 이 패키지를 쓰는 곳이 없다. 테스트만 쓴다(`tests/unit/test_phase6_history.py`, `tests/integration/test_integration.py:28-31`).

### 3.10 설정 스냅샷·차이·편집 이력

- `PipelineSnapshot`/`StageSnapshot`: 스테이지별 활성 여부·전략 이름·전략 설정·아티팩트·도구 바인딩·모델 오버라이드(`core/snapshot.py:19-125`). `EnvironmentManifest` 가 이것을 메타데이터와 함께 감싼다(`core/environment.py:628-729`). 하네스 설정의 직렬화 형식으로 쓸 만하다. 다만 호스트 `build_pipeline` 의 키워드 인자(한도·가드·빠른 경로 등)와 시스템 프롬프트가 모두 manifest 로 표현되는지는 미확인이다.
- `EnvironmentDiff.compute(a, b)`: dict 깊은 비교, `order` 키가 있는 리스트는 order 별로 비교(`core/diff.py:71-197`). 편집의 diff 표현으로 그대로 재사용할 수 있다.
- `EnvironmentManager`: `./environments/<env_id>.json` 저장, `update` 는 **제자리 덮어쓰기**다(버전·부모 없음, `core/environment.py:1698-1787`). `diff(env_a, env_b)` 제공(`:1821-1825`).
- `PipelineEnvironment`(자기수정 환경): 에이전트가 `env_*` 도구로 프롬프트·도구·스킬·설정을 바꿀 때마다 `EnvChangeEntry{seq, action, target, detail, ok}` 를 쌓는다(`core/environment_control.py:102-119,205-215`). `overlay()` 가 최종 상태+changelog 를 호스트 콜백으로 넘긴다(`:778-809`). 전후 값·부모 버전·평가 결과 연결이 없고, 콜백이 없으면 메모리에서 사라진다.
- 워크스페이스(파일시스템) 스냅샷: 런타임에는 없다. 호스트 프로토콜에 `make_sandbox`·`hydrate_workspace`·`publish_workspace` 가 있고(`host/host.py:88-100`), `save_pack` 이 호스트 콜백으로 sandbox 스냅샷을 맡긴다(`core/environment_control.py:414-480`). 구현은 레포 밖이다(미확인).

### 3.11 기록만으로 결정론적 재생이 가능한가 — 판정: **불가**

| 막는 요인 | 위치 |
|---|---|
| 모델 요청 본문이 기록되지 않는다. 실제 요청은 매 호출 만들어지는 사본(turn context 주입, 은퇴 도구 평문화, 정규화)이다 | `stages/s06_api/artifact/default/stage.py:606-675` |
| 응답 원문(`APIResponse.content`·`raw`)이 기록되지 않는다. 비스트림 응답과 internal loop 내부 응답은 블록이 남지 않는다 | `stage.py:541-565`, `tool_loop.py:416-417` |
| 도구 결과가 잘린다(8000자). 도구 부작용(파일 쓰기·명령)은 재실행하면 달라진다 | `executors.py:91,154` |
| 요청마다 바뀌는 입력: 현재 날짜·시각 블록과 검색된 기억이 turn context 로 들어간다 | `stages/s03_system/artifact/default/builders.py:176`, `stage.py:658-660` |
| 무작위 식별자: `run_id`, `pipeline_id`, checkpoint id, 아카이브 event id | `core/pipeline.py:3222,3228`, `stages/s20_persist/types.py:15-16`, `host/conversation_archive.py:176` |
| 샘플링: 기본 `temperature=0.0` 이지만 seed 를 넘기는 길이 없다(`APIRequest` 에 seed 칸 없음) | `core/state.py:165`, `llm_client/types.py:17-59` |
| 정식 녹화·재생 클라이언트가 없다. 레거시 `RecordingProvider` 는 텍스트·stop_reason·input/output 토큰만 저장(tool_use·캐시·스트림 없음)하고 짝이 되는 재생기가 없다 | `stages/s06_api/artifact/default/providers.py:317-363` |

---

## 4. 평가 — s14_evaluate 와 검증 비슷한 장치들

### 4.1 Stage 14 가 실제로 하는 일

- `EvaluateStage.execute`: 전략 → `EvaluationResult{passed, score, feedback, decision, criteria_results, metadata}` → scorer 로 빈 score 채움 → `state.evaluation_score/feedback` → `decision` 을 `loop_decision` 으로 매핑 → `evaluate.complete`(`stages/s14_evaluate/artifact/default/stage.py:78-110`, 타입 `stages/s14_evaluate/types.py:9-29`).
- 전략(`EVALUATOR_REGISTRY`, `strategies.py:427-451`):
  - `signal_based`(기본): 모델이 낸 완료 표지(`[COMPLETE]`/`[BLOCKED]`/`[ERROR]`)를 읽어 complete=1.0, blocked/error=0.0, 그 외 score 없음(`strategies.py:16-67`).
  - `criteria_based`: 호스트가 주입한 `QualityCriterion.check(state)->float` 가중 평균, 통과 기준 기본 0.6. 기준이 없으면 무조건 complete(`strategies.py:70-163`). 기준 콜러블은 manifest 로 표현할 수 없다(`:107-114`).
  - `binary_classify`: 첫 iteration 에 도구/continue 신호가 있으면 not_easy, 아니면 easy=1.0 으로 끝. 이후 신호 판정, 신호 없이 텍스트만 있으면 0.8 로 완료(`stages/s14_evaluate/artifact/adaptive/strategy.py:137-248`).
  - `evaluation_chain`: 처음으로 continue 가 아닌 판정을 낸 평가기가 이긴다. 빈 체인은 complete(`strategies.py:238-424`).
- scorer: `NoScorer` 는 늘 1.0(`strategies.py:166-174`). `WeightedScorer` 는 `state.metadata` 키 가중 평균, 키가 없으면 1.0(`:177-235`).
- **판단**: 여기 `score` 는 과제 정답과 무관한 **루프 제어 신호의 부산물**이다. 정답·기대 산출물·테스트를 입력으로 받는 길이 없다. 그리고 호스트 기본 조립은 Stage 14를 등록하지 않는다(`host/runner.py:615-683`, 등록 조건 `core/builder.py:215`). 그래서 실서비스 기록에는 `evaluate.*` 가 나오지 않고 `stage.bypass` 만 남는다.
- `PipelinePresets.evaluator` 는 "평가 프롬프트를 system 으로 쓴 LLM 파이프라인 + signal_based 평가"일 뿐이다. 구조화된 점수를 내는 judge 가 아니다(`core/presets.py:283-297`). PLAN 에 있던 `AgentEvaluation`(Generator/Evaluator 분리)은 코드에 없다(src grep 0건, `PLAN.md:73,692-697`).

### 4.2 이미 있는 결정론 검증 장치 (verifier 부품으로 재사용 후보)

| 장치 | 하는 일 | 기록 | 위치 |
|---|---|---|---|
| `DeliverableReviewer`(Stage 16 완료 검토자) | 완료 직전, 이번 턴에 Write/Edit 로 쓴 경로 + 답변에 언급된 경로를 실제로 읽어 빈 데이터 파일·깨진 JSON/JSONL·열 수가 들쭉날쭉한 CSV·사라진 파일이 있을 때만 한 번 되돌려 보낸다 | `loop.completion_review{files, missing, problems, paths}` | `stages/s16_loop/completion_review.py:300-402`, 호스트 기본 켬 `host/runner.py:711-722` |
| `describe_file`/`is_problem`/`build_digest` | 위 검토의 파일 요약 함수(공개 API) | — | `completion_review.py:237-298` |
| Bash `artifact_contracts` | 모델이 선언한 JSON(중복 키·필수 키·배열 길이)/CSV(헤더·열 수·허용값·유일키·행 수)/text(필수/금지 문구) 계약을 명령 직후 검사, 실패면 `is_error` | 도구 결과 본문과 `metadata["artifact_validation"]`(이벤트에는 결과 문자열 8000자 안에서만) | `tools/built_in/_artifact_contract.py:1-40,269-394`, `tools/built_in/bash_tool.py:230-250` |
| `StructuredOutputParser` | 최종 텍스트를 JSON 으로 파싱하고 JSON Schema 검증 | `ParsedResponse.structured_output_error` 에만. 이벤트·레코드 없음 | `stages/s09_parse/artifact/default/parsers.py:54-75,154-163` |
| `settle_structured` | 호스트 비스트림 경로 최종 스키마 검증. 실패 시 경고 로그 후 원문 반환 | 로그만 | `host/runner.py:807-833` |
| Stage 11 검토자들 | 스키마·민감 패턴·파괴적 결과·네트워크·크기 검토 | `tool_review.*` | `stages/s11_tool_review/artifact/default/reviewers.py:61-532` (호스트 미등록) |
| `classify_outcome` | ok/partial/failed 3분류 | 실행 카드 frontmatter | `host/execution_record.py:52-65` |

레포 밖에 "harness-bench/lab"(Harness-Bench 과제를 실제 호스트·격리 컨테이너로 돌리는 로컬 실험실)이 있다는 기록이 있다(`CHANGELOG.md:430,499,1401`, `stages/s16_loop/completion_review.py:3-6`). 점수 0.807/0.847 같은 채점 결과가 인용되지만, 그 채점기와 실행기 코드는 이 머신에서 찾지 못했다(**미확인**). 새 하네스의 r(x,τ)·k-trial 의 가장 가까운 선례이므로 확보할 가치가 크다.

---

## 5. 결정론적 테스트 인프라 — 오프라인 평가기·재생기에 재사용할 것

| 부품 | 형태 | 재사용 포인트 | 위치 |
|---|---|---|---|
| `BaseClient` 하위 가짜 클라이언트 | `_send(request)` 하나만 구현. 스트림은 기본 구현이 비스트림으로 떨어져 `message_complete` 1개를 낸다 | **재생 클라이언트의 정확한 이음매.** `_send` 가 받는 `APIRequest` 를 정규화·해시해 녹화 응답을 돌려주면 SDK·스트림 경로 모두 커버 | `llm_client/base.py:197-255` |
| 호스트 전 경로 오프라인 실행 | `monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: _Client(...))` + `_FakeHost` + `AgentTurnExecutor().run(host, ...)` | 실제 호스트 조립(가드·완료 검토·반복 차단·턴 예산·롤아웃)을 그대로 둔 채 LLM 만 바꿔 끼운다 | `tests/test_host_turn_executor_gates.py:51-110,644-695` |
| `HostServices` 프로토콜 | 설정·모델·키·sandbox·워크스페이스·메모리·도구 공급 | 평가용 Host 구현의 계약 | `host/host.py:66-140` |
| `MockProvider` | 미리 준비한 `APIResponse` 큐를 순서대로 반환, 요청 이력 보관, 단어 단위 가짜 스트림 | 스크립트형 정책. `APIStage(provider=MockProvider(...))` 로 끼움(레거시 어댑터 경유) | `stages/s06_api/artifact/default/providers.py:256-314`, `stage.py:117-162,217-220` |
| `RecordingProvider` | 내부 provider 를 감싸 요청/응답 일부를 녹화, JSON 저장 | 녹화 형식의 출발점(불완전, §3.11) | `providers.py:317-363` |
| `ScriptedClient` | 덕 타입 클라이언트. N번 tool_use 후 최종 텍스트, 받은 messages 기록 | internal loop·도구 왕복 시나리오 | `tests/unit/test_internal_agentic_loop.py:33-73` |
| `_FakePipeline` | 스크립트된 이벤트·상태 훅을 내는 덕 타입 파이프라인 | 호스트 집계(`turn_usage`) 단위 시험 | `tests/test_host_runner_usage.py:42-110` |
| 가짜 CLI 바이너리 | `FAKE_CLAUDE_SCENARIO` 로 성공/도구/생각/실제 와이어 모양/오류/멈춤 재현, `fake_codex.py` | CLI 백엔드 오프라인 실행 | `tests/_fixtures/fake_claude.py:1-50`, `tests/_fixtures/fake_codex.py` |
| golden 녹화 | 실제 Claude Code CLI 2.1.149/2.1.162 출력 + `.meta`(명령·버전·기대 텍스트) | "실녹화를 테스트 입력으로" 관행의 선례 | `tests/llm_client/golden/`, `test_golden_replay.py:1-60` |
| 적합성 하네스 | provider 무관 계약 시험, mocked/live 모드 | 클라이언트 교체 시 회귀 확인 | `tests/llm_client/conformance/harness.py:1-131` |
| 롤아웃 녹화 시험 | `MockProvider` 파이프라인 → JSONL 검증, 취소·백프레셔·실패 경로 | 녹화 계약 회귀 | `tests/unit/test_pipeline_rollout_recorder.py:19-60` |
| 백그라운드 작업 실행기 | `BackgroundTaskRunner`(동시성 제한·취소·재기동 시 정리) + `FileBackedRegistry`(jsonl 원장+출력 파일) | k-trial 배치 실행기의 뼈대 후보(현재 실행기 종류는 `local_bash`) | `runtime/task_runner.py:1-60`, `runtime/tasks.py:61-115,467-497`, `runtime/task_executors.py:29-58` |
| cron | `CronRunner` 가 `TaskRecord` 를 주기 제출 | 정기 회귀 평가 트리거 정도 | `cron/runner.py:1-50,162-193`, `cron/types.py:20-51` |

`notifications/registry.py` 는 이름→웹훅 URL 레지스트리뿐이다(`notifications/registry.py:13-36`). 평가 결과 알림에 쓸 수는 있으나 관측 기능은 없다. `scripts/` 에는 이벤트 문서 생성기 `gen_event_docs.py` 하나뿐이고 벤치마크·평가 스크립트는 없다.

비결정 요인을 끄는 방법(오프라인 평가 시):
- 날짜·시각 블록: 시스템 빌더를 시계 고정 빌더로 바꾸거나, 재생 해시 계산 전에 `<session-context>` 블록을 정규화한다(`stage.py:632-637`).
- 메모리: `memory_distill=False`, 메모리 provider 없음으로 조립(테스트 선례 `tests/test_host_turn_executor_gates.py:682-684`).
- 압축: `enable_compaction=False`(같은 선례). 압축을 켜면 부수 LLM 호출이 생긴다(§2.6).

---

## 6. 격차 표 — 새 설계가 필요로 하는 신호 대 현재 상태

| 필요 신호 | 현재 있음? | 위치 | 부족한 점 | 제안 |
|---|---|---|---|---|
| r(x,τ) ∈ [0,1] 검증 점수 | 없음 | `stages/s14_evaluate/artifact/default/stage.py:83-88`, `strategies.py:41-63` | s14 score 는 루프 판정 부산물(1.0/0.8/0.0/NoScorer 1.0). 과제 정답 입력 경로 없음. 호스트 미등록 | 파이프라인 밖 `Verifier` 프로토콜(`task spec + workspace + final text → {score, diagnostics}`). 결정론 부품으로 `describe_file`·`validate_artifact_contracts` 재사용. judge 를 쓰면 judge 토큰은 c(τ)와 분리 기록 |
| 검증 진단(diagnostics) | 부분 | `completion_review.py:389-402`, `_artifact_contract.py:24-36` | 이벤트에는 개수·경로만. 과제 기준 진단 없음 | Verifier 출력에 기준별 통과/실패·메시지 배열 |
| c(τ) 정책 토큰(호출 단위) | 부분 | `api.response`(`stage.py:541-560`), `turn_token_usage`(`core/state.py:193`) | internal loop 는 Stage 7에 1건으로 접힘. CLI 내부 왕복 미수집. reasoning 미분리. provider 마다 input 의미 다름 | `BaseClient` 를 감싸는 계량 래퍼에서 호출마다 `{purpose, provider, model, uncached_in, cache_write, cache_read, out, reasoning?}` 원장. `api.response` 열을 교차 검증용 정본으로 |
| c(τ) 부수 호출 분리 | 없음 | `compactors.py:257-262`, `skills/fork.py:176-180`, `host/distill.py:146` | usage 폐기 또는 다른 곳에만 | 같은 래퍼에서 `purpose` 로 분리 집계. 압축 포함 여부는 정책 결정 |
| c(τ) USD(참고용) | 부분·부정확 | `core/builder.py:157`, `pricing.py:91-210` | 비 Anthropic 0.0, `state.model` 기준, provider 보고 비용 무시, `None` 합산 과소 | 토큰 원장 → 가격은 오프라인 계산. 가격표 버전을 레코드에 |
| step 수: 모델 호출 | 부분 | `api.request` 개수, `turn_usage.calls`(`host/runner.py:1113`) | 두 값이 internal loop·CLI 에서 어긋남 | `api.request` 개수를 정본으로, CLI 는 envelope `num_turns` 수집 |
| step 수: iteration | 있음(슬라이스별) | `pipeline.complete.iterations`(`core/pipeline.py:2496`), `state.iteration` | 슬라이스마다 0부터 | 레코드에 슬라이스 합계와 슬라이스별 목록 |
| step 수: 도구 호출 | 부분 | `tool.call_start/complete`, `executor.tool_calls_total`(`s10 stage.py:428-430`), `_turn_event_counts`(`core/state.py:303`) | 누적 카운터는 슬라이스마다 리셋(`core/state.py:472`). 실행 기록은 슬라이스 단위 `state.events` 로 셈(`host/runner.py:1141-1154`) | 턴 전체 기준 `_turn_event_counts` 또는 롤아웃 집계로 통일 |
| 슬라이스(연속) 수 | 부분 | `task_progress` agent_event(`host/runner.py:1535-1544`) | PipelineEvent 가 아니라 롤아웃에 없음(`run_id` 개수로 역산만 가능) | 레코드에 `slices` 필드 |
| 종료 상태·사유 | 있음 | `pipeline.complete.status/termination_reason`, `core/run_status.py:14-38` | 호스트 단 종료(턴 예산·반복 거부)는 `state.shared` 에만(`turn_budget.py:187-192`, `repeat_stop.py:115-120`) | 레코드에 최종 사유 하나로 합쳐 기록(`model_completed / slice_cap / turn_budget / repeat_stop / blocked / error / cancelled`) |
| valid-output(형식 유효) | 부분 | `parsers.py:154-161`, `host/runner.py:829-833`, `bash_tool.py:237-240` | 스키마 검증 결과가 이벤트·레코드에 없음(로그만) | `parse.complete` 에 `valid` 추가(런타임 변경) 또는 Verifier 가 산출물 형식 검사를 별도 플래그로 |
| no-submission(제출 없음) | 부분 | `run_status`, `final_text` 비었음, `loop.completion_review.missing`, `[SUSPENDED]/[BLOCKED]/[ERROR]` 표지(`host/runner.py:1691-1702`) | 판정 정의가 없음 | `no_submission = 기대 산출물 부재 ∨ 최종 답 비었음 ∨ status ∉ {completed}` 를 Verifier 쪽에 고정 정의 |
| k-trial 반복 평가 | 없음 | `history/ab_test.py:11-95`(2방향만, 실행 외부) | 반복·시드·분산 개념 없음 | 배치 실행기(과제 × 하네스 × trial). `BackgroundTaskRunner` 를 실행기로, 레코드에 `trial_idx` |
| 잡음 대역 δ 추정 | 없음 | — | 같은 하네스 반복 평가 기능 없음 | k-trial 위에 base 하네스 반복 측정 |
| 편집 단위 이력 | 부분 | `core/environment_control.py:102-119,778-793`, `core/environment.py:1776-1787` | changelog 에 전후 값·부모·평가 연결 없음. manifest 는 덮어쓰기 | `EditRecord{edit_id, parent_id, diff(EnvironmentDiff), rationale, proposer, eval_ref}` 를 추가 전용 원장으로 |
| 하네스 버전 식별자 | 없음 | `core/environment.py:660`(구조 버전 문자열만) | 내용 해시 없음 | 하네스 설정의 정규화 JSON sha256 을 모든 레코드·롤아웃 줄에 |
| 과제·트라이얼 식별자 | 없음 | `events/types.py:33-40` | 봉투에 `session_id`/`run_id` 만 | `session_id` 에 규약을 싣거나 `session_runtime`/`metadata` 로 주입 후 레코드에 기록 |
| 재생 트리 노드(snapshot·artifact·diagnostics·score) | 없음 | `stages/s20_persist/types.py:19-45` | checkpoint 는 평면·부모 없음·호스트 미등록 | `ReplayNode` 스키마(§7.2) 새로 |
| 녹화된 LLM 응답(요청→응답) | 없음 | `providers.py:317-363`(레거시, 불완전) | 요청 본문·응답 블록·스트림 미보존 | `_send` 경계 녹화/재생 클라이언트, 정규화 요청 해시 키 |
| 도구 I/O 녹화 | 부분 | `tool.call_start.input`(전체), `tool.call_complete.result`(≤8000) | 절단. 부작용 재현 불가 | 도구 결과 전문을 내용 주소(blob)로 저장, 재생 시 도구 스텁이 녹화 결과 반환 |
| 워크스페이스 스냅샷 | 없음(런타임) | `host/host.py:88-100`, `core/environment_control.py:414-480` | 호스트 구현 미확인 | 노드 경계마다 작업 폴더 tar 해시(호스트 측) |
| 시간(지연) | 부분 | `api.ttft`, `tool.call_complete.duration_ms`, 봉투 `timestamp` | 스트림 전체 API 지연 필드 없음 | 타임스탬프 차로 파생하거나 `api.response` 에 `latency_ms` 추가 |
| 하네스 장치 작동 횟수 | 있음 | `host/harness_components.py:19-51` | 목록에 없는 장치는 안 셈 | 그대로 사용, 레코드에 포함 |
| 결정론 통제 | 부분 | `core/state.py:165`, `builders.py:176`, `stage.py:658-660` | seed 없음, 시계·기억 주입 | 평가 모드 Host: 시계 고정·메모리 끔·압축 끔 |

---

## 7. 제안 — 런타임을 고치지 않고 붙일 계측

### 7.1 이음매(seam) 목록

| 이음매 | 무엇을 얻나 | 위치 |
|---|---|---|
| `runner.build_client` 교체(테스트 선례) 또는 Host 의 클라이언트 생성 | 모든 정책 호출의 요청·응답·usage 를 가로챔(녹화·재생·계량) | `tests/test_host_turn_executor_gates.py:663-667`, `llm_client/base.py:216-225` |
| `session_runtime.rollout_recorder` 슬롯 | 엔진 이벤트 전량(순서·fsync). `record_nowait`/`flush` 만 있으면 어떤 객체든 됨 | `core/pipeline.py:2840-2853` |
| `stream_turn`/`run_turn` 의 `usage_sink`·`rollout_path` | 턴 합계 usage, 롤아웃 파일 경로 | `host/runner.py:1340-1355,1643-1654` |
| `pipeline.events(replay_from=…)` | 다중 구독 탭(저널 2048) | `core/pipeline.py:2750-2811` |
| hooks(`PIPELINE_START/END`, `PRE/POST_TOOL_USE`, `LOOP_ITERATION_END`) | 경계 시점 콜백(노드 경계 표시용) | `hooks/events.py:44-65`, `core/pipeline.py:3071-3079,2523-2529` |
| `PipelineEnvironment.overlay()`/changelog | 에이전트 자기수정 내역 | `core/environment_control.py:778-809` |
| `state._turn_event_counts` | 턴 전체 이벤트 타입별 횟수 | `core/state.py:301-303` |

### 7.2 레코드 스키마 초안

τ 하나당 하나(`TrajectoryRecord`):

```json
{
  "trajectory_id": "…", "task_id": "…", "trial_idx": 0,
  "harness_id": "sha256:…", "parent_harness_id": "sha256:…", "edit_id": "…",
  "policy": {"provider": "…", "model": "…", "thinking_level": null},
  "status": "completed", "termination_reason": "model_completed",
  "steps": {"model_calls": 7, "iterations": 6, "tool_calls": 9, "tool_errors": 1, "slices": 1},
  "tokens_policy": {"uncached_in": 0, "cache_write": 0, "cache_read": 0, "out": 0, "reasoning": null},
  "tokens_side": {"compaction": {…}, "judge": {…}},
  "cost_usd_offline": 0.0, "price_table": "2026-04",
  "verifier": {"score": 0.83, "valid_output": true, "no_submission": false, "diagnostics": [ … ]},
  "harness_components": {"completion_review": 1},
  "artifacts": ["out.csv"], "workspace_snapshot": "sha256:…",
  "rollout_path": "…jsonl", "llm_tape": "…jsonl", "timing": {"wall_ms": 0}
}
```

재생 트리 노드 하나(`ReplayNode`, Dream-RSI 노드 대응):

```json
{
  "node_id": "…", "parent_id": "…|root", "tree_id": "…", "created_seq": 12,
  "start_snapshot": "sha256:…", "end_snapshot": "sha256:…",
  "artifact": {"paths": ["…"], "final_text_sha": "…"},
  "diagnostics": [ … ], "score": 0.71,
  "trajectory_id": "…", "cost": {"policy_tokens": 0}
}
```

- 출처 매핑: `steps` 는 롤아웃의 `api.request`·`pipeline.complete.iterations`·`tool.call_*` 집계, `tokens_policy` 는 계량 래퍼(교차 검증: `api.response` 합 = 래퍼 합), `status`/`termination_reason` 은 마지막 `pipeline.complete` 와 `budget_stopped`/`repeat_stopped`, `harness_components` 는 `turn_usage()["harness"]`.
- `llm_tape` 는 `_send` 경계 녹화(정규화 요청 해시 → 응답 전체 블록+usage). 재생 시 같은 해시면 녹화 응답, 아니면 "미녹화 분기"로 표시하고 실패 처리 → 기록에 없는 정책 선택이 조용히 실제 호출로 새지 않게 한다.

### 7.3 런타임 쪽에 작게 고치면 좋은 것 (선택, 이 조사의 범위 밖)

- `tool_loop.py:422-454`: 내부 호출 usage 를 접지 말고 원장에 각각 넣기(또는 접은 건수 기록).
- `compactors.py:257-262`: `resp.usage` 를 `purpose` 와 함께 원장에 넣기.
- `parse.complete` 에 스키마 검증 결과(`valid`) 추가.
- `host/runner.py:1141-1154`: `state.events` 대신 `_turn_event_counts` 사용.
- `pipeline.complete`(run 경로)에 `total_cost_usd` 추가로 두 경로 맞추기.
- `docs/long_running_execution.md:42-46` 의 "20" 을 코드 값 2로 맞추기.

---

## 8. 미확인 항목

- 레포 밖 Harness-Bench 실험실(`harness-bench/lab`)의 채점기·실행기 코드와 점수 정의(`CHANGELOG.md:430,499,1401`).
- 호스트(xgen-workflow 등)가 `usage` 청크를 어디에 저장하는지(서버 trace·report-turn, `host/runner.py:1049-1050` 주석만 근거).
- 호스트의 워크스페이스 스냅샷·sandbox 영속 구현(`host/host.py:88-100` 프로토콜만 확인).
- 메모리 provider 실제 저장 위치(파일/SQL) — 실행 카드·저널이 어디에 남는지.
- `canvas_command` 이벤트 방출자.
- 롤아웃 큐(256) 백프레셔가 실제 스트림에서 터지는지 — 코드상 가능성만 확인.
- 벤더별 출력 토큰에 reasoning 이 포함되는지(OpenAI·Gemini·Codex) — 벤더 정의 확인 필요.
- 압축 호출 토큰을 c(τ)에 포함할지 — 논문 정의와 대조 필요.
- `EnvironmentManifest` 가 호스트 `build_pipeline` 의 모든 키워드 인자(턴 예산·반복 거부·완료 검토·빠른 경로)를 표현하는지.
