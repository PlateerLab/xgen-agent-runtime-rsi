# 12. 기존 런타임의 프로바이더 계층 — 재사용 관점 조사

> 대상: `PlateerLab/xgen-agent-runtime@2e015ae` (패키지 `xgen_agent_runtime` 4.75.0, HEAD `2e015ae` "Merge pull request #131 from PlateerLab/feat/thinking-control"). 조사일 2026-10-01.
> 범위: `src/xgen_agent_runtime/llm_client/` 전부, `stages/s05_cache`·`s06_api`·`s07_token`·`s08_think`, 그리고 이들을 묶는 `core/pipeline.py`·`host/runner.py`·`host/tool_surface.py` 의 접점. 문서 `docs/providers.md`, `docs/claude_code_cli.md`.
> 방법: 정적 읽기가 기본이다. **[실측]** 표시는 저장소 `.venv`(Python 3.12.3, anthropic 0.121.0, openai 2.53.0, google-genai 2.10.0)에서 읽기 전용 스크립트로 직접 확인한 것이다. 확인하지 못한 것은 **미확인**으로 적는다.
> 이하 경로는 `src/xgen_agent_runtime/` 기준 상대 경로다. 비용 정의 c(τ)는 [01-rrsi-paper.md](01-rrsi-paper.md) §1.2 를 따른다.

---

## 요약

- **프로바이더 계층은 거의 독립적이다.** `llm_client/` 가 런타임 내부에서 끌어 쓰는 것은 `core.config.ModelConfig`(평범한 dataclass), `core.state.TokenUsage`(dataclass), `core.errors`(예외·열거형), `core._head_tail_buffer`, `core.file_blocks` 다섯 가지뿐이다(§7.1). `PipelineState`·`Stage` 는 어디에서도 import 하지 않는다. 단, `import xgen_agent_runtime.llm_client` 는 패키지 `__init__` 을 거쳐 Pipeline·스테이지까지 184개 모듈을 함께 올린다 **[실측]**. 논리 결합이 아니라 import 무게 문제다.
- **표면은 비동기 두 메서드다.** `BaseClient.create_message(...) -> APIResponse` 와 `create_message_stream(...) -> AsyncIterator[dict]`(마지막 이벤트가 `{"type":"message_complete","response":APIResponse}`). 동기 API 는 없다. 하위 클래스가 구현하는 것은 `_send(APIRequest)` 하나(추상)와 선택적으로 `create_message_stream` 이다(`llm_client/base.py:197-255`).
- **정규형(canonical)은 Anthropic Messages 모양이다.** 메시지·도구·tool_use/tool_result·thinking 블록이 모두 Anthropic 형식이고, OpenAI·Gemini 는 `translators/_canonical.py` 가 경계에서 변환한다. 등록된 provider 이름은 15개다 **[실측]**: anthropic, bedrock, openai, azure_foundry(+azure, azure_openai), vllm, ollama, lmstudio, custom(+local), google, vertex, claude_code_cli, codex_cli.
- **루프를 소유하는 provider 두 개(claude_code_cli, codex_cli)** 는 하위 프로세스 안에서 자기 에이전트 루프를 돈다. 런타임은 (a) 도구를 MCP 로만 넘기고(`--mcp-config`/`-c mcp_servers.*`), (b) 네이티브 도구를 전부 끄고(`--tools ""` + 거부 목록 / codex `features.*=false`), (c) 최종 `APIResponse` 에서 이미 실행된 `tool_use` 를 지우고, (d) 실행 과정은 스트림 이벤트 `tool_use`/`tool_result` 로만 내보낸다. MCP 서버 자체는 런타임이 아니라 호스트(xgen-workflow)가 띄우고, 런타임은 그 서버가 광고·실행할 객체 `TurnToolSurface` 만 준다(§3.9).
- **토큰 회계에 c(τ)용으로 그대로 쓸 수 없는 결함이 여럿 있다.** (1) s07 은 실제 호출 모델이 아니라 `state.model` 로 값을 매긴다(`stages/s07_token/artifact/default/stage.py:91`). (2) 기본 계산기는 Anthropic 표만 쓰고, 표에 없는 모델(opus-4-7·opus-5·sonnet-5·gpt-5.x·CLI 별칭 `sonnet` 등)은 조용히 $0 이다 **[실측]**. (3) CLI 가 보고한 `cost_usd` 를 s07 은 무시한다. (4) Gemini 의 생각 토큰(`thoughts_token_count`)·캐시 토큰을 읽지 않는다. (5) Anthropic 출력 한도 재호출·스트림 재시도·압축(s02) 호출의 사용량이 버려진다(§5.4).
- **`TokenUsage` 에 reasoning 토큰 칸이 없다.** 입력·출력·캐시 생성·캐시 읽기·`cost_usd`·`duration_ms` 여섯 칸뿐이다(`core/state.py:12-52`). `total_tokens` 는 의도적으로 input+output 만 더한다 — 벤더마다 `input_tokens` 의 의미가 달라서다(Anthropic 은 캐시 제외, OpenAI 는 캐시 포함).
- **재시도는 s06 한 층만 한다.** SDK 재시도는 0, 타임아웃 재시도는 기본 1회, 나머지는 `ExponentialBackoffRetry`(최대 3회, `ErrorCategory.is_recoverable` 만). **모델·provider 페일오버는 런타임 어디에도 없다**(grep 결과 `skills/fork.py` 의 기본값 외 0건). 라우터(`AdaptiveModelRouter`)는 문자 수·tools·thinking 으로 Haiku/Sonnet/Opus 를 고르는 휴리스틱일 뿐이다.
- **생각(thinking) 조절은 `llm_client/thinking.py` 한 곳으로 정리돼 있고 재사용 가치가 높다.** `thinking_spec(provider, model) -> ThinkingSpec`, `normalize_thinking(spec, value) -> Optional[str]` 두 함수가 "off/on/minimal..max" 하나의 값을 모델별 실제 요청(Anthropic budget/adaptive+effort, OpenAI `reasoning_effort`, Gemini budget/level, vLLM chat_template_kwargs, Claude Code `--effort`/`MAX_THINKING_TOKENS=0`, Codex `model_reasoning_effort`)으로 바꾼다.
- **검증된 버그 2건(실측):** s08 `ThinkStage` 는 `ContentBlock` 을 dict 로 바꾸며 `thinking_text` 대신 `text`(None)를 읽어 생각 본문이 늘 `None`, 토큰 합계가 늘 0 이다. `GoogleClient` 는 `str(FinishReason.STOP)` 이 `'FinishReason.STOP'` 이라 stop_reason 정규화가 실패한다(google-genai 2.10.0).
- **결론:** 새 하네스는 `BaseClient` 하위 클래스들과 `ClientRegistry`·`CredentialBundle`·`thinking.py`·`timeouts.py`·`translators`·`_cli_runtime` 을 그대로 쓰고, s06/s07/s08 의 스테이지 코드는 버리고 다시 짜는 것이 맞다. 비용 c(τ)는 s07 을 거치지 말고 호출 지점마다 `APIResponse.usage` 를 직접 모으는 단일 관문에서 계산해야 한다(§8).

---

## 0. 파일 지도

| 파일 | 줄 수 | 역할 |
|---|---:|---|
| `llm_client/base.py` | 639 | `BaseClient`(추상), `ClientCapabilities`, 요청 조립·drops 협상·자가치유·provenance |
| `llm_client/types.py` | 127 | `APIRequest`, `ContentBlock`, `APIResponse` |
| `llm_client/registry.py` | 157 | `ClientRegistry`(provider 이름 → 클래스, 지연 import) |
| `llm_client/profiles.py` / `openai_compatible.py` | 257 / 287 | 선언형 OpenAI 호환 로컬 백엔드(ollama·lmstudio·custom) |
| `llm_client/credentials.py` | 168 | `ProviderCredentials`, `CredentialBundle`, `ConfigError` |
| `llm_client/anthropic.py` / `bedrock.py` | 828 / 204 | Anthropic Messages, Bedrock(상속) |
| `llm_client/openai.py` / `azure_foundry.py` / `vllm.py` | 596 / 223 / 83 | Chat Completions, Azure Foundry(상속), vLLM(상속) |
| `llm_client/google.py` / `vertex.py` | 542 / 121 | Gemini(google-genai), Vertex(상속) |
| `llm_client/claude_code.py` / `codex.py` | 874 / 620 | CLI 하위 프로세스 백엔드 2종 |
| `llm_client/_cli_runtime.py` | 675 | `CLIProcessRunner`(spawn·env 정리·kill ladder·줄 스트리밍) |
| `llm_client/translators/_canonical.py` / `_cli.py` / `_codex.py` | 708 / 1432 / 581 | 정규형 ↔ 벤더 변환, CLI argv/stdin/누산기 |
| `llm_client/thinking.py` | 496 | 생각 조절 표와 변환 함수 |
| `llm_client/timeouts.py` | 142 | 시간 상한·SDK 재시도 0 |
| `llm_client/model_discovery.py` / `local_probe.py` | 243 / 167 | 실시간 모델 목록 / Ollama 컨텍스트 창 탐지 |
| `stages/s06_api/artifact/default/stage.py` | 1036 | `APIStage`(재시도·스트림 감시·이벤트) |
| `stages/s06_api/artifact/default/router.py` / `retry.py` / `tool_loop.py` | 275 / 247 / 456 | 모델 라우터, 재시도 전략, 내부 도구 루프 |
| `stages/s07_token/artifact/default/{stage,pricing,trackers}.py` | 112 / 305 / 63 | 토큰 집계·가격표 |
| `stages/s08_think/artifact/default/{stage,budget,processors}.py` | 190 / 298 / 102 | 생각 블록 분리·예산 계획 |
| `stages/s05_cache/artifact/default/{stage,strategies}.py` | 135 / 246 | Anthropic `cache_control` 배치 |

---

## 1. 클라이언트 추상화

### 1.1 `BaseClient` 메서드

`llm_client/base.py:132-639`. 모든 공개 메서드가 `async` 다. 동기 래퍼는 없다.

