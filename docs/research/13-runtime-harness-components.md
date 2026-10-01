# 13. 기존 런타임 하네스 컴포넌트 인벤토리 (RRSI 컴포넌트 어휘 K 기준)

- 대상: `xgen-agent-runtime` 4.75.0 (`pyproject.toml:7`), `main` @ `2e015ae`, 조사일 2026-10-01
- 목적: RRSI(Regularized Recursive Self-Improvement)에서 에이전트 A=(π,H)의 하네스 H를 편집 가능한 컴포넌트 ℓ∈K = {prompt, control_flow, config, output_plumbing, context_mgmt, client_tool, skill, memory, subagent}로 분해하기 위해, **지금 코드에 있는 H** 를 컴포넌트별로 정리한다. 구조 부분집합은 K_str = {client_tool, skill, memory, subagent}다.
- 방법: 코드만 읽었다(저장소 변경 없음). 문서와 코드가 다르면 코드를 따랐고, 문서와 다른 점은 §10에 모았다. 이 저장소 밖의 호스트 구현(xgen-workflow)은 런타임과 맞닿는 지점의 동작만 확인했고, 호스트 쪽은 파일 위치 없이 동작으로만 적는다.
- 경로 표기:
  - `R/…` = `xgen-agent-runtime/src/xgen_agent_runtime/…`
  - `docs/…`, `CHANGELOG.md` = `xgen-agent-runtime/` 루트 기준
  - "미확인" = 이 저장소 안에서 확인하지 못한 항목

---

## 요약

1. **운영 경로는 매니페스트가 아니라 "턴마다 새로 조립하는 one-shot 파이프라인"이다.** `AgentTurnExecutor.run`(`R/host/turn_executor.py:172`)이 프롬프트·도구·메모리를 모으고 `build_pipeline`(`R/host/runner.py:495`)이 `PipelineBuilder`로 조립한다.
   - 21단계 중 실제로 등록되는 것은 s01·s02·s03·s04·(s05)·s06·s07·s09·s10·s16·(s18)·s21이다.
   - s08·s11·s14·s15·s17·s19·s20은 등록되지 않는다. s12·s13은 4.71.0에서 폐기됐다.
   - 그러므로 RRSI의 "현재 H" 는 매니페스트 JSON이 아니라 **턴 조립 코드 + 상수 + 노드 kwargs** 로 정의해야 실측과 맞는다.
2. **하네스 장치 대부분이 코드 상수다.** 예: 시스템 프롬프트 블록(`R/host/_constants.py`), 도구 설명(모두 인라인 문자열), 턴1 표면 `TURN_ONE_TOOLS`(`R/host/tool_exposure.py:79`), 문→가족 표 `GATES`(`R/tools/gates.py:107`), 임계값(30k prune, 0.8/0.7 압축, 반복 3/4/5/8, 턴 예산 1M/3M).
   - 데이터 파일은 `R/skills/bundled/*/SKILL.md` 10개뿐인데, 그마저 서버 턴 경로에 연결되어 있지 않다.
   - 그래도 상당수는 **이름이 붙은 단일 상수** 라서 "상수 하나 치환" 형태의 원자 편집으로 표현하기 쉽다.
3. **`host/harness_components.py` 는 레지스트리라기보다 관측 카운터다.** 장치 10개를 "층위 + 사건 이름 집합" 으로 묶어(`COMPONENTS`, `:19-33`), 턴마다 발화 횟수를 `usage.harness` 에 싣는다(`R/host/runner.py:1119-1121`).
   - 켜고 끄는 스위치나 편집 단위는 없다.
   - RRSI 컴포넌트 레지스트리로 넓힐 출발점으로 쓸 수 있다.
4. **"제안→평가→수락" 모양의 루프가 이미 셋 있다.**
   - `ForgeTool`: 등록 전에 `test_input` 으로 실제 실행해 통과해야 `verified=True` 로 노출(`R/host/forged_tools.py:779-830`).
   - 호스트 `WorkflowSelf`: draft → validate → test_run → apply → revert(호스트 구현, §4.2).
   - `PipelineMutator`: snapshot/restore/change log, `batch()` 실패 시 롤백(`R/core/mutation.py:584-605`).
   - 휴면 상태인 것: `PipelineEnvironment` + `env` 도구(프롬프트·도구·스킬 자기편집, 변경 로그; `R/core/environment_control.py:122`), `MutablePromptBuilder`(`R/stages/s03_system/artifact/default/builders.py:38`). xgen 서버(호스트)가 노출하는 도구 가족에 `environment` 가 없다.
5. **subagent 컴포넌트는 사실상 비어 있다.**
   - 4.70.0에서 위임 도구, 4.71.0에서 s12/s13과 하위 에이전트 라이브러리를 제거했다(`CHANGELOG.md:95-120`).
   - CLI 네이티브 `Task`/`Agent` 도 차단 목록으로 막는다(`R/host/runner.py:168-173`).
   - 남은 것은 연결되지 않은 `skills/fork.py` 와, LLM이 없는 `runtime/tasks.py` 뿐이다.
6. **컨텍스트 관리는 여러 겹이다.** 운영 경로에서 켜지는 것은 다음과 같다.
   - (a) 호출 전 입력 맞추기 `fit_input_to_budget`
   - (b) Stage 2 비용 트리거 결정적 prune(예상 프롬프트 > 30,000 토큰)
   - (c) Stage 2 용량 트리거 `LLMSummaryCompactor`(> 창의 80%, 목표 70%)
   - (d) Stage 4 `TokenBudgetGuard`(헤드룸 부족 → 압축 후 1회 재검사)
   - (e) s16 `TurnInputBudget` / `RepeatStop`(턴 종료 유도)
   - (f) 큰 도구 결과의 파일화(100k chars)
   - 0.8/0.7 비율은 **하드코딩** 이다(`R/stages/s02_context/artifact/default/stage.py:527,531,545`).
7. **비용 원장에 맹점이 있다.** `turn_usage`(`R/host/runner.py:1038`)는 Stage 7이 쌓은 호출별 사용량에서 `calls`, `first_call_prompt_tokens`, `max_call_prompt_tokens` 를 낸다.
   - 그러나 압축 요약(`purpose="s02.compact"`), 증류(`memory.rollup`), skill fork 호출은 Stage 6을 거치지 않으므로 이 원장에 들어가지 않는다.
   - RRSI의 비용 정규화 항을 쓰려면 이 구멍부터 메워야 한다.
8. **평가 데이터의 원천은 있지만 점수는 없다.**
   - 롤아웃 JSONL: opt-in, 최근 100개(`R/host/rollouts.py:22-23`).
   - 턴 실행 기록: ok/partial/failed 분류(`R/host/execution_record.py:52`).
   - `history/ab_test.py` 는 호출자가 없는 스캐폴드다.
9. **문서 드리프트가 크다.** docs/memory.md, hooks.md, mcp.md, long_running_execution.md("이어가기 20" ↔ 코드 2), architecture.md("Phase A 1회" ↔ 코드상 2~16이 매 반복). 새 설계의 기준선은 코드여야 한다(§10).

---

## 1. 하네스 골격 — 운영 경로에서 실제로 켜지는 것

### 1.1 조립 경로

| 순서 | 일 | 위치 |
|---|---|---|
| 1 | 노드 kwargs 수신, provider별 파라미터 검증 | `R/host/turn_executor.py:172-215` |
| 2 | 노출 정책 결정(hierarchy/flat, codex는 강제 flat) | `turn_executor.py:233-241` |
| 3 | 노드·포트 도구 어댑트, RAG 수집, 기기 도구 합류 | `turn_executor.py:284-337` |
| 4 | 이력 preload(DB Memory 포트, 텍스트만) | `turn_executor.py:379-391`, `R/host/memory.py:39` |
| 5 | 시스템 프롬프트 조립(기본 + 호스트 블록 + 상수 블록) | `turn_executor.py:406-663` |
| 6 | 내장 도구·저장 도구·자기진화 도구 등록, 입구(ToolSearch·SelfExtendGuide) | `turn_executor.py:530-671` |
| 7 | 증류 스펙, 결과 필터, CLI 표면 | `turn_executor.py:674-791` |
| 8 | 빠른 경로, 참조 파일 첨부, 입력 예산 맞추기 | `turn_executor.py:798-950` |
| 9 | 롤아웃 경로 할당 → `build_pipeline` | `turn_executor.py:1022-1095` |
| 10 | `stream_turn` / `run_turn` (자동 이어가기, usage, 실행 기록, 증류 발사) | `R/host/runner.py:1340`, `:1643` |

`build_pipeline` 이 등록하는 단계와, 등록되지 않는 단계는 다음과 같다.

| 단계 | 등록 조건 | 근거 |
|---|---|---|
| s01 Input, s06 API, s07 Token, s09 Parse, s21 Yield | 항상 | `R/core/builder.py:155-159` |
| s02 Context | **항상**(압축은 메모리 기능이 아니라는 결정) | `R/host/runner.py:650-659` |
| s03 System | 항상(`with_system`) | `runner.py:627` |
| s04 Guard | `enable_compaction=True` 일 때 `TokenBudgetGuard` 하나 | `runner.py:661-675` |
| s05 Cache | `enable_prompt_cache=True` 일 때(기본 꺼짐) | `runner.py:630-636` |
| s10 Tool | 레지스트리가 비어 있지 않을 때 | `runner.py:637-638`, `R/core/builder.py:201-210` |
| s16 Loop | 항상(`StandardLoopController(max_turns)`) | `runner.py:628`, `R/stages/s16_loop/artifact/default/stage.py:41` |
| s18 Memory | `memory_provider` 가 있을 때 | `runner.py:676-682` |
| s09 교체 | `output_schema` 가 있으면 `StructuredOutputParser` 로 | `runner.py:686-694` |
| s08, s11, s14, s15, s17, s19, s20 | 운영 경로에서 **미등록** | `R/core/builder.py:195-259`(조건부), `host/` 에 `with_*` 호출 없음 |
| s12, s13 | **폐기**(`RETIRED_STAGE_ORDERS`) | `R/core/artifact.py:115`, `CHANGELOG.md:95-120` |

CLI 백엔드(`claude_code`, `codex`)에서는 에이전트 루프를 CLI 서브프로세스가 소유한다. 이때 레지스트리는 파이프라인이 아니라 `TurnToolSurface` 로 넘어가고(`turn_executor.py:743-757`), 런타임 압축은 꺼진다(`turn_executor.py:1072`).

### 1.2 루프 구조

- 루프 경계 상수: `LOOP_START = 2`, `LOOP_END = 16`, `FINALIZE_START = 17`(`R/core/pipeline.py:832-835`).
- 루프 본체는 단계 2~16을 **매 반복** 돈다(`pipeline.py:3055-3058`). 즉 Stage 2(prune·압축), Stage 3(시스템 재조립, 카탈로그 캐시), Stage 4(가드)가 도구 왕복마다 다시 실행된다.
- `docs/architecture.md` 의 "Phase A — Setup (once per turn): 1~5" 설명과 다르다.
- 한 슬라이스는 `max_iterations`(호스트 기본 20; `runner.py:504`, `turn_executor.py:1053`)에 닿으면 `SUSPEND` 된다(`R/stages/s16_loop/artifact/default/controllers.py:121-131`).
- 호스트 브리지는 이렇게 멈춘 슬라이스를 `DEFAULT_MAX_CONTINUATION_SLICES = 2` 번까지 `CONTINUE_RUN` 으로 이어간다(`runner.py:1306`, `:1533-1545`).

### 1.3 구성 채널과 우선순위

| 우선순위 | 채널 | 운영 경로에서 쓰이는 방식 |
|---|---|---|
| 1 | 실행 1회 `ModelOverrides` (`R/core/config.py:88-157`) | 호스트 경로에서 사용 흔적 없음(미확인) |
| 2 | `PipelineMutator` / `refresh_runtime` | 호스트 경로에서 사용 안 함(턴마다 새 파이프라인) |
| 3 | `attach_runtime` 런타임 객체 | `llm_client`, `tool_context`, `memory_retriever`, `memory_strategy`, `system_builder` (`runner.py:704-790`) |
| 4 | `EnvironmentManifest` | 호스트 경로에서 사용 안 함(빌더 사용) |
| 5 | `PipelineConfig` 기본값 (`R/core/config.py:160-265`) | 빌더가 `with_model(**model_opts)` 로 덮음(`runner.py:602-628`) |

출처: `docs/architecture.md` "Configuration precedence". 실질적인 설정 표면은 **노드 kwargs** 다(§3.3).

