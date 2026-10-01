# geny-rsi — 스스로 나아지는 XGEN 에이전트 하네스

[![GitHub release](https://img.shields.io/github/v/release/PlateerLab/xgen-agent-runtime-rsi)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml)

**geny-rsi** 는 XGEN 의 두 번째 에이전트 런타임이다. 기존 런타임 **geny**(xgen-agent-runtime 의 21-stage 파이프라인)와
**입력·출력 계약이 같고**, 하네스는 **RRSI**(정규화된 하네스 진화)로, 탐색 계산의 배분은 **Dream-RSI**(기록 재생 기반 정책
개선)로 스스로 개선한다. XGEN 이 손으로 해 온 증거 기반 하네스 엔지니어링을 수식과 기록으로 기계화한 것이다.

XGEN 에서는 두 에이전트로 나란히 쓴다: **Agent Geny**(`agents/geny`, geny) 와 **Agent Geny RSI**(`agents/geny-rsi`, geny-rsi).

[English](README.en.md) · [**상세 비교 보고서**](docs/reports/2026-10-01-geny-vs-geny-rsi.md) · [사용 가이드](docs/GUIDE.md) · [설계 문서](docs/README.md) · [계획](docs/PLAN.md)

---

## 한눈에 — geny 와 geny-rsi

| | **geny** (기존) | **geny-rsi** (이 저장소) |
|---|---|---|
| 실행 코어 | 21-stage 파이프라인(단계 번호 고정) | 고정 커널 K₀ + 𝒦 타입 구성요소 하네스 H |
| 입력·출력 | `AgentTurnExecutor().run(host, **kwargs)` | **같다** — 청크·usage·모델 요청까지(결정적 재생으로 실측) |
| 다중 공급자 | 등록 공급자 15종(별칭 포함 — anthropic·openai·google·vllm·bedrock·vertex·azure·ollama·claude_code_cli·codex_cli …) | **같다**(런타임의 공급자 계층을 그대로 쓴다) |
| 하네스 개선 | 사람이 측정하고 고친다 | **RRSI**: 제안 → 누설 심사 → 평가 → 잡음 바닥·비용 규칙으로 채택 |
| 탐색 | 한 번에 한 경로 | branch × attempt 격자, 탐색 정책 π_E 는 **Dream-RSI** 로 개선 |
| 측정 단위 | 턴 로그 | 궤적 기록(발견 트리) + 정책 토큰 c(τ) — 그대로 재생 world 가 된다 |
| 쓰는 법 | `AgentTurnExecutor().run(host, **kwargs)` | `GenyRSITurnExecutor().run(host, **kwargs)` 또는 `GenyRSI(...)` — 기존 런타임은 geny-rsi 를 모른다 |

```
에이전트 A = (π, K₀, H, π_E)
  π    고정 정책        — 사용자가 고른 공급자·모델
  K₀   고정 커널        — I/O 계약 · 공급자 관문 · 사용량 원장 · 도구 실행·권한 · 한도 · 기록        (진화 금지)
  H    하네스          — 𝒦 9종 타입 구성요소의 내용 주소 패키지                                  (RRSI 가 진화)
  π_E  탐색 정책        — 분기·배치·정지를 정하는 코드                                            (Dream-RSI 가 진화)
```

---

## 결과 요약 (2026-10-01 · gpt-6-sol · claude-sonnet-5)

| 질문 | 결과(실측) |
|---|---|
| 갈아끼울 수 있나 | **실제 모델 응답 재생에서 두 엔진의 요청이 바이트 단위로 같다** — sonnet-5 81/81, gpt-6-sol 77/77 호출(32과제씩), 답·usage·점수 64/64 동일 |
| 같은 점수인가 | xgen-core: 네 조건 모두 점수 동일(gpt-6-sol 0.919/0.914, sonnet-5 1.000). xgen-hard: 차이는 모두 표준오차 안 |
| 스스로 나아지나 (RRSI) | gpt-6-sol 6라운드에서 3번 채택했지만 보류 분할에서 H\* 1.000 vs H0 0.997(잡음 안), 토큰 +5%. 두 모델 모두 천장이라 개선을 가릴 수 없다 → 천장 아래 모델(gpt-6-luna·claude-haiku-4-5)과 더 어려운 스위트로 다시 잰다. 실측에서 찾은 결함(측정되지 않은 편집 채택)은 0.2.0 에서 막았다 |
| 탐색을 더 잘 쓰나 (Dream-RSI) | gpt-6-sol 이 직접 개발한 탐색 정책이 라이브 확인에서 같은 최고점(8/8)을 **시도 25%·토큰 16% 적게** 찾아 RRSI 판정을 통과, 승격 |


자세한 수치·방법·한계는 [상세 비교 보고서](docs/reports/2026-10-01-geny-vs-geny-rsi.md).

---

## 빠른 시작

### 설치

GitHub Release 의 wheel 로 설치한다(xgen-agent-runtime 과 같은 방식, PyPI 미사용).

```bash
pip install \
  "xgen-agent-runtime-rsi @ https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases/download/v0.2.0/xgen_agent_runtime_rsi-0.2.0-py3-none-any.whl" \
  "xgen-agent-runtime @ https://github.com/PlateerLab/xgen-agent-runtime/releases/download/v4.80.0/xgen_agent_runtime-4.80.0-py3-none-any.whl" \
  "xgen-pdf @ https://github.com/PlateerLab/xgen-pdf/releases/download/v0.1.2/xgen_pdf-0.1.2-py3-none-any.whl"
```

### 라이브러리로 — `PipelinePresets` 와 같은 사용감

```python
from xgen_rsi import GenyRSI

agent = GenyRSI.minimal(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-...")
print((await agent.run("What is the capital of France?")).text)

worker = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...", workspace="./ws")  # 파일 도구 + 대화 이력
for chunk in worker.stream_sync("reports/ 를 읽고 summary.md 를 써 줘"):
    print(chunk, end="")

baseline = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...", engine="geny")  # 같은 호출로 기존 엔진
```

### 호스트(XGEN 서버 등)에서 — 진입점 하나

기존 런타임(xgen-agent-runtime)은 이 패키지를 모르고 고치지도 않는다. 호스트는 두 패키지를 똑같이 import 하고, 턴을
실행하는 자리에서 클래스만 고른다. 두 진입점은 같은 계약(`run(host, **kwargs)` → 글 조각 이터레이터 또는 최종 글)이다.

| XGEN 노드 | 런타임 | 패키지 | 진입점 |
|---|---|---|---|
| Agent Geny (`agents/geny`) | geny | xgen-agent-runtime | `AgentTurnExecutor` |
| Agent Geny RSI (`agents/geny-rsi`) | geny-rsi | xgen-agent-runtime-rsi | `GenyRSITurnExecutor` |

XGEN 의 Agent Geny RSI 노드는 Agent Geny 노드를 이어받아 포트·세부 설정·서버 배선이 같고, 진입점만 다르다.

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
rsi suite build ./suites/xgen-hard --suite xgen-hard
rsi evolve init runs/evo --suite ./suites/xgen-hard --policy policy.json --config rrsi.json
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
        (기존 엔진은 AgentTurnExecutor — 같은 계약)         공급자 계층·도구·계약 모듈은 xgen-agent-runtime 을 라이브러리로 쓴다    │
 ┌──────────────────────── K₀ 커널 (고정) ─────────────────────────────────────────────────────────────────────────▼──┐
 │ engine    context → prompt → client_tool → guard → config → call → parse → tools → control (결정 한 자리)           │
 │ model_call 요청 조립·스트림·재시도(런타임 공급자 계층 그대로) · ledger 모든 정책 호출과 c(τ) · tools 권한·거부·반복 차단 │
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
uv pip install --python .venv/bin/python -e ".[dev]" \
  "xgen-agent-runtime @ https://github.com/PlateerLab/xgen-agent-runtime/releases/download/v4.80.0/xgen_agent_runtime-4.80.0-py3-none-any.whl" \
  "xgen-pdf @ https://github.com/PlateerLab/xgen-pdf/releases/download/v0.1.2/xgen_pdf-0.1.2-py3-none-any.whl"
.venv/bin/python -m pytest -q        # 테스트(실제 모델 호출 없음)
.venv/bin/python -m mypy             # rsi_math strict
.venv/bin/ruff check src tests experiments
```

## 문서

- [상세 비교 보고서](docs/reports/2026-10-01-geny-vs-geny-rsi.md) — geny vs geny-rsi, 두 모델, 동등성·진화·탐색 실측
- [사용 가이드](docs/GUIDE.md) — 라이브러리 API, 서버 설정, 하네스 형식, 평가·진화·탐색 명령, 실험 재현
- [설계 문서 지도](docs/README.md) — 논문 분석, 수식 정본, 기존 런타임 조사, 융합 원칙, 아키텍처, I/O 호환, 평가, 위험
- [계획](docs/PLAN.md) — 단계 계획, 결정 항목, 구현 현황

## 참고문헌

- Xia et al., *RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*, arXiv:2609.24972, 2026. 코드: github.com/google-research/rrsi (Apache-2.0)
- Zheng et al., *Dream-RSI: Recursive Self-Improvement through Evolving Worlds*, arXiv:2609.14858, 2026
- PlateerLab, *xgen-agent-runtime* — geny(21-stage 하네스), 다중 공급자 계층, 호스트 계약

## 라이선스

Apache-2.0. RRSI 공식 구현(Apache-2.0)에서 옮기고 고친 부분의 고지는 [NOTICE](NOTICE).
