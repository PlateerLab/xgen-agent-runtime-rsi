# 11. xgen-agent-runtime 외부 I/O 계약: 교체 하네스가 지켜야 할 경계

> 조사 기준: `PlateerLab/xgen-agent-runtime@2e015ae` (4.75.0, `pyproject.toml:7`). 2026-10-01 에 코드만 읽었다(실행·테스트는 돌리지 않음). 소비자(호스트)인 xgen-workflow 의 같은 시점 운영 코드도 읽었지만 비공개 저장소이므로, 호스트 쪽은 **파일 위치 없이 동작과 기대만** 적는다.
> 목적: 21단계 파이프라인(하네스 코어)을 새 설계로 바꿀 때, 소비자가 눈치채지 못하게 **그대로 남겨야 할 입력·출력·부수 계약**을 정확히 적는다.
> 경로 표기: `runtime/X` = `xgen-agent-runtime/src/xgen_agent_runtime/X`, `rt-tests/X` = `xgen-agent-runtime/tests/X`. 줄 번호는 위 커밋 기준이다. "호스트" 는 런타임을 부르는 쪽(XGEN 서버의 xgen-workflow, Agent-XGeny 노드)을 말한다.
> 표시: **[실측]** 코드에서 직접 확인함, **미확인** 코드로 추정만 했거나 런타임 동작을 실측하지 않음.

---

## 요약

- **실제 운영 진입점은 하나다.** 호스트의 Agent 노드가 매 턴 `AgentTurnExecutor().run(host, **kwargs)` (`runtime/host/turn_executor.py:172`)를 부른다. 그 밖의 운영 코드는 `Pipeline`·`PipelineState` 를 직접 쓰지 않는다(운영 코드 검색 0건, 테스트만 사용).
- **입력 계약은 두 덩어리다.** ① 평평한 `**kwargs` dict(텍스트·도구·컨텍스트·메모리 포트 값, 노드 파라미터, 실행기가 넣는 신원·취소 훅) ② `host` 객체(`HostServices` 프로토콜, `runtime/host/host.py:66`). 운영 구현은 호스트의 `HostServices` 구현체 하나다.
- **출력 계약은 sync iterator 의 청크 문법이다.** 스트리밍이면 `str`(답 텍스트)·`{"type":"agent_event"}`·`{"type":"canvas_command"}`·`{"type":"usage"}` dict 를 낸다. 비스트리밍이면 `str` 하나를 돌려준다(`runtime/host/runner.py:1340-1742`). usage 청크는 끝에 **정확히 1회**, `usage_sink` dict 는 스트림을 닫아도 채운다.
- **소비자가 실제로 읽는 출력 필드는 생각보다 적다.** 텍스트 청크, agent_event 의 `type/tool_name/tool_input/result/result_length/error/tool_use_id/run_id/duration_ms/indicator/timestamp/citations`, usage 의 `input_tokens/output_tokens/cache_*/total_cost_usd/model/provider/calls/first_call_prompt_tokens/max_call_prompt_tokens/partial/harness`. thinking·api·stage 이벤트는 밖으로 나가지 않는다.
- **kwargs 는 부수 채널로도 쓰인다.** 런타임이 `kwargs["_sandbox_session"]`·`kwargs["_tool_surface"]` 를 써 넣은 **뒤에** 같은 dict 를 `host.build_host_skill_tools(**kwargs)`·`host.build_cli_runtime(provider, kwargs)` 로 넘기고, 호스트가 그 키를 읽는다. 호출 순서도 계약이다.
- **CLI provider(claude_code·codex)용 계약이 하나 더 있다.** `TurnToolSurface`(`runtime/host/tool_surface.py:34`)의 `tools_list()`·`async call()`·`exposed_names()`·`bind_loop()`·`registry`·`tool_context` 를 호스트의 MCP 엔드포인트가 그대로 쓴다.
- **암묵 계약도 있다.** 프로세스 전역 취소 레지스트리(`runtime/host/cancel_context.py`)의 모듈 상태를 호스트와 공유한다. `"[ERROR] "` 접두는 호스트가 오류 코드로 옮기고 HTTP 상태로 다시 매핑한다. 메모리 vault 의 실행 카드·대화 rollup·`_distill_state.json`, rollout JSONL, 도구용 `ToolContext.state_view`(add_event·shared·pending_tool_calls)도 여기에 든다.
- **런타임 저장소 안에 계약 고정 테스트가 있다.** `rt-tests/contract/test_public_runtime_contract.py` 는 `Pipeline.run/run_stream` 시그니처와 `PipelineEvent`·`PipelineState`·`PipelineResult`·`APIRequest`·`APIResponse`·`ContentBlock` 필드를 순서까지 고정한다. 다만 운영 소비자는 이 층을 직접 부르지 않는다. 파이프라인 층을 지킬지는 교체 범위에 따라 정할 일이다(§0).
- **swap-in 하네스의 최소 인터페이스**(§부록):
  - `AgentTurnExecutor.run(host, **kwargs)`
  - `HostServices` 호출 순서·의미
  - 청크 문법과 usage 형태
  - `TurnToolSurface`
  - `runtime/host/runner.py` 와 `cancel_context` 모듈 경로·심볼
  - 이 다섯을 지키면 xgen-workflow 운영 경로는 바뀌지 않는다.

---

## 0. 계층 지도: 누가 무엇을 부르나

```
[호스트 실행기 (xgen-workflow)]
        │  kwargs 주입: interaction_id, response_io_id, workflow_id, workflow_name, node_id,
        │               node_name, user_id, client_surface, client_device_id, local_folders,
        │               cancel_check, trace (+ 노드 파라미터·포트 값)
        ▼
  호스트의 Agent 노드(Agent-XGeny)
        │  전처리: 옛 포트 정규화, 대화에서 고른 모델, 자격증명 검사, 자리표시자 치환,
        │          동결·게스트·폴더 적용, 통제 정책, usage_sink={}, enable_prompt_cache=True,
        │          max_continuation_slices=10
        ▼
  AgentTurnExecutor().run(host=<HostServices 구현>, **kwargs)
        │  (runtime/host/turn_executor.py:172-1194)  ← ★ 하네스 교체의 1차 경계
        │  턴 조립: host.* 호출 → ToolRegistry, system_prompt, PipelineState, memory provider …
        ├─ SDK provider:  runner.build_pipeline(...) → Pipeline(21 stages)
        └─ CLI provider:  TurnToolSurface → kwargs["_tool_surface"] → host.build_cli_runtime
        ▼
  runner.stream_turn(...) / runner.run_turn(...)   (runtime/host/runner.py:1340 / 1643)
        │  Pipeline.run_stream 이벤트 → xgen 청크 번역     ← ★ 2차 경계(파이프라인 층)
        ▼
  호스트의 스트림 래퍼 (usage 가로채기·도구 span·다운로드 마커)
        ▼
  호스트 실행기 → SSE ("tool"/"canvas_command"/"download_artifact")
        ▼
  웹 UI·데스크톱 클라이언트
```

교체 경계를 어디에 두느냐에 따라 지킬 계약이 달라진다.

| 경계 | 새 하네스가 대체하는 것 | 반드시 유지 | 버려도 되는 것 |
|---|---|---|---|
| **A. `AgentTurnExecutor.run` 아래 전부** | 턴 조립 + 파이프라인 + 스트림 번역 | §2.1 kwargs, §2.4 HostServices 호출 순서, §3 청크 문법, §4.4 TurnToolSurface, §5 부수 계약 | `Pipeline`·`PipelineState` 내부 구조, 21 stage. 단 런타임 저장소의 `test_public_runtime_contract` 와 파이프라인을 직접 쓰는 호스트 테스트 일부는 다시 써야 한다(§1.4) |
| **B. `build_pipeline`·`stream_turn`·`run_turn` 아래** | 파이프라인만 | A 의 전부 + §2.5~2.6 시그니처 + 파이프라인 이벤트 이름·필드(§3.2) | stage 구현 |
| **C. stage 구현만** | 21 stage 내부 | B 의 전부 + `Pipeline.run/run_stream` 시그니처·dataclass 필드(§1.4) | 없음 |

운영 경로만 보면 **경계 A 가 최소 인터페이스**다. 이 문서는 A 를 기준으로 쓰고, B·C 에서 추가로 지킬 것은 따로 표시한다.

---

## 1. 공개 API 표면

### 1.1 패키지 루트 `xgen_agent_runtime/__init__.py`

`__all__` 에 94개 이름이 있다(`runtime/__init__.py:130-236`). 묶음별로 보면 다음과 같다.

- Core: `Pipeline`, `PipelineConfig`, `PipelineState`, `RunStatus`, `TerminationReason`, `CONTINUE_RUN`, `ContinuationInput`, `PipelineResult`, `ModelConfig`, `ModelOverrides`, `TokenUsage`, `CacheMetrics` (`:19-24`)
- 추상: `Stage`, `Strategy`, `StageDescription`, `StrategyInfo` (`:25`)
- 빌더·프리셋·환경·diff·artifact·introspection: `:47-93`
- 이벤트: `EVENT_CATALOG_VERSION`(=16, `runtime/events/catalog.py:67`), `EventBus`, `EventTypes`, `PipelineEvent`, `known_event_types` (`:94-100`)
- LLM 클라이언트: `APIRequest`, `APIResponse`, `BaseClient`, `ClaudeCodeCLIClient`, `ClientCapabilities`, `ClientRegistry`, `ConfigError`, `ContentBlock`, `CredentialBundle`, `ProviderCredentials` (`:101-112`)
- 오류: `GenyExecutorError` … `MutationLocked`, `ErrorCategory`, `ExecutorErrorCode` (`:26-37`)
- 메모리 배선: `MemoryAwareRetriever`, `MemoryProviderFactory`, `ProviderDrivenStrategy`, `GenyPresets`, `provider_from_manifest_memory` (`:113-119`)
- `__version__`: 배포 메타데이터에서 읽는다(`:123-128`). 호스트의 메모리 관리 API 가 실행기 버전 표시용으로 읽는다.

운영 소비자가 루트에서 직접 import 하는 것은 **없다**. 루트 심볼을 쓰는 곳은 `runtime/host/runner.py:29-36`(`CONTINUE_RUN, ClientRegistry, Pipeline, PipelineBuilder, PipelineState, RunStatus`)과 `turn_executor.py:185`(`PipelineState`) 같은 런타임 내부, 그리고 호스트 테스트뿐이다.

### 1.2 `xgen_agent_runtime.host` 패키지

```python
# runtime/host/__init__.py:17-20
from xgen_agent_runtime.host.host import CliRuntime, HostServices
__all__ = ["HostServices", "CliRuntime"]
__version__ = "0.1.0"
```

`AgentTurnExecutor` 는 패키지에서 다시 내보내지 않는다. 호스트는 **모듈 경로** `xgen_agent_runtime.host.turn_executor` 로 import 한다. 그래서 모듈 경로 자체가 계약이다.

### 1.3 호스트가 직접 import 하는 하위 모듈·심볼 (운영 코드)

호스트 운영 코드(테스트·스크립트 제외)를 검색한 결과다. "호스트 용도" 는 위치 대신 쓰임새로 적는다.