---

## 2. `host/harness_components.py` 심층

### 2.1 정체

파일은 51줄이다. 모듈 독스트링(`R/host/harness_components.py:1-10`)의 내용:

> 가드·복구·검증·맥락 장치는 단계 코드에 흩어져 있어 "이 장치가 실사용에서 얼마나 자주 켜지나" 를 볼 곳이 없었다. 각 장치가 이미 내는 사건(events/catalog.py)을 장치 이름으로 묶어 턴 usage 페이로드(`harness`)에 싣는다.

- `COMPONENTS`(`:19-33`)는 `장치 이름 → (층위, 사건들)` 매핑이다.

| 장치 | 층위 | 세는 사건 |
|---|---|---|
| `repeat_guard` | middleware | `tool.repeat_failure`, `tool.repeat_blocked`, `tool.same_result` |
| `second_machine` | middleware | `tool.not_in_sandbox` |
| `user_denied` | middleware | `tool.user_denied` |
| `message_repair` | recovery | `input.tool_calls_repaired` |
| `api_retry` | recovery | `api.retry`, `api.stream_restart` |
| `repeat_stop` | recovery | `loop.repeat_stop` |
| `turn_budget` | recovery | `loop.turn_budget` |
| `completion_review` | verification | `loop.completion_review` |
| `context_prune` | context | `context.pruned` |
| `context_compact` | context | `context.compacted` |

- `harness_summary(state)`(`:36-51`)는 `state._turn_event_counts` 를 읽어, 0회가 아닌 장치만 `{"components": {...}}` 로 돌려준다. 작업 폴더 빠른 경로 판정(`SharedKeys.WORKSPACE_FAST_PATH`)이 있으면 함께 넣는다.
- 카운터 원천:
  - `PipelineState._turn_event_counts`(`R/core/state.py:303`): 연속 슬라이스를 가로질러 턴 단위로 유지된다.
  - `add_event` 가 type별로 1씩 올린다(`state.py:498`).
  - `begin_turn` 에서 비운다(`state.py:391`).
- 소비처: `turn_usage` → `usage["harness"]`(`R/host/runner.py:1101`, `:1119-1121`). 이 usage는 `stream_turn` 마지막 청크(`runner.py:1581`)와 `usage_sink` 로 호스트에 간다. 주간 집계는 호스트 몫이다(미확인).

### 2.2 RRSI 관점의 평가

| 질문 | 현재 상태 |
|---|---|
| 컴포넌트 목록인가? | 일부만. 10개 장치는 control_flow, context_mgmt, recovery에 걸친 **가드류** 다. prompt, client_tool, skill, memory는 들어 있지 않다. |
| 켜고 끄기·편집 단위가 있는가? | 없다. 실제 스위치는 `build_pipeline` 인자(`repeat_stop_after`, `prune_over_tokens`, `turn_input_budget_tokens`, `enable_deliverable_review`, `enable_compaction`)에 흩어져 있다(`runner.py:516-524`). |
| 비용 귀속이 되는가? | 안 된다. 발화 횟수만 센다. 장치가 추가한 토큰과 호출은 연결되지 않는다. |
| 사건으로 셀 수 없는 장치는? | 독스트링이 직접 "사건을 내지 않는 장치는 아직 셀 수 없다(llm_client 사건은 state 를 거치지 않는다)" 고 적었다(`:8-9`). |
| 빠진 사건 | 카탈로그에는 있지만 `COMPONENTS` 에 없는 것: `tool.surface_restored`, `tool.gate_reachability_repaired`(`R/events/catalog.py:213-215`), `context.compaction_requested`, `context.retrieval_timeout`, `context.short_term_window`(`catalog.py:130-136`), `guard.compacting`(`:146`), `api.internal_loop_capped`(`:185`), `loop.budget_exceeded`(`:107`). |

정리하면, 이 모듈은 **(컴포넌트 이름 ↔ 관측 신호)** 를 묶는 최소 레지스트리다. RRSI에서는 이 표를 다음처럼 넓히는 것이 자연스럽다.
- 행마다 `ℓ`(K의 원소), 편집 핸들(설정 키·상수 경로), 비용 측정치(추가 토큰·추가 호출)를 붙인다.
- `harness_summary` 를 원자 편집 평가의 **사용률 신호** 로 쓴다. 예: "이 장치는 28일간 0회 발화 → 제거 후보".

---

## 3. 컴포넌트별 인벤토리

각 절의 순서: (가) 구현 → (나) 선택·구성 → (다) 데이터/코드 구분과 원자 편집 표현 → (라) 비용 흔적.

### 3.1 prompt

**(가) 구현**

시스템 프롬프트는 **문자열 연결** 로 조립된다. `AgentTurnExecutor.run` 이 붙이는 순서:

| 순서 | 블록 | 정의 | 붙이는 곳 | 조건 |
|---|---|---|---|---|
| 0 | 사용자 `system_prompt` 또는 `default_prompt="You are a helpful AI assistant."`(31자) | `R/host/_constants.py:14` | `turn_executor.py:406-408` | 키가 없을 때만 기본값을 쓴다. `""` 는 "시스템 프롬프트 없음" 을 뜻한다. |
| 1 | `host.jobs_prompt_block()` | 호스트 | `turn_executor.py:443` | 영구 작업 도구가 있을 때 |
| 2 | `host.environment_prompt(sandbox, provider)` | 호스트 | `turn_executor.py:455-457` | 호스트가 줄 때 |
| 3 | `EFFICIENCY_PROMPT_BLOCK`(1,210자) | `_constants.py:42-60` | `turn_executor.py:459-463` | 도구가 모델에 닿고, 사용자가 프롬프트를 비우지 않았을 때 |
| 4 | `MEMORY_PROMPT_BLOCK`(659자) / `MEMORY_READONLY_PROMPT_BLOCK`(619자) / `MEMORY_AUTO_PROMPT_BLOCK`(426자) | `_constants.py:69,87,102` | `turn_executor.py:487`, `:623-631` | 쓰기 도구가 남아 있는지로 고른다(`_memory_block_for` `:108`) |
| 5 | `SELF_EVOLUTION_PROMPT_BLOCK`(301자) | `_constants.py:26-32` | `turn_executor.py:652-653` | `WorkflowSelf` 가 실제로 등록됐을 때 |
| 6 (CLI) | 숨김 목록 `deferred_catalog_text` + `cli_tool_naming_note`(182자) | `R/tools/catalog.py:48`, `_constants.py:121` | `turn_executor.py:754-757` | CLI 백엔드 |
| 6' (CLI) | "도구를 전달할 수 없다" 안내 | 인라인 | `turn_executor.py:764-778` | 브릿지가 없을 때 |
| 7 | `_schema_instruction(output_schema)` | `R/host/runner.py:448-454` | `runner.py:599-600` | 구조화 출력 노드 |

Stage 3이 그 위에 블록을 얹는다.
- 기본 빌더 `_system_builder`(`runner.py:457-468`): `ComposablePromptBuilder([CustomBlock("base"), DateTimeBlock(), TurnNotesBlock()])`.
- 메모리가 있는 턴: `[CustomBlock base, PinnedFactsBlock, DateTimeBlock, RetrievedMemoryBlock, TurnNotesBlock]`(`runner.py:782-790`).
- 블록 정의(`R/stages/s03_system/artifact/default/builders.py`):
  - `DateTimeBlock` `:176`, volatile
  - `PinnedFactsBlock` `:219`, stable, `# Pinned Facts`
  - `RetrievedMemoryBlock` `:240`, volatile, `# Relevant Knowledge`
  - `TurnNotesBlock` `:326`, volatile
  - `CustomBlock` `:354`
  - `ComposablePromptBuilder` `:369`
- Stage 3 `execute`(`R/stages/s03_system/artifact/default/stage.py:280-375`):
  - stable과 volatile을 **첫 volatile 블록에서** 자른다(`_assemble_system` `:193-261`).
  - volatile 꼬리는 기본 `volatile_placement="turn_context"`(`:38`)일 때 `state.shared["turn_context_text"]` 로 가고, Stage 6이 마지막 사용자 메시지 옆에 붙인다(`R/stages/s06_api/artifact/default/stage.py:658-660`, `_inject_turn_context` `:606`). 캐시 접두를 지키려는 장치다.
  - 숨김 목록(카탈로그)을 stable 영역 끝에 붙인다(`stage.py:284-312`). 레지스트리 version이 바뀔 때만 다시 만든다(`:70-71`, `:288-291`).
- 빌더 슬롯 레지스트리: `static` / `mutable` / `composable` / `dynamic_persona`(`stage.py:45-56`). `DynamicPersonaPromptBuilder` 는 `PersonaProvider` 를 쓴다(`R/stages/s03_system/persona/provider.py:1-17`). 운영 경로에서는 `composable` 만 쓴다.

시스템 프롬프트가 아닌 **모델에 보이는 하네스 글** 도 prompt 성격이다(편집 대상 후보).
- 루프 개입 문구:
  - 턴 예산 `_SOFT_NOTE` / `_FINAL_NOTE`(`R/stages/s16_loop/turn_budget.py:47-56`)
  - 반복 종료 `_FINAL_NOTE`(`R/stages/s16_loop/repeat_stop.py:46-52`)
  - 산출물 점검 머리말·꼬리(`R/stages/s16_loop/completion_review.py:263-271`)
- 도구 단계 안내: `second_machine` 의 `NOTE`, `FOLDER_NOTE`, `WRONG_MACHINE_NOTE`(`R/stages/s10_tool/second_machine.py:52-69`), 반복 가드 차단 글(`R/stages/s10_tool/repeat_guard.py:116-136`).
- 압축 요약 지시문(`R/stages/s02_context/artifact/default/compactors.py:239-248`), 증류 지시문(`R/memory/facts.py:119-159`, `R/memory/rollup.py:174-263`).
- 사용자 안내(모델에는 안 보임): `SUSPEND_NOTICE`, `BUDGET_NOTICE`, `REPEAT_NOTICE`(`runner.py:1310-1327`), `CLAMP_NOTICE`(`R/host/context_budget.py:214-217`).

**(나) 선택·구성**
- 노드 kwargs: `system_prompt`, `enable_memory`, `enable_self_evolution`, `output_schema`(`turn_executor.py`).
- 관리자 설정 `GENY_TOOLS_WORKFLOW_SELF_ENABLED`(`_constants.py:170-172`).
- 실행 ID 접두 `deploy_` / `guest_` 와 `_frozen`(`_constants.py:161-167`).
- Stage 3 설정 `volatile_placement`(`stage.py:117-157`).
- 블록 포함 여부는 전부 `turn_executor.py` 의 if 분기다.

**(다) 데이터/코드**
- 블록 **본문**: 모듈 수준 문자열 상수다. 코드 파일을 고쳐야 하지만, 이름이 붙은 단일 상수라서 원자 편집으로 표현하기 쉽다. 예: `Edit(ℓ=prompt, target="host._constants.EFFICIENCY_PROMPT_BLOCK", op=replace_text, payload=…)`.
- 블록 **포함·순서**: 코드 분기다. 원자 편집으로 쓰려면 "블록 목록" 을 데이터화해야 한다. 예: `prompt.blocks = [base, jobs, env, efficiency, memory, self_evolution]` 같은 순서 있는 목록 + 조건식.
- 사용자 `system_prompt`: 이미 데이터(노드 파라미터)다.
- `MutablePromptBuilder` 는 런타임 편집 API(`set_base`/`append_section`/`clear_sections`, `builders.py:82-93`)와 `get_config()` 직렬화(`:79-80`)를 이미 갖췄다. "prompt 편집 = 섹션 append/replace" 를 표현하는 기성 그릇이다(운영 경로 미사용).

**(라) 비용 흔적**
- 모든 블록은 **고정 프리픽스** 에 들어가 모델 호출마다 다시 읽힌다(비용 ≈ 프리픽스 × 호출 수; `_constants.py:37-41` 주석).
- 코드 주석의 실측: 고정 프리픽스 ≈ 8,600토큰/호출, 그중 도구 스키마 6,200(`R/tools/built_in/self_extend_guide_tool.py:5-8`). 프롬프트 블록 몫은 약 2.4k 토큰이다(추정).
- volatile 블록(날짜·검색 기억·턴 안내)은 캐시 접두 밖에 있어 캐시 재생성을 일으키지 않는다(`builders.py:176-185`, `:240-247`).
- 추가 LLM 호출은 없다.

### 3.2 control_flow

**(가) 구현** (운영 경로에서 켜지는 것은 굵게)