| 메서드 | 위치 | 종류 | 설명 |
|---|---|---|---|
| `__init__(api_key="", base_url=None, default_headers=None, event_sink=None)` | `base.py:141-151` | 동기 | 하위 클래스마다 생성자 키워드가 다르다(Bedrock 은 `aws_*`, Vertex 는 `project/location/credentials_json`, CLI 는 `binary_path/workspace_dir/...`). |
| `create_message(*, model_config, messages, system="", tools=None, tool_choice=None, purpose="", response_format=None) -> APIResponse` | `base.py:197-225` | async, 비스트림 | `_build_request(stream=False)` → `_send()`. |
| `create_message_stream(*, model_config, messages, system="", tools=None, tool_choice=None, purpose="") -> AsyncIterator[dict]` | `base.py:227-249` | async generator | 기본 구현은 `create_message` 를 불러 `message_complete` 하나만 낸다. SDK·CLI 클라이언트가 모두 재정의한다. **`response_format` 인자가 없다** — 구조화 출력은 비스트림 경로에서만 요청할 수 있다. |
| `_send(request, *, purpose="") -> APIResponse` | `base.py:253-255` | async, **추상** | 벤더 호출. |
| `_build_request(...) -> APIRequest` | `base.py:259-338` | 동기 | 능력 협상(§1.7). CLI 클라이언트는 이것을 재정의해 `session_hint` 를 덧붙인다(`claude_code.py:528-561`, `codex.py:330-352`). |
| `thinking_provider() -> str` | `base.py:183-193` | 동기 | 생각 표에서 쓸 provider 이름(`claude_code_cli→claude_code`, `codex_cli→codex`, `azure_foundry→azure`, `custom→vllm`). |
| `supports(feature) -> bool` | `base.py:457-459` | 동기 | `capabilities.supports_<feature>` 조회. |
| `configure(**kwargs)` | `base.py:618-621` | 동기 | `setattr(self, f"_{k}", v)`. SDK 클라이언트는 덧붙여 `self._client=None` 로 연결 풀을 버린다(`anthropic.py:383-385`). |
| `warmup(*, timeout_s=8.0) -> bool` | `base.py:623-639` | async | 절대 raise 하지 않는 사전 연결. Anthropic/OpenAI 는 `models.list`, Bedrock 은 클라이언트 생성만, CLI 는 `--version` 핸드셰이크. |
| `aclose()` | `base.py:153-180` | async | `self._client`(SDK) 닫기. Claude Code 는 hot-spare 프로세스를 거둔다(`claude_code.py:411-431`). |
| `_heal_request_kwargs(kwargs, exc)` / `_invoke_with_heal(vendor_call, kwargs, *, purpose)` | `base.py:496-542` | 훅 / async | 벤더 400 이 문제 필드를 **명시할 때만** 요청을 고쳐 정확히 1회 재시도. 성공하면 `llm_client.drift_healed` 이벤트 + WARNING. |
| `_classify_error(e) -> APIError` | `base.py:589-597` | 동기 | 벤더 예외 → `APIError(category=...)`. 하위 클래스가 SDK 예외 계층으로 재정의. |
| `_provenance() -> dict` | `base.py:606-616` | 동기 | `{"provider", "sdk_version"}`. SDK 클라이언트는 여기에 `"response": <SDK 객체>` 를 붙여 `APIResponse.raw` 에 싣는다. |

### 1.2 `ClientCapabilities`

```python
# llm_client/base.py:72-129
@dataclass(frozen=True)
class ClientCapabilities:
    supports_thinking: bool = False
    supports_tools: bool = False
    supports_streaming: bool = True
    supports_tool_choice: bool = False
    supports_stop_sequences: bool = True
    supports_top_k: bool = False
    supports_system_prompt: bool = True
    supports_structured_output: bool = False
    supports_session_continuity: bool = False
    supports_mcp_passthrough: bool = False
    supports_budget_limit: bool = False
    supports_token_usage: bool = True
    supports_cost_usage: bool = False
    is_subprocess: bool = False
    requires_workspace: bool = False
    streaming_granularity: str = "token"   # "token" | "message" | "none"
    drops: tuple[str, ...] = field(default=())

    def supports(self, feature: str) -> bool:
        return bool(getattr(self, f"supports_{feature}", False))
```

`drops` 는 2.2.0 부터 **집행된다**: 선언된 필드는 요청에서 지워지고 `llm_client.parameter_dropped` 이벤트가 나간다(`base.py:372-443`). 단 인스턴스의 `supports_*` 가 True 면 그 drop 은 건너뛴다(`_DROP_FIELD_TO_CAPABILITY`, `base.py:364-370`) — `VLLMClient.configure_capabilities(supports_tools=True)` 가 실제로 tools 를 살리게 하려는 장치다.

"루프를 소유하는 provider" 의 판정 관례는 `is_subprocess and supports_tools and requires_workspace` 다(`claude_code.py:24-30`, `codex.py:23-29`). s06 은 더 단순하게 `capabilities.is_subprocess` 만 본다(`stages/s06_api/artifact/default/stage.py:928-932`).

### 1.3 `APIRequest`

```python
# llm_client/types.py:17-59
@dataclass
class APIRequest:
    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int = 8192
    system: Any = ""  # str or List[content blocks]
    temperature: float = 0.0
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Dict[str, Any]] = None
    stop_sequences: Optional[List[str]] = None
    stream: bool = False
    thinking: Optional[Dict[str, Any]] = None
    thinking_level: Optional[str] = None
    response_format: Optional[Dict[str, Any]] = None
    session_hint: Optional[Dict[str, Any]] = None
    mcp_config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
```

| 필드 | 누가 채우나 | 누가 읽나 |
|---|---|---|
| `model`·`max_tokens`·`temperature`·`top_p`·`top_k`·`stop_sequences` | `_build_request` 가 `ModelConfig` 에서 복사(`base.py:275-289`). `top_k` 는 `supports_top_k` 일 때만. | 모든 SDK 클라이언트. CLI 2종은 `drops` 로 temperature/top_p/top_k/max_tokens/stop_sequences/tool_choice 를 지운다(`claude_code.py:166-173`, `codex.py:127-134`). |
| `system` | 호출자 인자 그대로(문자열 또는 블록 리스트). | Anthropic 은 그대로, OpenAI 는 `developer` 메시지로(`_canonical.py:478-498`), Gemini 는 `system_instruction` 텍스트로(`google.py:344-348`), Claude Code 는 `--system-prompt`(`_cli.py:186-199`), Codex 는 stdin 머리 또는 `model_instructions_file`(`codex.py:273-309`). |
| `tools` | 호출자. 정규형 `{"name","description","input_schema"}`. | OpenAI `function`, Gemini `functionDeclarations` 로 변환(`_canonical.py:63-101`). CLI 는 무시(도구는 MCP 로만). |
| `tool_choice` | 호출자. 정규형 `{"type":"auto"|"any"|"none"|"tool","name"?}`. | OpenAI·Gemini 변환(`_canonical.py:107-151`). |
| `stream` | `_build_request(stream=...)`. | CLI 의 와이어 모드 결정(`_cli.py:149-162`). |
| `thinking` | 옛 길: `model_config.thinking_enabled` 이고 `supports_thinking` 일 때 `{"type", "budget_tokens"?, "display"?}`(`base.py:295-304`). | Anthropic 그대로 / OpenAI `reasoning_effort` 근사(`_canonical.py:657-683`) / Gemini `thinking_config`(`_canonical.py:686-708`) / CLI `--effort` 근사(`_cli.py:38-62`). |
| `thinking_level` | 새 길: `model_config.thinking_level` 을 `normalize_thinking(thinking_spec(...))` 으로 맞춘 값. 값이 있으면 `thinking=None` 으로 옛 길을 끈다(`base.py:308-317`). | 각 클라이언트가 `thinking.py` 의 변환 함수로 옮긴다(§4). |
| `response_format` | `create_message(response_format=...)` 만(`base.py:290-293`). | OpenAI `response_format`(strict 없음, `openai.py:91-110`), Gemini `response_mime_type`+스키마(`google.py:41-61`), Claude Code `--json-schema`(`_cli.py:271-275`), Codex `--output-schema <tmpfile>`(`codex.py:224-247`). Anthropic 은 미지원(무시 + `feature_unsupported`). |
| `session_hint` | CLI 클라이언트의 `_session_hint` 기본값(`claude_code.py:559-560`). | Claude Code `--resume`/`--session-id`(`_cli.py:277-283`), Codex `exec resume <id>`(`_codex.py:173-178`). |
| `mcp_config` | **고수준 표면에는 인자가 없다.** 저수준 `_send(APIRequest)` 를 직접 부르는 호출자만 채울 수 있다. | Claude Code 에서 생성자 `mcp_config` 보다 우선(`_cli.py:245`). Codex 는 생성자 값만 쓴다(`codex.py:256`). |
| `metadata` | 채우는 코드 없음. | Anthropic 이 그대로 `metadata` 로 전송(`anthropic.py:625-626`). |

### 1.4 `ContentBlock` 과 `APIResponse`

```python
# llm_client/types.py:62-127
@dataclass
class ContentBlock:
    type: str  # "text", "tool_use", "thinking"  (+ 실제로는 "redacted_thinking")
    text: Optional[str] = None
    tool_use_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    thinking_text: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

@dataclass
class APIResponse:
    content: List[ContentBlock] = field(default_factory=list)
    stop_reason: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    message_id: str = ""
    raw: Optional[Any] = None

    @property text -> str               # text 블록을 "\n" 으로 이음
    @property tool_calls -> List[ContentBlock]   # type == "tool_use"
    @property structured -> Optional[Any]        # raw["structured_output"] (CLI 전용)
    @property thinking_blocks -> List[ContentBlock]
    @property has_tool_calls -> bool    # stop_reason == "tool_use" or tool_calls
    @property cost_usd -> Optional[float]        # usage.cost_usd
```

- `ContentBlock.raw` 는 **다음 요청에 그대로 되돌려 보낼 정규형 블록**이다. s06 은 히스토리에 `raw` 를 우선 기록한다(`stages/s06_api/artifact/default/tool_loop.py:43-67`). Anthropic thinking 블록은 `signature` 를 `raw` 에 보존한다 — 빠뜨리면 다음 호출이 `thinking.signature: Field required` 로 거절된다(`anthropic.py:752-767`). `redacted_thinking` 도 `raw={"type","data"}` 로 보존한다(`anthropic.py:768-775`).
- Gemini 의 thinking 블록에는 `raw` 가 없고 `thought_signature` 도 보존하지 않는다(`google.py:253-258`, `381-383`). Gemini 3 계열의 함수 호출에 서명 반환이 필요한지는 **미확인**(벤더 문서 확인 필요, 잠재 위험).
- `stop_reason` 정규형은 Anthropic 값(`end_turn`·`tool_use`·`max_tokens`·`stop_sequence`·`content_filter`)이다. OpenAI·Gemini 는 `normalize_stop_reason`(`_canonical.py:33-55`)으로 바꾼다. **[실측]** google-genai 2.10.0 에서 `str(types.FinishReason.STOP) == 'FinishReason.STOP'` 이라 표에 걸리지 않고 그대로 흘러간다(`google.py:244`, `411`). `MAX_TOKENS` 도 `max_tokens` 로 바뀌지 않는다. 도구 호출 판정은 `has_tool_calls` 가 블록 존재로도 보므로 살아 있지만, 출력 한도 판정은 Gemini 에서 깨져 있다.
- `raw` 의 모양은 provider 마다 다르다. SDK 클라이언트(비스트림): `{"provider","sdk_version","response": <SDK 객체>}`. SDK 클라이언트(스트림): `{"provider","sdk_version"}` 만(`openai.py:380`, `google.py:303`) — 원본 사용량 객체가 없다. Claude Code: 결과 봉투 dict 사본 + `cli_version` + 와이어 진단 카운터(`_cli.py:1013-1029`). Codex: `{"unknown_line_count", ..., "session_id"?, "structured_output"?}`(`_codex.py:536-552`).
- CLI 응답에는 **이미 실행된 `tool_use` 가 없다**(`_cli.py:970-1004`, `_codex.py:528-562`). 따라서 `has_tool_calls` 는 거짓이고 호출자의 도구 단계는 자연히 아무것도 하지 않는다.

### 1.5 `TokenUsage`

```python
# core/state.py:12-52
@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens   # 캐시 제외(의도적)

    def __iadd__(self, other): ...   # cost_usd·duration_ms 는 None 보존 합(_sum_optional)
    def __add__(self, other): ...
```

provider 별로 각 칸이 무엇을 담는지가 다르다. c(τ)를 계산할 때 가장 중요한 표다.

