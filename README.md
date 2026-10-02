# geny-rsi — Geny + RRSI + Dream-RSI

[![GitHub release](https://img.shields.io/github/v/release/PlateerLab/xgen-agent-runtime-rsi)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml)

**geny-rsi** 는 XGEN 의 두 번째 에이전트 런타임이다. XGEN 에서는 **Agent Geny RSI**(`agents/geny-rsi`)로 쓴다.

- **Geny 기본** — 기존 런타임 geny(xgen-agent-runtime)의 요소 계층을 그대로 복사해 가진다. 공급자·도구·기억·작업·앱·스토리지·자기 진화·
  호스트 계약이 Agent Geny 와 같다. xgen-agent-runtime 을 import 하지도 의존하지도 않는 독립 패키지다.
- **RRSI** — 한 턴을 실행하는 harness pipeline 을 고정 커널과 편집 가능한 하네스로 나누고, 하네스를 측정으로 진화시킨다. **모든 Agent Geny RSI
  는 RSI 파이프라인의 기본값(H0)에서 시작하고, 그 에이전트를 쓰는 사용자의 사용(대화·피드백·기대 답)으로 자기 하네스를 진화시킨다.**
- **Dream-RSI** — 검증기가 있는 과제를 여러 갈래로 풀어 보는 탐색에서, 몇 갈래로 나누고 언제 멈출지 정하는 탐색 정책을 기록 재생으로 진화시킨다.

두 에이전트의 차이는 **harness pipeline 하나**다. RSI 의 강점은 사용자 fit 이다 — 같은 모델이라도 에이전트마다 하는 일·사용자·자료가 다르므로
맞는 하네스도 다르다. 그래서 패키지는 H0 만 싣고, 하네스는 XGEN 안에서 에이전트마다 진화한다([설계 40](docs/design/40-agent-self-evolution.md)).
방법론을 잰 실험과 두 런타임의 구현 검토는 [**비교 보고서**](docs/reports/geny-vs-geny-rsi.md)에 한 장으로 정리했다.

[English](README.en.md) · [**비교 보고서**](docs/reports/geny-vs-geny-rsi.md) · [사용 가이드](docs/GUIDE.md) · [설계 문서](docs/README.md) · [계획](docs/PLAN.md)

---

## Agent Geny 와 Agent Geny RSI

| | **Agent Geny** (`agents/geny`) | **Agent Geny RSI** (`agents/geny-rsi`) |
|---|---|---|
| 런타임 · 패키지 | geny · xgen-agent-runtime | geny-rsi · xgen-agent-runtime-rsi. **서로 import·의존하지 않는다** |
| 진입점 | `AgentTurnExecutor().run(host, **kwargs)` | `GenyRSITurnExecutor().run(host, **kwargs)` — 같은 계약 |
| 포트·세부 설정·자격증명·모델 | 같다 | 같다 |
| 기억·작업·도구·앱·스토리지·진화 이력 | runtime 의 요소 계층 | **같은 코드** — runtime 4.81.0 을 `xgen_rsi.base` 로 복사(파일 해시를 `COPY.json` 에 기록, 테스트로 검사) |
| **harness pipeline** | 21-stage 엔진. 사람이 측정하고 릴리스로 고친다 | 고정 커널 K₀ + 하네스 H. **H0 에서 시작해 그 에이전트의 사용으로 RRSI 가 진화시킨다**(에이전트마다 자기 하네스) |
| 탐색 정책 | 없다 | π_E. **Dream-RSI** 가 진화시킨다(검증기가 있는 탐색에서 쓴다) |

```
에이전트 A = (π, K₀, H, π_E)
  π    고정 정책   사용자가 고른 공급자·모델                                     두 에이전트 같음
  K₀   고정 커널   I/O 계약 · 공급자 관문 · 사용량 원장 · 도구 권한 · 한도 · 기록       하네스로 못 바꿈
  H    하네스     𝒦 9종 타입 구성요소의 내용 주소 패키지                           H0 에서 시작, 에이전트마다 RRSI 가 진화
  π_E  탐색 정책   분기·배치·정지를 정하는 코드                                     Dream-RSI 가 진화
```

XGEN 서버는 에이전트에 딸린 모든 기능을 **그 에이전트의 패키지**로 다룬다 — 대화 턴, [메모리]·[작업]·[도구]·[앱]·[스토리지]·[진화 이력] 화면
API, 예약 작업, 고정본·복제. Agent Geny RSI 의 데이터는 처음부터 끝까지 rsi 코드로 다뤄지고, 두 에이전트의 동작 차이는 harness pipeline 에서만
나온다. 특정 에이전트에 속하지 않는 플랫폼 기능(개인 SSH 연결 테스트, 범용 LLM 서비스 등)은 XGEN 의 기본 패키지(runtime)를 쓴다.

**언제 무엇을 쓰나**

- **Agent Geny** — 하네스를 사람이 고치고 검증한 runtime 릴리스로만 바꾸고 싶을 때.
- **Agent Geny RSI** — 그 에이전트를 쓰면서 하네스가 그 사용에 맞게 진화하기를 원할 때. 처음에는 H0 로 돌고(Agent Geny 와 같은 요청을 보낸다 —
  재생 동등성으로 실측), 진화가 채택한 변경만 그 에이전트에 쌓인다. 모든 판정 근거가 남고 되돌릴 수 있다.

---

## 에이전트마다 자기 하네스 — XGEN 안의 자가 진화

```
사용자 ──턴──► Agent Geny RSI ── 커널 + [이 에이전트의 현재 하네스 H_t] ──► 답        처음에는 H0
                    │ 궤적 요약(하네스 버전·종료·도구·비용)
                    ▼
               [사용 기록] ◄── 피드백(별점·이슈·코멘트) · 기대 답(품질평가) · 직접 넣은 과제
                    ▼ 판정 기준이 붙은 과제로
               [이 에이전트의 과제] evolve / heldout
                    ▼ 진화 실행(사용자가 시작하거나, 과제가 쌓이면)
               RRSI 라운드: 분석 → 제안 → 심사 → 평가 → 판정(δ·비용 규칙·가드)
                    ▼ 채택
               [이 에이전트의 하네스 계보] H0 → H1 → …            다음 턴부터 H_{t+1}, 언제든 되돌리기
```

- **하네스는 에이전트의 것이다.** 패키지는 H0 만 싣는다. 특정 모델·스위트에 맞춘 하네스를 미리 넣지 않는다 — 실험에서 진화시킨 하네스는
  그 실험 스위트에 맞춘 것이라 운영에 쓰지 않는다.
- **재료는 그 에이전트의 사용이다.** XGEN 이 이미 남기는 턴 기록(`execution_io`), 사용자 피드백(`user_feedbacks`), 기대 답(품질평가)을 과제로 만든다.
  판정 기준은 결정적 검사와 판정 모델의 기준 판정(`answer_criteria`)이다. 판정은 하네스 밖이다.
- **평가는 사용자의 데이터·외부 시스템을 건드리지 않는다.** 과제마다 독립 작업 공간에서 그 에이전트의 설정으로 돌고, 부작용이 있는 도구는 막는다.
- **한 에이전트의 진화는 그 에이전트에만 적용된다.** 복제·고정본은 하네스 계보를 같이 가져간다.

**구현 상태** ([설계 40](docs/design/40-agent-self-evolution.md) §4)

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 패키지를 H0 만으로, 두 런타임 동일성 결함 수정, XGEN 핀 | 0.6.0 |
| 2 | 에이전트 하네스·궤적 호스트 훅, 기준 판정 검사, 사용 기록 → 과제, 에이전트 진화 실행 API | 진행 중 |
| 3 | XGEN 저장·훅·피드백 → 과제·진화 작업자·API | 진행 중 |
| 4 | Agent Geny RSI 상세 [하네스] 탭 | 진행 중 |

관리자는 `XGEN_RSI_HARNESS_DIR`(디렉터리 경로 또는 `builtin:h0`)로 모든 에이전트를 하네스 하나로 고정할 수 있다.

---

## geny-rsi 를 이루는 세 부분

### 1. Geny 기본 — 요소는 그대로

geny-rsi 는 runtime 4.81.0 을 `xgen_rsi.base` 로 복사했다. 519개 파일 중 517개가 바이트 동일하고, 나머지 2개(버전 표기·docstring 경로)는
이유와 함께 `tools/sync_base.py` 에 적혀 있다. 공급자 계층(15종: anthropic·openai·google·vllm·bedrock·vertex·azure·ollama·claude_code_cli·codex_cli …),
도구·기억·작업·앱·스토리지·자기 진화·호스트 계약, 그리고 비교 기준인 21-stage 엔진까지 전부 이 사본에서 온다. 턴 조립(호스트 계약 26단계)도
runtime 과 같은 동작의 사본(`xgen_rsi.assembly`)이다. runtime 이 바뀌면 사본을 따로 갱신한다(`tools/sync_base.py`).

### 2. RRSI — 하네스를 측정으로 고친다

한 턴의 실행 순서는 커널이 고정하고(`context → prompt → client_tool → guard → config → call → parse → tools → control`), 각 칸의 동작은
하네스의 구성요소가 정한다. 구성요소는 𝒦 9종 — `prompt`·`context_mgmt`·`control_flow`·`output_plumbing`·`client_tool`·`skill`·`memory`·
`config`(·`subagent`, 비활성) — 이고, 하네스는 그 매개변수와 파일을 담은 내용 주소 패키지(`manifest.json`)다.

RRSI 는 오프라인 라운드로 하네스를 고친다.

1. 실패 궤적을 분석기가 실패 유형으로 정리한다.
2. 제안자가 편집 예산 안에서 하네스 편집을 낸다. 검토자가 누설·환경 단정·커널 침범을 심사한다.
3. 업무 스위트로 잰다. 점수가 잡음 바닥 δ 를 넘어 오르면 비용 규칙, 띠 안이면 `100·ΔS − 15·ΔC + 0.5·ν > 0` 일 때만 채택한다.
   평가에서 한 번도 읽히지 않은 파라미터를 고친 편집은 가드가 거른다.
4. 채택은 그 에이전트의 하네스 계보로 남고(H0 → H1 → …), 판정 입력은 전부 기록돼 다시 판정할 수 있다. 다음 턴부터 채택된 하네스로 돈다.

재료는 그 에이전트의 사용이다 — 턴 입력·출력, 사용자 피드백(별점·이슈·코멘트), 기대 답(품질평가). 이것을 판정 기준이 붙은 과제로 만들어
evolve·heldout 으로 나눈다([설계 40](docs/design/40-agent-self-evolution.md) §2.3).

### 3. Dream-RSI — 탐색 계산을 아낀다

검증기가 있는 과제는 여러 갈래로 풀어 볼 수 있다(`rsi dream explore`: branch × attempt 격자). 탐색 정책 π_E 가 몇 갈래로 나누고 언제 멈출지
정한다. 쌓인 탐색 트리가 재생 world 가 되고, π_E 후보를 새 생성 없이 재생해 Eq.1 `V = max s − β1·N + β2·N/max(1,k★)` 로 비교한다.
재생 승자는 같은 과제를 실제로 다시 탐색해 **RRSI 판정(바닥 + 비용 규칙)** 을 통과해야 승격한다. 에이전트의 진화 실행이 남긴 평가 시행이 곧
그 에이전트의 재생 world 다.

운영 대화 턴에는 결과를 채점할 검증기가 없어서 π_E 를 쓰지 않고 한 경로로 돈다.

---

## Geny 의 요소와 RSI

Geny 의 철학은 "에이전트 = 모델 + 요소(기억·작업·도구·앱·스토리지) + 자기 진화" 다. geny-rsi 는 요소를 그대로 두고, 요소를 **쓰는 방식**(하네스)만
측정해서 고친다. 요소의 내용(사용자 데이터)과 안전장치(권한·거부·샌드박스)는 하네스가 바꿀 수 없다.

| 요소 | 두 에이전트에서 같은 것 | 하네스가 정하는 것(RRSI 편집 대상) | 커널·호스트가 지키는 것 |
|---|---|---|---|
| **메모리** | vault·세션(STM)·장기 기억의 저장소와 형식, 기억 도구, [메모리] 화면, 턴 끝 증류 | `memory` 구성요소: 첫 반복의 고정 사실·관련 지식 주입(`retrieve`), 슬라이스 끝 대화 기록·rollup(`archive`). `prompt` 의 기억 안내 블록 | 기억 내용, 공급자 수명(열기·닫기), 증류, 게스트·고정본의 쓰기 차단 |
| **작업** | 작업 도구(예약·중지·목록), 스케줄러, [작업] 화면. 예약 작업은 그 에이전트의 턴으로 돈다 | 작업 도구 스키마의 노출(`client_tool`) | 실행 주체·권한, 게스트·고정본에서 작업 도구 제외 |
| **도구** | 내장 도구(파일·Bash·웹), 제작 도구·공용 도구, 커넥터 기기 도구, 노드로 붙인 도구 | `client_tool`: 매 호출에 보일 스키마, 앞 턴 도구 되살리기, 점진 공개 문, 순차·병렬 실행. `skill`: 하네스의 절차 문서(점진 공개) | 도구 구현, 권한·HITL·사용자 거부, 반복 실패 차단, 샌드박스, 등록 전 실행 테스트 |
| **앱** | 앱 만들기·배포·삭제 도구, 앱 러너, 앱 LLM, [앱] 화면 | 앱 도구의 노출 | 앱 실행·배포·주소, 앱 LLM 정책·한도 |
| **스토리지** | 에이전트 작업 공간(클라우드 원본 ↔ 러너 세션), 파일 동기화, [스토리지] 화면 | 없다 | 작업 공간 복원·발행, 파일 경로 담장 |
| **진화 이력** | 자기 진화 도구(에이전트가 자기 프롬프트·도구·연결을 고침)와 그 기록 | 자기 진화 도구의 노출 | 무엇을 고칠 수 있는지와 기록 |

**두 가지 진화는 다른 축이다.**

| | 자기 진화(진화 이력, 두 에이전트 공통) | 하네스 진화(RRSI, Agent Geny RSI) |
|---|---|---|
| 무엇을 바꾸나 | 그 에이전트가 **무엇인가** — 프롬프트·도구·연결 노드 | 턴을 **어떻게 실행하나** — 문맥 관리·프롬프트 조립·루프 결정·도구 노출·절차·기억 정책 |
| 누가·언제 | 에이전트가 대화 중에 | 제안자 모델이 그 에이전트의 사용에서 만든 과제로, 진화 실행 라운드에서 |
| 채택 기준 | 에이전트의 판단(기록은 [진화 이력]) | 잡음 바닥·비용 규칙·가드를 통과한 측정(기록은 [하네스]) |
| 범위 | 그 에이전트 하나 | 그 에이전트 하나 |
| 반영 | 즉시(그 워크플로우) | 채택 즉시 다음 턴부터(되돌릴 수 있다) |

둘은 서로 보완한다. 자기 진화가 에이전트의 일을 바꾸면 그 뒤의 사용이 과제가 되고, 하네스 진화가 그 일에 맞는 실행 방식을 찾는다.

---

## 결과 요약

[비교 보고서](docs/reports/geny-vs-geny-rsi.md) — 네 모델(gpt-6-sol·claude-sonnet-5·gpt-6-luna·claude-haiku-4-5) × 세 스위트(xgen-core·xgen-hard·xgen-pro).
이 실험은 **방법론이 동작하는지** 잰 것이다. 실험에서 진화시킨 하네스는 그 스위트에 맞춘 것이라 패키지에 넣지 않는다.

| 질문 | 결과(실측) |
|---|---|
| 갈아끼워도 잃지 않나 | 같은 응답을 재생하면 H0 는 geny 와 같은 요청을 바이트 단위로 보낸다(158/158). 진화 전 점수 차이는 모든 모델·스위트에서 잡음 안 |
| RRSI 가 점수를 올리나 | evolve 에서 luna 0.919 → 0.948(δ 위), sol 0.992 → 1.000. **보류 분할에서는 잡음 안**(luna 0.911 vs 0.907, sol 1.000 vs 0.997) |
| RRSI 가 비용을 줄이나 | haiku 같은 점수대에서 토큰 −10.6%(evolve). luna·sol 채택본은 보류 분할에서 토큰 +26%·+5% |
| Dream-RSI 가 탐색을 아끼나 | sol: 같은 최고점에 시도 −25%·토큰 −16% 로 승격. luna: 시도 −36% 후보가 토큰 +0.16% 라 거절 |
| 측정·가드가 일하나 | 비용만 늘린 후보 자동 거절(luna 7/10). 실측에서 해로운 편집 두 종류(측정 안 된 편집, 평가 환경을 사실로 박는 편집)를 찾아 가드로 막음 |

**정리.** 갈아끼워도 잃지 않고(H0 = geny), RRSI 는 진화에 쓴 과제에서 점수·비용을 고쳤으며, 손해 보는 변경을 자동으로 거르고 근거를 남긴다.
보류 분할에서 확인된 점수·비용 개선은 아직 없다 — 실험 스위트가 작고(heldout 8과제) 사용자와 무관한 합성 과제라는 점이 한계이고, 그래서 운영의
진화 재료는 각 에이전트의 실제 사용이다. 실험 평가는 기억·작업·자기 진화·코드 실행 없이 파일 도구로만 돌았다.

---

## 빠른 시작

### 설치

GitHub Release 의 wheel 로 설치한다(PyPI 미사용). 이 패키지 하나면 된다 — xgen-agent-runtime 은 필요 없다.

```bash
pip install "xgen-agent-runtime-rsi @ https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases/download/v0.6.0/xgen_agent_runtime_rsi-0.6.0-py3-none-any.whl"
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

두 패키지는 서로 모르고, 호스트가 각각 import 한다. 호스트가 턴에 넘기는 도구·기억 객체는 그 에이전트의 패키지 것으로 만든다(Agent Geny RSI 의
턴은 `xgen_rsi.base` 의 `Tool`·`ToolRegistry`·기억 공급자) — 한 턴 안에서 두 패키지의 객체를 섞지 않는다.

```python
from xgen_agent_runtime.host.turn_executor import AgentTurnExecutor   # geny — Agent Geny
from xgen_rsi import GenyRSITurnExecutor                             # geny-rsi — Agent Geny RSI

executor = GenyRSITurnExecutor() if use_geny_rsi else AgentTurnExecutor()
out = executor.run(host, **kwargs)   # 글 조각 이터레이터(streaming) 또는 최종 글
```

### 하네스를 진화시키기 · 탐색 정책을 진화시키기

```bash
rsi suite build ./suites/xgen-pro --suite xgen-pro
rsi evolve init runs/evo --suite ./suites/xgen-pro --policy policy.json --config rrsi.json
rsi evolve run runs/evo                  # H0 기준선 → δ 보정 → RRSI 라운드
rsi evolve export runs/evo ./H_star      # 채택된 하네스 → 디렉터리(실험·재현용, XGEN_RSI_HARNESS_DIR 로 고정해 볼 수 있다)

rsi dream explore runs/pool --suite ./suites/xgen-pro --policy policy.json --explorer builtin:parallel_refine --iteration 0
rsi dream cycle runs/cycle1 --worlds worlds.json --incumbent builtin:parallel_refine --iteration 1 ...
```

명령과 하네스 형식은 [사용 가이드](docs/GUIDE.md).

---

## 세 가지 레퍼런스

**1. XGEN 의 철학** — "No LangChain. No LangGraph. 모든 단계가 관측·변경·교체 가능한 파이프라인." 이 원칙을 그대로 두고 단위를
"21칸의 단계 번호"에서 **편집 단위의 종류(𝒦)** 로 바꿨다. CHANGELOG 4.27–4.75 의 증거 기반 판정("점수 비열등 + 비용 감소면 채택",
"부탁보다 구조", "결정론 신호가 있을 때만 끼어든다")은 **RRSI 를 손으로 하던 것**이다. 권한·HITL·사용자 거부·샌드박스는 하네스가 끌 수 없는
커널 소유다.

**2. RRSI** — *Regularized Recursive Self-Improvement of Agent Harnesses* ([arXiv:2609.24972](https://arxiv.org/abs/2609.24972)).
편집 공간은 열어 두고 **탐색 궤적을 정규화**한다. 제안 쪽은 담금질 편집 예산(Eq.4)·전체 이력 신용 할당(Eq.10/11)·정체 시 미시도 구성요소
탐색(Eq.13), 선택 쪽은 누설 심사·잡음 보정 바닥(Eq.5)·이득 시 비용 규칙(Eq.7)·띠 안 규칙(Eq.17)·구조 가지치기(Eq.14)·도메인 가드.
공식 구현(google-research/rrsi, Apache-2.0)과 **차분 테스트로 같은 결과**를 낸다.

**3. Dream-RSI** — *Recursive Self-Improvement through Evolving Worlds* ([arXiv:2609.14858](https://arxiv.org/abs/2609.14858)).
쌓인 발견 이력이 곧 **재생 world** 다. 탐색 정책 후보를 기록된 트리 위에서 결정적으로 재생해(새 생성 없음) Eq.1 로 비교하고 현재 정책을
포함한 argmax 로 고른다. geny-rsi 는 재생 승자를 실제로 다시 탐색해 **RRSI 판정을 한 번 더 통과해야** 승격한다.

---

## 아키텍처

```
호스트 ──GenyRSITurnExecutor().run(host, **kwargs)──► xgen_rsi.assembly(턴 조립 — runtime 과 같은 동작의 사본) ──► 커널
        (Agent Geny 는 AgentTurnExecutor — 같은 계약)       공급자·도구·기억·작업·앱·스토리지는 사본 xgen_rsi.base(4.81.0)     │
 ┌──────────────────────── K₀ 커널 (고정) ─────────────────────────────────────────────────────────────────────────▼──┐
 │ engine    context → prompt → client_tool → guard → config → call → parse → tools → control (결정 한 자리)           │
 │ model_call 요청 조립·스트림·재시도(base 공급자 계층) · ledger 모든 정책 호출과 c(τ) · tools 권한·거부·반복 차단         │
 │ stream    청크 문법·usage 1회·이어가기·취소·끝나지 못한 턴 기억 · recorder 궤적 + 발견 트리(재생 world 의 원천)          │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 ┌──────────────────────── H 하네스 (manifest.json, 𝒦 타입) ── 에이전트마다, 처음에는 H0 ────────────────────────────┐
 │ prompt · context_mgmt · control_flow · output_plumbing · client_tool · skill · memory · config   (subagent 비활성) │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 rsi_math 수식 정본 · evolve RRSI · explore/dream Dream-RSI · discovery 라이브 탐색 · agent GenyRSI · host LocalHost
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

`paper` 모드 = 공식 RRSI 구현과 비트 단위로 같은 결과, `xgen` 모드 = 문서화된 결정(동점 규칙, 𝒦 활성 집합, 점수 정규화, 정확한 유리수 동점
허용오차 등). **정확 경계 조기 종료**: 남은 시행이 전부 만점이어도 `Ŝ_max < min(S★−δ, Ŝ_t)` 이면 평가를 멈춘다 — 선택·𝒯_t·𝓑_t·N_t 가 전체
평가와 같음을 증명하고 공식 코드로 무작위 검증했다(1,788/1,788).

---

## 안전·거버넌스

- 권한·HITL·사용자 거부·샌드박스·한도·검증기·원장은 **커널 소유** — 하네스 편집으로 끌 수 없다.
- 누설 심사(평가 전): RRSI 6규칙 + XGEN 규칙(권한·거부 우회 유도, 커널 영역 침범, 고객·업무 특화, 사용자 문구 규범, 환경 단정 금지).
- 평가에서 한 번도 읽히지 않은 파라미터를 고친 편집은 채택하지 않는다(0.2.0 이후).
- 탐색 정책 코드는 별도 프로세스(CPU·메모리 한도) + 제한된 namespace 에서만 돈다.
- 운영 기록은 기본 **구조·점수·비용만**(내용 비보존). 설정 덤프·frontier 에 자격증명을 쓰지 않는다.

---

## 개발

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q        # 테스트(실제 모델 호출 없음)
.venv/bin/python -m mypy             # rsi_math strict
.venv/bin/ruff check src tests tools experiments
.venv/bin/python tools/sync_base.py --check   # xgen_rsi.base 가 runtime 4.81.0 사본 그대로인지
```

## 문서

- [비교 보고서](docs/reports/geny-vs-geny-rsi.md) — 두 런타임의 구현 검토, 교체 동등성, RRSI·Dream-RSI 방법론 실험, 운영 적용, 한계
- [설계 40 — 에이전트마다 자기 하네스](docs/design/40-agent-self-evolution.md) — XGEN 안에서 도는 자가 진화(운영 구조)
- [사용 가이드](docs/GUIDE.md) — 라이브러리 API, 서버 설정, 하네스 형식, 평가·진화·탐색 명령, 사본 갱신
- [설계 문서 지도](docs/README.md) — 논문 분석, 수식 정본, 기존 런타임 조사, 융합 원칙, 아키텍처, I/O 호환, 평가, 위험
- [계획](docs/PLAN.md) — 단계 계획, 결정 항목, 구현 현황

## 참고문헌

- Xia et al., *RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*, arXiv:2609.24972, 2026. 코드: github.com/google-research/rrsi (Apache-2.0)
- Zheng et al., *Dream-RSI: Recursive Self-Improvement through Evolving Worlds*, arXiv:2609.14858, 2026
- PlateerLab, *xgen-agent-runtime* — geny(21-stage 하네스), 다중 공급자 계층, 호스트 계약. 4.81.0 을 `xgen_rsi.base` 로 복사해 쓴다(Apache-2.0)

## 라이선스

Apache-2.0. RRSI 공식 구현(Apache-2.0)에서 옮기고 고친 부분과 xgen-agent-runtime 사본의 고지는 [NOTICE](NOTICE).