| 장치 | 위치 | 동작 | 기본값 |
|---|---|---|---|
| **루프 컨트롤러 `standard`** | `R/stages/s16_loop/artifact/default/controllers.py:69-133` | 다음 순서로 판정: 도구 결과 있음→계속, `complete`/`blocked`/`error` 신호, 남은 도구 호출 없음→완료, `iteration ≥ max_turns`→SUSPEND | `max_turns` = 노드 `max_iterations`(20) |
| `single_turn` / `budget_aware` / `multi_dim_budget` | `controllers.py:136-832` | 비용 0.9, 토큰 0.85, 벽시계, 도구 호출 수 차원 | 운영 미사용 |
| **자동 이어가기** | `R/host/runner.py:1306`, `:1533-1545`, `:1680-1682` | 재개 가능한 슬라이스를 `CONTINUE_RUN` 으로 | 2회(최대 3 슬라이스) |
| **완료 직전 산출물 점검** `DeliverableReviewer` | `R/stages/s16_loop/completion_review.py`; 배선 `runner.py:711-722`; 적용 `stage.py:164-174` | 이 턴에 쓰거나 언급한 파일을 읽어 MISSING/EMPTY/INVALID JSON/ragged rows를 찾으면 사용자 메시지 1개를 넣고 한 바퀴 더 돈다. LLM 호출 없음. | 턴당 1회, `MAX_FILES=12`, `MAX_READ_BYTES=2_000_000`(`:53-54`) |
| **반복 거부 종료** `RepeatStop` | `R/stages/s16_loop/repeat_stop.py:44-111`; 배선 `runner.py:731-739` | 거부된 호출이 N회 쌓이면 "도구 없이 보고" 문구 → 다음 응답에서 `complete` | `DEFAULT_STOP_AFTER=3` |
| **턴 입력 예산** `TurnInputBudget` | `R/stages/s16_loop/turn_budget.py:44-183`; 배선 `runner.py:741-749` | soft에서 "마무리" 문구, hard에서 "도구 없이 보고" → `complete` | 1,000,000 / 3,000,000 토큰(캐시 포함) |
| **반복 가드** | `R/stages/s10_tool/repeat_guard.py:38-53` | 같은 실패를 경고하다 차단, 같은 결과는 건너뛰기 | WARN 3, BLOCK 4, ANY_INPUT 5/8, SAME_RESULT 4/5 |
| **거부 가드** | `R/stages/s10_tool/denial_guard.py:36-99` | 사용자가 거부한 호출을 다시 부르면 실행 없이 `ERROR user_denied_repeat` | 턴 단위 |
| **두 기계 안내** `second_machine` | `R/stages/s10_tool/second_machine.py:38-175` | 샌드박스 도구가 기기 경로를 받으면 안내 | 턴당 2회 |
| **실행 순서** | `R/stages/s10_tool/artifact/default/stage.py:278-399` (`dispatch_calls`) | 실행 전: denial → repeat block → identical skip. 실행 후: observe. | 실행기 `sequential` 기본, 병렬도 10 |
| **문(gate) 열기** | `R/stages/s10_tool/artifact/default/routers.py:269-273` → `R/tools/gates.py:183` | Guide 호출 시 가족 activate | — |
| **API 재시도** | `R/stages/s06_api/artifact/default/retry.py:33-43` | `exponential_backoff` | 3회, 1~60s, jitter 0.1 |
| **메시지 수선** | `R/core/message_repair.py:79-232` | 짝 없는 `tool_use` 에 합성 결과, 고아 결과 제거, 중단된 호출 표시 | s01 `:93,117`, s06 `:582,663` |
| 도구 루프 위치 `tool_loop` | `R/stages/s06_api/artifact/default/tool_loop.py:70-166` | `pipeline`(기본) / `internal`(`max_inner_turns=10`) | 운영 경로는 `pipeline` |
| 권한 매트릭스, ASK→HITL | `R/permission/matrix.py:285-391`, `routers.py:620-775` | 규칙이 바인딩됐을 때만 | 운영 경로 미바인딩 |
| 훅 | `R/hooks/runner.py`; 바인딩 `R/core/pipeline.py:2075-2084` | PRE_TOOL_USE 차단·입력 수정 | 운영 경로 미바인딩(`host/` 에 `hook_runner` 없음) |
| 평가 s14, HITL s15 | `R/stages/s14_evaluate/…`, `R/stages/s15_hitl/…` | 모두 비LLM | 미등록 |

부연:
- 신호 파싱(s09)은 `[COMPLETE]`, `[BLOCKED]` 같은 정규식 기반이다(`R/stages/s09_parse/artifact/default/signals.py:15-37`). 운영 경로의 완료 판정은 사실상 "도구 호출이 없는 응답" 이다(`controllers.py:118-119`).
- `internal` 도구 루프는 `ToolDispatcher.dispatch` 를 쓰기 때문에 반복·거부·두 기계 가드를 거치지 않는다(`R/stages/s10_tool/dispatcher.py:68-85`). 운영 경로와는 무관하다.

**(나) 선택·구성**: 노드 kwargs가 `build_pipeline` 인자로 그대로 간다(`turn_executor.py:1053-1094`).
- `max_iterations`(기본 20)
- `max_continuation_slices`(기본 2)
- `repeat_stop_after`(기본 3, 0이면 끔)
- `turn_input_budget_tokens`(soft/hard, `_budget_pair` `:68-79`)
- `enable_deliverable_review`(기본 True, `tool_context` 가 있을 때만)

**(다) 데이터/코드**
- 임계값은 생성자 인자나 모듈 상수다. 노드 kwargs로 노출된 4개(`max_iterations`, `max_continuation_slices`, `repeat_stop_after`, `turn_input_budget_tokens`)는 **데이터** 다.
- 반복 가드 상수(`repeat_guard.py:38-53`), 산출물 점검 규칙(`completion_review.py:225-243`), 루프 판정 순서(`stage.py:155-184`)는 **코드** 다.
- 원자 편집 표현 제안:
  - `Edit(ℓ=control_flow, target="loop.repeat_stop.after", op=set, value=2)`
  - `Edit(ℓ=control_flow, target="loop.completion_reviewers", op=add, value="deliverable")`
- 장치를 끼우는 방법이 `add_completion_reviewer` / `set_repeat_stop` / `set_turn_input_budget` 세터로 이미 플러그인화되어 있다(`stage.py:66-75`). 그래서 "장치 on/off" 는 원자 편집으로 깔끔하게 떨어진다.

**(라) 비용 흔적**
- 산출물 점검: 개입할 때 **메인 루프 왕복 1회** 를 더한다(`runner.py:581-585` 독스트링).
- 반복 종료·턴 예산은 문구 하나를 붙여 턴을 일찍 끝내므로, 비용을 줄이는 장치다. 주석 근거: 벤치 033·087·086에서 건너뛴 호출 95·94·32회, 각 입력 300만 토큰(`runner.py:571-575`). dev 28일 p99 24.8만, 300만 초과 턴이 입력의 19%(`runner.py:586-596`).
- 이어가기는 슬라이스마다 최대 `max_iterations` 회 호출을 더한다. 기본값을 20에서 2로 낮춘 근거: 도구 120회·252만 토큰 턴(`runner.py:1300-1306`).
- 추가 LLM 호출은 없다(모두 비LLM 판정).

### 3.3 config

**(가) 구현**

| 층 | 객체 | 위치 |
|---|---|---|
| 모델 파라미터 | `ModelConfig`(model, max_tokens 8192, temperature 0.0, thinking_*, `thinking_level`) | `R/core/config.py:12-85` |
| 실행 1회 덮어쓰기 | `ModelOverrides` | `config.py:88-157` |
| 파이프라인 한계 | `PipelineConfig`(`max_iterations=50`, `cost_budget_usd`, `context_window_budget=200_000`, `stream`, `single_turn`, `artifacts`) | `config.py:160-265` |
| 단계 설정 | 각 단계 `get_config_schema` / `update_config`. 예: s02 `compaction_enabled`, `prune_over_tokens`, `background_compaction`, `retrieval_timeout_s` | `R/stages/s02_context/artifact/default/stage.py:197-292` |
| 전략 슬롯 | `StrategySlot.swap(impl_name, config)`, `SlotChain` | `R/core/slot.py:21-205` |
| 매니페스트 | `EnvironmentManifest`(stages, tools, model, pipeline, memory), `validate_manifest` | `R/core/environment.py:628`, `:1040` |
| 프리셋 | `build_manifest(preset=…)`: `worker_adaptive`, `vtuber`, `default` | `R/core/manifest_factory.py:76-80`, `:546` |
| 라이브 변형 | `PipelineMutator`(swap_strategy, update_stage_config, update_model_config, update_pipeline_config, set_stage_active, replace_stage, batch, snapshot/restore, change log) | `R/core/mutation.py:145-947` |
| 차분 | `EnvironmentDiff` | `R/core/diff.py` |
| 자기 편집 가능한 설정 | `_TUNABLE_MODEL_KEYS`, `_TUNABLE_PIPELINE_KEYS`, `_CORE_LOCKED_KEYS`(model, provider, api_key… 는 잠금) | `R/core/environment_control.py:51-67` |

**운영 경로의 실제 설정 표면 = 노드 kwargs** (`turn_executor.py`):

| kwarg | 기본 | 영향 컴포넌트 |
|---|---|---|
| `provider`, `model`, `temperature`(0.7), `max_tokens`(8192), `thinking` | — | config |
| `max_iterations`(20), `max_continuation_slices`(2) | | control_flow |
| `tool_exposure`(hierarchy/flat) | hierarchy | client_tool |
| `enable_memory`(True), `memory_distill`(True), `memory` | | memory |
| `enable_compaction`(True), `context_window`, `prune_over_tokens`(30,000) | | context_mgmt |
| `repeat_stop_after`(3), `turn_input_budget_tokens`(1M/3M) | | control_flow |
| `enable_prompt_cache`(False) | | output/cost |
| `enable_self_evolution`(True) | | client_tool / prompt |
| `enable_workspace_fast_path` | 꺼짐 | context_mgmt |
| `output_schema` | — | output_plumbing |

관리자 설정·환경변수:
- `GENY_TOOLS_*_ENABLED`(호스트가 읽는 도구 가족 스위치)
- `GENY_TOOLS_WORKFLOW_SELF_ENABLED`
- `GENY_PREFETCH_REFERENCED_FILES`(`turn_executor.py:853`)
- `WORKSPACE_FAST_PATH_ENABLED`(`R/host/workspace_fast_path.py:19`)
- `GENY_ROLLOUT_RECORDING_ENABLED`(`R/host/rollouts.py:23`)
- `GENY_IMAGE_MAX_BYTES` / `GENY_TURN_IMAGE_MAX_BYTES`(`turn_executor.py:62-63`)

**(다) 데이터/코드**
- 거의 전부 **데이터** 다(스칼라 kwargs, 매니페스트 필드).
- 단, 비율 상수 0.8/0.7/0.9(§3.5)와 헤드룸 식 `max(4096, max_tokens+2048)`(`runner.py:672`)은 코드다.
- 원자 편집: `Edit(ℓ=config, target="node.temperature", op=set, value=0.3)`. `PipelineMutator.update_model_config` / `update_pipeline_config` 가 이미 같은 모양의 API다(`mutation.py:209-252`).

**(라) 비용 흔적**: 간접적이다. `max_tokens` 는 출력 상한이자 헤드룸 계산의 입력이다. `context_window` 는 압축 트리거 기준이다(0이면 200k로 떨어져 작은 창 모델에서 400 오류; `runner.py:546-552` 독스트링). `thinking` 은 생각 토큰을 좌우한다.

### 3.4 output_plumbing

**(가) 구현**