| 런타임 모듈 | 심볼 | 호스트 용도 | 하네스 교체 영향 |
|---|---|---|---|
| `host.turn_executor` | `AgentTurnExecutor` | Agent 노드의 턴 실행 | **핵심 진입점** |
| `host.runner` (모듈 통째 재노출) | `build_pipeline, stream_turn, run_turn, settle_structured, build_cli_client` | 호스트의 import 경로 안정용 shim 이 모듈의 모든 이름(밑줄 포함)을 복사 | shim 이 `dir(_src)` 를 복사하므로 **심볼 이름이 사라지면 import 시점에 조용히 빠진다** |
| `host.runner` | `CLI_NATIVE_TOOLS_DENY`, `CLI_NATIVE_TOOL_CATALOG`(옛 이름) | CLI 배선 | CLI 배선 |
| `host.runner` | `build_client(provider, api_key, base_url, *, credentials=None)` | 앱용 LLM 호출(하네스 밖) | 앱 LLM |
| `host.runner` | `_map_provider` | 앱용 LLM | 앱 LLM |
| `host.runner` | `build_cli_client(...)` | CLI 턴, 메모리 증류용 LLM | CLI·증류 |
| `host.runner` | `build_codex_cli_client(...)` | Codex 턴과 Codex 서비스 | Codex |
| `host._constants` | `default_prompt`, `MEMORY_PROMPT_BLOCK`, `MEMORY_READONLY_PROMPT_BLOCK`, `SELF_EVOLUTION_PROMPT_BLOCK`, `_self_evolution_policy`, `cli_tool_naming_note` | 실행기 기본 프롬프트 복원, [기본정보] 화면의 프롬프트 미러, 메모리 vault | **프롬프트 미러**(§5.10) |
| `host.tool_exposure` | `is_turn_one`, `registers_core` | 도구 계층 판정(프롬프트 미러, 내장 도구, 자기진화 도구) | 도구 계층 판정 |
| `host.cancel_context` (재노출) | `request_cancel, is_cancelled, clear_cancel` | 실행기의 취소 요청, 스트림 헬퍼의 취소 확인 | **모듈 전역 상태 공유**(§5.3) |
| `host.memory_tools` | `build_memory_tools` | 프롬프트 미러 | 메모리 도구 |
| `host.execution_record`, `host.memory`, `host.tools`, `host.rag`, `host.distill`, `host.context_budget`, `host.conversation_archive`, `host.forged_tools`, `host.python_env` | 모듈 통째 재노출 | import 경로 안정용 shim | 실제로 쓰이는 것은 forged_tools(다수), python_env·tools(소수), distill 의 `DISTILL_STATE_FILENAME` 정도 |
| `host.param_validator`, `host.token_budget`, `host.tool_indicators` | 모듈 재노출 | 옛 Agent 경로·도구 지표 | 옛 Agent 경로·지표 |
| `host.local_folders`, `host.device_tools` | `is_folder_tool`, `model_tool_name`, `build_device_guide`, `build_device_tool` | 커넥터 기기 도구 | 기기 도구 |
| `host.ids`, `host.memory_wire` | `_SAFE_ID_RE, _safe_id`, 타입 태그 | vault 경로 | vault 경로 |
| `tools.base` | `Tool, ToolCapabilities, ToolResult, ToolContext, build_tool, tool_origin` | 호스트가 만든 도구 다수(작업·앱·자기진화·패키지·내보내기·이미지 생성·통제 정책·내장 도구 래퍼) | **도구 ABI**(§5.4) |
| `tools`, `tools.built_in`, `tools.errors`, `tools.fs`, `tools._ssh`, `tools.built_in._skill_gateway`, `tools.built_in._file_witness` | `ToolRegistry`, `BUILT_IN_TOOL_FEATURES`, `BUILT_IN_TOOL_CLASSES`, `get_builtin_tools`, `ToolErrorCode, ToolFailure`, `tool_fs`, `ssh_test_connection`, `witnessed_mutation` | 내장 도구 등록, 사용자 도구, SSH 연결 시험 | 도구 계층 |
| `memory.*` | `Importance, NoteDraft, NotePatch, RecordReceipt, EmbeddingDescriptor, MemoryProviderFactory, MEMORY_ENGINE_SYSTEM_PROMPT, make_summary, derive_graph_edges, embedding.*` | 호스트의 메모리 provider 구현·vault·임베딩 클라이언트 | **메모리 provider 프로토콜**(§2.4 C) |
| `stages.s02_context.types` | `MemoryChunk` | 호스트의 DB 기반 메모리 provider | ⚠ stage 내부 타입을 호스트가 import 한다. 하네스를 바꿀 때 경로를 남겨야 한다 |
| `llm_client.*` | `ClaudeCodeCLIClient, CodexCLIClient, ClientRegistry, thinking_spec, normalize_thinking, materialize_local_image_block, AzureEndpoint` | 앱 LLM, 대화 모델 선택, 이미지 첨부. XGEN 의 LLM 관리 서비스도 `AzureEndpoint` 를 쓴다(런타임이 없어도 돌게 try/except) | LLM 계층 |
| `core.config` | `ModelConfig` | 앱 LLM·증류·Codex 호출 설정 | LLM 호출 설정 |
| `security` | `SSRFError, validate_url` | 앱 LLM | |
| `host.param_validator` | `PROVIDER_TEMPERATURE_RANGES` | 대화 모델 선택 | |

`session/`·`runtime/`·`gateway/` 하위 패키지를 import 하는 소비자 코드는 **없다** [실측: XGEN 의 소비 저장소들을 검색해 0건]. §2.8 참고.

### 1.4 계약을 고정하는 테스트

**런타임 저장소**

| 테스트 | 고정하는 것 | 근거 |
|---|---|---|
| `rt-tests/contract/test_public_runtime_contract.py` | `Pipeline.run`·`run_stream` 시그니처 문자열 | `:30-41` |
| 〃 | `PipelineEvent` 필드 순서 `type, stage, iteration, timestamp, data, session_id, run_id, seq` | `:47-56` |
| 〃 | `APIRequest`(17필드), `ContentBlock`(7), `APIResponse`(6), `PipelineResult`(16) 필드 순서 | `:57-110` |
| 〃 | `PipelineState` 공개 필드 52개 순서 | `:113-169` |
| 〃 | 위 7타입이 루트 export + `__all__` 에 있을 것 | `:172-185` |
| `rt-tests/contract/test_error_codes_stability.py` | `ExecutorErrorCode` 문자열 값 동결(`_FROZEN`) | `:39`, `:86-118` |
| `rt-tests/contract/test_stage_uniformity.py` | stage order 유일·조밀, 이름 유일, 슬롯 표면 | `:220`, `:234` (하네스 내부. 경계 A·B 에서는 대상 아님) |
| `rt-tests/test_host_runner_usage.py` | usage 청크 정확히 1회·끝(`:123`), 오류 턴도 usage(`:190`), 취소 시 partial(`:208`), `run_turn` 이 usage_sink 채움(`:277`), rollout 수명(`:303-363`), record_failed_starts 게이트(`:433-467`), CLI 네이티브 전면 차단(`:518-532`) | |
| `rt-tests/test_host_turn_executor_gates.py` | 구조화 이미지 입력 보존(`:218,238`), fast path 배선(`:260`), **HostServices 에 위임 훅이 없을 것**(`:363-372`), `cli_bridge_available(provider)` 시그니처(`:388-391`) | |
| `rt-tests/test_turn_cancellation_scope.py` | per-turn `cancel_check` 가 interaction 전역 취소보다 우선(`:7,24`) | |
| `rt-tests/test_stop_is_heard_while_waiting.py` | 긴 대기 중에도 정지 감지(`:27`), 훅이 깨져도 턴 유지(`:76`) | |
| `rt-tests/unit/test_tool_surface_parity.py`, `test_turn_one_surface.py` | CLI 표면과 SDK 표면의 동일성 | 파일명 기준(본문 미정독, **미확인**) |

**호스트 저장소** — 경계 A 에서도 깨지면 안 되는 쪽. 호스트 테스트가 고정하는 성질을 범주로 적는다.

- 스트림 래핑: usage 청크는 trace 로 적산하고 하류로 흘리지 않음, 도구 결과 끝의 다운로드 마커 승격, 상류 `.close()` 가 러너 제너레이터로 전파, 비스트리밍 문자열은 그대로.
- usage 기록: 캐시·partial 분리, 닫힌 스트림은 sink 로 기록, usage 1회, harness 요약 전달.
- CLI 표면 동일성: CLI 가 보는 도구 = SDK 도구, Stage 10 경유 실행, 게스트·동결 턴 제외 규칙 동일, 프롬프트 = SDK 프롬프트 + 카탈로그 + 이름 규약.
- 호출 시그니처: 노드와 `turn_executor` 사이의 모든 키워드 호출이 실제 시그니처에 바인딩되는지(모듈 `xgen_agent_runtime.host.turn_executor` 를 직접 import).
- 도구 ABI: 주입 도구가 `to_api_format()`(name/description/input_schema)·`capabilities(tool_input)` 계약을 지키는지.
- 테스트 이음매: `xgen_agent_runtime.host.runner.build_client`/`build_pipeline` 을 바꿔 끼우는 테스트 다수(§5.9).

---

## 2. 진입점

### 2.1 `AgentTurnExecutor.run(host, **kwargs)`

```python
# runtime/host/turn_executor.py:169-172
class AgentTurnExecutor:
    """execute() 의 host-무관 판. 서버·커넥터가 같은 run() 을 돈다."""

    def run(self, host: Any, **kwargs):
```

- 생성자: 인자가 없다(`__init__` 미정의). 호스트는 매 턴 `AgentTurnExecutor()` 를 새로 만든다.
- 반환:
  - `streaming=True`(기본): `Iterator[Union[str, Dict[str, Any]]]`. `stream_turn(...)` 이 만든 generator(`:1150-1178`)이거나, 입력이 잘렸을 때 `CLAMP_NOTICE` 를 앞에 붙인 wrapper generator(`:1167-1177`)다. 조기 실패 때는 `iter([str])`(`:215`, `:1109`).
  - `streaming=False`: `str` (`run_turn` 반환값, `:1179-1194`). 조기 실패 때도 `str`.
- **동기 함수**다. 안에서 `asyncio.run(...)` 을 부르고(`:817`, `:873`, `:1005`, `:1105`) `stream_turn` 은 `asyncio.new_event_loop()` 를 만든다(`runner.py:1391`). 그래서 **이벤트 루프가 돌지 않는 워커 스레드에서** 불러야 한다(`runner.py:8-9` 주석: "The xgen executor runs `execute()` in a worker thread").
- 스트리밍 모드에서는 턴 조립(샌드박스·도구·메모리 provider 생성)이 `run()` 안에서 **바로** 일어난다. 파이프라인 실행과 teardown 은 generator 를 돌릴 때 일어난다. 소비자가 generator 를 한 번도 돌리지 않으면 `finally` 의 `on_close=_teardown`(`runner.py:1636`)이 돌지 않는다(**미확인**: 운영 소비자는 늘 돌리므로 실제 누수는 관측하지 않았다).

#### 2.1.1 kwargs 표 (런타임이 읽는 키)

"호스트 공급" 은 그 값이 운영에서 어디서 오는지를 적는다. "노드 기본" 은 호스트 Agent 노드 파라미터의 기본값이고, 실행기는 저장된 노드 설정을 평탄화해 kwargs 로 준다.

| 키 | 런타임 기본 | 쓰임 (turn_executor 줄) | 호스트 공급 |
|---|---|---|---|
| `text` | `None`→`""` | `TurnInput.from_raw` (`:188`), 사용자 턴 | 포트 `text`. 첨부가 있으면 `{"text","attachments"}` dict. 통제 정책이 가공한 값일 수 있음 |
| `streaming` | `True` | 반환 형태 (`:190`) | 노드 기본 True |
| `interaction_id` | `""` | `PipelineState(session_id=)`(`:362`), 취소 키(`:1121`), `workflow_schedule_` 접두면 스케줄 턴(`:434`), `deploy_`/`guest_` 접두면 자기진화 금지(`_constants.py:161-163`) | 실행기가 `setdefault` |
| `response_io_id` | `None` | 취소 키(`:192`, `:1121`) | 실행기가 `setdefault` |
| `node_name` | `""` | `build_pipeline(name=)`(`:175`, `:1045`) | 실행기 + 노드 `setdefault` |
| `provider` | `"openai"` | 분기 전반(`:197`) | 노드 기본 "openai", 대화에서 고른 모델이 덮어쓸 수 있음 |
| `temperature` | `0.7` | 사전 검증(`:205-215`), `build_pipeline`(`:1054`) | 노드 기본 0.7 |
| `output_schema` | `None` | dict 또는 pydantic class → dict(`:221-225`) | 포트 `output_schema` |
| `tool_exposure` | `None`→계층형 | `sends_every_schema` (`:233`) | 노드 기본 "hierarchy" |
| `tools` | `None` | `adapt_tools` (`:284`) | 포트 `tools`(통제 정책이 감쌀 수 있음) |
| `context` | `None` | `collect_rag` (`:287`) | 포트 `context` |
| `user_id` | `None` | 기기 도구·작업·내장 도구·자기진화·finalize (`:307,418,426,536,646,1136`) | 실행기가 `setdefault` |
| `client_surface` | `None` | `host.build_connector_mcp_tools` 로 그대로 전달(`:307`) | 실행기가 **강제 덮어씀** |
| `local_folders` | `None` | 폴더 도구·안내(`:301`). `None`=옛 클라이언트 규칙 | 실행기가 강제 덮어씀, 대화별 폴더 설정이 고침 |
| `memory` | `None` | `history_messages` → `state.messages`(`:379-394`) | 포트 `memory` |
| `system_prompt` | **키가 없을 때만** `default_prompt` | `:406-408`. `""` 는 "프롬프트 없음"이고 효율 블록도 붙이지 않는다(`:458`) | 노드 기본 `default_prompt`, 자리표시자 치환 후 |
| `workflow_id` | `""` | sandbox·작업·메모리·workspace·자기진화·rollout·finalize (`:417` 외 9곳) | 실행기가 `setdefault` |
| `workflow_name` | `""` | 작업 도구·자기진화(`:430`, `:647`) | 실행기가 `setdefault` |
| `enable_memory` | `True` | 메모리 provider·도구(`:475`) | 노드 기본 True |
| `enable_self_evolution` | `True` | `_self_evolution_policy`(`_constants.py:168`) | 노드 기본 True |
| `_frozen` | 없음 | 자기진화 금지(`_constants.py:164`) | 동결본 실행 시 호스트가 넣음 |
| `memory_distill` | `True` | 증류 스펙(`:678`, `:687`) | 노드 기본 True. 동결본은 off |
| `enable_workspace_fast_path` | 없음 → 관리자 설정 `WORKSPACE_FAST_PATH_ENABLED` | `:801-803` | **노드 미노출**(정의만 있음) |
| `enable_compaction` | `True` | 입력 예산·`build_pipeline`(`:893`, `:1072`) | 노드 기본 True |
| `max_tokens` | `8192` | `:894` | 노드 기본 8192 |
| `context_window` | `0` | `resolve_window`(`:906`) | 노드 기본 0 |
| `max_iterations` | `20` | `:1053` | 노드 기본 20 |
| `thinking` | `None` | `_thinking_param`: ""·auto·default → None(`:41-44`, `:1057`) | 노드 기본 "auto", 대화 선택 |
| `enable_prompt_cache` | `False` | `:1075` | 노드가 `setdefault(True)` |
| `repeat_stop_after` | 런타임 기본 3 | 키가 있고 해석될 때만 전달(`:1077-1082`) | **운영 공급 없음** |
| `prune_over_tokens` | 런타임 기본 30,000 | `:1084-1089` | **운영 공급 없음** |
| `turn_input_budget_tokens` | 런타임 기본 (1M, 3M) | `_budget_pair`(`:68-79`, `:1091-1095`) | **운영 공급 없음** |
| `cancel_check` | `None` | per-turn 취소 판정, 우선권 있음(`:1113-1123`) | Agent-XGeny 노드일 때 실행기가 자기 취소 판정 함수를 줌 |
| `tool_events` | `True` | agent_event 도구 사건 on/off(`:1154`) | 노드 기본 True |
| `max_continuation_slices` | `2` (`runner.py:1306`) | `:1161-1163`, `:1188-1190` | 노드가 `setdefault(10)` |
| `usage_sink` | `None` | `stream_turn/run_turn` 에 같은 객체를 그대로 넘김(`:1164`, `:1186`) | 노드가 `kwargs["usage_sink"] = {}` |

