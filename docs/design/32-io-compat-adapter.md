# 32. I/O 호환 — 갈아끼우기 계약과 검증 방법

> 근거: [11-runtime-io-contract.md](../research/11-runtime-io-contract.md) (특히 §6 "교체 시 반드시 유지할 계약" 49행, 부록 최소 인터페이스). 이 문서는 그 표의 각 항목을 **새 엔진이 어떻게 만족하는지**, 그리고 **어떻게 증명하는지**를 정한다.

---

## 1. 경계와 원칙

- 경계 **A**: `xgen_agent_runtime.host.turn_executor.AgentTurnExecutor().run(host, **kwargs)` 아래 전부(턴 조립 + 실행 + 스트림 번역)를 같은 계약의 `xgen_rsi.GenyRSITurnExecutor().run(host, **kwargs)` 가 대체한다. 어느 쪽을 쓸지는 호스트가 고르고, 기존 런타임은 고치지 않는다.
- 원칙: **의미 동등**(semantic equivalence). 소비자가 관찰하는 모든 것 — 반환 형태, 청크 문법과 순서, usage 필드와 의미, 안내·오류 문장, kwargs 역방향 키와 순서, HostServices 호출 순서·인자, 취소·close·teardown, 영속 형식 — 이 같아야 한다. 내부 구조는 자유.
- 계약 모듈(host 프로토콜, 도구 ABI, 메모리 프로토콜, cancel_context, runner 공개 심볼, `_constants`)은 **기존 패키지에 그대로 두고 import**(31 문서 §1.1). 재구현하지 않으므로 모듈 경로·심볼 계약은 자동 충족.

---

## 2. 자체 진입점 (기존 런타임 무변경)

```python
# 호스트 쪽 — 턴을 실행하는 자리에서 클래스만 고른다
from xgen_agent_runtime.host.turn_executor import AgentTurnExecutor   # geny
from xgen_rsi import GenyRSITurnExecutor                             # geny-rsi

out = (GenyRSITurnExecutor() if use_geny_rsi else AgentTurnExecutor()).run(host, **kwargs)
```

```python
# xgen_rsi/turn_executor.py
class GenyRSITurnExecutor:
    def run(self, host, **kwargs):
        plan = assemble_turn(host, kwargs, resources)      # xgen_rsi.assembly — 호스트 계약의 자체 사본
        if isinstance(plan, str):                           # 조기 종료 출력(기존과 같은 문구)
            return iter([plan]) if streaming else plan
        prepared = RSITurnExecutor().prepare(plan, host)
        return RSITurnExecutor().execute(prepared, plan, host)
        # 조립 실패 → "[ERROR] geny agent could not start: …" (기존과 같은 계약)
```

- 기존 런타임은 geny-rsi 를 모른다. 되돌리기 = 호스트가 `AgentTurnExecutor` 로 돌아가기.
- 턴 조립 사본은 런타임의 조립과 같은 동작이어야 한다 — §5 동등성 테스트와 실제 모델 재생 실험(`experiments/replay_equivalence.py`)이 잰다.
- 이력: 당초 설계는 런타임에 엔진 선택점을 두는 것이었고(4.76.0·4.79.0 에 들어갔다) 4.80.0 에서 걷어 냈다.

---

## 3. 계약 항목별 충족 방법

11 문서 §6 표를 묶음별로 재정리한다. "재사용" = 기존 모듈을 그대로 호출, "재현" = 새 코드로 같은 의미를 구현, "커널 소유" = 하네스가 바꿀 수 없음.