| 장치 | 위치 | 동작 |
|---|---|---|
| **스트림 변환** `stream_turn` | `R/host/runner.py:1340-1640` | `text.delta` → str. `tool.call_*` / `api.cli_tool_call` → `agent_event` dict. 끝에 `{"type":"usage"}` 1회. 오류 → `[ERROR]` 청크. |
| 도구 결과 표시 절단 | `runner.py:60-61`, `:940-952` | UI용 4,000자(꼬리 800 유지) |
| **구조화 출력** | `_schema_instruction` `runner.py:448`; `StructuredOutputParser` 교체 `:686-694`; `settle_structured` `:807` | 스키마 지시 + 파서 검증 + 최종 JSON 정규화(비스트림만) |
| **큰 도구 결과 파일화** | `R/stages/s10_tool/persistence.py:142-205`, 상한 `ToolCapabilities.max_result_chars=100_000`(`R/tools/base.py:67`) | 전문은 `{storage}/tool-results/{id}.json`, 모델에는 앞 480자 요약 |
| **호스트 결과 필터** | `ToolContext.result_filter`(`R/tools/base.py:201`), 적용 `routers.py:500-531`, 주입 `turn_executor.py:727-729`, `runner.py:724-729` | 결과가 모델·기록·화면으로 가기 전 개인정보·금칙어 처리(fail-open) |
| CLI 도구 정의 맞춤 | `R/tools/definition.py:45-49`, `:313` | 이름 48자, 설명 2,000자, 스키마 4,000바이트 |
| 멈춤 안내 | `runner.py:1310-1338` | 예산·반복·이어가기 한도 문구 |
| 입력 절단 안내 | `R/host/context_budget.py:214`, `turn_executor.py:1166-1176` | 스트림 앞에 `CLAMP_NOTICE` |
| s17 Emit, s20 Persist, s21 포맷터 | `R/stages/s17_emit`, `s20_persist`, `s21_yield/artifact/default/formatters.py` | 운영 경로 미등록(s21은 등록되지만 `default` 는 no-op) |
| 롤아웃 기록 | `R/core/rollout_recorder.py:118`, `R/host/rollouts.py` | §4.6 |

**(나) 선택·구성**: `output_schema`, `tool_events`(기본 True), `streaming`, 호스트 훅 `tool_result_filter`. 결과 상한은 도구별 `capabilities()` 로 정한다.

**(다) 데이터/코드**: 대부분 코드다. 구조화 출력 스키마는 데이터(노드 파라미터)다. `max_result_chars` 는 도구 클래스마다 코드에 있다. 원자 편집: `Edit(ℓ=output_plumbing, target="tool.result_persist.max_chars", op=set, value=40_000)`. 다만 지금은 도구마다 값이 다르므로 전역 키를 새로 만들어야 한다.

**(라) 비용 흔적**: 파일화는 큰 결과를 480자 미리보기로 바꿔 **이후 모든 호출의 입력** 을 줄인다. `_schema_instruction` 은 스키마 크기만큼 프리픽스를 늘린다. 추가 LLM 호출은 없다.

### 3.5 context_mgmt

**(가) 구현** — 겹마다 트리거와 동작

| # | 장치 | 위치 | 트리거 | 동작 | LLM | 운영 경로 |
|---|---|---|---|---|---|---|
| C1 | 입력 예산 맞추기 `fit_input_to_budget` | `R/host/context_budget.py:101-209` | 합계(system + tools + history + text + rag) > `effective_input_budget` − 예약 | RAG 블록부터 자르고, 그다음 사용자 텍스트(최소 1024토큰, 가운데 생략) | 없음 | SDK 경로만(`turn_executor.py:897`), `enable_compaction` 이 켜졌을 때 |
| C1a | 유효 예산 식 | `R/host/token_budget.py:213-226`, `:102-103` | — | window − max_tokens − max(1024, 2%·window). 메모리가 있으면 3,000을 더 예약(`turn_executor.py:923`) | — | 〃 |
| C2 | 참조 파일 첨부(증가 방향) | `R/host/referenced_files.py:34-36` | 요청에 경로 모양 토큰이 있을 때 | 파일당 16,000B, 합계 48,000B, 최대 12개를 RAG 블록에 붙임(C1이 같이 자를 수 있음) | 없음 | 기본 켜짐(`GENY_PREFETCH_REFERENCED_FILES`) |
| C3 | 작업 폴더 빠른 경로(증가 방향) | `R/host/workspace_fast_path.py:19-22` | opt-in, 전체 ≤ 32KiB · 8파일 · 64디렉터리 | 작업 폴더 전체를 첫 요청에 실어 탐색 왕복 제거 | 없음 | 기본 꺼짐 |
| C4 | 이력 preload | `R/host/memory.py:39-74` | DB Memory 포트 | 텍스트만, system/tool 메시지 제외 | 없음 | 켜짐 |
| C5 | 단기 기억 창 | `R/stages/s02_context/artifact/default/stage.py:353-388`, `R/memory/provider.py:1301-1307` | iteration 0, preload 없음, `ContextStage._provider` 있음 | 최근 2턴은 도구까지, 그 앞 3턴은 대화만(≤ 40,000자) | 없음 | **꺼짐**: 호스트가 `provider=` 를 넘기지 않는다(`runner.py:650-659`) |
| C6 | **비용 트리거 결정적 prune** | `R/core/context_prune.py:66-234`; 호출 `stage.py:490-510` | 예상 프롬프트 > `prune_over_tokens`(기본 30,000), **매 반복** | 중복 도구 결과 → 한 줄 참조, 오래된 base64 이미지 제거, 4,000자 넘는 오래된 결과 → 앞 600자. 최근 6메시지와 메시지 수·순서·`tool_use_id` 보존 | 없음 | 켜짐 |
| C7 | **용량 트리거 요약 압축** | `stage.py:512-547`, `R/core/compaction.py:122-234` | 예상 프롬프트 > 0.8 × `context_window_budget` | prune을 먼저 돌린 뒤(`compaction.py:149-157`) `LLMSummaryCompactor`: 최근 10개는 그대로, 그 앞은 요약 1개 + "Understood" 쌍으로 교체. 목표 0.7 × 창(`target_met` 사건). | **있음**(같은 모델, `max_tokens=2048`, 입력 전사 ≤ 12,000자; `compactors.py:199-262`) | 켜짐(SDK) |
| C7a | 백그라운드 압축 | `stage.py:532-538`, `:585-699` | 0.8~0.9 구간이고 `background_compaction=True` 일 때 | 다음 턴 Stage 2에서 적용. 접두 동일성 CAS 검사. | 있음 | **꺼짐**(`runner.py:658`, one-shot이라 결과가 버려진다) |
| C8 | 요청 압축 | `controllers.py:25-44` → `stage.py:517-530` | `budget_aware` / `multi_dim` 토큰 차원 ≥ 0.85, 후속 작업 있음 | 다음 패스 시작에 동기 압축 | 있음 | 꺼짐(standard 컨트롤러) |
| C9 | **가드 압축** `TokenBudgetGuard` | `R/stages/s04_guard/artifact/default/guards.py:14-88`; 회복 `s04 stage.py:252-276`; 자동 배선 `R/core/pipeline.py:3283-3317` | `창 − 예상 프롬프트 < 헤드룸` | `action="compact"` → 같은 압축기로 1회 압축·재검사, 그래도 안 되면 거절 | 있음(C7과 같은 압축기) | 켜짐(헤드룸 `max(4096, max_tokens+2048)`, ≤ 창/2; `runner.py:672-675`) |
| C10 | 내부 도구 루프 압축 | `R/stages/s06_api/artifact/default/tool_loop.py:428-438` | > 0.8 × 창 | 목표 0.7 | 있음 | 꺼짐(`internal` 루프 미사용) |
| C11 | 사라진 도구 호출의 평문화 | `R/core/message_repair.py:260-353`; `s06 stage.py:654-657` | 이번 턴에 없는 기기 도구 | 요청 사본에서 `tool_use`/`tool_result` 를 평문으로(입력 200자, 결과 400자) | 없음 | 켜짐(폴더 도구가 없는 턴) |
| C12 | 큰 도구 결과 파일화 | §3.4 | > 100,000자 | 480자 미리보기 | 없음 | 켜짐(`storage_dir` 가 있을 때) |
| C13 | 메모리 주입 상한 | `R/memory/provider.py:1195-1205`, `:1329`; `R/memory/retriever.py:171-336` | iteration 0만(`stage.py:424-425`), 시간 상한 10s | 총 10,000자: 층별 비율(recent .40, pinned .30, ltm .20…), identity 600자는 별도 | 없음(임베딩 1회) | 켜짐(메모리 턴) |
| C14 | 턴 입력 예산·반복 종료 | §3.2 | 누적 1M/3M 토큰, 거부 3회 | 문구 → 턴 종료 | 없음 | 켜짐 |
| C15 | 요청 정규화 | `R/core/message_repair.py:79-170` | 매 요청 | 짝 맞추기 | 없음 | 켜짐 |
| C16 | `compact_chat_history` | `R/host/token_budget.py:407-478` | — | 최근 6개 그대로 + 한국어 요약 프롬프트 | 있음 | 런타임 안에 호출자 없음(다른 노드용, 미확인) |
| C17 | s02 `hybrid` / `progressive_disclosure` 전략 | `R/stages/s02_context/artifact/default/strategies.py:28-134` | — | 최근 N턴 / 요약 표지 삽입 | 없음 | 미사용(기본 `simple_load` 은 아무것도 안 함, `:23-25`) |
| C18 | s19 Summarize (`OnContextFillPolicy` 0.8, 최소 간격 5턴) | `R/stages/s19_summarize/frequency_policy.py:86-110` | — | — | 전략에 따라 다름 | 미등록 |
| C19 | CLI 백엔드 | `turn_executor.py:1072`, `docs/long_running_execution.md` "Compaction ownership" | — | 런타임 압축을 끄고 CLI가 자체 압축 | — | CLI 턴 |

토큰 추정은 두 체계다.
- 단계 내부: `estimate_prompt_tokens` = (system + messages + tools)를 문자수 ÷ 4, 이미지 1,600(`R/core/token_estimate.py:28-29`, `:106-135`).
- 호스트 예산: 공급자별 계수 × 1.05 안전계수(`R/host/token_budget.py:38-50`).

두 체계가 다르므로 C1과 C6/C7의 판정이 어긋날 수 있다(추정, 실측 미확인).

**(나) 선택·구성**
- 노드 kwargs: `enable_compaction`, `context_window`, `prune_over_tokens`.
- `ContextStage` 생성자의 `compactor` / `compaction_enabled` / `prune_over_tokens` / `background_compaction`(`R/stages/s02_context/artifact/default/stage.py:86-160`).
- 압축기 슬롯 레지스트리: `truncate` / `summary` / `llm_summary` / `sliding_window`(`stage.py:110-120`).
- 가드 헤드룸은 `build_pipeline` 의 식으로 정한다.

**(다) 데이터/코드**
- 데이터: `prune_over_tokens`, `enable_compaction`, `context_window`, 압축기 이름과 `keep_recent`(슬롯 config).
- **코드에 박힌 값**:
  - 0.8 트리거(`stage.py:531`, `tool_loop.py:430`), 0.7 목표(`stage.py:527,545`; `tool_loop.py:436`), 0.9 백그라운드 상한(`stage.py:535`)
  - prune 세부 상수(`context_prune.py:70-77`)
  - 요약 프롬프트(`compactors.py:239-248`), 요약 `max_tokens=2048`(`compactors.py:206`)
  - 전사 12,000자 절단(`compactors.py:237`)
  - 메모리 예약 3,000(`turn_executor.py:923`)
- RRSI에서 context_mgmt를 원자 편집 대상으로 삼으려면 이 값들을 `ContextPolicy` 데이터 객체로 끌어올리는 리팩터가 먼저 필요하다.
- 원자 편집 표현 예:
  - `Edit(ℓ=context_mgmt, target="context.compaction.trigger_ratio", op=set, value=0.75)`
  - `Edit(ℓ=context_mgmt, target="context.compactor", op=swap, value={"impl":"llm_summary","keep_recent":6})`

**(라) 비용 흔적**
- C6: LLM 없이 "매 호출 다시 보내는 이력" 을 줄인다. 근거 주석: 16회 이상 호출 턴은 입력의 71~72%가 이력이고, 30,000에서 5회 이하 턴은 건드리지 않으며 8회 이상 턴 14개 중 13개를 덮는다(`context_prune.py:46-51`).
- C7/C9: 발화마다 **추가 LLM 호출 1회**(출력 ≤ 2,048토큰, 입력 ≤ 12,000자 + 프롬프트). 이 호출은 `turn_usage` 에 집계되지 않는다(§7).
- C2/C3: 첫 요청 입력을 늘리는 대신 읽기 왕복을 없앤다. 근거: 과제당 도구 호출 10.8회 중 4.2회가 입력 읽기(`referenced_files.py:4-9`).
- C13: 메모리 턴의 첫 반복에 최대 약 10.6k자(≈ 2.5~3.5k 토큰)를 더하고, 임베딩 질의 1회를 쓴다.

### 3.6 client_tool

**(가) 구현**
- **Tool ABC** `R/tools/base.py:304`:
  - 필수 멤버: `name`, `description`, `input_schema`, `execute`
  - 선택 멤버: `capabilities`, `check_permissions`, `required_config_keys`(`feature:*` 게이트 `:456`)
  - 직렬화: `to_api_format` `:470`