| provider | `input_tokens` | 캐시 칸 | `output_tokens` | reasoning 토큰 | `cost_usd` | 근거 |
|---|---|---|---|---|---|---|
| anthropic / bedrock | **캐시 제외** 입력 | creation·read 둘 다 채움(None→0 보정) | 출력(생각 포함) | 별도 칸 없음(출력에 포함) | None | `anthropic.py:777-790` |
| openai / azure / vllm / ollama / lmstudio / custom | `prompt_tokens`(**캐시 포함**) | read 만(`prompt_tokens_details.cached_tokens`) | `completion_tokens`(reasoning 포함) | 읽지 않음(`completion_tokens_details.reasoning_tokens` 는 SDK 에 있음 **[실측]**) | None | `openai.py:550-574` |
| google / vertex | `prompt_token_count` | **읽지 않음**(`cached_content_token_count` 는 SDK 에 있음 **[실측]**) | `candidates_token_count` | **읽지 않음**(`thoughts_token_count` 는 SDK 에 있음 **[실측]**). Gemini 가 생각 토큰을 candidates 와 따로 보고하는지는 **미확인**(벤더 문서상 별도로 알려져 있음) | None | `google.py:423-429` |
| claude_code_cli | 결과 봉투 `usage.input_tokens` | creation·read | `usage.output_tokens` | 없음 | 봉투 최상위 `total_cost_usd` | `_cli.py:697-721` |
| codex_cli | `input_tokens`(캐시 포함) | read(`cached_input_tokens`) | `output_tokens` | 없음 | None | `_codex.py:323-340` |

CLI 결과 봉투의 `usage`·`total_cost_usd` 가 **내부 루프 여러 턴의 누적값인지**는 저장소 골든 캡처가 모두 `num_turns: 1` 이라 확인할 수 없다(`tests/llm_client/golden/*`) — **미확인**. Codex `turn.completed.usage` 도 같다 — **미확인**.

### 1.6 스트림 이벤트 어휘

`create_message_stream` 이 내는 dict 의 `type` 값이다. 공식 dataclass 는 없고 관례다.

| `type` | 필드 | 내는 클라이언트 | 위치 |
|---|---|---|---|
| `text_delta` | `text` | 전부 | `anthropic.py:508-510`, `openai.py:324-326`, `google.py:259-261`, `_cli.py:1129-1131`, `_codex.py:460-463` |
| `thinking_delta` | `text` | anthropic, claude_code, codex. **Gemini 는 스트림 이벤트를 내지 않고** 블록에만 쌓는다(`google.py:254-258`). OpenAI 는 생각을 다루지 않는다. | `anthropic.py:511-513`, `_cli.py:1206-1218` |
| `input_json_delta` | `delta`(부분 JSON) | anthropic, claude_code | `anthropic.py:514-516` |
| `content_block_stop` | — | anthropic, claude_code | `anthropic.py:518-519` |
| `tool_use` | `id`,`name`,`input` | **CLI 2종만**(실행 알림). SDK 클라이언트는 tool_use 를 이벤트로 내지 않고 `message_complete` 의 응답에만 담는다. | `_cli.py:1349-1365`, `_codex.py:409-418` |
| `tool_result` | `tool_use_id`,`content`,`is_error` | CLI 2종만 | `_cli.py:1084-1122`, `_codex.py:420-439` |
| `result` / `error` / `cli_unknown` / `cli_malformed` | `raw` | claude_code(부기용) | `_cli.py:553-569` |
| `message_complete` | `response: APIResponse` | **전부, 마지막 한 번** | `base.py:249`, `anthropic.py:555-558`, `openai.py:382`, `google.py:305`, `claude_code.py:863-866`, `codex.py:605` |

s06 은 이 어휘를 상태 이벤트로 옮긴다: `text.delta`·`thinking.delta`·`api.tool_use`(+CLI 면 `api.cli_tool_call`)·`api.input_json_delta`·`api.content_block_stop`·`api.tool_result`(`stages/s06_api/artifact/default/stage.py:970-1013`). 마지막 프레임이 없으면 `NETWORK`(복구 가능) 오류로 본다(`stage.py:1015-1024`).

### 1.7 요청 협상(`_build_request`)

`base.py:259-338` 의 순서:

1. `ModelConfig` → `APIRequest` 복사. `top_k` 는 `supports_top_k` 일 때만.
2. `response_format` 이 있으면 싣고, `supports_structured_output` 가 아니면 `llm_client.feature_unsupported`.
3. `thinking_enabled` 이면 `supports_thinking` 일 때 `{"type": thinking_type, "budget_tokens"?, "display"?}`, 아니면 `feature_unsupported`.
4. `thinking_level` 이 있으면 `normalize_thinking(thinking_spec(self.thinking_provider(), model), level)` 로 맞추고, 결과가 있으면 `request.thinking=None`.
5. `top_k`·`tool_choice`·`stop_sequences` 미지원 알림(stop_sequences 는 지움).
6. `_apply_declared_drops` — `drops` 집행.

이벤트는 생성자 `event_sink` 로만 나간다. **런타임의 Pipeline·호스트 코드 어디에서도 `event_sink` 를 넘기지 않는다**(grep 0건, `core/`·`host/`·`stages/`). 즉 `feature_unsupported`·`parameter_dropped`·`drift_healed`·`unknown_wire_shape`·`tool_args_repaired` 는 지금 운영에서 버려진다. 새 하네스는 이 싱크를 꽂아야 한다.

### 1.8 오류 계약

```python
# core/errors.py:128-168
class ErrorCategory(str, Enum):
    RATE_LIMITED, TIMEOUT, NETWORK, TOKEN_LIMIT, AUTH, BAD_REQUEST, SERVER_ERROR, TERMINAL, UNKNOWN,
    CLI_NOT_FOUND, CLI_AUTH_FAILED, CLI_TIMEOUT, CLI_PROTOCOL_ERROR, CLI_PERMISSION_DENIED
    is_recoverable = {RATE_LIMITED, TIMEOUT, NETWORK, SERVER_ERROR, CLI_TIMEOUT, CLI_PROTOCOL_ERROR}
    is_fatal       = {AUTH, BAD_REQUEST, CLI_NOT_FOUND, CLI_AUTH_FAILED, CLI_PERMISSION_DENIED}

# core/errors.py:256-279
class APIError(GenyExecutorError):
    def __init__(self, message, *, category=ErrorCategory.UNKNOWN, status_code=None, code=None, cause=None)
    # code(ExecutorErrorCode, "exec.api.rate_limited" 등)는 category 에서 기본 유도
```

- `TOKEN_LIMIT`·`TERMINAL`·`UNKNOWN` 은 복구 가능도 치명도 아니다. `ExponentialBackoffRetry` 는 `is_recoverable` 만 재시도하므로 이들은 재시도되지 않는다. `TOKEN_LIMIT` 를 받아 압축하는 소비자는 **없다**(grep: `llm_client/` 와 옛 provider 파일 외 0건).
- Anthropic 은 400 을 `TOKEN_LIMIT` 와 `BAD_REQUEST` 로 신중하게 가른다(`anthropic.py:303-340`). OpenAI 는 모든 400 을 `BAD_REQUEST`(치명)로 본다(`openai.py:590-591`) — 컨텍스트 초과도 치명으로 끝난다.
- Gemini 는 타입 → `google.api_core` → 문자열 순서로 분류한다(`google.py:431-542`).
- CLI 는 종료 코드·stderr 의 **고정된 문구**만 인증 실패로 본다(`claude_code.py:81-112`, `codex.py:70-96`). Codex 는 `turn.failed` 만 치명, 다른 오류 줄은 출력이 하나도 없을 때만 실패로 본다(`codex.py:428-460`, `599-601`).
- 자가치유: Anthropic(샘플링 파라미터 deprecated, thinking enabled→adaptive, `anthropic.py:240-284`), OpenAI(`max_tokens→max_completion_tokens`, temperature/top_p 거절, `openai.py:200-255`). Google 은 훅을 구현하지 않는다.

---

## 2. 레지스트리·팩토리·자격증명·디스커버리

### 2.1 `ClientRegistry`

```python
# llm_client/registry.py:16-35
class ClientRegistry:
    _factories: Dict[str, Callable[[], Type[BaseClient]]] = {}   # 클래스 수준 전역
    @classmethod
    def register(cls, provider: str, factory: Callable[[], Type[BaseClient]]) -> None
    @classmethod
    def get(cls, provider: str) -> Type[BaseClient]        # 미등록이면 ValueError
    @classmethod
    def available(cls) -> List[str]
```

팩토리는 **클래스**를 돌려주고 인스턴스는 호출자가 만든다. SDK import 는 팩토리 호출 시점까지 미룬다. 등록(`registry.py:128-157`):

| 이름 | 클래스 | 비고 |
|---|---|---|
| `anthropic` | `AnthropicClient` | anthropic SDK 가 유일한 필수 의존성 |
| `openai` | `OpenAIClient` | `openai` 필요 |
| `google` | `GoogleClient` | `google-genai` 필요 |
| `vllm` | `VLLMClient` | 보수적 기본값(tools 꺼짐) |
| `claude_code_cli` | `ClaudeCodeCLIClient` | |
| `bedrock` | `BedrockClient` | `anthropic[bedrock]` |
| `vertex` | `VertexClient` | `google-genai`(+`google-auth`) |
| `codex_cli` | `CodexCLIClient` | |
| `azure_foundry`, `azure`, `azure_openai` | `AzureFoundryClient` | 같은 클래스의 별칭 |
| `ollama`, `lmstudio`, `custom`, `local` | 프로필 생성 클래스 | `_register_profile_providers`(`registry.py:147-157`) |

"provider+model 문자열 → 클라이언트" 의 실제 흐름은 다음 둘이다. 모델 문자열은 클라이언트 선택에 쓰이지 않는다 — 클라이언트는 provider 로만 고르고, 모델은 매 호출의 `ModelConfig.model` 로 넘긴다.

1. **Pipeline 경로**: `stages[6].config["provider"]` → `Pipeline._resolve_llm_client()`(`core/pipeline.py:3463-3497`) → `_build_client_for(provider)`(`pipeline.py:3499-3530`): `CredentialBundle.require(provider)` → `ClientRegistry.get(provider)` → `_creds_to_client_kwargs(provider, creds)`(`pipeline.py:113-268`) → CLI 면 매니페스트 MCP 서버를 `mcp_config` 에 합치고 `mcp__<server>` 를 `allow_tools` 에 넣음 → `client_cls(**kwargs)`.
2. **호스트 경로**(xgen-workflow 가 쓰는 길): `host/runner.py:68-93` `build_client(provider, api_key, base_url, *, credentials=None)`. `_PROVIDER_MAP`(`runner.py:51-58`)이 xgen 의 `"vllm"` 을 런타임 `"custom"` 프로필로 바꾼다(vLLM 기본 프로필은 tools 를 끄기 때문). CLI 는 별도 `build_cli_client`(`runner.py:270-388`)·`build_codex_cli_client`(`runner.py:391-445`).

### 2.2 프로필(OpenAI 호환 로컬 백엔드)

```python
# llm_client/profiles.py:66-105
@dataclass(frozen=True)
class ProviderProfile:
    name: str
    capabilities: ClientCapabilities
    aliases: Tuple[str, ...] = ()
    default_base_url: Optional[str] = None
    requires_base_url: bool = False
    default_max_tokens: Optional[int] = None
    is_local: bool = True
    description: str = ""
```

| 프로필 | 기본 base_url | base_url 필수 | `default_max_tokens` | 위치 |
|---|---|---|---|---|
| `ollama` | `http://localhost:11434/v1` | 아니오 | 8192 | `profiles.py:110-118` |
| `lmstudio` | `http://127.0.0.1:1234/v1` | 아니오 | 8192 | `profiles.py:120-128` |
| `custom`(별칭 `local`) | 없음 | 예 | 8192 | `profiles.py:130-142` |

