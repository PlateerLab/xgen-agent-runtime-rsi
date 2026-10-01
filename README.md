# geny-rsi — 스스로 나아지는 XGEN 에이전트 하네스

[![GitHub release](https://img.shields.io/github/v/release/PlateerLab/xgen-agent-runtime-rsi)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml)

**geny-rsi** 는 XGEN 의 두 번째 에이전트 런타임이다. 기존 런타임 **geny**(xgen-agent-runtime 의 21-stage 파이프라인)와
입력·출력 계약이 같고, 기억·작업·도구·앱·스토리지·자기 진화 같은 Geny 의 요소도 같다. 다른 것은 **한 턴을 실행하는 harness
pipeline** 하나다. geny-rsi 의 pipeline 은 고정 커널과 편집 가능한 하네스로 나뉘고, 하네스는 **RRSI** 로, 탐색 계산의 배분은
**Dream-RSI** 로 측정을 거쳐 개선된다. XGEN 이 손으로 해 온 증거 기반 하네스 엔지니어링을 수식과 기록으로 기계화한 것이다.

[English](README.en.md) · [**상세 보고서 (2차, luna·haiku)**](docs/reports/2026-10-02-luna-haiku-xgen-pro.md) · [1차 보고서](docs/reports/2026-10-01-geny-vs-geny-rsi.md) · [사용 가이드](docs/GUIDE.md) · [설계 문서](docs/README.md) · [계획](docs/PLAN.md)

---

## Agent Geny 와 Agent Geny RSI — 다른 것은 harness pipeline 하나

XGEN 에는 Geny 에이전트가 둘 있다. 캔버스·채팅·화면에서 쓰는 법은 같고, 고르는 것은 **턴을 어떻게 실행하느냐**다.

| | **Agent Geny** (`agents/geny`) | **Agent Geny RSI** (`agents/geny-rsi`) |
|---|---|---|
| 패키지 | xgen-agent-runtime | xgen-agent-runtime-rsi — **서로 import·의존하지 않는다** |
| 포트·세부 설정·자격증명·모델 | 같다 | 같다 |
| 기억·작업·도구·앱·스토리지·진화 이력 | runtime 의 요소 계층 | **같은 코드** — runtime 4.80.0 을 `xgen_rsi.base` 로 복사(파일 해시를 `COPY.json` 에 기록, 테스트로 검사) |
| **harness pipeline** | 21-stage 파이프라인. 사람이 측정하고 릴리스로 고친다 | 고정 커널 K₀ + 하네스 H(𝒦 9종 구성요소). **RRSI 가 모델 계열별로 측정해 채택한 하네스**를 쓴다 |
| 하네스가 바뀌는 길 | runtime 릴리스 | rsi 릴리스에 든 채택 하네스(`harnesses/` + `lineages.json`). 모델에 맞는 채택 하네스가 없으면 H0 = Agent Geny 와 같은 동작 |

XGEN 서버는 에이전트에 딸린 모든 기능을 **그 에이전트의 패키지**로 다룬다 — 대화 턴, [메모리]·[작업]·[도구]·[앱]·
[스토리지]·[진화 이력] 화면 API, 예약 작업, 고정본·복제. 그래서 Agent Geny RSI 의 데이터는 처음부터 끝까지 rsi 코드로 다뤄지고,
두 에이전트의 동작 차이는 harness pipeline 에서만 나온다. 특정 에이전트에 속하지 않는 플랫폼 기능(개인 SSH 연결 테스트,
범용 LLM 서비스 등)은 XGEN 의 기본 패키지(runtime)를 쓴다.

**언제 무엇을 쓰나**

- **Agent Geny** — 기본. 하네스는 runtime 릴리스로만 바뀐다(사람이 고치고 검증한 것).
- **Agent Geny RSI** — 고른 모델에 맞춰 **측정으로 채택된 하네스**를 쓰고 싶을 때. 판정에 쓰지 않은 보류 분할에서 점수가 잡음
  이상으로 오르거나, 같은 점수에서 비용이 줄었다는 기록이 있는 하네스만 패키지에 들어간다. 채택 하네스가 없는 모델에서는 Agent Geny 와
  같은 동작이다. **2026-10-02 현재 패키지에 든 채택 하네스는 없다** — [결과](#결과-요약).

---

## Geny 의 요소와 RSI

Geny 의 철학은 "에이전트 = 모델 + 요소(기억·작업·도구·앱·스토리지) + 자기 진화" 다. geny-rsi 는 요소를 그대로 두고, 요소를
**쓰는 방식**(하네스)만 측정해서 고친다. 요소의 내용(사용자 데이터)과 안전장치(권한·거부·샌드박스)는 하네스가 바꿀 수 없다.

| 요소 | 두 에이전트에서 같은 것 | 하네스가 정하는 것(RRSI 편집 대상) | 커널·호스트가 지키는 것(하네스로 못 바꿈) |
|---|---|---|---|
| **메모리** | vault·세션(STM)·장기 기억의 저장소와 형식, 기억 도구(`memory_write`·`memory_pin`), 화면 | `memory` 구성요소: 첫 반복에 고정 사실·관련 지식을 주입할지, 슬라이스 끝에 대화를 기록·요약할지. `prompt` 의 기억 안내 블록, `context_mgmt` 의 검색 시간 한도 | 기억 **내용**(사용자 데이터), 공급자 수명(열기·닫기), 턴 끝 증류, 게스트·고정본의 쓰기 차단 |
| **작업** | 작업 도구(예약·중지·목록), 스케줄러, [작업] 화면. 프롬프트 작업은 그 에이전트의 턴으로 돈다(RSI 에이전트면 RSI 하네스로) | `client_tool` 노출: 작업 도구 스키마를 언제 보이는가 | 실행 주체·권한, 게스트·고정본에서 작업 도구 제외 |
| **도구** | 내장 도구(파일·Bash·웹), 제작 도구(ForgeTool)·공용 도구, 커넥터 기기 도구, 노드로 붙인 도구 | `client_tool`: 매 호출에 보일 스키마, 앞 턴에 쓴 도구 되살리기, 점진 공개 문, 한 단계의 도구 호출을 순차·병렬로. `skill`: 하네스가 가진 일반 절차 문서(점진 공개) | 도구 구현, 권한·HITL·사용자 거부, 반복 실패 차단, 샌드박스, 등록 전 실행 테스트 |
| **앱** | 앱 만들기·배포·삭제 도구, 앱 러너, 앱이 쓰는 LLM(에이전트 모델), [앱] 화면 | 앱 도구의 노출(`client_tool`) | 앱 실행·배포·주소, 앱 LLM 정책·한도 |
| **스토리지** | 에이전트 작업 공간(클라우드 원본 ↔ 러너 세션), 파일 동기화, [스토리지] 화면 | 없다 | 작업 공간 복원·발행, 파일 경로 담장 |
| **진화 이력** | 자기 진화 도구(WorkflowSelf — 에이전트가 자기 프롬프트·도구·연결을 고침)와 그 기록 | 자기 진화 도구의 노출 | 무엇을 고칠 수 있는지와 기록 |

**두 가지 진화는 다른 축이다.**

| | 자기 진화(진화 이력, 두 에이전트 공통) | 하네스 진화(RRSI, Agent Geny RSI) |
|---|---|---|
| 무엇을 바꾸나 | 그 에이전트가 **무엇인가** — 프롬프트·도구·연결 노드(워크플로우) | 턴을 **어떻게 실행하나** — 문맥 관리·프롬프트 조립·루프 결정·도구 노출·절차 |
| 누가·언제 | 에이전트가 대화 중에, 사용자 요청이나 판단으로 | 제안자 모델이 업무 스위트에서, 오프라인 라운드로 |
| 채택 기준 | 에이전트의 판단(기록은 [진화 이력]) | 잡음 보정 바닥·비용 규칙·가드를 통과한 측정 |
| 범위 | 그 에이전트 하나 | 모델 계열 전체(같은 모델을 쓰는 모든 Agent Geny RSI) |
| 반영 | 즉시(그 워크플로우) | rsi 릴리스(사람 승인·CI) |

하네스 진화는 사용자의 워크플로우·기억·파일을 건드리지 않는다. 자기 진화가 바꾼 워크플로우는 다음 턴부터 같은 하네스로 돈다.

---

## RSI 로 달성하려는 것

**목표: 같은 모델로 더 잘, 더 싸게 — 그리고 그 사실을 기록으로 증명한다.** 하네스(턴 실행 방식)는 모델마다 맞는 모양이 다르다.
사람이 모델마다 하네스를 고치는 대신, 측정이 허락한 변경만 쌓는다.

- **RRSI — 하네스를 고친다.** 실패 궤적을 분석한 제안자가 하네스 편집을 내고(누설 심사·편집 예산 안에서), 업무 스위트로 잰다.
  점수가 **잡음 바닥(δ)을 넘어** 오르면 비용 규칙을 보고 채택하고, 띠 안이면 점수·비용·새 구조 가점을 합친 값(Eq.17)이 양수일 때만
  채택한다 — 비용만 늘리는 편집은 거절된다. 평가에서 한 번도 읽히지 않은 편집과 환경을 사실로 박는 편집은 거른다. 채택은 모델 계열별
  하네스 계보로 남고, 판정 입력은 전부 기록돼 다시 판정할 수 있다. **운영 반영(패키지)은 따로** — 판정에 쓰지 않은 보류 분할에서 이득이
  확인된 하네스만 넣는다.
- **Dream-RSI — 탐색 계산을 아낀다.** 쌓인 탐색 기록을 재생 world 로 써서, 새 생성 없이 탐색 정책(몇 갈래로 나누고 언제 멈출지)
  후보를 비교한다. 재생에서 이긴 정책도 **실제로 다시 탐색해 RRSI 판정을 통과해야** 승격한다.
- **무엇을 하지 않나.** 정책(모델) 자체를 학습하지 않는다. 사용자 데이터·권한·안전장치를 바꾸지 않는다. 측정 없이, 사람 승인
  없이 운영을 바꾸지 않는다.

## harness pipeline 비교 — geny 와 geny-rsi

| | **geny** (기존) | **geny-rsi** (이 저장소) |
|---|---|---|
| 실행 코어 | 21-stage 파이프라인(단계 번호 고정) | 고정 커널 K₀ + 𝒦 타입 구성요소 하네스 H |
| 입력·출력 | `AgentTurnExecutor().run(host, **kwargs)` | **같다** — 청크·usage·모델 요청까지(결정적 재생으로 실측) |
| 다중 공급자 | 등록 공급자 15종(별칭 포함 — anthropic·openai·google·vllm·bedrock·vertex·azure·ollama·claude_code_cli·codex_cli …) | **같다**(공급자 계층 사본 `xgen_rsi.base.llm_client`) |
| 하네스 개선 | 사람이 측정하고 고친다 | **RRSI**: 제안 → 누설 심사 → 평가 → 잡음 바닥·비용 규칙으로 채택 |
| 탐색 | 한 번에 한 경로 | branch × attempt 격자, 탐색 정책 π_E 는 **Dream-RSI** 로 개선 |
| 측정 단위 | 턴 로그 | 궤적 기록(발견 트리) + 정책 토큰 c(τ) — 그대로 재생 world 가 된다 |
| 쓰는 법 | `AgentTurnExecutor().run(host, **kwargs)` | `GenyRSITurnExecutor().run(host, **kwargs)` 또는 `GenyRSI(...)` |

```
에이전트 A = (π, K₀, H, π_E)
  π    고정 정책        — 사용자가 고른 공급자·모델
  K₀   고정 커널        — I/O 계약 · 공급자 관문 · 사용량 원장 · 도구 실행·권한 · 한도 · 기록        (진화 금지)
  H    하네스          — 𝒦 9종 타입 구성요소의 내용 주소 패키지                                  (RRSI 가 진화)
  π_E  탐색 정책        — 분기·배치·정지를 정하는 코드                                            (Dream-RSI 가 진화)
```

---

## 결과 요약

**2차 (2026-10-02 · gpt-6-luna · claude-haiku-4-5 · xgen-pro)** — [상세 보고서](docs/reports/2026-10-02-luna-haiku-xgen-pro.md)

| 질문 | 결과(실측) |
|---|---|
| 갈아끼워도 잃지 않나 | 진화 전 geny-rsi(H0)는 두 모델·두 분할 모두 기존 runtime 과 점수 차이가 잡음 안(luna 0.919 vs 0.906, haiku 0.826 vs 0.810, evolve) |
| 점수가 오르나 (RRSI) | luna: 5라운드 2번 채택, evolve 0.919 → 0.948(δ 0.025 위). 하지만 **보류 분할(k=4)에서는 H\* 0.911 vs H0 0.907 vs geny 0.916 — 잡음 안**, 토큰 +26% → 패키지에 넣지 않음 |
| 비용이 주나 (RRSI) | haiku: 같은 점수대에서 토큰 −10.6% 편집 채택(무효 처리한 실행에선 −45.8%). 진화는 2/5 라운드에서 API 크레딧 소진으로 중단 — 재개 후 갱신 |
| 측정·가드가 일하나 | 비용만 늘린 후보 7/10 자동 거절(luna). 실측 중 결함 3건을 찾아 막음: 빈 분석, **평가 환경을 사실로 박는 편집**(운영에서 틀림), 장애 시행의 0점 기록 |
| 탐색을 아끼나 (Dream-RSI) | gpt-6-sol 이 개발한 luna 탐색 정책: 같은 최고점에 시도 −36%, 그러나 토큰 +0.16% → 확인 판정이 승격을 막음 |

**1차 (2026-10-01 · gpt-6-sol · claude-sonnet-5)** — [상세 보고서](docs/reports/2026-10-01-geny-vs-geny-rsi.md): 실제 응답 재생에서 두 엔진의
요청이 바이트 단위로 같음(81/81, 77/77). 두 모델 모두 천장(0.9~1.0)이라 하네스 개선을 가릴 수 없었음. gpt-6-sol Dream 정책은 시도 25%·토큰 16% 절감으로 승격.

**정리:** geny-rsi 가 지금 확실히 하는 일은 **손해 없이 갈아끼우고, 측정이 허락한 변경만 남기는 것**이다. 보류 분할에서 확인된 점수·비용 개선은
아직 없다. 평가 호스트에 코드 실행 도구가 없다는 점(운영과 다름)과 작은 보류 분할(8과제)이 가장 큰 한계다.

---

## 빠른 시작

### 설치

GitHub Release 의 wheel 로 설치한다(PyPI 미사용). 이 패키지 하나면 된다 — xgen-agent-runtime 은 필요 없다.

```bash
pip install "xgen-agent-runtime-rsi @ https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases/download/v0.4.0/xgen_agent_runtime_rsi-0.4.0-py3-none-any.whl"
```

### 라이브러리로 — `PipelinePresets` 와 같은 사용감

```python
from xgen_rsi import GenyRSI

agent = GenyRSI.minimal(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-...")
print((await agent.run("What is the capital of France?")).text)

worker = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...", workspace="./ws")  # 파일 도구 + 대화 이력
for chunk in worker.stream_sync("reports/ 를 읽고 summary.md 를 써 줘"):
    print(chunk, end="")

baseline = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...", engine="geny")  # 같은 호출로 21-stage 엔진(geny, xgen_rsi.base 사본)
```

### 호스트(XGEN 서버 등)에서 — 진입점 하나

두 패키지는 서로 모르고, 호스트가 각각 import 한다. 두 진입점은 같은 계약(`run(host, **kwargs)` → 글 조각 이터레이터 또는
최종 글)이다. 호스트가 턴에 넘기는 도구·기억 객체는 그 에이전트의 패키지 것으로 만든다(Agent Geny RSI 의 턴은 `xgen_rsi.base` 의
`Tool`·`ToolRegistry`·기억 공급자) — 한 턴 안에서 두 패키지의 객체를 섞지 않는다.

| XGEN 노드 | 런타임 | 패키지 | 진입점 |
|---|---|---|---|
| Agent Geny (`agents/geny`) | geny | xgen-agent-runtime | `AgentTurnExecutor` |
| Agent Geny RSI (`agents/geny-rsi`) | geny-rsi | xgen-agent-runtime-rsi | `GenyRSITurnExecutor` |

XGEN 의 Agent Geny RSI 노드는 포트·세부 설정·서버 배선이 Agent Geny 와 같고, 턴은 처음부터 끝까지 이 패키지로 돈다.

```python
from xgen_agent_runtime.host.turn_executor import AgentTurnExecutor   # geny (기존)
from xgen_rsi import GenyRSITurnExecutor                             # geny-rsi

executor = GenyRSITurnExecutor() if use_geny_rsi else AgentTurnExecutor()
out = executor.run(host, **kwargs)
```

**하네스는 패키지에 담겨 온다.** 설정이 없으면 패키지의 계열 표(`harnesses/lineages.json`)로 모델에 맞는 하네스를 고르고,
없으면 내장 H0(Agent Geny 와 같은 동작)로 돈다. RRSI 로 채택한 하네스는 이 저장소의 `harnesses/` 와 그 표에 넣어 릴리스하므로,
호스트는 runtime 처럼 **버전만 올리면** 새 하네스를 쓴다. 관리자는 `XGEN_RSI_HARNESS_DIR=builtin:<이름>`(패키지 하네스 하나)이나
디렉터리 경로, `XGEN_RSI_LINEAGE_FILE`(계열 표)로 덮어쓸 수 있다.

### 하네스를 진화시키기

```bash
rsi suite build ./suites/xgen-pro --suite xgen-pro
rsi evolve init runs/evo --suite ./suites/xgen-pro --policy policy.json --config rrsi.json
rsi evolve run runs/evo                  # H0 기준선 → δ 보정 → RRSI 라운드
rsi evolve export runs/evo ./H_star      # 채택된 하네스 → XGEN_RSI_HARNESS_DIR=./H_star
```

라이브 탐색·Dream 사이클·평가 명령과 하네스 형식은 [사용 가이드](docs/GUIDE.md).

---

## 세 가지 레퍼런스

**1. XGEN 의 철학** — "No LangChain. No LangGraph. 모든 단계가 관측·변경·교체 가능한 파이프라인." 이 원칙을 그대로 두고 단위를
"21칸의 단계 번호"에서 **편집 단위의 종류(𝒦)** 로 바꿨다. 구성은 산출물(manifest 의 내용 주소)이고, CHANGELOG 4.27–4.75 의
증거 기반 판정("점수 비열등 + 비용 감소면 채택", "부탁보다 구조", "결정론 신호가 있을 때만 끼어든다")은 **RRSI 를 손으로 하던 것**이다.
권한·HITL·사용자 거부·샌드박스는 하네스가 끌 수 없는 커널 소유다.

**2. RRSI** — *Regularized Recursive Self-Improvement of Agent Harnesses* ([arXiv:2609.24972](https://arxiv.org/abs/2609.24972)).
편집 공간은 열어 두고 **탐색 궤적을 정규화**한다. 제안 쪽은 담금질 편집 예산(Eq.4)·전체 이력 신용 할당(Eq.10/11)·정체 시 미시도
구성요소 탐색(Eq.13), 선택 쪽은 누설 심사·잡음 보정 바닥(Eq.5)·이득 시 비용 규칙(Eq.7)·띠 안 규칙(Eq.17)·구조 가지치기(Eq.14)·도메인 가드.
공식 구현(google-research/rrsi, Apache-2.0)과 **차분 테스트로 같은 결과**를 낸다.

**3. Dream-RSI** — *Recursive Self-Improvement through Evolving Worlds* ([arXiv:2609.14858](https://arxiv.org/abs/2609.14858)).
쌓인 발견 이력이 곧 **재생 world** 다. 탐색 정책 후보를 기록된 트리 위에서 결정적으로 재생해(새 생성 없음) Eq.1
`V = max s − β1·N + β2·N/max(1,k★)` 로 비교하고 현재 정책을 포함한 argmax 로 고른다. geny-rsi 는 재생 승자를 실제로 다시 탐색해
**RRSI 판정을 한 번 더 통과해야** 승격한다.

---

## 아키텍처

```
호스트 ──GenyRSITurnExecutor().run(host, **kwargs)──► xgen_rsi.assembly(턴 조립 — 호스트 계약, 런타임과 같은 동작) ──► 커널
        (기존 엔진은 AgentTurnExecutor — 같은 계약)         공급자 계층·도구·계약 모듈은 사본 xgen_rsi.base(4.80.0 복사)       │
 ┌──────────────────────── K₀ 커널 (고정) ─────────────────────────────────────────────────────────────────────────▼──┐
 │ engine    context → prompt → client_tool → guard → config → call → parse → tools → control (결정 한 자리)           │
 │ model_call 요청 조립·스트림·재시도(base 공급자 계층) · ledger 모든 정책 호출과 c(τ) · tools 권한·거부·반복 차단         │
 │ stream    청크 문법·usage 1회·이어가기·취소·끝나지 못한 턴 기억 · recorder 궤적 + 발견 트리(재생 world 의 원천)          │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 ┌──────────────────────── H 하네스 (manifest.json, 𝒦 타입) ────────────────────────────────────────────────────────┐
 │ prompt · context_mgmt · control_flow · output_plumbing · client_tool · skill · memory · config   (subagent 비활성) │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 rsi_math 수식 정본 · evolve L1 RRSI · explore/dream L2 Dream-RSI · discovery 라이브 탐색 · agent GenyRSI · host LocalHost
```

---

## 수식 → 코드

| 논문 | 코드 (`xgen_rsi.rsi_math`) |
|---|---|
| RRSI Eq.3 Ŝ, Ĉ (가중 일반화·누락 = 0) | `aggregate` |
| Eq.4/9 담금질 편집 예산 b_t | `edit_budget`, `check_edit_cardinality` |
| Eq.5 잡음 보정 바닥 | `floor_ok` |
| Eq.6/7/17 ΔS·ΔC, 비용 규칙, 띠 안 규칙 | `delta_s`, `relative_cost_change`, `cost_rule` |
| Eq.10/11 이력·𝒯_t·g_t | `edit_records`, `tried`, `recent_yield` |
| Eq.13 σ_t, 𝒰_t, 예약 슬롯 | `stall_flag`, `exploration`, `reserved_variants` |
| Eq.14 𝓑_t | `prune_set` |
| Eq.15/16 𝒦_str, ν_t | `K_STR`, `novelty`, `accepted_counts` |
| Algorithm 2 판정·선택·S★ | `judge`, `select_round`, `update_s_star` |
| δ 보정 | `calibrate`, `bootstrap_se` |
| 정확 경계 조기 종료(우리 정리) | `can_stop_exactly`, `early_stop_record_delta` |
| Dream-RSI Eq.1, V^m, 선택 | `replay_value`, `mean_value`, `select_policy`, `assert_non_decreasing` |
| Appendix B 평가자 | `parallel_penalty`, `pareto_auc_v1`, `pareto_reward`, `anytime_auc` |
| 사이클 간 기본 β 규칙, 격자 검증 | `next_default_beta`, `validate_grid` |

`paper` 모드 = 공식 RRSI 구현과 비트 단위로 같은 결과, `xgen` 모드 = 문서화된 결정(동점 규칙, 𝒦 활성 집합, 점수 정규화,
정확한 유리수 동점 허용오차 등). **정확 경계 조기 종료**: 남은 시행이 전부 만점이어도 `Ŝ_max < min(S★−δ, Ŝ_t)` 이면 평가를
멈춘다 — 선택·𝒯_t·𝓑_t·N_t 가 전체 평가와 같음을 증명하고 공식 코드로 무작위 검증했다(1,788/1,788).

---

## 안전·거버넌스

- 권한·HITL·사용자 거부·샌드박스·한도·검증기·원장은 **커널 소유** — 하네스 편집으로 끌 수 없다.
- 누설 심사(평가 전): RRSI 6규칙 + XGEN 규칙(권한·거부 우회 유도, 커널 영역 침범, 고객·업무 특화, 사용자 문구 규범).
- 탐색 정책 코드는 별도 프로세스(CPU·메모리 한도) + 제한된 namespace 에서만 돈다.
- 운영 기록은 기본 **구조·점수·비용만**(내용 비보존). 설정 덤프·frontier 에 자격증명을 쓰지 않는다.
- 운영 반영은 언제나 **사람 승인**(MR/PR) 후 CI 로. 수식은 판정하고, 사람은 승인하고 헌법을 관리한다.

---

## 개발

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q        # 테스트(실제 모델 호출 없음)
.venv/bin/python -m mypy             # rsi_math strict
.venv/bin/ruff check src tests experiments
```

## 문서

- [2차 보고서](docs/reports/2026-10-02-luna-haiku-xgen-pro.md) — 천장 아래 모델(gpt-6-luna·claude-haiku-4-5) × xgen-pro, 진화·보류 분할·탐색 실측
- [1차 보고서](docs/reports/2026-10-01-geny-vs-geny-rsi.md) — gpt-6-sol·claude-sonnet-5, 재생 동등성·진화·탐색 실측
- [사용 가이드](docs/GUIDE.md) — 라이브러리 API, 서버 설정, 하네스 형식, 평가·진화·탐색 명령, 실험 재현
- [설계 문서 지도](docs/README.md) — 논문 분석, 수식 정본, 기존 런타임 조사, 융합 원칙, 아키텍처, I/O 호환, 평가, 위험
- [계획](docs/PLAN.md) — 단계 계획, 결정 항목, 구현 현황

## 참고문헌

- Xia et al., *RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*, arXiv:2609.24972, 2026. 코드: github.com/google-research/rrsi (Apache-2.0)
- Zheng et al., *Dream-RSI: Recursive Self-Improvement through Evolving Worlds*, arXiv:2609.14858, 2026
- PlateerLab, *xgen-agent-runtime* — geny(21-stage 하네스), 다중 공급자 계층, 호스트 계약. 4.80.0 을 `xgen_rsi.base` 로 복사해 쓴다(Apache-2.0)

## 라이선스

Apache-2.0. RRSI 공식 구현(Apache-2.0)에서 옮기고 고친 부분의 고지는 [NOTICE](NOTICE).