- **ToolRegistry** `R/tools/registry.py:34`:
  - `register(core=…)` `:66`, `activate` `:128`, `list_exposed` / `list_deferred` `:162-166`
  - `version` `:59-62`: 바뀌면 Stage 3이 표면을 다시 만든다.
  - `to_api_format(exposed_only=True)` `:201`
- **내장 도구 카탈로그**: `BUILT_IN_TOOL_CLASSES` 64개(`R/tools/built_in/__init__.py:116-171`), 가족 `BUILT_IN_TOOL_FEATURES`(`:200-228`).
  - 서버(호스트)가 노출하는 가족은 `web, parsing, workflow, filesystem, shell`, 조건부로 `ssh, audio` 다.
- **계층 노출(점진 공개)**:
  - 턴1 표면 화이트리스트 `TURN_ONE_TOOLS`(`R/host/tool_exposure.py:79-154`): Bash/Read/Write/Edit/Glob/Grep, ToolSearch, ToolBatch, memory_* 6개, JobGuide, AppGuide, SelfExtendGuide, WebFetch, WebSearch, BrowserGuide, LocalControl, SshListServers.
  - 판정 함수 `registers_core(name, flat)`(`:176`). `hierarchy` / `flat` 은 `normalize_exposure`(`:41`)가 정한다.
  - 문(gate) → 가족 표 `GATES`(`R/tools/gates.py:107-128`). 도달성 자동 수선(`reachability_fixes` `:199`)과 이력 기반 재개방(`restore_from_history` `:256`)은 Stage 3이 표면을 굳히기 직전에 실행한다(`R/stages/s03_system/artifact/default/stage.py:344-349`).
  - `ToolSearch`(`R/tools/built_in/tool_search_tool.py:147`): 기본 10개, 최대 100개, 모든 토큰 일치, 검색에 걸린 도구를 activate(`:283-287`).
  - 숨김 목록 글 `deferred_catalog_text`: 한 줄 72자, 전체 4,000자 상한(`R/tools/catalog.py:27-29`).
  - 입구 보장 `ensure_surface_entrances`(`R/host/runner.py:471-492`).
- **도구 정의 맞춤**: `R/tools/definition.py`(`safe_tool_name` `:80`, `normalize_input_schema` `:232`, `fit_definition` `:313`). SDK와 CLI가 같은 `api_definition`(`:338`)을 쓴다.
- **CLI 표면** `TurnToolSurface`(`R/host/tool_surface.py:34`): 같은 레지스트리와 같은 `ToolStage.dispatch_calls`(`:148`)를 MCP 브릿지로 노출한다. CLI 네이티브 도구는 `CLI_NATIVE_TOOLS_DENY`(`R/host/runner.py:145`)로 전면 차단한다.
- **노드·포트 도구 어댑터** `adapt_tools`(`R/host/tools.py:341`): LangChain, callable dict, 스킬 페이로드의 `dispatch_tool`. 기기 도구 래퍼(`R/host/device_tools.py:153`). MCP 어댑터 이름 `mcp__{server}__{tool}`(`R/tools/mcp/adapter.py:14`).
- **ToolBatch**(목록 작업을 한 왕복에; 턴1 표면)와 `EFFICIENCY_PROMPT_BLOCK` 이 짝을 이룬다.
- **도구 설명은 전부 인라인 Python 문자열** 이다. `tools/` 밑에 별도 설명 파일은 없다.

**(나) 선택·구성**
- 노드 kwargs: `tools`, `tool_exposure`, `local_folders`, `client_surface`, `enable_memory`, `enable_self_evolution`.
- 관리자 차단: `GENY_TOOLS_*_ENABLED`.
- `feature:*` 게이트(`R/core/pipeline.py:762`)는 매니페스트 경로에서만 적용된다.
- codex는 턴 안에서 도구 목록을 다시 읽지 않으므로 강제 flat이다(`turn_executor.py:38`, `:234-241`).

**(다) 데이터/코드**
- 표면 정책(`TURN_ONE_TOOLS`, `GATES`)은 **코드 안의 데이터 구조**(frozenset/dict)다. 집합 원소 추가·삭제로 원자 편집을 표현하기 아주 좋다. 예: `Edit(ℓ=client_tool, target="exposure.turn_one", op=remove, value="TableExport")`. 실제로 4.x에서 TableExport를 턴1에서 뺀 이력이 있다(`tool_exposure.py:89-90`).
- 도구 **설명과 스키마**: 인라인 문자열이므로 코드 편집이다. 그래도 도구 이름 단위로 주소를 매길 수 있다(`description(tool=X)`).
- 도구 **추가**: 새 클래스 작성이므로 코드다. 예외는 ForgeTool로, 스크립트 + 스펙 JSON을 DB에 저장하는 **데이터** 경로다(§4.1).

**(라) 비용 흔적**
- 턴1 스키마는 **모든 호출의 입력** 에 들어간다.
- 실측(이번 조사, `api_definition` JSON 문자수): Bash 2,318, Grep 977, ToolBatch 1,004, ToolSearch 946, WebFetch 721, Edit 699, Read 651, WebSearch 533, SelfExtendGuide 532, Write 499, Glob 439. 합계 약 9.3k자이고, 메모리 도구 6개 약 2.4k자를 더한다.
- 코드 주석의 실측: 도구 스키마가 프리픽스의 72%(6,200/8,600 토큰). 자기확장 도구 6개 2,854토큰을 문 하나(약 110토큰)로 바꿨다(`self_extend_guide_tool.py:5-16`).
- 숨김 도구는 카탈로그 한 줄만 쓰는 대신, 쓰는 턴에 왕복 1회(ToolSearch 또는 Guide)가 는다.

### 3.7 skill

이 코드베이스에서 "skill" 은 **네 가지** 를 뜻한다. RRSI의 `skill` 컴포넌트로 무엇을 삼을지 먼저 정해야 한다.

| 뜻 | 구현 | 운영 경로 | 위치 |
|---|---|---|---|
| (a) SKILL.md 스킬(Claude Code 식) | `parse_skill_file`, `load_skills_dir`; `SkillTool` 은 스킬 1개 = 도구 1개(이름 = skill id, 설명에 `[skill, mode]`); `allowed_tools` 부여; inline/fork 모드; 번들 10개(batch, debug, environment, loop, lorem-ipsum, simplify, skillify, stuck, tool-builder, verify) | **미연결**: `skills/` 밖에서 `SkillToolProvider` / `load_bundled_skills` 를 부르는 곳이 없다 | `R/skills/loader.py:50,71,307`, `R/skills/skill_tool.py:117-207`, `R/skills/bundled/*/SKILL.md` |
| (b) 자기 작성 스킬 | `env` 도구의 `create_skill` / `edit_skill` / `enable_skill` → `PipelineEnvironment`(세션 범위, `save_pack` 으로만 영속) | 미연결(environment 가족 미노출) | `R/tools/built_in/env_tools.py:34-72`, `R/core/environment_control.py:484-610` |
| (c) **문(Guide) + 가족 = "Tool Set = Skill"** | `SKILL_GATEWAYS`(`R/tools/built_in/__init__.py:193`), `GATES` 표, 호스트 소유 스킬 도구(`host.build_host_skill_tools`: AppGuide 등), JobGuide | **운영 중** | `R/tools/gates.py:107-128`, `R/tools/built_in/_skill_gateway.py:53-59` |
| (d) xgen 노드 스킬 페이로드 | 호스트의 스킬 포트 값 `SkillPayload{skill_id, manifest_md, dispatch_tool}` | `dispatch_tool` 만 쓰고 **`manifest_md`(프롬프트 블록)는 버린다** | `R/host/tools.py:315-326`(`R` 전체에 `manifest_md` 참조 없음) |

**(나) 선택·구성**
- (c)의 문은 `TURN_ONE_TOOLS` 에 올라가고 방(가족)은 deferred로 등록된다. 문을 부르면 라우터가 가족을 연다(`R/stages/s10_tool/artifact/default/routers.py:273`).
- `HostSelections.skills=["*"]`(`R/core/environment.py:174`)는 "라이브러리가 적용하지 않는다" 고 명시되어 있다(`:153-169`).

**(다) 데이터/코드**
- (a)는 **데이터**(SKILL.md 프런트매터 + 본문)다. RRSI 원자 편집에 가장 잘 맞는 형식인데, 지금 서버에서는 죽어 있다.
- (c)는 코드다. 문 도구의 설명·지도 문자열(예: `_MAP`, `self_extend_guide_tool.py:38`)과 `GATES` 의 한 줄로 이루어져 있다.
- 원자 편집 표현 예:
  - `Edit(ℓ=skill, target="gates.SelfExtendGuide.family", op=add, value="PythonEnv")`
  - (a)를 되살린다면: `Edit(ℓ=skill, target="skills/verify/SKILL.md", op=replace_section, …)`

**(라) 비용 흔적**
- (c): 문 하나는 약 100토큰이다(설명 + 빈 스키마). 열면 가족 스키마가 다음 호출부터 실리고 왕복 1회가 는다.
- (a): 스킬마다 도구 정의 1개, fork 모드는 **별도 LLM 호출** 이다(`R/skills/fork.py:145-150`). 운영 비용은 0이다(미연결).

### 3.8 memory

**(가) 구현** (운영 경로 기준)

- **공급자**: `host.build_memory_provider(workflow_id, interaction_id)`(호스트; `turn_executor.py:481`). 계약은 `MemoryProvider`(`R/memory/provider.py:1139`)이고, 층은 STM, LTM, notes, vector, index, curated, global이다(`docs/MEMORY_SPEC.yaml:22-64`).
- **3갈래 배선**(`R/host/runner.py:751-799`). 하나라도 빠지면 기억이 조용히 죽는다는 주석이 있다.
  1. Stage 2 `retriever` ← `MemoryAwareRetriever(provider)`(`R/memory/retriever.py:133`)
     - 층: L0 recent_turns, L1 summary, identity card, pinned, vault map, LTM, vector, keyword, backlink, curated
     - 총 10,000자 예산(`provider.py:1329`). iteration 0에만 실행.
     - 결과는 `state.metadata["memory_pinned"/"memory_context"]` 로 간다(`s02 stage.py:447-464`).
  2. Stage 18 `strategy` ← `ConversationArchivingStrategy`(`R/host/conversation_archive.py:75`): STM `record_turn` + vault 대화 노트에 append(발화당 2,000자).
  3. Stage 3 `builder` ← Pinned/Retrieved 블록(§3.1).
- **자기 관리 도구 6개**(`R/host/memory_tools.py:116`): `memory_write`, `memory_pin`, `memory_read`, `memory_list`, `memory_search`, `memory_categories`. 모두 턴1 core다.
- **턴 종료 실행 기록**(`R/host/execution_record.py:116`): `daily` 카드와 `executions-<day>.md` 저널에 ok/partial/failed를 남긴다. 동기 실행, 상한 10초(`runner.py:1157-1203`).
- **턴 종료 증류**(`R/host/distill.py:240`): `_close_memory_provider` 가 백그라운드 데몬 스레드로 발사한다(`runner.py:1220-1230`). 워크플로당 in-flight 1개.
  - ① `FactExtraction`: 원자 사실 원장, `FACT_EXTRACTION_SCHEMA`(`R/memory/facts.py:69-116`)
  - ② `MemoryRollup`: 롤링 다이제스트(`R/memory/rollup.py:301`)
  - ③ daily: 코드 렌더
  - ④ evergreen: 5 pass마다(`distill.py:34`, `:143`)
- **운영 경로에서 꺼진 것**:
  - `ContextStage` / `MemoryStage` 의 `provider=` 를 호스트가 넘기지 않으므로, `MemoryStage._drive_provider`(`record_execution` / `reflect` / `promote`; `R/stages/s18_memory/artifact/default/stage.py:229-277`), 공급자 `retrieve` 병행, 단기 기억 창이 돌지 않는다.
  - `run_compaction` 의 `record_compaction` 도 공급자가 없어 건너뛴다(`R/core/compaction.py:203-208`).
  - s18 `reflective` 전략은 플래그만 세우고(`strategies.py:46-62`), 저장소 안의 `reflect()` 구현은 전부 `()` 를 돌려준다.