**런타임은 읽지 않지만 그대로 통과시켜야 하는 키** — `host.resolve_*(provider, kwargs)`(`:217-220`), `host.build_host_skill_tools(**kwargs)`(`:449`), `host.build_cli_runtime(provider, kwargs)`(`:779-787`)가 kwargs 전체를 받는다. 호스트가 읽는 키:
- 모델·자격증명: `openai_model`·`anthropic_model`·…·`codex_model`, `api_key`, `base_url`, `azure_deployment`, `azure_api_version`, `cli_max_budget_usd`
- 호스트 내부 표식: CLI 추가 환경, 통제 정책, 동결 여부, 폴더 기기, 클라이언트 기기, `trace`, `node_id`, `yield_output` 등(일부는 `_` 로 시작하는 키)

이 키들은 **같은 dict 로** 넘겨야 한다.

**런타임이 kwargs 에 써 넣는 키**(부수 채널, §5.1):

| 키 | 쓰는 곳 | 읽는 곳 |
|---|---|---|
| `_sandbox_session` | `:421` (make_sandbox 직후) | 호스트의 `build_host_skill_tools` (패키지 설치 도구 등이 샌드박스 세션을 씀) |
| `_self_evolution_allowed` | `:470` | **읽는 곳 없음** [실측 검색 0건]. 흔적만 남은 키 |
| `_tool_surface` | `:752` (CLI 이고 도구가 있을 때만) | 호스트의 CLI 런타임 구성(MCP 브릿지 묶기, CLI 작업 디렉터리 결정) |

#### 2.1.2 조기 종료 출력 (파이프라인 전)

| 조건 | 출력 | 근거 |
|---|---|---|
| provider 별 temperature 범위 위반 | `"[ERROR104: …]"` (`validate_agent_params` 반환문) | `turn_executor.py:203-215`, `param_validator.py:56-81` |
| 조립 중 예외(자격증명·vLLM 사전 점검·sandbox·CLI 빌드 등) | `"[ERROR] geny agent could not start: {exc}"`. 메모리 provider 는 직접 닫는다 | `:1097-1109` |

둘 다 streaming 이면 `iter([msg])` 이고 `close()` 메서드가 없다. 호스트는 `getattr(stream, "close")` 로 확인한다.

#### 2.1.3 턴 조립 순서 (호스트가 관찰할 수 있는 순서)

호스트 구현은 순서에 기대고 있다. 예: `_sandbox_session` 은 `build_host_skill_tools` 보다 먼저 써야 한다. 메모리 지침 문구는 `register_builtin_tools`·`register_forged_tools` 가 게스트 도구를 지운 **뒤**의 레지스트리를 보고 고른다.

1. `validate_agent_params` (`:203`)
2. `host.resolve_model / resolve_api_key / resolve_base_url / resolve_credentials` (`:217-220`)
3. `host.cli_bridge_available(provider)` — CLI 이고 메서드가 있을 때만 (`:269-280`)
4. `adapt_tools(kwargs["tools"])` → `collect_rag(text, context, context_builder=host.rag_context_builder)` (`:284-293`)
5. `host.build_connector_mcp_tools(user_id, client_surface)` + 폴더 필터 (`:306-332`)
6. `host.folder_device_info()` / `host.local_device_platform()` — 선택 훅 (`:339-340`)
7. `PipelineState(session_id=interaction_id)`, `shared[TURN_NOTES / RETIRED_TOOL_CALLS / geny.device_folders]`, 이력 preload와 워터마크 2개 (`:362-394`)
8. `host.make_sandbox(workflow_id, user_id)` → `kwargs["_sandbox_session"]` (`:416-421`)
9. `host.build_job_tools(...)` (workflow_id·user_id 둘 다 있을 때) → `host.jobs_prompt_block()` (`:425-443`)
10. `host.build_host_skill_tools(**kwargs)` (`:447-451`)
11. `host.environment_prompt(sandbox, provider)` (`:455-457`), `EFFICIENCY_PROMPT_BLOCK` (`:458-463`)
12. `_self_evolution_policy(kwargs, host.setting)` (`:469`)
13. `host.build_memory_provider(workflow_id, interaction_id)` → 메모리 도구 6종 등록 (`:475-508`)
14. 도구가 모델에 닿는 턴: `host.register_builtin_tools(registry, core=, user_id=, anthropic_api_key=host.resolve_api_key("anthropic", kwargs), ssh_servers=host.load_ssh_servers())` → job·skill 도구 등록 → `host.agent_workspace_dir` / `host.hydrate_workspace`(sandbox 가 없을 때) / `host.workspace_storage_root` → `host.build_run_tool_context(interaction_id=, run_dir=, extras=, storage_dir=, extra_allowed=[], sandbox=)` → `host.register_forged_tools(...)` (`:521-621`)
15. 메모리 지침 블록 선택 (`memory_write` 가 실제로 있는지 본다) (`:623-632`)
16. `host.register_workflow_self_tools(...)` → `registry.get("WorkflowSelf")` 가 있으면 프롬프트 블록 (`:637-664`)
17. `ensure_surface_entrances(registry)` (ToolSearch·SelfExtendGuide) (`:668-671`)
18. `DistillSpec` (codex 는 생략) (`:674-723`)
19. `host.tool_result_filter()` — 선택 훅 (`:727-729`)
20. CLI 면 `TurnToolSurface(...)` → `kwargs["_tool_surface"]`, 숨김 카탈로그와 이름 규약을 프롬프트에 붙임 (`:735-777`) → `host.build_cli_runtime(provider, kwargs)` (`:778-787`)
21. fast path (`host.setting_truthy`) → 요청에 이름이 나온 파일 붙이기 (`host.setting("GENY_PREFETCH_REFERENCED_FILES","1")`) (`:794-886`)
22. 컨텍스트 예산 (`resolve_window(..., vllm_probe=host.fetch_vllm_max_model_len)`) (`:893-952`)
23. 첨부 절대경로화·세션 이미지 hydrate → `pipeline_input` (`:971-1016`)
24. rollout 경로 (`host.setting_truthy("GENY_ROLLOUT_RECORDING_ENABLED")`) (`:1022-1042`)
25. `build_pipeline(...)` (`:1044-1096`)
26. `stream_turn` / `run_turn`. teardown 은 `host.finalize_turn(...)` 뒤에 CLI·run_dir 정리 (`:1125-1147`)

### 2.2 `TurnInput` (`runtime/host/turn_input.py:39-115`)

```python
@dataclass(frozen=True)
class TurnInput:
    text: str = ""
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    @classmethod
    def from_raw(cls, raw: Any) -> "TurnInput": ...
    def with_text(self, text: str) -> "TurnInput": ...
    def as_pipeline_input(self) -> Any: ...
```

`from_raw` 가 받아야 하는 입력 모양(`:48-102`):
- `str`·`None`
- OpenAI content array: `{"type":"text"|"input_text"}`, `{"type":"image_url"|"input_image"}`(data URL 이면 `{"kind":"image","mime_type","data"}`, 아니면 `{"kind":"image","url"}`), `{"type":"image"|"img"}`
- XGen 봉투 dict: `text`/`input_str`/`input`/`content` + `attachments|images|files` + `metadata`
- 그 밖(generator 포함)은 `_coerce_text` 로 이어 붙인다(`_constants.py:136-150`)

`as_pipeline_input()` 은 첨부·메타가 없으면 **`str`**, 있으면 `{"text","attachments","metadata"}` dict 다(`:107-115`). 이 dict 를 받는 쪽이 Stage 1 normalizer 다(`runtime/stages/s01_input/artifact/default/normalizers.py:70-161`). 새 하네스도 두 모양을 다 받아야 한다(테스트 `test_host_turn_executor_gates.py:218,238`).

### 2.3 `cancel_context` (`runtime/host/cancel_context.py:116-204`)

```python
def request_cancel(interaction_id: Optional[str], response_io_id: Optional[int] = None, *, ttl_seconds: float = 60.0) -> None
def is_cancelled(interaction_id: Optional[str], response_io_id: Optional[int] = None, *, cancel_check: Optional[Callable[[], bool]] = None) -> bool
def clear_cancel(interaction_id: Optional[str], response_io_id: Optional[int] = None) -> None
```

- 프로세스 전역 dict `_cancelled_until`(`:129`)에 키 `"{iid}"` 또는 `"{iid}:{io_id}"` 를 TTL 동안 넣는다.
- `cancel_check` 가 있으면 **그것만** 본다(`:181-182`). 지난 interaction 의 취소가 다음 턴을 오염시키지 않게 하려는 것이다.
- 런타임은 `_cancelled()`(`turn_executor.py:1117-1123`)를 `stream_turn(cancel_check=)` 로 넘긴다. 이 함수는 이벤트 사이(`runner.py:1422`)와 대기 중 0.2초 폴링(`runner.py:854`, `:867-897`)에서 불린다. 예외가 나면 False 로 친다.
- 호스트: 실행기가 `request_cancel/clear_cancel` 을, 스트림 헬퍼가 `is_cancelled/clear_cancel` 을 재노출 모듈로 쓴다. **모듈 객체가 하나여야** 상태가 공유된다(§5.3).

### 2.4 `HostServices` 프로토콜 (`runtime/host/host.py:65-253`)

`@runtime_checkable Protocol` 이지만 호스트 구현체는 상속하지 않는다. 런타임은 선택 훅을 `getattr` 로 확인한다. 아래 "필수"는 **운영 턴에서 무조건 불리는지**로 판정했다. "호스트 구현 메모" 는 호스트가 그 메서드에서 하는 일의 요지다.

| 그룹 | 메서드 (시그니처) | 필수/선택 | 런타임 호출 위치 | 실패 처리 | 호스트 구현 메모 |
|---|---|---|---|---|---|
| A | `setting(name: str, default: str = "") -> str` | 필수 | `_constants.py:170`(자기진화), `turn_executor.py:699-709,853` | 예외면 조립 실패 | 관리자 설정 → env → 기본값 순 |
| A | `setting_truthy(name: str) -> bool` | 필수 | `:803`(fast path), `:1028`(rollout) | 〃 | |
| A | `resolve_model(provider, params) -> str` | 필수 | `:217` | 〃 | 동결본은 고정된 모델 |
| A | `resolve_api_key(provider, params) -> str` | 필수 | `:218`, `:537`(anthropic 채널), `:708` | 〃 | |
| A | `resolve_base_url(provider, params) -> Optional[str]` | 필수 | `:219` | 〃 | |
| A | `resolve_credentials(provider, params) -> Optional[Dict]` | 필수 | `:220` | 〃 | |
| B | `make_sandbox(workflow_id: str, user_id) -> Optional[GenySandbox]` | 필수 | `:416` | 〃 | 러너가 죽어 있어도 세션 객체를 돌려줌 |
| B | `agent_workspace_dir(workflow_id, *, create=True) -> str` | 내장 도구가 있을 때 | `:552` | 경고 후 내장 도구 생략 | |
| B | `workspace_storage_root(workflow_id) -> str` | 〃 + rollout | `:585`, `:1036` | rollout 쪽은 조립 실패 | rollout 이 켜졌으면 호스트 설정 `GENY_AGENT_WORKSPACE_ROOT` 가 절대경로여야 함 |
| B | `hydrate_workspace(workflow_id, run_dir) -> bool` | sandbox 가 없을 때만 | `:568` (True/None/False 세 갈래) | False 면 publish 생략 | |
| B | `publish_workspace(workflow_id, run_dir, *, origin="agent") -> None` | **런타임이 부르지 않음** | 없음 (finalize_turn 이 맡음) | — | |
| B | `environment_prompt(sandbox, provider) -> str` | 필수 | `:455` | 조립 실패 | |
| C | `build_memory_provider(workflow_id, interaction_id) -> Optional[MemoryProvider]` | `enable_memory` 일 때 | `:481`, `distill.py:214` | 조립 실패 | DB 기반 provider |
| D | `jobs_prompt_block() -> str` | 작업 도구가 있을 때 | `:443` | 조립 실패 | |
| E | `build_connector_mcp_tools(user_id, client_surface) -> List[Tool]` | 필수 | `:306` | 경고 후 무시 | 연결 카탈로그 스냅샷은 턴마다 1회 |
| E | `build_host_skill_tools(**kwargs) -> List[Tool]` (프로토콜 기본 `[]`) | 사실상 필수 | `:449` | 경고 후 무시 | AppGuide 등 호스트 소유 스킬 도구 |
| E | `build_job_tools(workflow_id, workflow_name, user_id, *, in_scheduled_run: bool, interaction_id: str) -> List[Tool]` | wf·user 있을 때 | `:428` | 경고 후 무시 | |
| E | `register_workflow_self_tools(registry, *, workflow_id, user_id, workflow_name) -> None` | 자기진화가 허용될 때 | `:643` | 경고 후 무시 | |
| E | `register_forged_tools(registry, *, workflow_id, workspace_dir, core: bool, sandboxed: bool) -> None` | 내장 도구 + wf | `:608` | 경고 후 무시 | 게스트 턴 제외 규칙 포함 |
| E | `register_builtin_tools(registry, *, core: bool, user_id, anthropic_api_key: str, ssh_servers) -> Dict[str, Any]` | 도구가 닿는 턴 | `:530` | 경고 후 내장 도구 생략 | **반환 dict 의 `"tools"`·`"extras"` 키를 런타임이 읽는다**(`:545`, `:596`) |
| E | `build_run_tool_context(**kwargs) -> ToolContext` | 〃 | `:593` | 〃 | |
| E | `load_ssh_servers() -> List` | 〃 | `:538` | 〃 | |
| E | `tool_result_filter() -> Optional[async (Tool, ToolResult) -> ToolResult]` | 선택 (getattr, 기본 None) | `:152-166`, `:727` | 실패면 필터 없음 | |
| H | `rag_context_builder(text, item) -> Optional[str]` | 필수 (속성 접근) | `:288` | 항목별 예외는 `collect_rag` 가 흡수 | |
| H | `fetch_vllm_max_model_len(base_url, model) -> Optional[int]` | SDK provider | `:908` | **미확인** (`context_budget.resolve_window` 내부 처리는 정독하지 않음) | |
| H | `agent_vault_root(workflow_id) -> str` | 증류 | `distill.py:77` | 백그라운드 | |
| H | `build_turn_memory_llm(provider, model, api_key, base_url, *, cli_auth_mode="", cli_oauth_token="", cli_binary_path="", credentials=None) -> Optional[Any]` | 증류 | `distill.py:201` | 백그라운드 | |
| G | `finalize_turn(*, sandbox, workflow_id, user_id, hydrated_wf: str, hydrated_ws: Optional[str]) -> None` | 필수 | `:1133` | ⚠ 따로 감싸지 않음. 스트리밍은 `on_close` 의 try 가 삼키고(`runner.py:1636-1640`), 비스트리밍은 예외가 그대로 올라간다(`:1193-1194`) | workspace 반영 |
| F | `build_cli_runtime(provider, params) -> Tuple[client, Optional[cleanup]]` | claude_code·codex | `:779`, `:784` | 조립 실패 | CLI 클라이언트와 MCP 브릿지를 만듦 |
| F | `cli_bridge_available(provider) -> bool` | 선택 (없으면 True) | `:269-280` | 예외면 True | |
| F | `local_device_platform() -> str` | 선택 | `:141-149` | 실패면 None | |
| (비선언) | `memory_write_available(workflow_id) -> bool` | 선택 | `:108-122` (write_available 이 None 일 때만) | 실패면 True | |
| (비선언) | `folder_device_info() -> Optional[dict]` (`name, platform, online, remote`) | 선택 | `:125-138` | 실패면 `{}` | |
| (비선언) | 속성 `record_failed_starts: bool` (기본 True) | 선택 | `runner.py:1125-1135` | — | 호스트에는 없음 → True |