세 프로필 모두 `_LOCAL_CAPABILITIES`(tools·tool_choice·structured_output 켜짐, `drops=("thinking_enabled","top_k")`, `profiles.py:42-63`)를 쓴다. 생성 클래스는 `OpenAICompatibleClient`(`openai_compatible.py:98-245`)를 상속하며 덧붙이는 것은: api_key 기본 `"EMPTY"`, `max_tokens` 바닥값(Ollama 의 `num_predict=128` 잘림 방지, `openai_compatible.py:219-245`), `num_ctx`·`think` 를 `extra_body` 로(`profiled_client_kwargs`, `profiles.py:195-228`), 깨진 도구 인자 JSON 복구(`_repair_json`, `openai_compatible.py:57-95`) — 복구도 실패하면 원문을 `tools.errors.UNPARSED_ARGUMENTS_KEY` 로 감싸 넘긴다(`openai_compatible.py:149-182`; `tools` 패키지로의 지연 import 하나가 유일한 바깥 결합).

### 2.3 자격증명

```python
# llm_client/credentials.py:35-97, 100-168
@dataclass(frozen=True)
class ProviderCredentials:
    api_key: str = ""
    base_url: Optional[str] = None
    default_headers: Optional[Mapping[str, str]] = None
    binary_path: Optional[str] = None
    extras: Mapping[str, Any] = field(default_factory=dict)
    auth_mode: str = "auto"          # 'api_key' | 'oauth' | 'setup_token' | 'auto'
    def is_empty(self) -> bool       # auth_mode != 'auto' 도 "자격 있음" 으로 친다
    # __repr__ 은 api_key 를 가린다

@dataclass(frozen=True)
class CredentialBundle:
    by_provider: Mapping[str, ProviderCredentials] = field(default_factory=dict)
    def get(self, provider) -> ProviderCredentials        # 없으면 빈 값
    def require(self, provider) -> ProviderCredentials    # 비면 ConfigError
    def has(self, provider) -> bool
    def providers(self) -> list[str]
    def preferred_provider(self, order=("claude_code_cli","anthropic","openai","google","vllm")) -> Optional[str]
```

provider 별 생성자 인자 매핑은 `_creds_to_client_kwargs`(`core/pipeline.py:113-268`) **한 곳**에 있다:

| provider | 생성자에 들어가는 것 |
|---|---|
| 프로필(ollama 등) | `profiled_client_kwargs`: api_key(기본 EMPTY)·base_url·headers·`extras.ollama_num_ctx|num_ctx`·`extras.think` |
| `vllm` | api_key·base_url·headers |
| `bedrock` | `extras.aws_region/aws_access_key_id/aws_secret_access_key/aws_session_token/aws_profile`·base_url(VPC)·headers. 키가 없으면 boto3 기본 체인 |
| `vertex` | `extras.project/location/credentials_json`·api_key(express)·base_url·headers |
| `claude_code_cli` | api_key·binary_path·auth_mode·`extras` 의 workspace_dir(=workspace_root)·settings_path·bare_mode·max_budget_usd·default_permission_mode·mcp_config·allow_tools·disallow_tools·extra_args·timeout_s·strict_wire·env_extras |
| `codex_cli` | api_key·binary_path·auth_mode·`extras` 의 workspace_dir·sandbox_mode·bypass_sandbox·mcp_config·extra_args·timeout_s·strict_wire·env_extras |
| 그 외(anthropic·openai·google·azure_*) | api_key·base_url·headers. **Azure 의 `api_version`·`deployment` 는 이 경로로 못 넘긴다** — base_url 문자열에 붙여 넣은 값을 `AzureEndpoint` 가 파싱하는 길뿐이다(`azure_foundry.py:74-109`). |

`CredentialBundle` 문서 자체가 "임베딩/LTM 키는 아직 env 사다리로 읽는다" 고 밝힌다(`credentials.py:108-112`).

### 2.4 모델 디스커버리·로컬 탐지

- `discover_models(provider, *, api_key=None, base_url=None, transport=None, timeout=6.0) -> ModelDiscovery`(`model_discovery.py:169-240`). 절대 raise 하지 않는다. openai·vllm·lmstudio·custom·local 은 `GET <base>/models`, ollama 는 `GET <root>/api/tags`, anthropic 은 `GET /v1/models?limit=1000`, google 은 `GET /v1beta/models`(generateContent 지원만). claude_code_cli·codex_cli·bedrock·vertex 는 `source="unavailable"`. 결과형 `ModelInfo(id, display_name)`, `ModelDiscovery(provider, models, source, error)`(`model_discovery.py:55-75`). `azure_foundry` 는 분기가 없어 "unsupported provider" 다.
- `probe_ollama_num_ctx` / `resolve_local_context_window`(`local_probe.py:107`, `130`): Ollama `/api/show` 로 컨텍스트 창을 읽는다. 호스트가 명시적으로 불러야 한다.

---

## 3. provider 별 특성

### 3.1 기능 표

| provider | tools | 스트림 | thinking(옛 `thinking`) | thinking(`thinking_level`) | 캐시 | 이미지 | 구조화 출력 | 비용 보고 |
|---|---|---|---|---|---|---|---|---|
| anthropic | 예 | 토큰 | 예 | 표 기반 | `cache_control`(s05) | base64/url/path→base64, tool_result 안 이미지도 | 아니오 | 아니오 |
| bedrock | 예 | 토큰 | 예 | 표 기반(anthropic 표 공유) | `cache_control`(s05) | anthropic 과 같음 | 아니오 | 아니오 |
| openai | 예 | 토큰 | `drops` 로 지움 | `reasoning_effort` | 자동(벤더) | `image_url` part, tool 결과 이미지는 다음 user 메시지로 | 예 | 아니오 |
| azure_foundry | openai 와 같음 | | | azure 표(=openai 표) | | | | |
| vllm | **아니오**(`drops`) | 토큰 | 지움 | vllm 표(chat_template_kwargs) | 자동 | openai 와 같음 | **아니오** | 아니오 |
| ollama·lmstudio·custom | 예 | 토큰 | 지움 | vllm 표(custom→vllm) | 자동 | openai 와 같음 | 예 | 아니오 |
| google·vertex | 예 | 토큰 | `drops` 로 지움 | Gemini 표 | 미지원 | `inlineData`/`fileData` | 예 | 아니오 |
| claude_code_cli | CLI 내부(MCP) | 토큰 | `--effort` 근사 | `--effort`/`MAX_THINKING_TOKENS=0` | CLI 자체 | 현재 턴만 base64(stream-json) | `--json-schema` | **예**(`total_cost_usd`) |
| codex_cli | CLI 내부(MCP) | **메시지 단위** | `thinking.effort` 키만 | `model_reasoning_effort` | CLI 자체 | 현재 턴의 **path 소스만** `--image` | `--output-schema` | 아니오 |

`file` 블록은 모든 경로에서 "경로를 가리키는 텍스트" 로 낮춘다(`core.file_blocks.file_block_pointer`, `_canonical.py:291-295`). 이미지는 `materialize_local_image_block`(`_canonical.py:177-229`)이 path 소스를 읽어 PNG/JPEG/WebP/GIF 만 받고, 2048px·4MiB 를 넘으면 JPEG 로 줄이며, `XGEN_RUNTIME_IMAGE_MAX_BYTES`(기본 20MiB) 를 넘으면 거절한다. PIL 이 필수다.

### 3.2 anthropic

- 짧은 별칭 → 정본 ID: `opus→claude-opus-4-7`, `sonnet→claude-sonnet-4-6`, `haiku→claude-haiku-4-5-20251001`(`anthropic.py:42-61`). (Claude Code CLI 의 별칭 대상과 다르다 — `thinking.py:178-182` 는 CLI 의 `sonnet` 을 `claude-sonnet-5-5` 로 본다.)
- 샘플링 파라미터 제거: thinking 이 켜져 있으면(`disabled`·`between_tools` 는 꺼짐으로 침) temperature/top_p/top_k 를 지운다(`anthropic.py:139-146`, `655-665`). `opus-4-7/4-8`, `opus-5`, `sonnet-5`, `fable-5`, `mythos` 는 무조건 지운다(`anthropic.py:108-115`, `672-682`).
- Opus 4.7 의 `thinking.type=enabled` → `adaptive` 로 바꾸고 `budget_tokens` 를 지운다(`anthropic.py:193-220`, `690-703`).
- Claude 5 계열(기본으로 생각함)은 thinking 미지정이어도 `{"type":"adaptive","display":"summarized"}` 를 넣는다 — 생략하면 빈 thinking 만 흘러 첫 내용 감시(180초)에 걸렸다(`anthropic.py:705-721`).
- **출력 한도 재호출**: `stop_reason=="max_tokens"` 이고 텍스트가 하나도 없으면 한도를 올려(비스트림 16000, 스트림 32000) 한 번 더 부른다(`anthropic.py:117-127`, `447-467`, `532-554`). 첫 호출의 응답(=사용량)은 **버려진다** — c(τ) 누락 원인(§5.4).
- 스트림은 `messages.stream()` 의 전체 이벤트를 순회해 thinking·input_json 델타를 바로 내보낸다(`anthropic.py:495-558`). 스트림 도중 예외도 자가치유 판정을 거친다(`anthropic.py:559-589`).
- `effort` 는 SDK 판에 기대지 않으려고 `extra_body.output_config` 로 싣는다(`anthropic.py:627-642`).

### 3.3 bedrock

`AnthropicClient` 상속(`bedrock.py:116-204`). 핵심은 모델 ID 처리다: 요청 모델을 정본 핵심 ID 로 줄여(`core_model_id`, `bedrock.py:83-98`) Anthropic 의 접두사 규칙이 맞게 kwargs 를 만든 뒤, 전송 직전에 지역 추론 프로필 ID 로 바꾼다(`to_bedrock_model_id`, `bedrock.py:101-113`; `us./eu./apac.` + `anthropic.<id>-v1:0`). geo 접두사·ARN 은 그대로 보낸다. `warmup` 은 네트워크 없이 클라이언트만 만든다. 가격 조회도 `core_model_id` 로 Bedrock ID 를 벗겨 Anthropic 표를 쓴다(`stages/s07_token/artifact/default/pricing.py:31-40`).

### 3.4 openai / azure_foundry

- **Chat Completions 만** 쓴다(`openai.py:257-265`). `docs/providers.md:12` 의 "Responses / Chat Completions" 는 사실과 다르다.
- `o1/o3/o4/gpt-5*` 는 `max_completion_tokens` 로 보내고 temperature/top_p 를 지운다(`openai.py:51-88`, `393-429`).
- 스트림은 `stream_options={"include_usage": True}` 를 꼭 붙인다 — 없으면 사용량 청크가 오지 않아 몇 달간 비용이 $0 이었다(`openai.py:287-293`). 도구 호출은 델타를 모아 끝에서 블록으로 만들며, tool_use 이벤트는 내지 않는다(`openai.py:328-373`).
- 응답의 reasoning 내용(vLLM 의 `reasoning_content` 등)은 읽지 않는다. 생각 블록이 생기지 않는다.
- `thinking_level` 은 spec 의 `via` 로 갈린다: `openai_effort` 면 `reasoning_effort`(끄기는 `none`) + off 가 아니면 temperature/top_p 제거, 아니면 `vllm_request` 결과를 `extra_body` 에 합친다(`openai.py:455-486`).
- `AzureFoundryClient`(`azure_foundry.py:112-223`): `api_version` 이 있으면 옛 deployments 경로(`AsyncAzureOpenAI`), 없으면 v1(`AsyncOpenAI` + `<resource>/openai/v1/`). `model` 자리에 배포 이름을 넣는다. `api-key` 헤더를 함께 보낸다.

### 3.5 vllm / OpenAI 호환 로컬