**(나) 선택·구성**
- kwargs: `enable_memory`(True), `memory_distill`(True; codex는 건너뜀 `turn_executor.py:674-686`), `memory`(이력 preload).
- 호스트 훅: `memory_write_available`(`:116-118`), `record_failed_starts`(`runner.py:1125-1138`).
- 매니페스트 경로에서는 `EnvironmentManifest.memory`(`R/core/environment.py:667`) → `provider_from_manifest_memory`(`R/memory/factory.py:208`).
- `MemoryHooks` 기본값(`provider.py:1217-1343`): `max_inject_chars` 10000, `max_results` 5, `window_*` 등.

**(다) 데이터/코드**
- 기억 **내용**(노트·사실·핀)은 **데이터** 다(vault 파일·DB).
- 주입 예산과 층 비율은 `MemoryHooks` dataclass 필드다. 데이터화하기 쉬운 코드다.
- 증류 지시문과 스키마는 인라인 상수다(`facts.py:119-169`, `rollup.py:49-263`).
- 배선 3갈래와 증류 케이던스는 코드다.
- 원자 편집 표현 예:
  - `Edit(ℓ=memory, target="memory.hooks.max_inject_chars", op=set, value=6000)`
  - `Edit(ℓ=memory, target="memory.distill.evergreen_every", op=set, value=10)`
  - `Edit(ℓ=memory, target="prompt.memory_block", op=replace_text, …)` (prompt와 겹친다)

**(라) 비용 흔적**
- 컨텍스트: 시스템 접두에 메모리 블록 0.4~0.66k자와 `# Pinned Facts`(≤ 3,000자), 턴 맥락에 `# Relevant Knowledge`(남은 예산 안), 도구 정의 약 2.4k자.
- 추가 LLM 호출: 증류가 **턴당 2회 + 5턴마다 1회**, 응답 후 백그라운드다(`distill.py:14-15`).
  - 롤링 다이제스트는 커서가 없어서 직전 다이제스트 + 최근 STM 최대 300턴을 매번 다시 보낸다. 세션이 길수록 입력이 커진다(`rollup.py:317-352`, 서브조사 결과).
- 임베딩: 턴당 질의 1회(L3), 노트 쓰기마다 전문 재임베딩(파일 스토어; `R/memory/providers/file/notes_store.py:187-193`).
- 증류 호출은 `turn_usage` 에 집계되지 않는다(LLM 클라이언트를 호스트가 따로 만든다; `R/host/host.py:204`, 집계 여부 미확인).

### 3.9 subagent

**(가) 구현** — 현재 상태는 "**없음**"

- 제거 이력:
  - 4.70.0: 위임 도구 `Agent`, `SubAgent*`, `Task*`, `DelegationGuide` 와 그 가족·문·훅(`CHANGELOG.md:164-212` "Removed").
  - 4.71.0: `stages/s12_agent` 전부, s13 단계, `with_agent` / `with_task_registry`, 매니페스트 `subagents` 섹션, `PipelineState.delegate_requests` / `agent_results`, s09 `CompletionSignal.DELEGATE`(`CHANGELOG.md:95-120`).
  - 플랫폼 결정: "하위 에이전트 오케스트레이션은 다시 들이지 않는다"(`CHANGELOG.md:99-100`).
  - `stages/s12_agent/`, `s13_task_registry/` 디렉터리에는 `__pycache__` 만 남아 있다.
  - 저장된 매니페스트의 해당 항목은 경고 한 줄과 함께 빠진다(`docs/architecture.md` "Retired slots").
- CLI 백엔드: Claude Code의 `Task`, `Agent`, `ListAgents`, `TaskOutput`, `TaskStop` 을 네이티브 차단 목록으로 막는다(`R/host/runner.py:168-173`). Codex도 하위 에이전트 기능을 끈다(`CHANGELOG.md` 4.70.0 "CLI 네이티브 도구 끄기").
- 남은 유사물:
  - `skills/fork.py`: fork 모드 스킬을 "격리된 하위 에이전트" 로 돌리는 러너(`R/skills/fork.py:1-14`). 기본 러너는 Anthropic 단일 호출이다(`:83-185`, 기본 모델 `claude-sonnet-4-6` `:79`, 출력 4,096). **미연결**(§3.7 (a)).
  - `runtime/tasks.py` + `BackgroundTaskRunner`(`R/runtime/task_runner.py:47`): `LocalBashExecutor` 같은 **비LLM** 백그라운드 작업이다(`R/runtime/task_executors.py:47`). 호스트 Job/Trigger/Cron은 별개로 남아 있다.

**(나) 토큰 회계**
- 폐기 전 구조는 이 저장소에서 더는 확인할 수 없다. 제거된 코드의 회계 방식은 미확인이다.
- fork 러너는 사용량을 **`ToolResult.metadata` 에만** 넣는다(`fork.py:172-178`). Stage 7의 `turn_token_usage`(`R/stages/s07_token/artifact/default/trackers.py:26,46`)에는 들어가지 않는다. 되살리면 비용 원장에서 빠진다.

**(다) 데이터/코드**: 지금은 편집할 대상이 없다. 새로 들인다면 **코드 + 데이터(하위 에이전트 정의)** 가 함께 필요하다.

**(라) 비용 흔적**: 현재 0. 플랫폼 결정을 바꿔야 하는 영역이므로, RRSI 설계에서 `subagent` 를 K_str에 둘지는 정책 판단이 필요하다(§9).

---

## 4. 기존 자기개선·자기진화 메커니즘

"제안 → 평가 → 수락" 형태로 정리했다.

### 4.1 ForgeTool — 등록 전 실행 검증 게이트 (운영 중)

- **제안**: 에이전트가 workspace에 스크립트를 쓰고 `ForgeTool(name, description, entrypoint, input_schema, dependencies, test_input, …)` 을 부른다(`R/host/forged_tools.py:573-676`). 계약은 stdin JSON → stdout JSON, 실패는 `{"error": …}` 또는 0이 아닌 종료 코드다(`:8-13`).
- **평가**:
  - 정적 검사: 이름 정규식, 실행기 allowlist, 경로(`validate_spec` `:185`, 호출 `:733`).
  - 의존성 격리 환경 구성과 핀 고정(`:755`).
  - **실행 테스트**: `run_forged_tool_test`(`:999-1025`)는 에이전트가 부를 때와 **같은 `RegistryRouter`**(입력 jsonschema 검증 포함), 같은 샌드박스로 1회 실행한다. 시간 상한은 120초(`:88`).
- **수락/거절**:
  - 통과 시 `verified=True` → 등록(`:785`, `:830`).
  - 실패 시 미검증 초안으로만 저장하고 노출하지 않는다. 실패 출력과 고칠 점을 돌려준다(`:805-828`).
  - 다음 세션 복원 때도 `enabled and verified` 만 등록한다(`:1071`, `:1140`).
  - 사람의 [테스트] 버튼도 같은 함수를 쓴다(`:1009-1011` 독스트링).
- **영속**: `ForgedToolSpecStore` 포트(서버 = DB, 로컬 = RPC; `:41-58`). `calls` / `errors` / `last_error` 통계가 있다(`:117-121`, `_record` `:384`).
- **노출**: `SelfExtendGuide` 문 뒤에 있다(`R/tools/gates.py:118-123`). 쓰인 턴은 1.4%다(`self_extend_guide_tool.py:7-8`).
- RRSI 시사점: "**client_tool 원자 편집 + 단위 테스트 게이트 + 사용 통계**" 가 이미 한 묶음으로 있다. 하네스 편집 일반으로 넓히기 좋은 본보기다. 다만 평가는 "돌아가는가" 에 그치고, "좋아졌는가"(과제 성공·비용)는 보지 않는다.

### 4.2 WorkflowSelf — 그래프 자기편집 (운영 중, 호스트 소유)

- 위치: 호스트 구현(xgen-workflow). 런타임 쪽은 등록 훅(`R/host/host.py:143`)과 정책(`R/host/_constants.py:152-175`)만 가진다.
- 행동: `guidance, graph, find_nodes, node_spec, add_node, attach, connect, set_param, remove_node, disconnect, validate, test_run, apply, discard, history, revert`.
  - 쓰기는 **프로세스 전역 드래프트** 에 쌓는다.
  - `apply` 는 compile 통과 시에만 커밋한다.
  - `test_run` 은 기본으로 부작용 없는 사전 검사를 하고, `live=true` 면 실제로 실행한다.
  - `revert` 는 이전 버전을 **새 버전으로** 되살린다.
- 정책: `deploy_` / `guest_` / 고정본 / `workflow_id` 없음 / 관리자 off에서는 차단한다(`_constants.py:161-175`).
- 커밋된 그래프는 **다음 턴** 부터 반영된다(턴마다 그래프를 다시 읽어 도구를 조립).
- RRSI 시사점: 하네스의 **도구 배선(client_tool / skill)** 을 바꾸는 제안 → 정적 검증 → 시험 실행 → 커밋 → 버전 되돌리기가 모두 있다. 변경 사유(`reason`)도 남긴다. 평가가 "컴파일 가능 + 시험 실행 성공" 에 그치는 점은 ForgeTool과 같다.

### 4.3 PipelineEnvironment + `env` 도구 — 하네스 자기편집 (휴면)

- `R/core/environment_control.py:1-24`: 세션이 **자기 운영 환경**(시스템 프롬프트, 활성 도구, 활성 스킬, 조정 가능한 모델·파이프라인 설정)을 런타임에 편집한다. 변경은 **다음 턴** 에 반영된다.
- 행동(`R/tools/built_in/env_tools.py:34-72`): `view, get_prompt, set_prompt, append_prompt, enable_tool, disable_tool, enable_skill, disable_skill, create_skill, edit_skill, forge_tool, save_pack, get_settings, set_setting, get_config, set_config, changelog, save`.
- 불변식:
  - 이미 제공된 도구·스킬 안에서만 켜고 끌 수 있다(`environment_control.py:20-23`).
  - model, provider, api_key는 잠금이다(`:67`).
  - 변경마다 `EnvChangeEntry(seq, action, target, detail, ok)` 를 남긴다(`:103-120`).
  - 영속은 호스트 콜백 `env_persistence` 가 맡는다(`:34-37`, `R/core/pipeline.py:1991-1995`).
- 프롬프트 편집 대상: `MutablePromptBuilder`(`builders.py:38-143`)의 `set_base` / `append_section`.
- **운영 상태**: `environment` 가족(`R/tools/built_in/__init__.py:220`)이 xgen 서버(호스트) 노출 가족에 없고, `build_pipeline` 은 `MutablePromptBuilder` 를 쓰지 않는다. 그래서 휴면이다. Geny 계보의 기능이다.
- RRSI 시사점: K의 prompt, client_tool, skill, config 원자 편집을 **행동 이름 + 변경 로그** 로 표현하는 API가 이미 있다. RRSI 편집 연산자의 1차 구현체로 재활용할 수 있다. 다만 지금 구조는 "에이전트가 스스로 편집" 이므로, RRSI의 "외부 최적화기가 편집하고 정규화된 평가로 수락" 과는 주체와 게이트가 다르다.

### 4.4 PipelineMutator · Snapshot · Diff — 설정 수준 원자 편집과 롤백 (라이브러리)

- `PipelineMutator`(`R/core/mutation.py:145`):
  - 연산: `swap_strategy`, `update_stage_config`, `update_model_config`, `update_pipeline_config`, `set_stage_active`, `update_strategy_config`, `replace_stage`, `reorder_chain` / `add_to_chain` / `remove_from_chain`, `register_hook`, `bind_tool_to_stage`, `set_stage_model`(`:156-584`).
  - 연산마다 `MutationRecord` 를 남긴다(`get_change_log` `:839`).
  - `batch()` 는 snapshot 체크포인트를 잡고, 예외가 나면 `restore` 하고 로그를 잘라 낸다(`:584-605`).
  - 실행 중인 단계를 바꾸려 하면 `MutationLocked` 다.
- `PipelineSnapshot` / `EnvironmentDiff`(`R/core/snapshot.py`, `R/core/diff.py`): 두 환경의 깊은 비교.
- 운영 경로는 턴마다 파이프라인을 새로 만들기 때문에 쓰지 않는다.
- RRSI 시사점: "**원자 편집 = 기록된 MutationRecord, 거절 = restore**" 패턴이 그대로 있다. `ℓ` 태그만 붙이면 config / control_flow / context_mgmt 편집의 실행기로 쓸 수 있다.

### 4.5 메모리 증류·실행 기록 — 경험 축적 (운영 중)

- 증류(§3.8): "LLM이 판단하고, 스키마가 구속하고, 코드가 저장한다. 스키마 위반이나 실패는 이전 상태를 절대 훼손하지 않는다"(`R/host/distill.py:18-19`).
  - 사실 원장의 `upserts` / `supersedes` 는 **기억 데이터에 대한 제안 → 스키마 검증 → 적용** 루프다. 하네스 편집은 아니다.