- 프로토콜에 **없어야 할** 것: 위임 훅 6종(`rt-tests/test_host_turn_executor_gates.py:363-372`).
- HITL·권한·hook runner 를 호스트에서 주입하는 경로는 운영에 **없다** [실측]: `host/runner.py`·`turn_executor.py`·`host.py` 에서 `hitl|hook_runner|permission` 은 CLI 의 `permission_mode` 인자(`runner.py:280,318`)만 있다.

**호스트가 돌려주는 객체의 하위 계약**

- `GenySandbox` (`runtime/tools/_geny_sandbox.py:64-117`):
  - 속성: `workdir: str`(절대경로), `extra_roots`, `readonly_roots`
  - 메서드: `async ensure()`, `async exec(argv, *, cwd, stdin, env, timeout_s) -> ExecResult(rc, stdout, stderr)`, `async read_bytes(path)`, `async write_bytes(path, data) -> int`
  - 호스트 세션 객체에는 실시간 반영용 선택 메서드(`enable_live_publish()`, `stop_live_publish()`)가 더 있다. 런타임은 부르지 않는다.
  - 런타임이 직접 쓰는 것: `_sandbox.workdir`(`turn_executor.py:563,869`), `RunnerFS(_sandbox, workdir)`(`:869`), `hydrate_sandbox_images(..., _sandbox, ...)`(`:1006`)
- `MemoryProvider` (`runtime/memory/provider.py:1139-1182`):
  - 메서드: `descriptor/initialize/close/stm/ltm/notes/vector/curated/global_/index/retrieve/record_turn/record_execution/reflect/snapshot/restore/promote/set_hooks`
  - 호스트 구현: DB 기반 provider(STM·notes·vector·index 계층)와 읽기 전용 provider
  - 하네스가 실제로 부르는 것: `notes().list/read/write/update`(execution_record·conversation_archive), `stm()/ltm()/vector()/curated()/index()/set_hooks()`(retriever), `record_turn()`(strategy), `close()`(teardown)
- `ToolContext` (`runtime/tools/base.py:102-201`): `build_run_tool_context` 가 `ToolContext(session_id, working_dir, storage_path, allowed_paths, extras, sandbox)` 로 만든다. 런타임은 `result_filter` 를 덮어쓰고(`turn_executor.py:729`) `working_dir` 를 읽는다(`:870,982`).
- `build_cli_runtime` 이 돌려주는 client: `BaseClient` 하위(`ClaudeCodeCLIClient`/`CodexCLIClient`). 파이프라인 Stage 6 이 `create_message_stream(...)` 청크(`text_delta`, `thinking_delta`, `tool_use`, `input_json_delta`, `content_block_stop`, `tool_result`, `message_complete{response}`)와 `capabilities.is_subprocess`·`streaming_granularity` 를 읽는다(`runtime/stages/s06_api/artifact/default/stage.py:925-1013`).

### 2.5 `build_pipeline` (`runtime/host/runner.py:495-799`) — 경계 B·C

```python
def build_pipeline(
    *,
    name: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: Optional[str] = None,
    system_prompt: str = "",
    registry: Optional[ToolRegistry] = None,
    max_iterations: int = 20,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    stream: bool = True,
    output_schema: Optional[Dict[str, Any]] = None,
    llm_client: Optional[Any] = None,
    memory_provider: Optional[Any] = None,
    memory_distill_spec: Optional[Any] = None,
    tool_context: Optional[Any] = None,
    tool_result_filter: Optional[Any] = None,
    context_window_budget: int = 0,
    enable_compaction: bool = True,
    credentials: Optional[Dict[str, Any]] = None,
    enable_prompt_cache: bool = False,
    enable_deliverable_review: bool = True,
    repeat_stop_after: Optional[int] = 3,
    prune_over_tokens: Optional[int] = DEFAULT_PRUNE_OVER_TOKENS,   # 30,000
    turn_input_budget_tokens: Optional[Tuple[int, int]] = (
        DEFAULT_TURN_SOFT_TOKENS,
        DEFAULT_TURN_HARD_TOKENS,
    ),                                                               # (1M, 3M)
    thinking_level: Optional[str] = None,
) -> Pipeline:
```

**반환은 `Pipeline` 인스턴스**다. `stream_turn`/`run_turn` 이 그 위에서 쓰는 표면은 다음과 같다.
- `run(input, state)`, `run_stream(input, state)`, `aclose()`, `attach_runtime(session_runtime=)`
- 사적 속성 `_memory_provider`·`_memory_distill_spec`(`:778,793`, teardown `:1174-1230`), `_resolved_provider_name(state)`(`:1079-1081`), `_attached_session_runtime`(`:1262`)
- 경계 A 로 바꾸면 이 표면은 하네스 내부가 된다.

**kwargs 의미 계약** (구현 근거)

| kwarg | 의미 | 근거 |
|---|---|---|
| `output_schema` | 시스템 프롬프트에 `# Output format` 지시를 붙이고 `StructuredOutputParser` 로 교체. 비스트리밍 최종 텍스트는 `settle_structured` 로 정규 JSON | `:599-600`, `:685-694`, `:448-454`, `:807-833` |
| `llm_client` | 있으면 provider·키 배선을 무시한다. 없으면 `build_client(provider, api_key, base_url[, credentials=])`. credentials 가 비었으면 3인자 형태로 부른다(테스트 monkeypatch 보존) | `:696-704`, `:68-95` |
| `registry` | 숨긴 도구가 있으면 `ToolSearch` 를, 자기확장 계열이 있으면 `SelfExtendGuide` 를 core 로 등록 | `:471-492`, `:596`, `:637-638` |
| `context_window_budget` | 0 이면 런타임 기본(200k). Stage 2 압축 80%, Stage 4 guard, Stage 16 판정의 기준 | `:546-551`, `:608-610` |
| `enable_compaction` | False 면 압축·prune·guard 를 모두 끈다. Stage 2 는 메모리 배선용으로 늘 등록 | `:553-559`, `:650-675` |
| `enable_prompt_cache` | Stage 5 aggressive (anthropic·bedrock 만 표시) | `:630-636` |
| `memory_provider` | Stage 2 retriever + Stage 18 `ConversationArchivingStrategy` + 프롬프트 블록(base, PinnedFacts, DateTime, RetrievedMemory, TurnNotes) | `:751-798` |
| `tool_context` | Stage 10 컨텍스트로 attach, 완료 직전 산출물 대조(`enable_deliverable_review`) | `:706-722` |
| `tool_result_filter` | Stage 10 컨텍스트의 `result_filter` | `:724-729` |
| `repeat_stop_after`·`turn_input_budget_tokens` | Stage 16 반복 거부 종료·턴 입력 예산 | `:731-749` |
| `thinking_level` | `ModelConfig.thinking_level` (클라이언트가 모델별 요청으로 바꾼다) | `:611-613` |
| `max_iterations` | `max_iterations` + `with_loop(max_turns=)` | `:606`, `:628` |

turn_executor 가 넘기는 값(`turn_executor.py:1044-1096`): CLI 면 `registry=None`, `tool_context=None`, `tool_result_filter=None` 이고 `enable_compaction=False`(`:1052, 1063, 1065, 1072`)다. `enable_deliverable_review` 는 넘기지 않는다(기본 True).

### 2.6 `stream_turn` / `run_turn` / `turn_usage` — 경계 B·C

```python
# runtime/host/runner.py:1340-1355
def stream_turn(
    pipeline: Pipeline,
    text: Any,
    state: PipelineState,
    *,
    tool_events: bool = True,
    result_sink: Optional[Dict[str, str]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    on_close: Optional[Callable[[], None]] = None,
    host: Optional[Any] = None,
    rollout_path: Optional[str | os.PathLike[str]] = None,
    max_continuation_slices: int = DEFAULT_MAX_CONTINUATION_SLICES,   # 2
    usage_sink: Optional[Dict[str, Any]] = None,
    on_loop: Optional[Callable[[Any], None]] = None,
) -> Iterator[Union[str, Dict[str, Any]]]:

# runtime/host/runner.py:1643-1654
def run_turn(
    pipeline: Pipeline,
    text: Any,
    state: PipelineState,
    *,
    output_schema: Optional[Dict[str, Any]] = None,
    host: Optional[Any] = None,
    usage_sink: Optional[Dict[str, Any]] = None,
    rollout_path: Optional[str | os.PathLike[str]] = None,
    max_continuation_slices: int = DEFAULT_MAX_CONTINUATION_SLICES,
    on_loop: Optional[Callable[[Any], None]] = None,
) -> str:

# runtime/host/runner.py:1038
def turn_usage(pipeline: Pipeline, state: PipelineState) -> Optional[Dict[str, Any]]:
```

- `on_loop`: 턴 루프가 열릴 때 `on_loop(loop)`, 닫힐 때 `on_loop(None)` 를 부른다(`:1393, 1606, 1664, 1719`). CLI 턴에서는 `TurnToolSurface.bind_loop` 다(`turn_executor.py:1165,1191`). 브릿지 호출을 이 루프에서 실행하려고 쓴다(§4.4).
- 이어가기: 슬라이스가 `resumable` 이면 `CONTINUE_RUN` 으로 최대 `max_continuation_slices` 번 다시 돈다(`:1533-1546`, `:1680-1682`).
- teardown 순서(`:1588-1640`):
  1. `usage_sink` 채움(비었을 때만, `partial=True`)
  2. `agen.aclose()`
  3. `pipeline.aclose()`
  4. `on_loop(None)`
  5. rollout 종료·보존 정리와 `session_runtime` 복원
  6. 메모리 실행 기록(10초 상한)
  7. 메모리 provider close → 증류 발사(데몬 스레드)
  8. `loop.close()`
  9. `on_close()`

### 2.7 `execution_record.record_turn_execution` (`runtime/host/execution_record.py:116-228`)

```python
async def record_turn_execution(provider: Any, *, input_text: str, output_text: str, success: bool,
    duration_ms: int, session_id: str, provider_name: str = "", model: str = "", error: str = "",
    cancelled: bool = False, tool_calls: int = 0, tool_failures: int = 0, blocked: int = 0) -> None
```

턴이 끝날 때마다 vault 에 두 가지를 쓴다(형식은 §5.6). 런타임 안에서는 `runner._record_execution` 만 부르고(`runner.py:1180-1200`), 호스트는 재노출만 한다(운영 사용 0). 도구 통계는 `state.events` 의 `tool.execute_complete{count,errors}`·`tool.repeat_blocked{tools}` 에서 센다(`runner.py:1141-1154`).

### 2.8 `session/`, `runtime/`, `gateway/` 패키지