`VLLMClient`(`vllm.py:22-83`)는 base_url 이 필수이고 기본 tools·tool_choice·구조화 출력이 꺼져 있다(`drops=("thinking_enabled","top_k","tool_choice","tools")`). `configure_capabilities(**overrides)` 로 켠다. xgen 호스트는 이 보수성을 피하려 `"vllm"` 을 `"custom"` 프로필로 매핑한다(`host/runner.py:48-58`). 생각 조절은 서빙 모델 이름의 정규식 표로 고른다(§4).

### 3.6 google / vertex

- `genai.Client(api_key, http_options={timeout(ms), base_url?, headers?})`(`google.py:111-154`). base_url 이 무시되면 한 번 경고한다(`google.py:156-182`).
- 자가치유 훅 없음. 스트림에서 생각 파트를 `thinking_delta` 로 내지 않는다(블록에만, `google.py:252-258`).
- 구조화 출력: `response_mime_type="application/json"` + 새 SDK 면 `response_json_schema`, 옛 SDK 면 `response_schema`(`google.py:28-61`).
- 함수 응답 이름은 앞선 tool_use 의 id→name 표로 채운다(`_canonical.py:567-654`).
- stop_reason 정규화 버그(§1.4) **[실측]**.
- `VertexClient`(`vertex.py:38-121`): `credentials_json`(서비스 계정) → `api_key`(express) → ADC 순. project 가 없으면 생성 시 `ValueError`.

### 3.7 claude_code_cli

생성자(`claude_code.py:181-293`) 주요 인자: `binary_path`, `workspace_dir`(cwd), `api_key`, `auth_mode`, `settings_path`, `bare_mode`, `max_budget_usd`, `default_permission_mode`, `mcp_config`, `allow_tools`, `disallow_tools`, `extra_args`, `timeout_s=300.0`, `env_extras`, `event_sink`, `strict_wire`, `runner_factory`(호스트 샌드박스용 spawn 훅), `session_hint`, `prewarm_spawn`.

**argv**(`translators/_cli.py:105-318`):

```
--print
  (stream)  --verbose --input-format stream-json --output-format stream-json --include-partial-messages
  (oneshot) --output-format json
--bare                      # auth 채널이 api_key 일 때만 (resolve_auth_channel, _cli.py:72-102)
--model <model>
--system-prompt <text>      # 블록 리스트면 text 만 이어 붙임
--effort <level>            # thinking_level(off 제외) 또는 budget 근사
--allowedTools "<...>"  --disallowedTools "<...>" --disable-slash-commands
--permission-mode <m>  --max-budget-usd <x>  --settings <path|json>
--mcp-config <json|path> --strict-mcp-config     # request.mcp_config 우선, 없으면 생성자 값
--json-schema <schema>      # response_format.type == json_schema
--resume <sid> | --session-id <sid>
<extra_args...>
-- <flattened prompt>        # 비스트림만. 스트림은 stdin
```

- `--bare` 와 env `CLAUDE_CODE_SIMPLE=1` 은 같은 스위치라 구독 채널에서 켜면 모든 턴이 인증 실패한다. 클라이언트가 구독 채널에서 그 env 를 걷어낸다(`claude_code.py:435-474`).
- 생각 끄기는 argv 가 아니라 env `MAX_THINKING_TOKENS=0` 이다(`claude_code.py:486-490`).
- 입력: 다중 턴 히스토리를 **하나의 user 봉투**로 납작하게 만든다("## Conversation so far … ## Current input", tool_use 는 `[Tool call: name(json)]`, tool_result 는 `[Tool result] …`, thinking 은 버림, 현재 턴 이미지만 실제 블록, `_cli.py:347-551`). 즉 CLI 는 매 호출 전체 기록을 평문으로 다시 받는다. `session_hint.resume` 을 쓸 때도 stdin 은 전체 기록을 보낸다 — 중복 여부는 **미확인**(Codex 는 resume 시 마지막 assistant 이후만 보낸다, `codex.py:310-322`).
- 출력 누산기 `StreamJsonAccumulator`(`_cli.py:807-1377`): 델타형·전체 메시지형·`stream_event` 래퍼를 모두 받는다. tool_use 는 인자가 모인 `content_block_stop` 에서 한 번만 이벤트로 내고(중복 방지 `_emitted_tool_ids`), `user` 봉투의 `tool_result` 를 이벤트로 낸다. `finalize()` 는 thinking+text 블록만 응답에 담고 tool_use 는 뺀다. `system` init 줄의 `tools` 목록으로 네이티브 도구 누수를 검산한다(`_report_native_leaks`, `_cli.py:1033-1059` → `host.runner.native_tool_leaks` 지연 import).
- 오류 처리: `type:error` 줄과 `error=authentication_failed` 주석을 즉시 `APIError` 로(`claude_code.py:822-843`). 버전 핸드셰이크(`--version`, 10초)를 한 번 하고 모든 오류·응답에 `cli_version` 을 붙인다(`claude_code.py:565-628`).
- hot-spare: 스트림 성공 직후 같은 argv 로 다음 턴 프로세스를 미리 띄워 90초 보관(`claude_code.py:295-431`). argv·생각 끄기 여부가 같고 살아 있을 때만 재사용. env `GENY_CLI_PREWARM=0` 으로 끈다. 서버(턴마다 새 클라이언트)는 `prewarm_spawn=False` 를 넘긴다(`host/runner.py:288-296`).
- 비스트림 + 이미지면 stream-json 와이어로 바꾼다(`claude_code.py:693-701`).

### 3.8 codex_cli

생성자(`codex.py:139-187`): `binary_path`, `workspace_dir`, `api_key`, `auth_mode`, `sandbox_mode="workspace-write"`, `bypass_sandbox`, `mcp_config`, `extra_args`, `timeout_s`, `env_extras`, `event_sink`, `strict_wire`, `runner_factory`, `session_hint`, `host_tools_only`.

- argv(`_codex.py:160-230`): `exec [resume <id>] --json --skip-git-repo-check -m <model> (--sandbox <mode> | --dangerously-bypass-approvals-and-sandbox) [-c model_reasoning_effort="<e>"] [--output-schema <file>] <-c mcp_servers.*...> [host_only -c ...] <extra> [--image <path>...] -`. 프롬프트는 stdin(`-`).
- MCP: `{"mcpServers": {...}}` 를 `-c mcp_servers.<name>.command/args/env/default_tools_approval_mode/tool_timeout_sec` 로 바꾼다(`_codex.py:53-101`). 승인 모드 기본 `"approve"` — codex exec 의 승인 정책이 never 라 주석 없는 MCP 도구가 한 번도 실행되지 않았기 때문이다(`_codex.py:83-91`). 도구 1회 제한 3600초.
- `host_tools_only=True`(호스트 기본): `features.<16종>=false`, `web_search="disabled"`, 환경·앱·협업·권한 지시 끔, 번들 스킬 끔, `tool_output_token_limit=150000`, `model_instructions_file=<시스템 프롬프트 임시 파일>`(`_codex.py:113-157`, `codex.py:273-289`).
- 인증: api_key 채널에서만 `OPENAI_API_KEY` 를 자식 env 에 넣는다. 구독(ChatGPT 로그인, `$CODEX_HOME/auth.json`) 채널에서 키가 보이면 청구 채널이 뒤집힌다(`codex.py:191-201`).
- 세션: `thread.started.thread_id` 를 잡아 다음 호출을 `exec resume` 으로(`codex.py:393-398`). resume 이면 stdin 에 시스템 프롬프트를 다시 보내지 않고 마지막 assistant 이후 메시지만 보낸다(`codex.py:291-328`).
- 누산기 `CodexEventAccumulator`(`_codex.py:246-562`): `item.started/completed` 의 `command_execution→Bash`, `mcp_tool_call→mcp__<server>__<tool>`, `file_change→ApplyPatch`, `web_search→WebSearch` 를 tool_use/tool_result 이벤트로, `agent_message`→text, `reasoning`→thinking, `turn.completed.usage`→사용량. `stop_reason` 은 늘 `"end_turn"`. JSON 처럼 보이는 최종 텍스트는 `raw["structured_output"]` 에도 싣는다.
- 경고 줄(type:error)은 바로 죽이지 않고 모아 두었다가 출력이 0이면 실패시킨다(`codex.py:571-601`).

### 3.9 "루프를 소유하는 provider" 를 런타임이 붙이는 방식

새 하네스가 반드시 다시 풀어야 할 문제라 따로 정리한다.

1. **도구 표면은 MCP 하나.** 런타임은 MCP 서버를 직접 띄우지 않는다. 턴 조립기 `AgentTurnExecutor` 가 CLI 턴이면 레지스트리·도구 컨텍스트·턴 상태를 `TurnToolSurface` 로 묶어 `params["_tool_surface"]` 로 호스트에 넘긴다(`host/turn_executor.py:735-775`). 호스트(xgen-workflow)의 `Host.build_cli_runtime(provider, params) -> CliRuntime`(`host/host.py:230-241`)이 MCP 브릿지를 띄우고 그 서버 설정을 `mcp_config` 로 CLI 클라이언트에 넣는다. 브릿지는 `TurnToolSurface.tools_list()`(MCP `tools/list`, SDK 가 보내는 것과 같은 `api_definition` 함수 사용, `host/tool_surface.py:105-134`)와 `call(name, arguments)`(MCP `tools/call`, s10 `ToolStage.dispatch_calls` 로 실행하고 턴 이벤트 루프로 넘겨 돈다, `tool_surface.py:141-170`)를 그대로 노출한다. 결과는 `to_mcp_result` 로 MCP 모양이 된다(`tool_surface.py:173-`). 브릿지를 못 띄우는 호스트는 `cli_bridge_available(provider) -> False` 로 알리고, 그때 실행기는 도구를 약속하지 않는다(`host/host.py:243-247`, `turn_executor.py:762-775`).
2. **네이티브 도구는 전부 끈다.** Claude Code: 이름 거부 상위집합 `CLI_NATIVE_TOOLS_DENY`(40종 **[실측]**, `host/runner.py:145-199`) + `--tools ""`(`runner.py:367-373`) + `--disable-slash-commands`. Codex: `host_tools_only`. 매 세션 init 의 도구 목록으로 누수를 경고한다(`runner.py:205-224`).
3. **자식 env 는 화이트리스트.** `HOME/PATH/LANG/프록시/CA/CODEX_HOME/CLAUDE_CONFIG_DIR` 등만 넘기고 나머지는 지운다(`_cli_runtime.py:142-247`). 호스트는 `CLI_QUIET_ENV`(자동 업데이트·텔레메트리 끔, `MAX_MCP_OUTPUT_TOKENS=150000`, `runner.py:261-268`)를 더한다.
4. **응답 계약.** 최종 `APIResponse` 에 tool_use 가 없으므로 호출자의 도구 단계는 아무것도 하지 않는다. 내부 루프의 도구 사건은 스트림 이벤트로만 관찰된다. s06 은 이 이벤트에 `source="cli"` 를 붙이고 `api.cli_tool_call` 을 덧붙여 낸다(`stage.py:990-999`). 내부 도구 루프 전략(`InternalAgenticLoop`)은 subprocess 클라이언트를 만나면 파이프라인 모드로 물러선다(`tool_loop.py:237-258`).
5. **시간.** CLI 는 도구를 스트림 안에서 실행하므로 s06 의 첫 내용·무응답 감시를 걸지 않는다(`stage.py:946-954`). 대신 `CLIProcessRunner.timeout_s`(호스트 기본 3600초)가 벽시계 상한이다. 부모가 끝난 뒤에도 MCP 자식이 stdout 을 붙잡는 경우에 대비해 종료 후 5초만 더 읽는다(`_cli_runtime.py:299-310`). 소비자가 끊으면 `aclosing` 으로 SIGTERM→2초→SIGKILL 프로세스 그룹 종료(`claude_code.py:803-813`, `_cli_runtime.py:341-416`).
6. **궤적 관점의 함의.** CLI 한 번의 호출 = 정책 호출 여러 번 + 도구 실행 여러 번이다. 런타임이 돌려주는 사용량은 호출 단위 하나뿐이고, 내부 턴별 사용량은 없다(누적인지조차 미확인, §1.5). 도구 실행은 MCP `call` 시점(호스트 쪽)과 스트림 이벤트로 두 번 관찰된다.