- 실행 기록: `classify_outcome`(`R/host/execution_record.py:52-65`).
  - ok = 끝까지 돌았고 도구 실패·반복 차단이 없음
  - partial = 끝까지 돌았지만 실패나 차단이 있음
  - failed = 오류·취소·미완료
  - 도구 통계는 `_tool_stats_from_events`(`R/host/runner.py:1141-1154`)에서 온다.
  - 이 라벨은 RRSI 평가의 **약한 보상 신호**(과제 단위)로 쓸 수 있다.
- s18 `StructuredReflectiveStrategy` / `insight.py`(`R/stages/s18_memory/insight.py:33-117`): 대기열에 들어온 Insight를 정규화하는 그릇이다. 운영 경로 미사용.

### 4.6 롤아웃 기록 (opt-in)

- `GENY_ROLLOUT_RECORDING_ENABLED=true` 일 때(`R/host/rollouts.py:23`, 판정 `R/host/turn_executor.py:1028-1043`) 턴 하나를 JSONL 파일 하나로 남긴다. 경로는 `<storage_root>/executor/rollouts/rollout-<UTC>-<sha256(interaction)[:16]>-<uuid>.jsonl`(`rollouts.py:28-47`).
- 최근 100개만 보관한다(`ROLLOUT_KEEP_LAST` `:22`, `prune_rollout_files` `:50-88`).
- 기록기 `RolloutRecorder`(`R/core/rollout_recorder.py:118`):
  - 단일 writer task, bounded queue 256
  - terminal 사건은 fsync한 뒤 발행
  - 기록이 실패하면 `pipeline.error` 로 턴 실패(`docs/rollouts.md:37-41`)
  - 기존 `session_runtime` 위에 겹쳐 붙인다(`R/host/runner.py:1233-1300`).
- 내용: 파이프라인 사건 스트림 전체(프롬프트, 응답, 도구 입출력 포함 가능; `docs/rollouts.md:16-18`).
- RRSI 시사점: 같은 입력을 다른 H로 다시 돌리는 **오프라인 재생·비교** 의 원자료가 된다. 다만 보관이 100개뿐이고, 결과 라벨이 없다.

### 4.7 history/ — A/B 실행 스캐폴드 (미사용)

- `ABTestRunner.create_test(env_a_id, env_b_id, user_input)` 는 두 실행 ID를 만들고 "실행은 외부에서" 맡긴다(`R/history/ab_test.py:11-56`). 저장은 `HistoryService`(SQLite)이고, `ExecutionReplayer` / `DebugExecutor` 도 있다(`R/history/replay.py:12`, `:133`).
- `history/` 밖에서 부르는 곳이 런타임 안에 없다. Geny 계보로 보인다.
- RRSI 시사점: "두 환경, 같은 입력, 비교" 라는 **평가 하네스의 뼈대** 는 있다. 실행·채점·정규화는 비어 있다.

### 4.8 요약 표

| 메커니즘 | 편집 대상(ℓ) | 제안자 | 평가 게이트 | 수락·롤백 | 운영 |
|---|---|---|---|---|---|
| ForgeTool | client_tool | 에이전트 | 스키마 + 실제 1회 실행 | verified 플래그 / 미노출 초안 | ○ |
| WorkflowSelf | client_tool, skill(그래프 노드) | 에이전트 | compile + test_run | apply / discard / revert(버전) | ○ (호스트) |
| PipelineEnvironment(env) | prompt, client_tool, skill, config | 에이전트 | 없음(범위 제한만) | changelog, `save` 콜백 | × (휴면) |
| PipelineMutator | config, control_flow, context_mgmt(슬롯) | 호스트 코드 | 없음 | change log, batch restore | × (운영 경로 미사용) |
| 증류 | memory(데이터) | LLM | JSON 스키마 | 실패 시 이전 상태 유지 | ○ |
| 실행 기록 | — (평가 신호) | 코드 | — | — | ○ |
| 롤아웃 | — (재생 데이터) | 코드 | — | — | opt-in |
| ab_test | 환경 전체 | 사람 | 없음 | — | × |

---

## 5. 서브에이전트·위임 상세

§3.9에서 다뤘다. 설계에 필요한 점만 덧붙인다.

- 이벤트 카탈로그는 추가만 한다는 규칙 때문에 `agent.*`, `subagent.*`, `task.*`, `task_registry.*` 는 `RETIRED_EVENT_TYPES` 로 남아 있다(`CHANGELOG.md:127-128`, `EVENT_CATALOG_VERSION = 16` `R/events/catalog.py:67`). RRSI가 하위 에이전트를 다시 들이면 이 이름 공간을 재활용할지 결정해야 한다.
- 위임이 없으므로 현재의 "작업 분해" 수단은 다음뿐이다.
  - (1) 같은 컨텍스트 안의 `ToolBatch` 와 병렬 도구 호출(`EFFICIENCY_PROMPT_BLOCK`)
  - (2) 스크립트 일괄 처리(Bash)
  - (3) 호스트 Job(스케줄된 별도 턴, `JobGuide` 뒤)
  - (3)은 **별도 턴 = 별도 usage** 로 회계되고, 같은 턴 안의 하위 컨텍스트는 아니다.
- `docs/architecture.md` 는 아직 "Stage 6 internal 루프 = CLI parity" 등을 설명하지만, 하위 에이전트 회계에 대한 서술은 없다.

---

## 6. 컨텍스트 관리 트리거 한눈에 보기

모든 비율은 `context_window_budget`(호스트가 해석한 실제 창; 0이면 200,000)을 기준으로 한다.

| 임계 | 값 | 무엇이 일어나나 | 하드코딩? | 근거 |
|---|---|---|---|---|
| 입력 예산 | window − max_tokens − max(1024, 2%·window) − (메모리면 3,000) | 호출 전 RAG → 텍스트 절단 | 식은 코드 | `R/host/token_budget.py:213-226`, `R/host/turn_executor.py:923` |
| prune 비용 트리거 | 30,000 토큰(추정) | 중복·이미지·오래된 대형 결과 정리, 매 반복 | 기본값은 kwarg, 세부는 상수 | `R/core/context_prune.py:68-77` |
| 압축 트리거 | 0.8 | `LLMSummaryCompactor` 동기 실행 | **예** | `R/stages/s02_context/artifact/default/stage.py:531` |
| 압축 목표 | 0.7 | `target_met` / `compaction_target_missed` 사건 | **예** | `stage.py:527,545`, `R/core/compaction.py:177-200` |
| 백그라운드 상한 | 0.9 | (운영 꺼짐) | **예** | `stage.py:535` |
| 가드 헤드룸 | max(4096, max_tokens+2048), ≤ window/2 | compact → 재검사 → 거절 | 식은 코드 | `R/host/runner.py:672-675` |
| 요약 보존 | 최근 10메시지 | — | 생성자 기본 | `R/stages/s02_context/artifact/default/compactors.py:171` |
| 요약 입력 | 전사 12,000자 | — | **예** | `compactors.py:237` |
| 요약 출력 | 2,048토큰, temperature 0 | — | **예** | `compactors.py:204-209` |
| 메모리 주입 | 10,000자(층 비율) | iteration 0만 | dataclass 기본 | `R/memory/provider.py:1195-1205`, `:1329` |
| 도구 결과 파일화 | 100,000자 → 미리보기 480자 | 파일 + 요약 | 도구별 | `R/tools/base.py:67`, `R/stages/s10_tool/persistence.py:99-101` |
| 턴 입력 예산 | soft 1M / hard 3M 토큰 | 마무리 / 도구 없이 보고 | kwarg | `R/stages/s16_loop/turn_budget.py:44-45` |
| 반복 거부 종료 | 3회 | 보고 후 종료 | kwarg | `R/stages/s16_loop/repeat_stop.py:44` |
| 슬라이스 | 반복 20회 × (1 + 이어가기 2) | SUSPEND → CONTINUE | kwarg | `runner.py:504`, `:1306` |
| 참조 파일 | 16KB/파일, 48KB 합계, 12개 | 첫 턴에 첨부 | 상수 | `R/host/referenced_files.py:34-36` |

---

## 7. 비용 계측 지점과 맹점

| 비용 원천 | 계측되는가 | 근거 |
|---|---|---|
| 메인 루프 모델 호출(Stage 6) | ○. Stage 7이 호출마다 `turn_token_usage` 에 쌓는다. `turn_usage` 가 input/output/cache/cost/`calls`/`first_call_prompt_tokens`/`max_call_prompt_tokens` 를 낸다. | `R/stages/s07_token/artifact/default/trackers.py:26,46`, `R/host/runner.py:1038-1122` |
| CLI 백엔드 호출 | ○. Stage 6이 `last_api_response` 로 올린다. Claude Code가 보고한 `total_cost_usd` 를 우선한다. | `runner.py:1043-1047` 독스트링 |
| 압축 요약 호출(`s02.compact`) | **×**. 압축기가 `client.create_message` 를 직접 부르고, 사건에는 토큰이 없다. | `R/stages/s02_context/artifact/default/compactors.py:257-290` |
| 증류(`memory.rollup`, facts) | **×**(런타임 원장에는 없음; 호스트 집계는 미확인) | `R/host/distill.py:146-149`, `R/host/host.py:204` |
| skill fork | **×**(미연결, metadata에만) | `R/skills/fork.py:172-178` |
| 임베딩 | ×(런타임 원장에 없음) | `R/memory/embedding/*` |
| 하네스 장치 발화 | 횟수만 | `R/host/harness_components.py` |

RRSI의 정규화 항(예: 토큰·호출 비용 페널티)을 정확히 쓰려면 다음이 필요하다.
- (1) 압축·증류 호출을 Stage 7과 같은 원장(또는 `usage.harness_cost`)에 귀속시킨다.
- (2) 장치별 추가 토큰을 기록한다. 예: 산출물 점검이 만든 추가 왕복, 메모리 주입 문자수, 카탈로그 길이.

---

## 8. 원자 편집 표현을 위한 주소 체계 제안 (요약)

현재 코드에서 "한 군데만 바꾸면 되는" 지점을 `ℓ:주소` 로 정리했다. 새 설계의 편집 연산 `Edit(ℓ, target, op, payload)` 의 target 후보다.

| ℓ | 주소 예 | 현재 실체 | op |
|---|---|---|---|
| prompt | `prompt.block.efficiency` | `R/host/_constants.py:42` 상수 | replace_text |
| prompt | `prompt.blocks` (순서·조건) | `R/host/turn_executor.py:406-663` 분기 | insert / remove / reorder (데이터화 필요) |
| control_flow | `loop.repeat_stop.after`, `loop.turn_budget.{soft,hard}`, `loop.max_iterations`, `loop.max_continuations` | `build_pipeline` 인자 / kwargs | set |
| control_flow | `loop.completion_reviewers` | `LoopStage.add_completion_reviewer` | add / remove |
| control_flow | `tool.repeat_guard.{warn,block,…}` | `R/stages/s10_tool/repeat_guard.py:38-53` 상수 | set (데이터화 필요) |
| config | `model.temperature`, `model.max_tokens`, `model.thinking_level` | kwargs / `ModelConfig` | set |
| output_plumbing | `parse.parser`, `tool.result_persist.max_chars`, `output.schema` | s09 교체 / 도구 capabilities / kwarg | swap / set |
| context_mgmt | `context.prune_over_tokens`, `context.compaction.{trigger,target}`, `context.compactor` | kwarg / **하드코딩** / 슬롯 | set / swap |
| client_tool | `exposure.turn_one`, `tool.description[X]`, `tool.registry += forged(X)` | `TURN_ONE_TOOLS` / 인라인 / ForgeTool | add / remove / replace_text / register |
| skill | `gates[G].family`, `gates[G].description`, `skills/<id>/SKILL.md` | `GATES` / 문 도구 / (미연결 데이터) | add / replace_text |
| memory | `memory.hooks.max_inject_chars`, `memory.distill.{enabled,evergreen_every}`, `memory.blocks` | `MemoryHooks` / `distill.py:34` / 프롬프트 블록 | set / replace_text |
| subagent | (없음) | 4.71.0에서 제거 | — |

---

## 9. 최종 표