| 패키지 | export | 소비자 |
|---|---|---|
| `session` | `Session, SessionManager, FreshnessPolicy, FreshnessStatus, FileSessionPersistence` (`runtime/session/__init__.py:3-13`) | 없음 |
| `runtime` | `BackgroundTaskExecutor, BackgroundTaskRunner, FileBackedRegistry, InMemoryRegistry, LocalBashExecutor, TaskFilter, TaskRecord, TaskRegistry, TaskStatus` (`runtime/runtime/__init__.py:22-45`) | 없음 |
| `gateway` | `InboundMessage, GatewayReply, PlatformAdapter, GatewayRunner, GatewayHandler, Telegram/Discord/Slack…, build_gateway, build_platform_adapter` (`runtime/gateway/__init__.py:24-47`) | 없음 |

셋 다 운영 진입점이 **아니다**. 교체할 때 패키지를 남길지는 라이브러리 정책으로 정할 일이고, xgen 운영에는 영향이 없다 [실측 검색]. `pyproject.toml` 에 console script·entry-point 도 없다(`[project.scripts]` 부재, 4.69 즈음 `xgen-agent-sidecar` 제거, `CHANGELOG.md:215-227`).

---

## 3. 출력 측

### 3.1 스트리밍 청크 문법

```
chunk := str                                              # 답 텍스트(또는 안내·오류 문장)
       | {"type": "agent_event",    "data": AgentEventData}
       | {"type": "canvas_command", "data": <도구가 낸 payload 그대로>}
       | {"type": "usage",          "data": UsagePayload}    # 끝에 최대 1회
```

이것 말고는 내지 않는다 [실측: `runner.py:1434-1587` 의 yield 전수]. `download_artifact` 는 런타임이 아니라 호스트가 만든다.

### 3.2 파이프라인 이벤트 → 청크 번역 (`runner.py:1434-1529`) — 경계 B·C 에서 유지할 이벤트

| 파이프라인 이벤트 (`PipelineEvent.type`) | 읽는 `data` 키 | 청크 | 발생 위치 |
|---|---|---|---|
| `text.delta` | `text`, `granularity`(`"message"` 이면 같은 문장 반복을 합침) | `str` | `stages/s06_api/artifact/default/stage.py:972-987` |
| `pipeline.complete` | `status`, `resumable`, `termination_reason`, `result` (그 슬라이스에서 텍스트를 하나도 흘리지 않았을 때만 `result` 를 str 로 낸다) | `str`(폴백) | `core/pipeline.py:2630-2647` (스트림 경로. `run()` 경로 `:2493-2504` 에는 `result` 가 없다) |
| `pipeline.error` | `error` | `"\n[ERROR] {error}"` | `core/pipeline.py:2651-2660`, `:2710-2717` |
| `tool.call_start` (tool_events) | `name`, `input`, `tool_use_id` | agent_event `tool_call` | `stages/s10_tool/artifact/default/executors.py:73-83` |
| `tool.call_complete` (tool_events) | `name`, `error`, `result`, `is_error`, `duration_ms`, `tool_use_id` (결과가 없으면 `result_sink[name]`) | agent_event `tool_result`/`tool_error` | `executors.py:120-155` (결과 8000자, 오류 2000자에서 자름) |
| `canvas_command` | 전체 `data` | `{"type":"canvas_command","data":…}` (tool_events 와 무관) | 호스트 도구(예: WorkflowSelf)가 `state_view.add_event("canvas_command", payload)` 로 냄 |
| `api.cli_tool_call` (tool_events) | `name`, `id`, `input` | agent_event `tool_call` (시작 시각 기록) | `stage.py:990-999` |
| `api.tool_result` + `source=="cli"` (tool_events) | `tool_use_id`, `content`(블록 → 텍스트, `_stringify_content`), `is_error` | agent_event `tool_result`/`tool_error` (duration 은 러너가 잰다) | `stage.py:1004-1013` |

**밖으로 내보내지 않는 이벤트** [실측]: `thinking.delta` 와 `pipeline.start`, 그리고 `stage.*`·`api.request/response/ttft/retry`·`token.tracked`·`loop.*`·`context.*`·`memory.*`·`tool.execute_*`·`tool.repeat_*`·`hitl.*` 등 이벤트 카탈로그(`runtime/events/catalog.py:82-308`, 버전 16)의 나머지 전부. 따라서 **소비자는 thinking 텍스트를 받지 않는다.**

### 3.3 `agent_event.data` 하위 타입

```python
# tool_call  (runner.py:919-937)
{"type": "tool_call", "tool_name": str, "tool_input": str,      # dict 면 json.dumps(ensure_ascii=False)
 "timestamp": iso8601, "tool_use_id"?: str, "run_id"?: str,     # 같은 값. id 가 비면 키 자체를 뺀다(:900-916)
 "indicator"?: {display_label, verb_running, verb_done, icon, category,
                expected_duration_ms, render_hint, tool_name}}  # host/tool_indicators.py:99-124
# tool_result (runner.py:953-982)
{"type": "tool_result", "tool_name": str,
 "result": str,                  # 4000자 넘으면 머리 3200 + "…[N chars truncated]…" + 꼬리 800 (:60-61, :940-950)
 "result_length": int,           # 자르기 전 길이
 "citations": None, "timestamp": iso8601,
 "duration_ms"?: int, "tool_use_id"?: str, "run_id"?: str, "indicator"?: {...}}
# tool_error
{"type": "tool_error", "tool_name": str, "error": str,           # 비면 "tool execution failed"
 "timestamp": iso8601, "duration_ms"?: int, "tool_use_id"?: str, "run_id"?: str, "indicator"?: {...}}
# task_progress (runner.py:1535-1544)     — 자동 이어가기 직전
{"type": "task_progress", "status": "continuing", "reason": str, "slice": int, "timestamp": iso8601}
# task_suspended (runner.py:1551-1561)    — 이어가기 한도에 닿음
{"type": "task_suspended", "status": "suspended", "reason": str, "resumable": True,
 "checkpoint_id": Optional[str], "timestamp": iso8601}
# task_blocked (runner.py:1563-1572)
{"type": "task_blocked", "status": "blocked", "reason": str, "resumable": False, "timestamp": iso8601}
```

- 머리+꼬리 자르기에는 이유가 있다. 문서 도구가 결과 **끝**에 다운로드 마커를 붙이고, 호스트가 그 마커를 `download_artifact` 로 승격한다(`runner.py:943-944`).
- `task_*` 3종을 읽는 소비자는 없다 [실측: 호스트·클라이언트 검색 0건]. 사용자는 텍스트 안내(`SUSPEND_NOTICE`)로 보게 된다.

### 3.4 특수 텍스트 청크 (사용자에게 보이는 문장)

| 상수/형태 | 언제 | 근거 |
|---|---|---|
| `CLAMP_NOTICE` ("[안내: 입력이 모델 컨텍스트보다 커서 …]\n\n") | 입력 클램프가 걸렸고 schema 가 없을 때, **첫 청크** | `host/context_budget.py:214-217`, `turn_executor.py:1167-1177` |
| `SUSPEND_NOTICE` | 이어가기 한도에 닿았고 schema 가 없을 때. `task_suspended` 바로 앞 | `runner.py:1310-1313`, `:1547-1550` |
| `BUDGET_NOTICE.format(used=…)` / `REPEAT_NOTICE` | 턴 예산·반복 거부로 끝났을 때(schema 없음) | `runner.py:1317-1337`, `:1573-1576` |
| `"\n[ERROR] {error}"` | `pipeline.error` | `runner.py:1463-1466` |
| `"[ERROR] geny agent could not start: …"` / `"[ERROR104: …]"` | 조기 종료 | §2.1.2 |

### 3.5 usage 페이로드 (`turn_usage`, `runner.py:1038-1122`)

```python
{"input_tokens": int, "output_tokens": int,
 "cache_read_tokens": int, "cache_creation_tokens": int,
 "total_cost_usd": Optional[float],      # provider 보고값(TokenUsage.cost_usd) 우선, 없으면 state.total_cost_usd>0
 "model": Optional[str],                 # last_api_response.model → state.model
 "provider": Optional[str],              # pipeline._resolved_provider_name(state) → state.llm_client.provider
 "calls": int,                           # 모델 왕복 수
 "first_call_prompt_tokens": int,        # 첫 호출 프롬프트(anthropic/bedrock 은 캐시 포함)
 "max_call_prompt_tokens": int,
 "harness"?: {"components": {name: int},  # host/harness_components.py:19-51
              "fast_path": {"active","reason","file_count","total_bytes"}},
 "partial"?: True}                       # 취소·닫힘으로 끝난 턴
```

- 출처는 `state.turn_token_usage`(Stage 7 이 API 호출마다 쌓는 `TokenUsage` 목록)다. API 호출이 0회면 `None` 이고, 이때 usage 청크를 내지 않는다(`:1064-1066`).
- 캐시 의미: anthropic·bedrock 은 캐시를 `input_tokens` **밖에서** 따로 센다. OpenAI 계열은 안에 포함한다(`:1086-1090`). 호스트는 이 차이를 전제로 쿼터를 계산한다(anthropic·bedrock 일 때만 캐시를 더함). **이 비대칭을 바꾸면 과금이 틀어진다.**
- `harness.components` 의 장치 이름: `repeat_guard, second_machine, user_denied, message_repair, api_retry, repeat_stop, turn_budget, completion_review, context_prune, context_compact`(`harness_components.py:19-33`). 호스트의 trace 수집기가 장치별로 누적한다. 새 하네스에 같은 장치가 없으면 키가 비거나 0 이 된다. 호스트는 키가 없어도 견딘다(`isinstance(..., dict)` 검사).

### 3.6 순서·종료 보장

1. (선택) `CLAMP_NOTICE` 가 가장 먼저 온다.
2. 슬라이스 안에서는 텍스트 청크와 도구 agent_event 가 **파이프라인 이벤트 순서 그대로** 섞여 나온다. 같은 호출의 `tool_call` 은 `tool_result|tool_error` 보다 먼저 온다(Stage 10 은 start → 실행 → complete).
3. 슬라이스가 끝날 때: 이어가면 `task_progress` 를 내고 다음 슬라이스로 간다. 아니면 다음 중 **하나**: [`SUSPEND_NOTICE`, `task_suspended`] / `task_blocked` / 예산·반복 안내.
4. usage 청크가 **정확히 1회, 마지막에** 온다(오류 턴 포함, 협조적 취소면 `partial`). `rt-tests/test_host_runner_usage.py:123,190,208` 이 고정한다.
5. 소비자가 `.close()` 하면 `GeneratorExit` 가 일어나 usage 청크는 없다. 대신 `usage_sink` 를 `partial=True` 로 채우고(`:1589-1596`) teardown 은 끝까지 돈다.
6. `pipeline.error` 다음에도 턴은 "완료"로 치고, 남은 단계(안내·usage)를 마저 돈다(`turn_completed=True`, `:1577`).

### 3.7 비스트리밍 반환 (`run_turn`, `runner.py:1677-1710`)

| 결과 | 반환 문자열 |
|---|---|
| 성공 + schema | `settle_structured(text, schema)`: 검증되면 압축 JSON, 실패하면 원문 |
| 성공 | `result.text + _stop_notice(state)` |
| `status=="suspended"` | `"[SUSPENDED] {reason or 'slice_limit'}"` |
| `status=="blocked"` | `"[BLOCKED] {reason or 'blocked'}"` |
| 그 밖의 실패 | `"[ERROR] {result.error}"` |
| 예외 | 그대로 raise (`:1711-1713`) |

### 3.8 파이프라인 층 출력 — 경계 C

- `PipelineEvent` dataclass(`runtime/events/types.py:10-40`): `type, stage, iteration, timestamp(UTC iso), data, session_id, run_id, seq`. 필드 순서는 계약 테스트가 고정한다.
- `PipelineResult`(`runtime/core/result.py:13-110`): 16필드와 property `status`·`termination_reason`·`resumable`·`checkpoint_id` (`:55-77`).
- `RunStatus` 값 `running|completed|suspended|blocked|failed|cancelled`, `TerminationReason` 11종(`runtime/core/run_status.py:14-38`).
- `ExecutorErrorCode` 문자열(동결, `rt-tests/contract/test_error_codes_stability.py:39`).
- rollout JSONL 은 `dataclasses.asdict(PipelineEvent)` 를 한 줄씩 쓴다(`runtime/core/rollout_recorder.py:307-318`). 경계 A 에서도 rollout 을 켠 배포에서는 이 형식이 남는다(§5.6).

### 3.9 소비자가 실제로 읽는 출력 필드 (추적 결과)

| 소비자(호스트 쪽 역할) | 읽는 것 |
|---|---|
| Agent 노드의 스트림 래퍼 | `chunk["type"]=="usage"` 면 `data` 를 trace 에 적산하고 **흘리지 않음**. `agent_event.data.type=="tool_call"` 이면 앞선 텍스트 span 을 기록. `tool_result.result` 에서 다운로드 마커를 꺼냄. `str` 청크는 오류 코드 변환을 거침 |
| 도구 span 기록 | `type, tool_name, tool_input, result, error, tool_use_id|run_id, duration_ms, timestamp` |
| 턴 usage 기록 | `input_tokens, output_tokens, model, cache_read_tokens, cache_creation_tokens, provider, calls, first_call_prompt_tokens, max_call_prompt_tokens, partial, harness, total_cost_usd` |
| 실행기 | `str` 를 모아 최종 텍스트로. dict 중 `agent_event|canvas_command|download_artifact` 만 클라이언트로 통과(`yield_output=False` 여도 통과), 나머지는 하류 노드 입력 버퍼. 비 generator 결과는 `str(result)` |
| SSE 계층 | `event_type|type, tool_name, tool_input(call/start만), result, result_length(result만), error(error만), citations, run_id, tool_use_id, indicator, duration_ms, timestamp` → SSE `{"type":"tool"}`. `canvas_command`·`download_artifact` 는 그대로 통과 |
| trace 수집기 | usage 키 전부(위) |
| 웹 UI | `event_type`(tool_call/tool_start/tool_result/tool_error), `tool_name`, `tool_input`(JSON 문자열), `result`, `error`, `tool_use_id ?? run_id`, `duration_ms` |
| OpenAI 호환 엔드포인트 | 본문 안의 `[ERRORnnn: …]` 마커 → HTTP 상태 |