---

## 4. 생각(thinking) 조절 — `llm_client/thinking.py`

호출자가 고르는 값은 `None`(모델 기본, 아무것도 안 보냄) · `"off"` · `"on"` · `"minimal"|"low"|"medium"|"high"|"xhigh"|"max"` 하나다(`thinking.py:1-28`, `LEVEL_ORDER`·`VALUES` `37-39`).

```python
# llm_client/thinking.py:47-94
@dataclass(frozen=True)
class ThinkingSpec:
    kind: str = "none"              # "none" | "toggle" | "levels"
    levels: Tuple[str, ...] = ()
    can_disable: bool = True
    default: str = ""               # 아무것도 안 보낼 때 모델이 하는 것
    via: str = ""                   # anthropic_budget | anthropic_adaptive | openai_effort | gemini_budget
                                    # | gemini_level | ctk_enable_thinking | ctk_thinking | vllm_effort
                                    # | cli_effort | codex_effort
    off_type: str = "disabled"      # Sonnet 5.5 는 "between_tools"
    verified: bool = False          # 2026-10-01 dev 실측 여부
    budgets: Tuple[Tuple[str, int], ...] = field(default_factory=tuple)
    def options(self) -> Tuple[str, ...]      # 화면 선택지
    @property controllable -> bool
    def budget(self, level) -> int
    def to_dict(self) -> Dict[str, Any]       # {"kind","options","default","can_disable","verified"}

NONE = ThinkingSpec()

def thinking_spec(provider: str, model: str) -> ThinkingSpec          # thinking.py:342-386
def normalize_thinking(spec: ThinkingSpec, value: Any) -> Optional[str]  # thinking.py:389-420
```

`thinking_spec` 은 provider 별칭(`custom|openai_compatible|deepseek→vllm`, `azure_foundry→azure`, `gemini→google`, `thinking.py:310-316`)을 정리한 뒤 표를 고른다. 접두사 표는 경계(끝·`-`·`@`)까지 보는 최장 일치다(`_match`, `thinking.py:319-327`). 표에 없는 모델은 `NONE`(조절 불가)이다 — 모르는 모델에 값을 보내면 400 이거나 조용히 무시되기 때문이다.

| provider | 표 | 비고 |
|---|---|---|
| anthropic / bedrock | `_ANTHROPIC`(`thinking.py:149-175`) | Bedrock ID·별칭은 `_anthropic_core` 로 벗김. haiku-4-5·sonnet-4-5·opus-4-5 는 budget, 4-6 이후 adaptive. opus-5-5·fable·mythos 는 끌 수 없음 |
| claude_code | anthropic 표에서 파생, 항상 5단계 `cli_effort` | CLI 별칭 `haiku→claude-haiku-4-5`, `sonnet→claude-sonnet-5-5`, `opus→claude-opus-5-5`(`thinking.py:178-182`). `MAX_THINKING_TOKENS=0` 이 안 듣는 모델은 끌 수 없음(`184`) |
| openai / azure | `_OPENAI`(`thinking.py:193-220`) | gpt-4.1·4o 는 표에 없음 → NONE. gpt-6-astra·gpt-5·o-series 는 끌 수 없음 |
| codex | `_CODEX` 우선, 없으면 openai 표를 `codex_effort` 로 | `thinking.py:368-377` |
| google / vertex | `_GEMINI`(`thinking.py:228-265`) | 2.5 는 budget(0=끔, 2.5 Pro 는 못 끔), 3 계열은 level |
| vllm | `_VLLM` 정규식(`thinking.py:275-297`) | 서빙 이름의 basename 으로 매칭. qwen3.8 은 toggle, gpt-oss·glm-5.3 은 effort |

`normalize_thinking` 규칙(`thinking.py:389-420`): 빈 값/`auto`/`default`/조절 불가 → None, `none`→`off`, 끌 수 없는 모델의 `off` → 가장 약한 강도, toggle 모델의 강도 → `on`, levels 모델의 `on` → 기본 강도(없으면 medium), 없는 강도 → 가장 가까운 강도(같으면 약한 쪽). **[실측]** `anthropic/claude-opus-5-5/off → low`, `anthropic/claude-sonnet-4-6/xhigh → high`, `openai/gpt-5.4/max → xhigh`, `vllm/qwen3.8-27b/high → on`, `claude_code/sonnet/off → low`, `openai/gpt-4.1/high → None`.

전송 모양 함수: `anthropic_request(spec, level, max_tokens)`(off 면 `{"type": off_type}`, budget 이면 `budget_tokens` + `max_tokens` 를 예산 위로 올림, adaptive 면 `display:summarized` + `output_config.effort` + high 이상에서 `max_tokens` 바닥 16k/32k/64k, `thinking.py:427-445`), `openai_effort`, `gemini_thinking_config`, `vllm_request`, `claude_code_flags`, `codex_effort`(`thinking.py:448-480`).

함의: `anthropic_request` 는 `max_tokens` 를 **올린다**. 생각 강도를 높이면 출력 상한(=비용 상한)이 자동으로 커진다 — c(τ) 예산을 걸 때 고려해야 한다.

옛 길(`ModelConfig.thinking_enabled/budget_tokens/type/display`)도 남아 있고 `thinking_level` 이 있으면 무시된다. s08 의 예산 계획기(§6.4)는 옛 길의 `thinking_budget_tokens` 만 바꾼다.

---

## 5. 토큰·비용 회계(s07)와 c(τ)

### 5.1 흐름

1. s06 이 호출마다 `api.response` 이벤트에 input/output/cache 토큰을 싣는다(`stages/s06_api/artifact/default/stage.py:541-560`). 상태에 쌓지는 않는다.
2. s06 이 `state.last_api_response = response` 로 **마지막 응답 하나**를 남긴다(`stage.py:565`).
3. s07 `TokenStage.execute`(`stages/s07_token/artifact/default/stage.py:81-112`):
   - `usage = tracker.track(response, state)` — `DefaultTracker` 는 `state.token_usage += usage`(세션 누적), `state.turn_token_usage.append(usage)`(턴 내 호출 목록)(`trackers.py:21-28`).
   - `cost = calculator.calculate(usage, state.model)` → `state.accumulate_cost(cost)` → `state.total_cost_usd += cost`(턴 누적, `core/state.py:607-615`).
   - 캐시 지표, `token.tracked` 이벤트.
4. 턴 끝 `Pipeline._end_turn`: `session_cost_usd += total_cost_usd - _accounted_turn_cost_usd`(`core/pipeline.py:3328-3346`).
5. 예산: `state.is_over_budget()` = `total_cost_usd >= cost_budget_usd`(턴 단위, `core/state.py:617-626`).
6. 호스트 보고: `host/runner.py:1038-1122` `turn_usage(pipeline, state)` 가 `turn_token_usage` 합계를 내고, 비용은 **provider 보고값(`usage.cost_usd`) 우선, 없으면 s07 누적**을 쓴다. 호출별 프롬프트 크기는 anthropic/bedrock 만 캐시를 더한다.

### 5.2 가격 계산

```python
# stages/s07_token/artifact/default/pricing.py:12-88
def _lookup_prices(pricing, model) -> Optional[Dict[str, float]]   # 정확 일치 → 최장 접두사 → Bedrock ID 벗겨 재시도
def _price_usage(usage: TokenUsage, prices: Dict[str, float]) -> float
    # "cache_write" 키가 있으면 Anthropic 의미: input(캐시 제외)·creation·read 를 각각 과금
    # 없으면 OpenAI/Google 의미: input 에 캐시 포함 → cache_read 요율이 있을 때만 차감
    # 항상 max(0, cost)
```

| 계산기 | 표 | 기본 여부 |
|---|---|---|
| `AnthropicPricingCalculator` | `ANTHROPIC_PRICING`(opus-4-6, sonnet-4-6, haiku-4-5, sonnet-4-5, opus-4-5, opus-4-1, sonnet-4, opus-4, haiku-3-5, 3-haiku) | **s07 기본**(`stage.py:48`), 매니페스트 기본도 `anthropic_pricing`(`core/manifest_factory.py:327`, `476`) |
| `UnifiedPricingCalculator` | Anthropic + OpenAI(gpt-4.1·o3·o4-mini·gpt-4o 계열) + Google(gemini-2.5·3·3.1) | 선택 |
| `CustomPricingCalculator` | 입력·출력 단일 요율 | 선택 |

**[실측]** 입력 1000·출력 1000 토큰: `claude-sonnet-4-6` → $0.018(양쪽), `us.anthropic.claude-sonnet-4-6-v1:0` → $0.018, `claude-opus-4-7`·`claude-sonnet-5`·`claude-opus-5-5`·`sonnet`·`gpt-5.4` → **$0.0(양쪽)**, `gpt-4.1`·`gemini-2.5-pro` → 기본 계산기 $0.0 / Unified $0.01·$0.01125.

### 5.3 "정책 토큰" 정의에 필요한 것

c(τ)가 "궤적이 소비한 정책 토큰 수" 라면 호출 단위 정의가 provider 마다 달라야 한다(§1.5 표).

```
anthropic/bedrock : input + cache_creation + cache_read + output
openai 계열/codex : input(캐시 포함) + output(reasoning 포함)
google/vertex     : prompt_token_count + candidates_token_count (+ thoughts_token_count — 현재 미수집)
claude_code_cli   : 결과 봉투 usage 기준 (내부 턴 누적 여부 미확인)
```

`TokenUsage.total_tokens`(input+output)는 Anthropic 에서 캐시분을, Gemini 에서 생각분을 빠뜨린다.

### 5.4 c(τ)를 정확히 잴 수 없게 만드는 지점 (전부 코드 근거)

| # | 지점 | 결과 | 근거 |
|---|---|---|---|
| G1 | s07 이 `state.model` 로 가격 조회 | 라우터가 모델을 바꾸면(Opus 승격 등) 다른 모델 요율로 계산. CLI 별칭(`sonnet`)은 $0 | `s07 stage.py:91`, `s06 stage.py:369-398`(라우팅은 상태를 바꾸지 않음) |
| G2 | 가격표 누락 | opus-4-7 이후·sonnet-5·gpt-5.x·대부분 로컬 모델이 $0, 경고 없음 | `pricing.py:94-176`, §5.2 실측 |
| G3 | CLI `cost_usd` 무시 | s07 은 `usage.cost_usd` 를 보지 않고 가격표로만 계산한다. CLI 모델명(별칭 등)은 표에 없어 $0 이므로 `total_cost_usd`(예산 가드가 읽는 값)가 CLI 에서 사실상 0 | `s07 stage.py:91-92`, `turn_usage` 만 `cost_usd` 를 우선(`runner.py:1071-1074`) |
| G4 | Gemini 생각·캐시 토큰 미수집 | 출력 토큰 과소(생각 토큰이 별도 보고라면), 캐시 할인 반영 불가 | `google.py:423-429` |
| G5 | Anthropic 출력 한도 재호출 | 첫 호출 사용량 버림 | `anthropic.py:447-467`, `532-554` |
| G6 | s06 재시도 | 실패한 시도(스트림 중간 끊김·타임아웃)의 사용량은 기록되지 않음. 벤더 청구 여부는 미확인 | `stage.py:736-858` |
| G7 | `InternalAgenticLoop` 실패 경로 | 소비 사용량을 `state.token_usage` 에만 더하고 비용·`turn_token_usage` 에는 넣지 않음 | `tool_loop.py:444-451` |
| G8 | `InternalAgenticLoop` 성공 경로 | 내부 N회 호출 사용량을 최종 응답 하나로 합침 → `turn_token_usage` 의 호출 수가 1로 셈 | `tool_loop.py:453-454`, `runner.py:1094-1115` |
| G9 | 보조 LLM 호출 | s02 압축(`compactors.py:257-261`), 스킬 포크(`skills/fork.py:145`, `256`) 의 사용량이 상태 회계에 들어가지 않음 | grep: `token_usage +=` 는 s07 trackers 와 tool_loop 뿐 |
| G10 | 스트림 응답의 원본 사용량 객체 없음 | OpenAI reasoning 세부·Gemini thoughts 를 사후에 꺼낼 수 없음(비스트림만 `raw["response"]` 있음) | `openai.py:375-381`, `google.py:298-304` |
| G11 | s08 생각 토큰 집계 | 늘 0 **[실측]** | §6.4 |