| 묶음 | 항목 | 충족 방법 | 증명(테스트) |
|---|---|---|---|
| 진입·반환 | 진입점·모듈 경로, `streaming` 분기, 동기·워커 스레드, 조기 종료 출력(`[ERROR104: …]`, `[ERROR] geny agent could not start: …`, `iter([str])` 에 close 없음) | 자체 진입점 `GenyRSITurnExecutor.run` 이 같은 문구·형태를 재현, 조기 검증은 기존 `validate_agent_params` 재사용 | G-1 골든 스트림, C-1 시그니처 |
| 입력 | kwargs 의미 31개, 정의만 있는 4개, kwargs 통과(같은 dict), 역방향 키 `_sandbox_session`/`_tool_surface` 와 시점 | assembly 가 기존 26단계 순서 재현, 같은 dict 객체 전달 | C-2 호스트 호출 기록 비교(순서·인자·dict 동일성) |
| 입력 | `TurnInput` 수용 모양, 첨부 → str/dict | 기존 `TurnInput` 재사용 | 기존 테스트 이식 |
| 호스트 | `HostServices` 필수 메서드·선택 훅 6개(getattr), `register_builtin_tools` 반환 형태, 정책 적용 뒤 표면 판정 | assembly 재현(같은 getattr 규칙) | C-2 + FakeHost(14 문서 §5 패턴) |
| 프로토콜 | GenySandbox, MemoryProvider, 도구 ABI·`state_view`(add_event·shared·pending_tool_calls), canvas_command 통과 | 재사용(커널 ToolRunner 가 같은 ToolContext 제공) | G-2 도구 시나리오, canvas 시나리오 |
| 출력 | 청크 문법 4종, agent_event 필드, 결과 머리 3200+꼬리 800(다운로드 마커 보존), task_* 3종, 안내 문장(CLAMP 맨 앞, SUSPEND/BUDGET/REPEAT, schema 턴 제외), 오류 접두 | 커널 `stream.py` 재현(형식 상수는 기존 모듈에서 import) | G-1 골든 스트림 바이트 비교(타임스탬프·id 정규화 후) |
| 출력 | usage 페이로드(필드·anthropic/bedrock 캐시 비대칭·harness·partial), 정확히 1회·마지막, `usage_sink` 동일 객체 update, close 시 partial | `UsageLedger.external_usage_payload()` 가 기존 `turn_usage` 의미로 생성 | U-1 공급자별 usage 동등(가짜 응답의 usage 를 공급자 형식으로) |
| 출력 | 구조화 출력(비스트리밍 압축 JSON/원문, 스트림 원문), 비스트리밍 반환 규칙(`[SUSPENDED] r`, `[BLOCKED] r`, `[ERROR] …`, 예외 raise) | output_plumbing(H0) + 커널 반환 규칙 | G-3 |
| 제어 | close() 전파·teardown 순서, 협조적 취소(per-turn `cancel_check` 우선, 대기 중 폴링, 전역 레지스트리 공유), 자동 이어가기(resumable 슬라이스 N회) | 커널 재현, cancel_context 재사용 | X-1 취소·close 시나리오 |
| CLI | `TurnToolSurface`(registry, tool_context, exposed_names, tools_list, call, bind_loop), MCP 서버 이름 `connector`·도구 접두 `mcp__connector__X`, `on_loop` | 기존 `TurnToolSurface` 재사용(새 엔진이 같은 객체를 만들어 kwargs 에 기록) | CLI 가짜 바이너리 시나리오(기존 테스트 자산) |
| 영속 | 메모리 이력 preload 워터마크, 실행 카드·저널·대화 rollup, `_distill_state.json` 키, rollout 경로·형식(옵트인) | 기존 `execution_record`, `conversation_archive`, `distill` 재사용. rollout 은 기존 형식 + RSI recorder 는 **별도 경로** | P-1 파일 형식 비교 |
| 설정·문자열 | host.setting 키 5종, 이미지 예산 env, dex-core 거부 문구, `.xgeny/python-env.json`, `_constants` 프롬프트 심볼 | 재사용 | 기존 테스트 이식 |
| 배포 | 배포 이름·import 이름·wheel 핀 | 기존 런타임은 그대로, 새 wheel 추가 | 배포 체크리스트 |

### 3.1 파이프라인 층 계약(경계 C)의 처리

`Pipeline.run/run_stream`, `PipelineEvent/State/Result`, `APIRequest/Response/ContentBlock` 필드 고정 테스트(`rt-tests/contract/test_public_runtime_contract.py`)는 **기존 엔진의 계약**으로 남는다(기존 런타임은 그대로이므로 계속 유효). 새 엔진은 이 층을 제공하지 않는다. workflow 테스트 중 `stream_turn(pipeline, …, PipelineState(...))` 처럼 파이프라인 층을 직접 쓰는 것은 기존 엔진 대상 테스트로 유지하고, 새 엔진에는 경계 A 수준의 동등 테스트를 새로 둔다.

---

## 4. 알려진 특이 동작 — 재현할지 고칠지 (11 문서 §5.11)