---

## 4. 소비자 측 호출부

### 4.1 Agent 노드 → `AgentTurnExecutor().run`

**run() 을 부르기 전에 호스트가 kwargs 를 이렇게 바꾼다** (런타임이 받는 값의 출처, 요지)

1. `node_name` 기본값
2. 옛 포트(rag_context/args_schema/skills)와 옛 provider 이름(auto/deepseek) 정규화
3. 대화에서 고른 provider·model·thinking 적용
4. 자격증명 사전 검사. 없으면 오류 코드 문자열을 바로 반환(런타임을 부르지 않음)
5. 시스템 프롬프트의 자리표시자 치환
6. trace 에 provider·model 기록
7. 동결본(증류 off 등)·게스트·대화별 폴더(`local_folders`, 폴더 기기) 적용
8. 통제 정책 적용(입력 텍스트·도구를 가공하고 정책 정보를 kwargs 에 실음)
9. `HostServices` 구현 객체 생성(노드, provider, 원본 kwargs 를 들고 있음)
10. `kwargs["usage_sink"] = {}`, `setdefault("enable_prompt_cache", True)`, `setdefault("max_continuation_slices", 10)`
11. `result = AgentTurnExecutor().run(host, **kwargs)`

**받은 뒤**: `str|bytes|dict` 이거나 iterable 이 아니면 오류 코드 변환을 거쳐 그대로 돌려준다. 그 밖에는 스트림 래퍼를 거친다. 스트림이 닫혀 usage 청크를 못 받았으면 `kwargs["usage_sink"]` 로 기록한다. 이 `kwargs` 는 노드 쪽 dict 이고, `run(**kwargs)` 로 얕게 복사되지만 `usage_sink` 의 **값 객체는 같다**.

### 4.2 실행기의 kwargs 주입

| 실행기 | 주입 키 |
|---|---|
| 워크플로 실행기(agents 계열 노드) | `interaction_id, response_io_id, workflow_id, workflow_name, node_id, node_name, user_id` (setdefault). `client_surface, client_device_id, local_folders` (강제). Agent-XGeny 노드면 `cancel_check`. `trace` |
| 에이전트 전용 실행기 | 같은 집합 + 포트 payload(`tools, context, memory, output_schema`), `text`(첨부가 있으면 dict). 공급 노드가 실패하면 `system_prompt` 에 안내를 덧붙임(키가 없으면 `default_prompt` 를 먼저 복원) |

### 4.3 호스트 구현이 맡는 것

메서드별 요지는 §2.4 표에 있다. 런타임이 모르는 **정책**은 모두 호스트가 집행한다.
- 게스트(`guest_`/`deploy_` 접두)·동결 턴의 도구 제한 같은 정책은 호스트가 레지스트리·스킬 목록·작업 도구 단계에서 적용한다. 런타임은 정책 내용을 모른다.
- 연결 카탈로그 스냅샷은 턴마다 1회.
- 런타임의 선택 훅 프로브와 이름이 맞아야 한다: `memory_write_available`, `folder_device_info`, `local_device_platform`, `cli_bridge_available`, `tool_result_filter`.

### 4.4 CLI provider 경로: `TurnToolSurface` 계약 (`runtime/host/tool_surface.py:34-217`)

```python
class TurnToolSurface:
    def __init__(self, *, registry: Any, tool_context: Any, state: Any, server_name: str = "connector") -> None
    registry; tool_context; state; server_name; events: List[Tuple[str, Dict]]
    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop]) -> None
    @property running -> bool
    def exposed_names(self) -> Tuple[str, ...]
    def tools_list(self) -> List[Dict[str, Any]]          # [{"name","description","inputSchema"}]
    async def call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]   # {"content":[...], "isError": bool}
def to_mcp_result(result: Dict[str, Any]) -> Dict[str, Any]
```

호스트의 사용 방식:
- CLI 런타임을 만들 때 표면을 턴 토큰에 묶어 MCP 브릿지에 등록하고, `len(surface.registry)`·`surface.exposed_names()` 로 노출 상태를 본다.
- CLI 작업 디렉터리는 `surface.tool_context.working_dir` 에서 얻는다.
- 내부 JSON-RPC 엔드포인트: `tools/list → surface.tools_list()`. `tools/call → before=surface.exposed_names(); await surface.call(name, args)`. 호출 뒤 노출 목록이 바뀌었으면 결과 메타데이터에 목록 변경 표시를 붙인다.

실행 계약:
- `call()` 은 **턴 루프**(`bind_loop` 로 받은 루프)에서 `ToolStage.dispatch_calls` 를 돈다. 다른 스레드에서 오면 `run_coroutine_threadsafe` 를 쓴다(`tool_surface.py:155-170`).
- 루프가 없으면 `{"content":[{"type":"text","text":"Error: this turn is no longer running."}],"isError":True}` 를 돌려준다.
- 표면 안의 도구 사건은 파이프라인 버스로 내지 않고 `surface.events` 에만 남긴다(`:57-59`, `:136-139`). CLI 도구의 UI 사건은 CLI 스트림의 `api.cli_tool_call`/`api.tool_result(source=cli)` 에서 온다(§3.2).
- `TurnToolSurface.state` 는 파이프라인 state 와 같은 객체다(`turn_executor.py:743-748`). 그래서 CLI 경로에서도 도구의 `state_view.add_event("canvas_command")` 가 같은 버스로 나간다고 본다(**미확인**: 실측하지 않음).
- MCP 서버 이름 `"connector"`(`turn_executor.py:261`), 도구 이름 `mcp__connector__X`(`_constants.py:121-133`). 호스트의 CLI 설정은 이 접두를 전제로 구성된다.
- codex 는 턴 중 목록을 다시 읽지 않는다. 그래서 강제로 평면 노출한다(`turn_executor.py:38`, `:234-241`). 호스트 브릿지도 codex 에서는 목록 재조회 대기를 0 으로 둔다.

### 4.5 다른 구성요소의 소비

| 구성요소 | 의존 | 런타임 쪽 근거 |
|---|---|---|
| XGEN LLM 관리 서비스 | `xgen_agent_runtime.llm_client.azure_foundry.AzureEndpoint(endpoint).v1_base_url`. 런타임이 없어도 돈다(try/except) | `runtime/llm_client/azure_foundry.py` |
| 샌드박스 서비스 | 문자열 `".xgeny/python-env.json"` == 런타임 `host.python_env.ENV_FILE` | `runtime/host/python_env.py:35` |
| 데스크톱 클라이언트 | 거부 문구 `'사용자가 이 명령의 실행을 거부했습니다 (위험할 수 있는 명령).'` 를 런타임이 사용자 거부로 인식 | `runtime/host/tools.py:136-140` |
| 웹 UI | agent_event 필드(§3.9), ForgeTool 결과 `{"ok":true,"name",…}` 를 JSON 문자열로 받는다는 가정 | `runtime/host/forged_tools.py` |
| 배포 설정 | 컨테이너 TZ(런타임 4.50.0+ 날짜 블록이 전제) | — |
| 배포 핀 | 호스트가 `xgen-agent-runtime` v4.75.0 GitHub Release wheel 을 URL 로 정확 고정 | `pyproject.toml:6-7` |

### 4.6 정의만 있고 운영에서 안 쓰는 것 vs 실제로 쓰는 것

| 항목 | 상태 | 근거 |
|---|---|---|
| kwargs `repeat_stop_after`, `prune_over_tokens`, `turn_input_budget_tokens`, `enable_workspace_fast_path` | **정의만 있음**. 노드 파라미터에 없고 운영 코드에서도 넣지 않는다 → 런타임 기본값으로 돈다 | §2.1.1, 호스트 검색 |
| kwargs `enable_prompt_cache`, `max_continuation_slices` | 노드가 `setdefault` 로 **늘 넣는다**(True, 10) | §4.1 |
| kwargs `_self_evolution_allowed` (출력) | 쓰기만 하고 읽는 곳 없음 | `turn_executor.py:470` |
| `HostServices.publish_workspace` | 런타임이 부르지 않음 | §2.4 |
| `build_pipeline(enable_deliverable_review=)` | turn_executor 가 넘기지 않음(기본 True) | `turn_executor.py:1044-1096` |
| `stream_turn(result_sink=)` | 넘기지만 이제는 보조다. 결과는 `tool.call_complete.result` 가 우선 | `runner.py:1482-1488` |
| agent_event `task_progress/suspended/blocked` | 내보내지만 읽는 소비자 없음 | §3.3 |
| `host.record_failed_starts` | 호스트에는 없음 → 늘 True | `runner.py:1135` |
| `session/`·`runtime/`·`gateway/` | 소비자 없음 | §2.8 |

---

## 5. 부수 채널 계약 (암묵 계약)

### 5.1 kwargs 를 통한 양방향 전달

- 런타임은 `run(host, **kwargs)` 로 받은 **얕은 복사본** dict 에 `_sandbox_session`(`:421`)과 `_tool_surface`(`:752`)를 써 넣는다. 그 dict 를 `host.build_host_skill_tools(**kwargs)`(`:449`)와 `host.build_cli_runtime(provider, kwargs)`(`:779`)로 넘기고, 호스트가 그 키를 읽는다.
- **순서 계약**: `make_sandbox` → `_sandbox_session` 쓰기 → `build_host_skill_tools`. `TurnToolSurface` 생성 → `_tool_surface` 쓰기 → `build_cli_runtime`.
- 호스트 객체가 들고 있는 params 는 노드 쪽 원본 dict 다. 런타임이 쓴 키는 거기에 **보이지 않는다**. 호스트가 그 키를 원본 dict 에서 읽도록 바뀌면 깨진다.

### 5.2 `usage_sink` 객체 동일성

노드가 만든 `{}` 를 런타임이 `update` 로 채운다(`runner.py:1585-1586`, `:1593-1594`, `:1686-1688`). **새 dict 로 바꿔 끼우면 안 된다.** 이미 usage 청크로 받은 턴에도 sink 가 채워지므로, 호스트는 둘 중 하나만 쓴다(이중 기록 방지, 호스트 테스트가 고정).

### 5.3 프로세스 전역 상태 (모듈 단일성)

- `cancel_context._cancelled_until`(`cancel_context.py:129`): 호스트가 `request_cancel` 로 쓰고 런타임 `is_cancelled` 가 읽는다(§2.3). 모듈 경로 `xgen_agent_runtime.host.cancel_context` 를 남겨야 하고, 새 하네스도 같은 모듈의 `is_cancelled` 를 써야 한다.
- 증류의 in-flight·pending 레지스트리(`host/distill.py:40-45`)와 데몬 스레드(`:302`): 워크플로당 하나로 합친다. 프로세스 수명에 기대는 동작이다.
- 호스트 MCP 브릿지의 토큰 → 표면 등록: 표면 객체가 턴 동안 살아 있어야 한다.

### 5.4 도구 ABI 와 `ToolContext.state_view` (호스트가 만든 도구가 하네스에 기대는 것)

- `Tool` 추상 클래스(`runtime/tools/base.py:304-498`): `name`, `description`, `input_schema`, `async execute(input, context) -> ToolResult`, `capabilities(input) -> ToolCapabilities`, `to_api_format()` 같은 것. 호스트 도구가 직접 상속한다. `build_tool(...)`(`:526-547`)도 쓴다.
- `ToolResult`(`:210-243`): `content, is_error, metadata, display_text, persist_full, state_mutations, artifacts, new_messages, mcp_meta`. 도구가 `state_mutations={WITNESSED_KEY: [...]}` 를 돌려주면 하네스가 `state.shared` 에 반영한다(`runtime/tools/built_in/_file_witness.py:52-62`). 키는 네임스페이스가 있어야 한다(`executor.file_witnessed`, `runtime/core/shared_keys.py:51`).
- `ToolContext.state_view` 에 호스트 도구가 기대는 것:
  - `add_event(type, data)`: canvas_command 를 스트림으로 내보낸다(`runtime/core/state.py:476-506`)
  - `shared` dict: 파일 witness 장부(`_file_witness.py:42-49`)
  - `pending_tool_calls` (각 항목에 `tool_name`, `tool_use_id`): WorkflowSelf 가 자기 호출 id 를 찾는다
- `ToolContext` 필드 중 호스트 도구가 읽는 것: `sandbox`, `working_dir`, `state_view`, `extras`, `metadata` (+ 없는 필드 `tool_use_id` 를 getattr 로 본다) [실측 검색].
- 어댑터 계약(`runtime/host/tools.py`):
  - Tools 포트 값 모양: `Tool` 인스턴스, LangChain 덕타입, `{"name","func"|"function","description","input_schema"|"args_schema"}` dict, `{"dispatch_tool": …}` 스킬 페이로드(dict·dataclass) (`:310-338`)
  - 이름 충돌이 나면 바꿔 단다(`:365-376`). 예약 이름은 내장 도구 + `memory_*` 6종(`:276-296`)
  - 사용자 거부는 `"ERROR user_denied: …"` 로 정규화한다(`:134-190`)
  - 반환이 `None` 이면 "연결된 도구 없음"이다(`:362-363`)