---

## 6. 라우팅·재시도·타임아웃·페일오버(s06), 캐시(s05), 생각(s08)

### 6.1 `APIStage.execute` 구조

`stages/s06_api/artifact/default/stage.py:461-570`:

```
cfg = self._route_model(state)          # resolve_model_config(state) → router.route(cfg, state)
client = self._resolve_client(state)    # state.llm_client → legacy adapter → ClientRegistry 로 즉석 생성 → APIError(NO_CLIENT)
use_stream = self._resolve_stream(state)
call_once(extra_messages) := api.request 이벤트 → (_call_streaming_with_retry | _call_with_retry) → api.response / api.error 이벤트
response = await self._tool_loop.run(call=call_once, client=client, state=state)
state.last_api_response = response; state.add_message("assistant", assistant_content_blocks(response))
```

`_call_kwargs`(`stage.py:643-675`)가 요청 직전에 하는 일: 이번 턴에 없는 도구의 옛 호출을 평문으로 바꾸고(`retire_tool_calls_by_name`), `turn_context_text` 를 최신 user 메시지 사본 끝에 `<session-context>` 로 붙이고(캐시 경계 밖, `stage.py:605-641`), 내부 루프의 임시 메시지를 덧붙이고, `normalize_messages_for_request`(tool_use/tool_result 짝 맞춤, `core/message_repair.py:79-170`, 표준 라이브러리만 사용)로 정리한다.

### 6.2 모델 라우터

```python
# stages/s06_api/interface.py:109-126
class ModelRouter(Strategy):
    def route(self, cfg: ModelConfig, state: PipelineState) -> Optional[ModelConfig]
```

- `PassthroughRouter`(기본): 항상 None(`router.py:30-38`).
- `AdaptiveModelRouter`(`router.py:41-272`): 첫 일치 규칙 — ① `thinking_enabled` → heavy(`claude-opus-4-7`) ② 추정 문자 수 ≥ 12000 → heavy ③ `state.tools` 있음 → balanced(`claude-sonnet-4-6`) ④ ≤ 800 → light(`claude-haiku-4-5-20251001`) ⑤ 그 외 balanced. 문자 수는 `state.system`+`state.messages` 의 텍스트·도구 입력/결과 길이(`router.py:208-254`). 같은 모델이면 None. 바뀌면 `api.model_routed` 이벤트(`stage.py:390-397`). 상태는 바꾸지 않는다 → G1.
- 라우터는 **같은 클라이언트** 안에서 모델 문자열만 바꾼다. provider 를 넘나드는 라우팅은 없다. 기본 모델명이 Anthropic 이라 다른 provider 에서 켜면 잘못된 모델명을 보낸다.

### 6.3 재시도·타임아웃·페일오버

```python
# stages/s06_api/interface.py:51-66
class RetryStrategy(Strategy):
    def should_retry(self, category: ErrorCategory, attempt: int) -> bool
    def get_delay(self, attempt: int) -> float
    @property max_retries -> int
```

| 전략 | 재시도 대상 | 지연 | 위치 |
|---|---|---|---|
| `ExponentialBackoffRetry(max_retries=3, base_delay=1.0, max_delay=60.0, jitter=0.1)` **기본** | `category.is_recoverable` | `min(base·2^attempt, max) ± jitter` | `retry.py:30-146` |
| `NoRetry` | 없음 | 0 | `retry.py:149-164` |
| `RateLimitAwareRetry(max_retries=5, fallback_delay=5.0)` | RATE_LIMITED·TIMEOUT·SERVER_ERROR | `set_retry_after()` 값 또는 fallback. **`set_retry_after` 를 부르는 코드는 없다**(헤더를 읽지 않음) | `retry.py:167-247` |

타임아웃은 `llm_client/timeouts.py` 한 곳이 정하고 호출 시점에 env 로 읽는다:

| 함수 | env | 기본 | 쓰임 |
|---|---|---|---|
| `connect_timeout_s()` | `XGEN_LLM_CONNECT_TIMEOUT_S` | 10s | SDK connect·pool |
| `first_chunk_timeout_s()` | `XGEN_LLM_FIRST_CHUNK_TIMEOUT_S` | 180s | s06 스트림 첫 내용 감시(`stage.py:63-114`) |
| `idle_timeout_s()` | `XGEN_LLM_IDLE_TIMEOUT_S` | 120s | s06 청크 사이 감시 |
| `request_timeout_s()` | `XGEN_LLM_REQUEST_TIMEOUT_S` | 600s | s06 비스트림 `asyncio.wait_for`(`stage.py:751-759`) |
| `timeout_retries()` | `XGEN_LLM_TIMEOUT_RETRIES` | 1 | TIMEOUT 은 이 횟수만 재시도(`stage.py:726-734`) |
| `SDK_MAX_RETRIES` | — | 0 | SDK 자체 재시도 끔(`timeouts.py:81`) |
| `sdk_read_timeout_s()` | — | max(위 셋)+30 | SDK 소켓 읽기(감시보다 길게) |
| `sdk_client_kwargs(sdk)` / `genai_timeout_ms()` | — | — | SDK 생성자 인자(그 SDK 의 `Timeout` 클래스로, httpx2 대응) |

`timeouts.py:1-27` 의 설명: 이전엔 SDK 기본(600초·재시도 2) × 스테이지 재시도 4 로 호출 하나가 최악 2시간을 잡았다. 스테이지 `timeout_ms` 설정은 있으면 첫 내용 상한과 비스트림 상한을 대체한다(`stage.py:711-724`). 스트림 재시도 전에는 `api.stream_restart` 를 내 화면이 부분 출력을 버리게 한다(`stage.py:860-872`).

**페일오버(모델·provider 대체)는 없다.** s06·pipeline·host 어디에도 대체 모델/프로바이더 체인이 없다(grep `failover|fallback_model|fallback_provider` → `skills/fork.py` 의 포크 기본값뿐). 재시도는 같은 클라이언트·같은 모델에만 한다.

### 6.4 생각 스테이지(s08)

- `ThinkingBudgetPlanner.plan(state) -> int`(`stages/s08_think/interface.py:26-45`). `StaticThinkingBudget(10000)` 기본, `AdaptiveThinkingBudget`(base 4000 + tools 4000 + reflection 4000 + 4000자마다 2000, [2000, 24000] 클램프, `budget.py:99-237`). `apply_thinking_budget` 이 `state.thinking_budget_tokens` 를 바꾼다(`budget.py:240-264`). **ThinkStage 자신은 계획기를 부르지 않는다** — 호스트 훅이 `apply_planned_budget` 를 불러야 한다(`stage.py:83-91`의 docstring). 그리고 이 값은 옛 길(`thinking_enabled`+`budget_tokens`)에만 쓰이고 `thinking_level` 이 있으면 무시된다.
- `ThinkStage.execute`(`s08 stage.py:119-161`)는 응답의 thinking 블록을 분리해 처리기에 넘긴다. **[실측]** `ContentBlock` 을 `__dict__` 로 바꾼 뒤 `block.get("thinking", block.get("text",""))` 를 읽어 본문이 `None`, `budget_tokens_used` 칸이 없어 `total_thinking_tokens` 가 늘 0 이다(`s08 stage.py:136-144`, `176-184`).

### 6.5 프롬프트 캐시(s05)

- `CacheStrategy.apply_cache_markers(state)`(`stages/s05_cache/interface.py:13-18`)가 `state.system`·`state.tools`·`state.messages` 를 **제자리에서** 바꾼다.
- 대상은 `state.llm_client.provider in ("anthropic","bedrock")` 뿐이다(`strategies.py:17-43`). OpenAI·Gemini·vLLM 은 벤더 자동 캐시, CLI 는 자체 캐시.
- `NoCacheStrategy`(**s05 기본**, `stage.py:36`), `SystemCacheStrategy`(시스템의 안정 영역 끝에 1개), `AggressiveCacheStrategy`(도구 배열 끝 + 시스템 안정 영역 + 끝에서 N(기본 4)번째 메시지, 최대 3개). 지난 턴 표식을 먼저 지운다(4개 상한 회피, `strategies.py:46-72`). 안정/휘발 분할은 s03 이 남긴 `state.shared["system_parts"]` 를 쓴다(`strategies.py:75-116`).
- `cache_prefix` 는 전송되지 않고 호스트 집계용 키만 만든다(`stage.py:92-118`).
- 결합: 순수 함수로 바꾸기 쉽다 — 입력 `(system, tools, messages, provider, system_parts)`, 출력 같은 셋.

---

## 7. 결합도 분석

### 7.1 `llm_client/` 가 끌어오는 런타임 내부 모듈 (전수)

grep `from xgen_agent_runtime.` 중 `llm_client` 밖의 것:

| import | 위치 | 성격 |
|---|---|---|
| `core.config.ModelConfig` | `base.py:31`, `claude_code.py:43`, `codex.py:40` | 평범한 dataclass(`core/config.py:12-85`). 요청 설정 객체로 쓰임 |
| `core.errors.{APIError, ErrorCategory, GenyExecutorError}` | `base.py:32`, 각 SDK 클라이언트, `credentials.py:28` | 예외·열거형. 외부 의존 없음 |
| `core.state.TokenUsage` | `types.py:14`, SDK 클라이언트, `_cli.py:22`, `_codex.py:29` | dataclass. 같은 모듈의 `PipelineState` 는 import 하지 않음 |
| `core._head_tail_buffer.HeadTailBuffer` | `_cli_runtime.py:61` | stderr 머리·꼬리 보존 버퍼(표준 라이브러리만) |
| `core.file_blocks.file_block_pointer` | `_canonical.py:25` | file 블록 → 경로 안내 문장(순수) |
| `tools.errors.UNPARSED_ARGUMENTS_KEY` (지연) | `openai_compatible.py:178` | 문자열 상수 |
| `host.runner.native_tool_leaks` (지연, try/except) | `_cli.py:1045` | 실패해도 무시되는 경보 |
| (가격 조회 쪽 역방향) `llm_client.bedrock.core_model_id` | `s07 pricing.py:36` | s07 → llm_client |

`PipelineState`·`Stage`·`Pipeline`·이벤트 버스는 `llm_client/` 어디에도 없다. **그러나** `xgen_agent_runtime/__init__.py` 가 `Pipeline`·`PipelineBuilder`·환경 관리자 등을 import 하므로 `import xgen_agent_runtime.llm_client` 한 줄이 184개 모듈(스테이지 포함)을 올린다 **[실측: 0.88초, `core.pipeline` 로드됨]**. `core/__init__.py` 도 같은 문제다.