| # | 동작 | 결정(제안) | 이유 |
|---|---|---|---|
| Q1 | 첨부 있는 턴에서 실행 카드 미기록(추정, `_clip(dict)` AttributeError) | **고침**(H0 에서 dict 입력 텍스트 추출) | 데이터 연속성 버그. 소비자에게 보이는 형식은 동일 |
| Q2 | 스트리밍 오류 청크가 `"\n[ERROR] "` 로 시작해 오류 코드 변환을 못 탐 | **재현(기본) + 플래그로 고침** | 고치면 사용자 화면·HTTP 상태가 바뀜(외부 관찰 변화). 사용자 결정 필요 → PLAN 결정 D-6 |
| Q3 | `finalize_turn` 예외가 비스트리밍 턴에서만 올라옴 | 재현 | 예외 전파 의미 변경은 소비자 영향 |
| Q4 | generator 미소비 시 teardown 미실행 | **고침**(약한 참조 finalizer 로 teardown 보장) | 자원 누수 방지, 외부 관찰 변화 없음 |
| Q5 | `pipeline.complete` 폴백 텍스트가 슬라이스 단위 판정 | 재현 | 출력 텍스트 동일성 |
| Q6 | `hydrate_workspace` 의 None 처리 | 재현 | 호스트 호환 |
| Q7 | stage 오류 코드 손실(`exec.stage.failed`) — 10 문서 §0-7 | 새 엔진은 원 코드 보존. 외부에는 오류 *문장*만 나가므로 청크는 동일, 내부 기록만 정확해짐 | 외부 관찰 변화 없음 |
| Q8 | 턴 입력 예산이 OpenAI 계열 캐시를 이중 계산(14 문서) | **고침** | 내부 한도 계산 버그. 종료 시점이 달라질 수 있어 변경 기록 |

---

## 5. 증명 계획 — 동등성 테스트 묶음

| ID | 이름 | 방법 | 판정 |
|---|---|---|---|
| C-1 | 시그니처 | `inspect.signature`, 반환 타입 분기 | 일치 |
| C-2 | 호스트 호출 기록 | 기록형 FakeHost 로 두 엔진의 HostServices 호출 열(메서드·키워드·kwargs dict 의 `id()`)을 비교 | 열 동일 |
| G-1 | 골든 스트림 | 시나리오 × 가짜 클라이언트(`BaseClient._send` 스크립트 응답)로 두 엔진의 청크 열 비교. timestamp·uuid·duration 정규화 | 바이트 동일(정규화 후) |
| G-2 | 도구 시나리오 | 도구 호출·오류·거부·canvas_command·큰 결과(머리/꼬리)·병렬 호출 | 동일 |
| G-3 | 구조화·비스트리밍 | schema 턴, suspended/blocked/error 반환 | 동일 |
| U-1 | usage 동등 | 공급자별(anthropic·bedrock·openai·google·vllm·CLI) usage 원형을 스크립트로 주입 | 필드·값 동일 (단 원장이 *새로 잡는* 압축·증류 호출 토큰은 차이 허용 목록에 명시 — 기존이 누락하던 값) |
| X-1 | 취소·close | 대기 중 취소, 스트림 중 close, 이어가기 한도 | 순서·partial·teardown 동일 |
| P-1 | 영속 형식 | vault 카드·rollup·distill_state·rollout 파일 비교 | 형식 동일 |
| L-1 | 실제 공급자 소규모 회귀 | dev 자격으로 공급자별 3–5 시나리오, 두 엔진 | 청크 문법 동일, 결과 품질은 평가 세트에서(34 문서) |

- 시나리오 원천: 기존 런타임 테스트 자산(가짜 클라이언트·ScriptedClient·가짜 CLI 바이너리·실녹화 golden, 14 문서 §5) + workflow 테스트의 FakeHost 패턴.
- **usage 차이 허용 목록(U-1)**: 새 원장은 기존이 빠뜨린 호출(압축 요약, 메모리 증류, 내부 도구 루프 왕복, 재시도)을 셀 수 있다. 외부 `input_tokens/output_tokens` 가 기존보다 커지는 것은 *정확해지는 것*이지만 과금·쿼터에 영향을 준다 → **외부 usage 에 포함할지는 결정 항목**(PLAN 결정 D-7). 기본값 제안: 외부 usage 는 기존 정의(메인 루프 호출)로 유지하고, 정확한 c(τ) 는 내부 원장·trace 에만 기록 → 과금 의미 불변.

---

## 6. 단계적 교체 순서

1. 기존 런타임은 그대로 둔다(변경 없음).
2. 새 엔진 + H0 로 C/G/U/X/P 테스트 통과(로컬·CI).
3. 호스트가 `GenyRSITurnExecutor` 를 고르는 범위(워크플로·사용자 단위)로 L-1 회귀.
4. 평가 세트에서 Ŝ(H0_rsi) vs Ŝ(pipeline21) 동등(δ 안) 확인 — "교체 자체의 회귀 없음" 증명.
5. canary(일부 워크플로) → 기본 전환(사람 결정).
6. 이후부터 RRSI 진화 H\* 가 lineage 로 배포된다.