- Context 포트 값 모양(`runtime/host/rag.py:34-80`): `{"tool": …}` 은 도구로 등록한다. `{"rag_service", "search_params"}` 는 `host.rag_context_builder` 로 검색한다. 그 밖의 str·`{content|text}` 는 그대로 붙인다. RAG 블록은 **사용자 턴 뒤**에 붙인다(`turn_executor.py:971`).
- Memory 포트 값 모양(`runtime/host/memory.py:39-74`): `List[BaseMessage]`, `{"role"|"type","content"}` dict, `(messages, context_str)` 튜플. system·tool 역할은 버리고 텍스트만 남긴다.

### 5.5 설정 키와 환경 변수

| 이름 | 읽는 곳 | 경로 | 의미 |
|---|---|---|---|
| `GENY_TOOLS_WORKFLOW_SELF_ENABLED` | `_constants.py:170` | `host.setting` | 0/false/no/off 면 자기진화 끔 |
| `CLAUDE_CODE_AUTH_MODE`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_BINARY_PATH` | `turn_executor.py:699-709` | `host.setting` | 증류 LLM 인증 채널 |
| `GENY_PREFETCH_REFERENCED_FILES` (기본 "1") | `:853` | `host.setting` | 요청 파일 미리 붙이기 |
| `WORKSPACE_FAST_PATH_ENABLED` | `host/workspace_fast_path.py:19`, `turn_executor.py:803` | `host.setting_truthy` | 빠른 경로 |
| `GENY_ROLLOUT_RECORDING_ENABLED` | `host/rollouts.py:23`, `turn_executor.py:1028` | `host.setting_truthy` | rollout JSONL |
| `GENY_IMAGE_MAX_BYTES`(20MiB), `GENY_TURN_IMAGE_MAX_BYTES`(40MiB), 옛 이름 `XGENY_*` | `turn_executor.py:47-63` | **`os.getenv`, 모듈 import 시점** | 세션 이미지 첨부 예산 |
| `CLI_QUIET_ENV` = `DISABLE_AUTOUPDATER=1, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1, MAX_MCP_OUTPUT_TOKENS=150000` | `runner.py:261-267` | CLI subprocess env | |
| `GENY_CLI_PREWARM`, `CLAUDE_CODE_BINARY`, `CODEX_BINARY` | `llm_client/claude_code.py:257,287`, `llm_client/codex.py:167` | env | LLM 클라이언트 층 |
| 호스트 쪽: `GENY_AGENT_WORKSPACE_ROOT`(rollout 이 켜지면 절대경로 필수) | 호스트 | env | 호스트 소유 |

하네스 코어(`core/`·`stages/`)는 env 를 읽지 않는다 [실측: `core/environment.py:1646` 의 subprocess env 병합 하나만 있음]. 설정은 모두 `host.setting*` 으로 들어온다. 새 하네스도 **env 를 직접 읽지 말고 host 로 받는 규약**을 지키는 편이 안전하다.

### 5.6 영속 상태 형식

| 형식 | 쓰는 곳 | 형태 | 읽는 소비자 |
|---|---|---|---|
| 실행 카드 (vault `daily`) | `execution_record.py:149-202` | 제목 `Execution #N — <입력 60자>`(취소면 ` · cancelled`), 태그 `execution` + `success|partial|failure` + `auto` (+`cancelled`), importance MEDIUM/HIGH/LOW, 파일명 `exec-{N:04d}-{hex8}.md`, frontmatter `session_id, execution_number, success, outcome, tool_calls, tool_failures, duration_ms`. N = `notes.list(category="daily", tag="execution")` 개수 + 1 | 호스트의 메모리 브라우저(범용 카테고리 열람). 특정 키 직접 의존은 **미확인** |
| 실행 저널 (vault `executions`) | `:204-228` | `executions-YYYY-MM-DD.md`. 한 줄 `- HH:MM {✅|⚠️|❌} #N <입력> (s)` 를 append | 〃 |
| 대화 rollup (vault `conversations`) | `host/conversation_archive.py:157-246` | `<sid>__user.md`(옛 이름) 또는 `<sid>__user__<slug>.md`, `## turn-<eid8>` + `<!--meta-->`. frontmatter `session_id, date_first, date_last, turn_count, kinds, counterparts, event_ids[:200]`. sid 는 `_safe_id(state.session_id)` = interaction_id | 〃 |
| state.metadata 워터마크 | `turn_executor.py:385-391` | `"memory.provider_strategy_recorded_idx"`, `"geny_bridge.conversation_archived_idx"` = preload 길이 | 미리 불러온 이력을 다시 기록하지 않게 한다(중복 방지) |
| 증류 상태 | `host/distill.py:36,94,140-188,240-302` | `<agent_vault_root>/_distill_state.json`: `passes, last_evergreen_pass, last_launch, last_status(running|ok|skipped|error), last_error, last_report` | **호스트의 메모리 관리 API 가 이 키들을 읽는다** |
| 사실 원장 | 증류 결과 | `<vault>/memory/critical/__facts__.md` | 호스트의 메모리 관리 API(존재 여부) |
| rollout | `host/rollouts.py:28-47`, `core/rollout_recorder.py:307-318` | `<storage_root>/executor/rollouts/rollout-<UTC ts>-<sha256(iid)[:16]>-<uuid>.jsonl`, 최근 100개 유지. 한 줄 = `asdict(PipelineEvent)` | 관리자 opt-in. 읽는 운영 코드는 **미확인** |
| workspace | `host.finalize_turn` | 호스트 소유 (런타임은 `hydrated_ws/hydrated_wf` 만 넘긴다. hydrate 가 성공한 턴에서만 삭제를 전파) | 호스트 |

### 5.7 문자열 계약

- `"[ERROR] "` 접두 → 호스트가 `[ERRORnnn: …]` 형식의 오류 코드로 옮기고, OpenAI 호환 엔드포인트가 그 마커를 HTTP 상태로 다시 매핑한다.
  - ⚠ 형태 차이: 스트리밍 `pipeline.error` 청크는 `"\n[ERROR] …"` 처럼 **줄바꿈으로 시작**한다(`runner.py:1466`). 조기 종료와 비스트리밍 `[ERROR]` 는 줄 시작에 접두가 온다. 호스트는 지금의 두 형태를 전제로 처리하므로, 새 하네스가 이 차이를 바꾸면 사용자에게 보이는 문구가 달라질 수 있다(코드 경로 기준, **미실측**. 처리 방침은 32 문서·PLAN D-6).
- 사용자 거부: `_DENIAL_CODES = {"user_denied","access_denied","denied_by_user"}`, 옛 문구 `"사용자가 이 명령의 실행을 거부했습니다"`(`host/tools.py:134-140`) ↔ 데스크톱 클라이언트(§4.5).
- `ENV_FILE = ".xgeny/python-env.json"` ↔ 샌드박스 서비스(§4.5).
- 게스트 판정 접두 `guest_`/`deploy_`(`_constants.py:162`, 호스트도 같은 접두를 씀), 스케줄 턴 접두 `workflow_schedule_`(`turn_executor.py:434`).

### 5.8 스레드·이벤트 루프 모델

- `run()`·`stream_turn` 은 동기다. 내부 private 루프에서 `run_until_complete` 를 쓴다. 호출 스레드에 돌아가는 루프가 있으면 `asyncio.run` 이 실패한다(**미확인**: 실측하지 않음. Python 규칙상 RuntimeError).
- 메모리 provider 는 턴 스레드·턴 루프에서 만들고 쓰고 닫는다(`runner.py:1206-1219` 주석).
- CLI 표면 호출은 서빙 루프(FastAPI)에서 턴 루프로 `run_coroutine_threadsafe` 된다(§4.4).

### 5.9 테스트 이음매 (호스트 테스트가 기대는 것)

- 호스트 테스트는 `xgen_agent_runtime.host.runner.build_client` 를 `lambda provider, api_key, base_url, **kw: FakeClient(...)` 형태로 바꿔 끼운다.
- `build_pipeline` 도 spy 로 바꿔 끼우고 `**kw` 에서 `registry`·`system_prompt` 같은 값을 읽는다.
- 이 이음매는 turn_executor 가 `build_pipeline` 을 **호출할 때마다** runner 모듈에서 import 하고(`turn_executor.py:178-183`), `build_pipeline` 이 `build_client` 를 모듈 전역에서 찾기 때문에 성립한다(`runner.py:699,703`). 새 하네스가 이것을 깨면 운영은 그대로여도 호스트 테스트 수십 개가 깨진다.
- 가짜 LLM 은 `BaseClient` 를 상속하고 `_send(request, *, purpose="")` 또는 `create_message_stream(...)` 이 `{"type":"text_delta"}`·`{"type":"message_complete","response":APIResponse}` 를 내는 방식이다. `APIResponse(content=[ContentBlock(type="text"|"tool_use", …)], stop_reason=…)` 생성자도 계약이다.

### 5.10 프롬프트 층 계약 ([기본정보] 미러)

호스트의 [기본정보] 화면은 `AgentTurnExecutor.run` 이 만드는 시스템 프롬프트를 실행 없이 "같은 순서·같은 게이팅으로" 다시 만들어 보여 준다. 쓰는 심볼:
- `_constants` 의 `MEMORY_PROMPT_BLOCK`·`MEMORY_READONLY_PROMPT_BLOCK`·`SELF_EVOLUTION_PROMPT_BLOCK`·`cli_tool_naming_note`·`default_prompt`·`_self_evolution_policy`
- `tool_exposure.is_turn_one`, `memory_tools.build_memory_tools`

새 하네스가 프롬프트 조립 순서나 블록을 바꾸면 이 미러가 실제와 어긋난다. 호스트에 미러 동일성 테스트가 있다.

### 5.11 알려진 특이 동작 (재현할지, 의도적으로 고칠지 결정할 것)

1. 첨부가 있는 턴은 `pipeline_input` 이 dict 다. `_record_execution(input_text=text)` → `_clip(dict)` 에서 AttributeError 가 나고 debug 로그만 남는다. 그래서 실행 카드가 기록되지 않는 것으로 보인다(`runner.py:1617`, `execution_record.py:33-35,159`. 코드 경로 기준 **미실측**).
2. 스트리밍 오류 청크의 선행 `\n` 때문에 오류 텍스트 형태가 경로마다 다르다(§5.7).
3. `finalize_turn` 예외가 비스트리밍 턴에서만 밖으로 올라간다(§2.4 G).
4. generator 를 한 번도 돌리지 않으면 teardown 이 돌지 않는다(§2.1).
5. `pipeline.complete` 폴백 텍스트는 슬라이스 단위로 판정한다. 앞 슬라이스만 흘렸으면 뒤 슬라이스의 `result` 를 str 로 다시 낸다(`runner.py:1457-1462`).
6. `hydrate_workspace` 는 프로토콜상 `bool` 인데 런타임은 `None`("해당 없음")도 처리한다(`turn_executor.py:568-580`).

---

## 6. 교체 시 반드시 유지할 계약

"필수" = 운영 경로가 바로 깨지거나 데이터·과금·보안이 틀어짐. "선택" = 테스트·관측·UI 보조, 또는 정의만 있고 운영에서 쓰지 않음. 경계 A 기준.