### 7.2 새 하네스에서 그대로 재사용 가능한 것

- `llm_client/types.py` — `APIRequest`, `ContentBlock`, `APIResponse`.
- `llm_client/base.py` — `BaseClient`, `ClientCapabilities`(협상·drops·자가치유·provenance 포함).
- SDK 클라이언트 전부: `anthropic.py`, `bedrock.py`, `openai.py`, `azure_foundry.py`, `vllm.py`, `google.py`, `vertex.py`, `profiles.py` + `openai_compatible.py`.
- `llm_client/registry.py` — `ClientRegistry`(전역 클래스 사전이라 하네스 쪽 provider 추가도 `register` 로).
- `llm_client/credentials.py` — `ProviderCredentials`, `CredentialBundle`, `ConfigError`.
- `llm_client/thinking.py` — 표와 변환 함수 전부.
- `llm_client/timeouts.py` — 단, `first_chunk/idle` 감시는 호출자(하네스) 몫이다.
- `llm_client/model_discovery.py`, `local_probe.py`.
- `llm_client/translators/` — `_canonical.py`(PIL 의존), `_cli.py`, `_codex.py`.
- `llm_client/_cli_runtime.py` — `CLIProcessRunner`, `scrub_env`, `detect_binary`, `parse_stream_json_line`.
- `llm_client/claude_code.py`, `codex.py` — 클라이언트 자체는 파이프라인과 무관하다(도구 표면은 MCP 설정 값으로만 받는다).
- `core/errors.py`(`APIError`·`ErrorCategory`·`ExecutorErrorCode`), `core/state.py` 의 `TokenUsage`, `core/config.py` 의 `ModelConfig` — 데이터형으로.
- `core/message_repair.py` 의 `normalize_messages_for_request` — 요청 경계의 tool_use/tool_result 짝 맞춤(표준 라이브러리만).
- `stages/s06_api/artifact/default/stage.py:63-114` 의 `_watched_stream` — 모듈 수준 순수 비동기 함수(`APIError`·`timeouts` 만 의존). 복사하거나 그대로 import.

### 7.3 어댑터가 필요한 것

| 대상 | 왜 | 어댑터 |
|---|---|---|
| import 무게 | 패키지 `__init__` 이 Pipeline 을 함께 올림 | 받아들이거나, 하네스 쪽에 `llm_client` 만 노출하는 얇은 재노출 모듈. 런타임을 고치지 않는 한 import 자체는 피할 수 없음 |
| `_creds_to_client_kwargs` | 순수 함수인데 `core/pipeline.py:113-268` 에 있음 | 하네스가 import 해 쓰거나 복사(의존: `ProviderCredentials`, `profiles`) |
| CLI MCP 합치기·자동 허용 | `_merge_cli_mcp_config`(`pipeline.py:375-`)와 `mcp__<server>` 자동 허용(`pipeline.py:3510-3529`) | 하네스의 클라이언트 팩토리에 같은 단계 |
| `build_cli_client` / `build_codex_cli_client` / `CLI_NATIVE_TOOLS_DENY` / `CLI_QUIET_ENV` / `native_tool_leaks` | `host/runner.py` 에 있음(그 모듈은 Pipeline 을 import) | 함수 자체는 클라이언트만 만든다 — import 해 쓰거나 복사 |
| 도구 표면(`TurnToolSurface`) | `ToolRegistry`·s10 `ToolStage.dispatch_calls`·턴 상태에 묶임 | 하네스의 도구 실행기를 감싸는 `tools_list()/call()` 객체를 새로 만들고, 호스트 MCP 브릿지 계약(`Host.build_cli_runtime`)을 맞춤 |
| 가격 계산(`_lookup_prices`, `_price_usage`) | 함수는 순수, 표는 낡음, 계산기 클래스는 `Strategy`·`ConfigSchema` 상속 | 두 함수만 쓰고 표는 하네스가 관리(누락 시 경고) |
| `AdaptiveModelRouter` | `PipelineState`(`system`·`messages`·`tools`)만 읽음 | 그 세 속성을 가진 덕 타이핑 객체를 넘기면 동작. 다만 휴리스틱이 Anthropic 전용 |
| 재시도 전략 3종 | `Strategy`·`ConfigSchema` 상속, 로직은 수십 줄 | 그대로 인스턴스화 가능(파이프라인 없이도 생성됨). 루프는 하네스가 작성 |
| s05 캐시 배치 | `state.system/tools/messages/llm_client/shared` 를 제자리 수정 | 같은 속성을 가진 객체를 넘기거나, 로직을 순수 함수로 옮김 |
| `event_sink` | 아무도 꽂지 않음 | 하네스 이벤트 버스로 연결(클라이언트 생성자 인자) |
| 스트림 이벤트 어휘 | dict 관례, 타입 없음 | 하네스 쪽에 TypedDict/열거형으로 고정하고 변환층 하나 |

### 7.4 파이프라인에 묶여 있어 다시 써야 하는 것

- `APIStage`(`stages/s06_api/artifact/default/stage.py:165-1036`) — 재시도 루프, TTFT 측정(`state.shared`), 이벤트(`state.add_event`), 히스토리 기록(`state.add_message`), 클라이언트 해석(`state.llm_client`), `ModelConfig` 해석(`Stage.resolve_model_config`, `core/stage.py:360-394`).
- `PipelineToolLoop` / `InternalAgenticLoop`(`tool_loop.py`) — `state.tool_dispatcher`·`state._context_compactor`·`run_compaction`·`state.cost_budget_usd` 에 묶임. 사용량 합산 방식이 c(τ)에 맞지 않음(G7·G8).
- `TokenStage`·트래커(`stages/s07_token/...`) — `state.token_usage`·`turn_token_usage`·`accumulate_cost`·`state.model`. G1~G3.
- `ThinkStage`·예산 계획기(`stages/s08_think/...`) — `PipelineState` 의존 + 추출 버그(G11).
- `turn_usage`(`host/runner.py:1038-1122`) — `Pipeline`·`PipelineState` 의존.
- `Pipeline._resolve_llm_client` / `_build_client_for` / `warmup` / `aclose` 의 클라이언트 수명 관리(`core/pipeline.py:2388-2430`, `3463-3530`, `1160-1177`).
- s02 압축기의 LLM 호출(사용량 미집계, G9).

---

## 8. 새 하네스가 프로그래밍할 최소 프로바이더 인터페이스

기존 클래스를 고치지 않고 감쌀 수 있는 최소 계약을 제안한다. 모든 `BaseClient` 하위 클래스가 이미 만족한다.

```python
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol
from xgen_agent_runtime.core.config import ModelConfig
from xgen_agent_runtime.llm_client import APIResponse, ClientCapabilities

class LLMProvider(Protocol):
    provider: str                       # 레지스트리 이름
    capabilities: ClientCapabilities    # is_subprocess / supports_* / drops / streaming_granularity

    async def create_message(
        self, *, model_config: ModelConfig, messages: List[Dict[str, Any]],
        system: Any = "", tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Dict[str, Any]] = None, purpose: str = "",
        response_format: Optional[Dict[str, Any]] = None,
    ) -> APIResponse: ...

    def create_message_stream(          # async generator; 마지막 이벤트는 message_complete
        self, *, model_config: ModelConfig, messages: List[Dict[str, Any]],
        system: Any = "", tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Dict[str, Any]] = None, purpose: str = "",
    ) -> AsyncIterator[Dict[str, Any]]: ...

    def thinking_provider(self) -> str: ...
    async def warmup(self, *, timeout_s: float = 8.0) -> bool: ...
    async def aclose(self) -> None: ...
```

하네스가 이 계약 위에 직접 가져야 할 것:

1. **팩토리**: `ClientRegistry.get(name)(**creds_to_kwargs(name, creds), event_sink=...)`. CLI 는 `mcp_config`(하네스의 MCP 표면 주소)·네이티브 끄기·`prewarm_spawn=False`(원샷 호스트)를 함께.
2. **루프 소유 판정**: `caps.is_subprocess and caps.supports_tools` → "Owns-loop" provider. 이때 하네스는 도구를 직접 실행하지 않고 MCP 표면(`tools/list`·`tools/call`)으로만 제공하며, 궤적은 스트림 이벤트 `tool_use`/`tool_result` 와 MCP `call` 기록으로 재구성한다. 최종 응답에는 tool_use 가 없다.
3. **비용 관문(c(τ))**: 모든 LLM 호출(본 호출·압축·요약·평가·포크)을 하나의 함수로 통과시키고 거기서 `APIResponse.usage` 를 기록한다. 정책 토큰은 §5.3 의 provider 별 정의로 계산하고, 달러가 필요하면 `_price_usage` + 하네스 소유 가격표(누락 시 경고)를 쓰되 `usage.cost_usd` 가 있으면(Claude Code) 그것을 우선한다. 가격 조회 키는 `state.model` 이 아니라 **실제 호출에 쓴 `model_config.model`(또는 `response.model`)**. 남는 누락(G4·G5·G10: Gemini thoughts, Anthropic 출력 한도 재호출, 스트림 원본 사용량)은 클라이언트를 고치지 않고는 메울 수 없다 — 재사용 원칙과 정확도 사이에서 결정이 필요하다(재호출 끄기: Anthropic 재호출은 설정 스위치가 없다).
4. **재시도·감시**: `_watched_stream`(첫 내용 180s·무응답 120s, CLI 제외) + `ErrorCategory.is_recoverable` 기반 재시도 + TIMEOUT 1회 제한. 페일오버가 필요하면 하네스가 새로 만든다(런타임에 없음).
5. **생각 조절**: `ModelConfig.thinking_level` 하나만 쓰고 옛 `thinking_enabled` 길은 쓰지 않는다. 선택지는 `thinking_spec(client.thinking_provider(), model).to_dict()`.
6. **요청 위생**: `normalize_messages_for_request(messages)` 를 매 호출 직전에.
7. **이벤트 싱크**: 클라이언트 `event_sink` 를 꽂아 `llm_client.*` 이벤트를 받는다.

---

## 9. 미확인·후속 확인 목록

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| Claude Code 결과 봉투 `usage`/`total_cost_usd` 가 내부 다중 턴 누적값인가 | 미확인(골든 캡처 모두 `num_turns:1`) | 도구를 2회 이상 쓰는 프롬프트로 stream-json 캡처 |
| Codex `turn.completed.usage` 가 누적인가, 턴당인가 | 미확인 | codex-cli 캡처 |
| Gemini `candidates_token_count` 에 생각 토큰이 포함되는가 | 미확인(SDK 에 `thoughts_token_count` 칸 존재는 실측) | Gemini 2.5/3 실호출 |
| Gemini 3 함수 호출에 `thought_signature` 반환이 필요한가 | 미확인 | 벤더 문서·실호출 |
| s06 재시도의 실패한 시도가 벤더에 청구되는가 | 미확인 | 벤더 청구 대조 |
| Claude Code 에서 `session_hint.resume` + 전체 기록 stdin 이 중복 컨텍스트를 만드는가, 호스트가 이를 쓰는가 | 미확인 | xgen-workflow `build_cli_runtime` 구현 확인 |
| 호스트(xgen-workflow)의 MCP 브릿지 구현·전송 방식(stdio/http) | 미확인(런타임 밖) | xgen-workflow 저장소 조사 |
| `docs/providers.md` 의 "OpenAI Responses", "anthropic cost telemetry" 서술 | 코드와 불일치 확인(Chat Completions 만, `supports_cost_usage=False`) | — |