| 컴포넌트 ℓ | 현재 구현 위치 | 편집 단위(데이터/코드) | 비용 영향 | 새 설계에서의 표현 제안 |
|---|---|---|---|---|
| **prompt** | 기본 `default_prompt`(`R/host/_constants.py:14`). 상수 블록 EFFICIENCY(`:42`) / MEMORY×3(`:69,87,102`) / SELF_EVOLUTION(`:26`) / CLI 이름 규약(`:121`). 호스트 블록(jobs, environment). Stage 3 `ComposablePromptBuilder` + Pinned / Retrieved / DateTime / TurnNotes 블록(`R/stages/s03_system/artifact/default/builders.py:176-441`). 숨김 목록(`R/tools/catalog.py:31-101`). 루프·가드 개입 문구. | 본문: **코드 상수**(이름 붙은 문자열, 치환 쉬움). 포함·순서: **코드 분기**. 사용자 프롬프트: **데이터**. | 고정 프리픽스 × 호출 수(블록 합 약 2.4k자 + 카탈로그 ≤ 4k자). volatile 블록은 캐시 밖. 추가 호출 없음. | 블록을 `PromptBlockSpec{id, text, condition, volatile, order}` 데이터 목록으로 끌어올리고 `MutablePromptBuilder` 를 그릇으로 쓴다. 편집 = 블록 텍스트 치환 / 블록 on·off / 순서. 길이 증가분을 비용 페널티에 반영. |
| **control_flow** | s16 `StandardLoopController`(`controllers.py:69`), `DeliverableReviewer`, `RepeatStop`, `TurnInputBudget`(`R/stages/s16_loop/*`). s10 repeat / denial / second_machine 가드. s06 재시도. 자동 이어가기(`R/host/runner.py:1306`). `max_iterations`. | 노출된 4개는 **데이터**(kwargs). 가드 임계·판정 순서는 **코드 상수**. 장치 on/off는 세터로 플러그인화. | 이어가기·반복이 호출 수를 좌우. 산출물 점검 +1 왕복. 예산·반복 종료는 비용을 줄임. 추가 LLM 없음. | `ControlPolicy{max_iterations, max_continuations, repeat_stop_after, turn_budget, reviewers[], repeat_guard{…}}` 데이터 객체. 편집 = 스칼라 set, 리뷰어 add/remove. `harness_summary` 발화율로 효용 평가. |
| **config** | `ModelConfig` / `PipelineConfig`(`R/core/config.py`), 단계 config schema, `StrategySlot`, 노드 kwargs(`turn_executor.py`), `PipelineMutator`(`R/core/mutation.py`), `_TUNABLE_*`(`R/core/environment_control.py:51-67`). | 대부분 **데이터**. 잠금 키(model, provider, credentials)는 편집 금지. | temperature, max_tokens, thinking이 출력·생각 토큰을 좌우. `context_window` 오설정은 압축 실패나 400 오류로 이어짐. | `ConfigEdit{key, value}` + 잠금 목록(`_CORE_LOCKED_KEYS` 재사용). 실행기는 `PipelineMutator.update_*` 와 `batch` 롤백. RRSI에서는 π를 고정하므로 model/provider 편집을 금지. |
| **output_plumbing** | `stream_turn` 이벤트 변환(`R/host/runner.py:1340`), 구조화 출력(`:448`, `:686-694`, `:807`), 큰 결과 파일화(`R/stages/s10_tool/persistence.py`), `result_filter`(`routers.py:500-531`), CLI 정의 맞춤(`R/tools/definition.py`), 멈춤 안내. | 대부분 **코드**. 스키마·필터는 **데이터 / 훅**. 결과 상한은 도구별 코드 값. | 파일화가 이후 모든 호출의 입력을 줄임. 스키마 지시는 프리픽스 증가. 추가 호출 없음. | `OutputPolicy{result_persist_chars, preview_chars, display_limit}` 전역 키 신설. 구조화 출력은 config로 분류. 편집 빈도가 낮으므로 K 안에서 낮은 사전확률. |
| **context_mgmt** | 입력 예산 `fit_input_to_budget`(`R/host/context_budget.py:101`), prune(`R/core/context_prune.py`), Stage 2 압축(`R/stages/s02_context/artifact/default/stage.py:466-547`) + `LLMSummaryCompactor`(`compactors.py:142`), Stage 4 가드 회복(`R/core/pipeline.py:3283-3317`), 참조 파일 / 빠른 경로, 도구 호출 평문화, 메모리 주입 상한. | 일부 **데이터**(prune_over_tokens, enable_compaction, compactor 슬롯). 0.8/0.7/0.9 비율, 요약 프롬프트, 12k 전사, 2,048 출력, 메모리 예약 3,000은 **하드코딩 코드**. | 압축 발화마다 LLM 1회(원장 밖). prune은 무LLM으로 이력 재전송을 줄임. 참조 파일은 첫 호출을 늘리는 대신 왕복을 줄임. | 먼저 `ContextPolicy{prune:{over_tokens, protect_last, trim_over, keep}, compaction:{trigger, target, keep_recent, transcript_chars, summary_tokens, prompt}, input_budget:{margin, reserve}}` 로 데이터화(리팩터 선행). 압축 호출을 원장에 귀속. |
| **client_tool** (K_str) | `Tool` / `ToolRegistry`(`R/tools/base.py:304`, `R/tools/registry.py:34`), 내장 64개(`R/tools/built_in/__init__.py:116-171`), 서버(호스트) 노출 가족, `TURN_ONE_TOOLS`(`R/host/tool_exposure.py:79`), `ToolSearch` + 숨김 목록, `TurnToolSurface`(CLI), `adapt_tools`, ForgeTool(검증 게이트). | 노출 정책은 **코드 내 데이터 구조**(집합 편집 쉬움). 설명·스키마는 **인라인 코드 문자열**. 새 도구는 코드, 단 ForgeTool 경로는 **데이터**(스크립트 + 스펙). | 턴1 스키마가 프리픽스의 약 72%(약 6.2k토큰 실측 주석, 이번 조사 내장 약 9.3k자 + 메모리 약 2.4k자). 숨김은 왕복 +1. | `ToolEdit{op: expose / hide / describe / register / unregister, name, payload}`. 등록계 편집은 ForgeTool식 **사전 실행 게이트** 를 통과해야 하고, 노출계 편집은 프리픽스 토큰 변화를 비용으로 계산. 설명을 데이터 파일로 분리하면 프롬프트 최적화 대상이 됨. |
| **skill** (K_str) | 운영: 문(Guide) + 가족(`R/tools/gates.py:107-128`, `SKILL_GATEWAYS`, 호스트 AppGuide / JobGuide). 미연결: SKILL.md 스킬(`R/skills/*`, 번들 10개), `env.create_skill`, 노드 스킬 `manifest_md`(버려짐, `R/host/tools.py:315-326`). | 문·가족은 **코드**(표 한 줄 + 문 설명 문자열). SKILL.md는 **데이터**(미사용). | 문 하나 약 100토큰. 열면 가족 스키마 + 왕복 1회. fork 모드는 별도 LLM(원장 밖). | 스킬 = `SkillSpec{gate_name, gate_description, family[], instructions_md}` 로 통일. `GATES` 와 SKILL.md를 하나의 데이터 원천으로 합침. 편집 = 가족 원소 add/remove, 문 설명·지침 텍스트 치환, 스킬 생성(ForgeTool 같은 게이트). `manifest_md` 를 버리는 문제부터 해결. |
| **memory** (K_str) | 공급자(호스트), `MemoryAwareRetriever`(`R/memory/retriever.py:133`), `ConversationArchivingStrategy`, Pinned / Retrieved 블록, 메모리 도구 6개(`R/host/memory_tools.py:116`), 실행 기록, 증류(`R/host/distill.py`; `R/memory/facts.py`, `R/memory/rollup.py`). | 기억 내용은 **데이터**. `MemoryHooks` 예산·비율은 dataclass(데이터화 쉬움). 증류 지시·스키마·케이던스와 3갈래 배선은 **코드**. | 주입 ≤ 약 10.6k자(iteration 0), 도구 약 2.4k자 / 호출. 증류 턴당 2 + 5턴마다 1 LLM(원장 밖, 다이제스트 입력이 세션 길이에 비례). 임베딩 질의 1 + 쓰기마다 재임베딩. | `MemoryPolicy{inject_budget, layer_ratios, tools_exposed[], distill:{enabled, evergreen_every, facts_schema, digest_prompt}, archive:{on}}`. 편집은 정책 수준에 한정하고, 기억 **내용** 편집은 RRSI H에서 제외(데이터와 하네스 분리). 증류 비용을 원장에 귀속. |
| **subagent** (K_str) | **없음**. 4.70.0 위임 도구, 4.71.0 s12/s13 제거(`CHANGELOG.md:95-212`). CLI 네이티브 Task/Agent 차단(`R/host/runner.py:168-173`). 잔존: `R/skills/fork.py`(미연결), `R/runtime/tasks.py`(비LLM). | 편집 대상 없음. 되살리면 코드 + 정의 데이터. | 현재 0. fork를 쓰면 사용량이 metadata에만 남아 원장 밖. | 플랫폼 결정("다시 들이지 않는다", `CHANGELOG.md:99-100`)과 충돌한다. 1차 설계에서는 K에서 **비활성(빈 집합)** 으로 두고, 들인다면 `SubagentSpec{system, tools[], budget}` 과 **같은 원장 회계**(Stage 7)를 전제조건으로 한다. 대체 수단은 ToolBatch, 스크립트, Job. |

---

## 10. 문서–코드 불일치·주의 (기준선은 코드)

| 문서 주장 | 코드 사실 | 근거 |
|---|---|---|
| Phase A(1~5단계)는 턴당 한 번(`docs/architecture.md`) | 루프 본체가 2~16을 매 반복 실행 | `R/core/pipeline.py:832-835`, `:3055-3058` |
| 호스트 브리지는 기본 20 슬라이스를 자동으로 이어간다(`docs/long_running_execution.md`) | `DEFAULT_MAX_CONTINUATION_SLICES = 2` | `R/host/runner.py:1306` |
| `docs/memory.md`(2.1.0): `vector_search` 전략, `vault` 전략, `reflective` 서브 LLM, `tools/built_in/memory_tools`, 9개 메모리 도구 | 이런 전략은 없고 reflective는 플래그만. 도구는 `host/memory_tools.py` 의 6개. | `R/stages/s02_context/artifact/default/stage.py:103-107`, `R/stages/s18_memory/artifact/default/strategies.py:46-62`, `R/host/memory_tools.py:116` |
| `docs/hooks.md`: `runner.on_pre_tool_use`, 예외로 거부, POST 훅이 결과를 바꿈 | 그런 API는 없다. 예외는 삼켜진다. POST는 관찰만 한다. | `R/hooks/runner.py:82-84`, `:242-249`; `R/stages/s10_tool/artifact/default/routers.py:546-570` |
| `docs/mcp.md`: `mcp.connect(name, command=…)`, `list_tools`, `close` | `connect(name, config)`, `discover_tools`, `disconnect_all` | `R/tools/mcp/manager.py:691`, `:874`, `:819` |
| `permission/__init__.py`: "Stage 4가 매트릭스를 참조" | 매트릭스를 부르는 곳은 Stage 10 라우터 하나 | `R/permission/__init__.py:34`, `routers.py:367` |
| `MEMORY_SPEC.yaml` 주입 8,000자 | `MemoryHooks.max_inject_chars = 10000` | `docs/MEMORY_SPEC.yaml` retrieval 절, `R/memory/provider.py:1329` |
| s10 병렬도 주석 "ParallelExecutor 기본과 일치" | 실효값 10 vs 클래스 기본 5 | `R/stages/s10_tool/artifact/default/stage.py:28`, `executors.py:235` |
| `shared_keys.py` 의 `executor.tool_review_flags`, `executor.hitl_request` | 실제 키는 `tool_review_flags`, `hitl_request` | `R/core/shared_keys.py:63,74` |

추가 주의:
- `strip_leading_orphan_tool_results` 는 압축기의 `_safe_recent` 가 쓴다(`R/stages/s02_context/artifact/default/compactors.py:8,14-17`). 서브조사에서 "호출자 없음" 이라고 보고됐는데, 이는 틀린 보고다.
- 호스트 쪽 사실은 W 저장소의 해당 줄만 확인했다. 호스트의 `build_memory_provider`, `build_turn_memory_llm`, `rag_context_builder` 내부 비용과 집계는 미확인이다.
- 본문의 토큰 수 가운데 "실측 주석" 은 코드 주석의 수치를 옮긴 것이고, 이번 조사에서 새로 잰 것은 문자수(`api_definition` JSON 길이, 프롬프트 블록 길이)뿐이다. 토큰 환산은 추정이다.