| 계약 항목 | 위치 | 소비자 | 형태 (signature/fields) | 필수/선택 |
|---|---|---|---|---|
| 턴 진입점 | `runtime/host/turn_executor.py:169-172` | Agent 노드 | `AgentTurnExecutor().run(host, **kwargs) -> Iterator[str|dict] | str`, 모듈 경로 `xgen_agent_runtime.host.turn_executor` | 필수 |
| 반환 형태 분기 | `turn_executor.py:190,1149-1194` | Agent 노드, 실행기 | `streaming` True → iterator, False → `str` | 필수 |
| 동기·워커 스레드 실행 | `runner.py:8-9,1391` | 실행기 스레드풀 | 루프 없는 스레드에서 부름. 내부 private 루프 | 필수 |
| 입력 kwargs 의미 | §2.1.1 | 실행기·노드 | `text, streaming, provider, temperature, system_prompt(키가 없으면 기본), tools, context, memory, output_schema, interaction_id, response_io_id, workflow_id, workflow_name, user_id, client_surface, local_folders, node_name, enable_memory, memory_distill, enable_self_evolution, _frozen, tool_exposure, enable_compaction, max_tokens, context_window, max_iterations, thinking, enable_prompt_cache, cancel_check, tool_events, max_continuation_slices, usage_sink` | 필수 |
| 정의만 있는 kwargs | `turn_executor.py:801,1077-1095` | 없음 | `repeat_stop_after, prune_over_tokens, turn_input_budget_tokens, enable_workspace_fast_path` | 선택 |
| kwargs 통과(같은 dict) | `turn_executor.py:217-220,449,779-787` | 호스트 구현, Agent 노드 | 런타임이 모르는 키(`*_model, api_key, base_url, azure_*, cli_max_budget_usd`, 호스트 내부 표식, `trace…`)를 그대로 넘김 | 필수 |
| kwargs 역방향 키와 순서 | `turn_executor.py:421,752` | 호스트 구현 | `_sandbox_session` 은 `build_host_skill_tools` 전에, `_tool_surface` 는 `build_cli_runtime` 전에 | 필수 |
| `TurnInput` 수용 모양 | `turn_input.py:47-115` | 실행기, 웹/앱 첨부 | str / content array / `{"text","attachments","metadata"}` → 파이프라인에 str 또는 dict | 필수 |
| HostServices 필수 메서드 | `host.py:73-241` | 호스트 구현 | §2.4 표의 "필수" 행(시그니처 그대로) | 필수 |
| HostServices 선택 훅(getattr) | `turn_executor.py:108-166,269-280`, `runner.py:1135` | 호스트 구현 | `memory_write_available, folder_device_info, local_device_platform, cli_bridge_available, tool_result_filter, record_failed_starts`. 없으면 기본 동작 | 필수(이름·의미) |
| `register_builtin_tools` 반환 | `turn_executor.py:545,596` | 호스트 구현 | `{"tools": …, "extras": dict}` | 필수 |
| 호스트 정책 뒤의 표면 판정 | `turn_executor.py:623-632,652` | 게스트·동결 정책 | 메모리·자기진화 문구는 **정책이 적용된 뒤의 registry** 를 보고 고름 | 필수 |
| HostServices 에 위임 훅 없음 | `rt-tests/test_host_turn_executor_gates.py:363` | — | `build_turn_delegation` 등 6종 부재 | 선택 |
| GenySandbox 프로토콜 | `tools/_geny_sandbox.py:64-117` | 호스트 샌드박스 세션 | `workdir, extra_roots, readonly_roots, ensure, exec→ExecResult, read_bytes, write_bytes` | 필수 |
| MemoryProvider 프로토콜 | `memory/provider.py:1139-1182` | 호스트 메모리 provider | `notes/stm/ltm/vector/curated/index/record_turn/close/set_hooks…` | 필수 |
| 청크 문법 | `runner.py:1434-1587` | 스트림 래퍼, 실행기 | `str` / `{"type":"agent_event"}` / `{"type":"canvas_command"}` / `{"type":"usage"}` | 필수 |
| agent_event 도구 필드 | `runner.py:900-982` | 도구 span 기록, SSE 계층, 웹 UI | `type∈{tool_call,tool_result,tool_error}, tool_name, tool_input(str), result(머리+꼬리 4000), result_length, error, citations, timestamp, duration_ms?, tool_use_id?=run_id?, indicator?` | 필수 |
| 결과 꼬리 보존 | `runner.py:60-61,940-950` | 다운로드 마커 승격 | 4000자 넘으면 꼬리 800자 유지 | 필수 |
| agent_event task_* | `runner.py:1535-1572` | 없음 | `task_progress/suspended/blocked` | 선택 |
| 안내 문장 | `runner.py:1310-1326`, `context_budget.py:214` | 사용자 화면 | `CLAMP_NOTICE`(맨 앞), `SUSPEND/BUDGET/REPEAT_NOTICE`. schema 턴에는 붙이지 않음 | 필수 |
| 오류 텍스트 접두 | `runner.py:1466,1702`, `turn_executor.py:1108` | 호스트 오류 코드 변환, OpenAI 호환 엔드포인트 | `"[ERROR] …"`(스트림은 `"\n[ERROR] …"`), `"[ERROR104: …]"`, `"[SUSPENDED] r"`, `"[BLOCKED] r"` | 필수 |
| 구조화 출력 | `runner.py:599-600,685-694,807-833` | 하류 노드 JSON 파싱 | 비스트리밍 최종 = 압축 JSON 또는 원문. 스트림 = 모델 원문 | 필수 |
| usage 페이로드 | `runner.py:1049-1122` | 턴 usage 기록, trace 수집기 | `input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, total_cost_usd, model, provider, calls, first_call_prompt_tokens, max_call_prompt_tokens, harness?, partial?`. anthropic·bedrock 캐시는 input 밖에서 셈 | 필수 |
| usage 1회·끝 + sink | `runner.py:1579-1596`, `:1684-1690` | Agent 노드 | 청크는 정확히 1회 마지막. `usage_sink` 를 같은 객체로 `update`. 닫히면 `partial` | 필수 |
| thinking 미노출 | `runner.py:1434-1529` | 전 소비자 | `thinking.delta` 를 내보내지 않음(지금 동작) | 선택 |
| `.close()` 전파와 teardown | `runner.py:1588-1640` | 스트림 래퍼, 실행기 | 닫히면 파이프라인·CLI·메모리 정리 후 `on_close` | 필수 |
| 협조적 취소 | `cancel_context.py:169-190`, `runner.py:854,867-897` | 실행기, 스트림 헬퍼 | per-turn `cancel_check` 우선, 대기 중에도 폴링, 모듈 전역 레지스트리 공유 | 필수 |
| 자동 이어가기 | `runner.py:1306,1533-1546,1680-1682` | 노드 `max_continuation_slices=10` | `resumable` 슬라이스를 최대 N회 이어감 | 필수 |
| CLI 도구 표면 | `tool_surface.py:34-217` | 호스트 CLI 런타임, MCP 엔드포인트·브릿지 | `registry, tool_context(.working_dir,.result_filter), exposed_names(), tools_list()→[{name,description,inputSchema}], async call()→{content,isError}, bind_loop()` | 필수 (CLI provider) |
| CLI MCP 서버 이름·접두 | `turn_executor.py:261`, `_constants.py:121-133` | 호스트 CLI 설정 | 서버 `"connector"`, 도구 `mcp__connector__X` | 필수 (CLI) |
| `on_loop` 계약 | `runner.py:857-864,1393,1606` | `TurnToolSurface.bind_loop` | 루프가 열리면 loop, 닫히면 None | 필수 (CLI) |
| runner 모듈 심볼 | `runner.py` | 호스트 shim, 앱 LLM, 증류, Codex 서비스, Agent 노드 | `build_client(provider, api_key, base_url, *, credentials=None)`, `_map_provider`, `build_cli_client(...)`, `build_codex_cli_client(...)`, `CLI_NATIVE_TOOLS_DENY`, `CLI_NATIVE_TOOL_CATALOG`, `build_pipeline`, `stream_turn`, `run_turn`, `settle_structured` | 필수 |
| 도구 ABI | `tools/base.py:25-638` | 호스트 도구 다수 | `Tool, ToolResult(state_mutations…), ToolContext(sandbox, working_dir, state_view, extras, metadata, result_filter), ToolCapabilities, build_tool, tool_origin` | 필수 |
| `state_view` 표면 | `core/state.py:476-506` | 호스트 도구(WorkflowSelf, 앱 도구) | `add_event(type, data)` → 스트림, `shared` dict, `pending_tool_calls[{tool_name,tool_use_id}]` | 필수 |
| canvas_command 통과 | `runner.py:1494-1495` | SSE 계층, 실행기, 웹 UI 캔버스 | 도구가 낸 `canvas_command` 이벤트를 `{"type","data"}` 로 바로 내보냄(tool_events 와 무관) | 필수 |
| 메모리 이력 preload 워터마크 | `turn_executor.py:379-394` | vault 중복 방지 | `state.metadata[STM/ARCHIVED key] = len(history)` | 필수 |
| 실행 카드·저널·대화 rollup 형식 | `execution_record.py:149-228`, `conversation_archive.py:157-246` | 메모리 브라우저 | §5.6 | 필수(데이터 연속성) |
| `_distill_state.json` 키 | `distill.py:36,240-302` | 호스트 메모리 관리 API | `passes, last_evergreen_pass, last_launch, last_status, last_error, last_report` | 필수 |
| rollout 경로·형식 | `rollouts.py:22-47`, `rollout_recorder.py:307-318` | 관리자 opt-in | `executor/rollouts/rollout-<ts>-<hash>-<uuid>.jsonl`, `asdict(PipelineEvent)`, 100개 유지 | 선택 |
| 설정 키 (host.setting) | §5.5 | 관리자 설정 | `GENY_TOOLS_WORKFLOW_SELF_ENABLED, GENY_PREFETCH_REFERENCED_FILES, WORKSPACE_FAST_PATH_ENABLED, GENY_ROLLOUT_RECORDING_ENABLED, CLAUDE_CODE_*` | 필수 |
| 이미지 예산 env | `turn_executor.py:47-63` | 배포 env | `GENY_IMAGE_MAX_BYTES`, `GENY_TURN_IMAGE_MAX_BYTES` (`XGENY_*` 폴백) | 선택 |
| 문자열 교차 계약 | `host/tools.py:134-140`, `host/python_env.py:35` | 데스크톱 클라이언트, 샌드박스 서비스 | 거부 문구·코드, `.xgeny/python-env.json` | 필수 |
| `_constants` 프롬프트 심볼 | `host/_constants.py:14-175` | 프롬프트 미러, 실행기 | `default_prompt, MEMORY_*_PROMPT_BLOCK, SELF_EVOLUTION_PROMPT_BLOCK, EFFICIENCY_PROMPT_BLOCK, cli_tool_naming_note, _self_evolution_policy` | 필수(import) / 선택(문구 동일성) |
| shim 대상 모듈 경로 | `host/{runner,cancel_context,distill,forged_tools,python_env,tools,memory,memory_tools,rag,context_budget,conversation_archive,execution_record,param_validator,token_budget,tool_indicators}.py` | 호스트 shim 15개 | `from xgen_agent_runtime.host import <m>`. 없으면 호스트 모듈 import 가 깨짐 | 필수 |
| stage 내부 타입 누출 | `stages/s02_context/types.py` | 호스트 메모리 provider | `MemoryChunk` | 필수(경로 유지) |
| `__version__` | `__init__.py:123-128` | 호스트 메모리 관리 API | 배포 메타데이터 버전 문자열 | 선택 |
| 파이프라인 층 고정 | `rt-tests/contract/test_public_runtime_contract.py` | 런타임 자체 테스트, 호스트 테스트 일부 | `Pipeline.run/run_stream` 시그니처, `PipelineEvent/State/Result`, `APIRequest/Response/ContentBlock` 필드 | 선택(경계 A) / 필수(경계 C) |
| 테스트 이음매 | `turn_executor.py:178-183`, `runner.py:699-703` | 호스트 테스트 다수 | `host.runner.build_client`·`build_pipeline` 을 바꿔 끼우면 실행 경로가 그것을 따름 | 선택 |
| 배포 단위 | `pyproject.toml:6-7` | 호스트·LLM 관리 서비스 | 배포 이름 `xgen-agent-runtime`, import `xgen_agent_runtime`, GitHub Release wheel URL 핀 | 필수 |

---

## 부록: swap-in 하네스의 최소 인터페이스 (경계 A)

아래 시그니처·모듈 경로를 지키면 xgen-workflow 운영 경로는 **코드 수정 없이** 그대로 돈다. 내부의 21단계·`Pipeline`·`PipelineState` 는 자유롭게 바꿀 수 있다. 그 경우 런타임 저장소의 계약 테스트(`test_public_runtime_contract.py`)와 파이프라인을 직접 쓰는 호스트 테스트(`stream_turn(pipeline, "hi", PipelineState(...))` 류)는 다시 써야 한다.

```python
# 1) 진입점 — 모듈 경로 고정
# xgen_agent_runtime/host/turn_executor.py
class AgentTurnExecutor:
    def run(self, host: "HostServices", **kwargs) -> "Iterator[str | dict] | str": ...
    # kwargs: §2.1.1. 같은 dict 를 host.resolve_*/build_host_skill_tools/build_cli_runtime 에 넘기고,
    #         그 전에 kwargs["_sandbox_session"], (CLI) kwargs["_tool_surface"] 를 써 넣는다.

# 2) 호스트 프로토콜 — 메서드 이름·키워드·호출 순서 고정 (§2.4, §2.1.3)
# xgen_agent_runtime/host/host.py : HostServices, CliRuntime (+ host/__init__ 재노출)

# 3) 출력 청크
#   str | {"type":"agent_event","data":{type: tool_call|tool_result|tool_error|task_*, ...}}
#       | {"type":"canvas_command","data":...} | {"type":"usage","data":UsagePayload}  (끝에 정확히 1회)
#   kwargs["usage_sink"] (같은 dict) 를 update — 닫혀도 partial 로 채움.
#   .close() → 전부 정리 후 host.finalize_turn → cli_cleanup → run_dir 정리.

# 4) CLI 표면 (claude_code·codex)
# xgen_agent_runtime/host/tool_surface.py
class TurnToolSurface:
    registry: Any; tool_context: Any; state: Any
    def bind_loop(self, loop) -> None: ...
    def exposed_names(self) -> tuple[str, ...]: ...
    def tools_list(self) -> list[dict]: ...            # {"name","description","inputSchema"}
    async def call(self, name: str, arguments: dict) -> dict: ...   # {"content":[...],"isError":bool}

# 5) 공유 모듈 — 경로·심볼 유지
# xgen_agent_runtime/host/cancel_context.py : request_cancel, is_cancelled, clear_cancel (전역 상태 공유)
# xgen_agent_runtime/host/runner.py         : build_client, _map_provider, build_cli_client,
#     build_codex_cli_client, CLI_NATIVE_TOOLS_DENY, CLI_NATIVE_TOOL_CATALOG, settle_structured,
#     (테스트·shim 용) build_pipeline, stream_turn, run_turn, turn_usage
# xgen_agent_runtime/host/_constants.py     : 프롬프트 블록 심볼 (§5.10)
# xgen_agent_runtime/tools/base.py          : Tool/ToolResult/ToolContext(state_view: add_event·shared·pending_tool_calls)
# xgen_agent_runtime/memory/provider.py     : MemoryProvider 프로토콜 + Importance/NoteDraft/NotePatch
# 그 밖의 host.* shim 대상 모듈 (§6 표 "shim 대상 모듈 경로")
```

최소 인터페이스에서 빠지면 **운영 장애**로 바로 이어지는 것: ① kwargs 역방향 키와 그 순서 ② usage 비대칭(anthropic·bedrock 캐시 분리) ③ `[ERROR]` 접두 ④ `canvas_command` 통과 ⑤ 취소 레지스트리 공유 ⑥ vault·distill 영속 형식.
