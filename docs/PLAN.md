# PLAN — RSI 하네스 프레임워크 구축 계획

> 목표: 기존 21-stage 하네스(`xgen-agent-runtime`)의 **입력·출력 인터페이스만 유지**하고, 기존 방법론 + RRSI + Dream-RSI 를 융합한 새 하네스 프레임워크를 처음부터 설계·구현한다. 다중 공급자 사용은 그대로 유지하고, 두 논문의 핵심 수식과 계산은 전부 정확히 구현한다.
> 근거 문서: [README.md](README.md) 의 문서 지도. 원칙은 [design/30](design/30-fusion-philosophy.md), 구조는 [design/31](design/31-architecture.md).
> 상태(2026-10-01): **Phase 0–3·7 구현 완료, Phase 4–6 코드 완료(실제 LLM 측정 미실행), Phase 8–9 미착수.** 현황은 [§4](#4-구현-현황-2026-10-01).

---

## 0. 결정 항목

| ID | 결정 | 선택지 | 권장 | 영향 |
|---|---|---|---|---|
| D-1 | 패키지·저장소 | (a) 새 저장소 `xgen-agent-runtime-rsi`, import `xgen_rsi` (b) 기존 런타임 하위 패키지 | (a) + GitHub PlateerLab (기존 런타임과 같은 공개 범위) | Phase 1 시작점 |
| D-2 | 교체 경계 | A(`AgentTurnExecutor.run` 아래 전부) / B / C | (당초) A + 기존 런타임에 엔진 디스패처 → **변경: A, 런타임 무변경 — 호스트가 진입점을 고른다**(아래 §3), 기본 기존 엔진) | workflow 코드 무변경 |
| D-3 | subagent kind | (a) 비활성(플랫폼 결정 "다시 들이지 않는다" 존중, 𝒦_enabled = 8종) (b) 같은 원장 회계를 전제로 제한된 subcall 허용 | (a)로 시작, 수식은 𝒦 9종 유지하고 `enabled` 로 계산 | ν·𝒰_t 계산, 헌법 |
| D-4 | 첫 lineage(정책) | I-1 은 Qwen3.8-27B(기존 측정 연속) + 운영 주력 모델 1종 | Qwen 먼저, 주력 모델은 I-2 구축 후 | 평가 비용·의미 |
| D-5 | 탐색 역할 모델 | proposer/critic/analyst/digester/policy-dev/judge 각각 XGEN 등록 LLM 중 선택 | proposer·critic·policy-dev = 최상위 모델, digester = 중간, judge = 정책과 다른 계열 고정 | 예산의 지배 항목 |
| D-6 | 스트림 오류 청크 `"\n[ERROR] "` | 재현 / 고침 | 재현(기본) + 플래그로 고침 | 사용자 화면·HTTP 상태 |
| D-7 | 외부 usage 정의 | 기존(메인 루프 호출) 유지 / 모든 정책 호출 포함 | **기존 유지**, 정확한 c(τ) 는 내부 원장·trace 에만 | 과금·쿼터 의미 |
| D-8 | Harness-Bench 실험실 | `harness-bench/lab` 실행기·채점기·과제 데이터 위치와 라이선스 | 위치 확인 후 I-1 로 이식 | Phase 4 선행 |
| D-9 | 평가 세트 확장 | I-2(업무 workspace) ≥ 100+30 과제 구축 자원·데이터 정책(합성/허가만) | 승인 | δ 크기(34 문서 §3) |
| D-10 | 운영 기록을 Dream 세계로 | 옵트인·구조/점수만 저장 허용 여부 | 허용(내용 비보존) | 세계 풀 크기 |

---

## 1. 단계 개요

```
Phase 0  조사·계획                                   ✅ 완료
Phase 1  수식 라이브러리 rsi_math + 차분 테스트          ✅ 완료
Phase 2  커널 + 하네스 형식 + Gateway/원장 + Recorder 골격 ✅ 완료
Phase 3  H0 이식 + 자체 진입점(GenyRSITurnExecutor) + 동등성 증명   ✅ 완료(런타임 무변경)
Phase 4  평가 인프라(EvalHost·검증기·xgen-core) + δ 보정 + H0 기준선   ◐ 코드 완료, 실제 LLM 기준선 미측정
Phase 5  L1 RRSI 엔진 + 파일럿(T=5) → 본 실행(T=20)       ◐ 엔진 완료(각본 E2E), 파일럿 미실행
Phase 6  탐색 계층(π_E·LiveQuestion)                    ◐ 평가 시점 라이브 탐색 완료, 운영 턴 안 탐색·샌드박스 분기 미착수
Phase 7  L2 Dream 엔진(세계·재생·평가자·개발 에이전트·사이클) ✅ 코드 완료, 실제 세계 풀 사이클 미실행
Phase 8  융합 운영: L1/L2 교대, 승격 파이프라인, lineage, canary  ☐
Phase 9  (실험) L1 제어기 Dream 화, 인스턴스·lineage 확장       ☐
```

의존: 1 → 2 → 3 → 4 → 5 → (6 → 7) → 8 → 9. Phase 6 의 샌드박스 분기 PoC 는 Phase 4 와 병행 가능.

---

## Phase 1 — 수식 라이브러리 `rsi_math` (규모: 중)

**목표**: 05 문서의 모든 수식을 순수 함수로, 공식 RRSI 구현과 비트 단위로 같은 결과(paper 모드)를 내도록 구현.

산출물
- 저장소 골격(pyproject, 정확 고정 의존성, CI: ruff·mypy·pytest).
- `xgen_rsi/rsi_math/{types,rrsi,calibrate,bounds,dream,units,modes}.py` (33 문서 §1–5 시그니처).
- 테스트: 검산 벡터(05 §8 전부), 차분 테스트(공식 rrsi 커밋 `be50316` 고정, hypothesis), 성질 테스트(33 §6.3), 정확 경계 정리 테스트(33 §4).

종료 기준
- 05 §8 벡터 100% 통과. 차분 테스트 각 대상 1,000 예제 이상 불일치 0.
- `rsi_math` 라인 커버리지 100%, mypy strict 통과.
- `paper`/`xgen` 모드 차이가 33 §7 표 항목에만 존재함을 테스트로 고정.

검증: `pytest tests/rsi_math -q`, `pytest tests/differential -q`.

---

## Phase 2 — 커널·하네스 형식 골격 (규모: 대)

**목표**: 31 문서의 커널(L0)과 하네스 형식을 세운다. 아직 운영 동작은 없다.

산출물
- `kernel/`: gateway(llm_client 감싸기, purpose 태그), ledger(CallRecord, c(τ), 외부 usage 생성기), limits, tools(기존 도구 ABI 실행), stream(청크 어댑터), recorder(TrajectoryRecord/ReplayNode, 기본 저장), loader, measure, errors.
- `harness/`: spec(manifest 스키마·version_id·lineage), kinds(9종 Protocol), engine(TurnEngine, Action 실행), overlay.
- 구성요소 격리: 구성요소 예외 → 비활성 + 기본 동작 + 이벤트.
- 원장 정합성 테스트: 공급자별 usage 원형(anthropic·bedrock·openai·google·vllm·claude_code·codex) → CallRecord 변환, reasoning·cache·CLI num_turns 포함.

종료 기준
- 가짜 클라이언트로 최소 하네스(프롬프트 1블록 + 표준 루프)가 한 턴을 돌고, 원장 합 = 응답 usage 합.
- Gateway 우회 정책 호출이 불가능함을 테스트로 증명(구성요소에 BaseClient 미노출).
- manifest 의 kind 선언 기반 태그 판정이 정확(편집 주소 → ℓ).

---

## Phase 3 — H0 이식 + 자체 진입점 (규모: 대) — **교체 가능성 증명**

**목표**: 지금 운영 동작을 구성요소(H0)로 옮기고, 기존 I/O 계약을 의미 동등으로 만족함을 증명한다.

산출물
- `harnesses/h0/`: 13 문서 인벤토리의 운영 경로 장치 전부(프롬프트 블록, 도구 노출·문·ToolSearch, 컨텍스트 6장치, 루프 가드·DeliverableReviewer·RepeatStop·TurnInputBudget, 구조화 출력, 메모리 주입·도구·증류).
- `kernel/executor.py` + `assembly.py`: HostServices 26단계 순서, kwargs 역방향 키.
- 자체 진입점 `GenyRSITurnExecutor`(기존 `AgentTurnExecutor` 와 같은 계약). **기존 런타임은 고치지 않는다** — RSI 코드는 이 저장소에만 둔다.
- 동등성 테스트 묶음 C-1, C-2, G-1~3, U-1, X-1, P-1 (32 문서 §5), 시나리오 원천 = 기존 테스트 자산.
- 특이 동작 결정(32 문서 §4, D-6) 반영.

종료 기준
- C/G/U/X/P 전부 통과(정규화 후 바이트 동일, usage 는 허용 목록 외 동일).
- 실제 모델로 결정적 재생 동등성(같은 응답 → 같은 요청) 확인. 운영 반영은 승인된 MR/PR 머지 후 CI 로만 한다.
- 기존 런타임 무변경 확인.

---

## Phase 4 — 평가 인프라 + δ 보정 + H0 기준선 (규모: 대)

**선행**: D-8(실험실·과제 데이터), D-4, D-5(judge).

산출물
- `evolve/domain.py`, `evolve/evaluate.py`: EvalHost(격리 컨테이너, 고정 데이터), Verifier 인터페이스, 태스크 사양, 재개 안전 러너, 누락 처리, 정확 경계 조기 종료(옵션).
- I-1 Harness-Bench 도메인(설계/홀드아웃 분할 고정, 검증기 이식).
- 운영 가드 세트 G 초판(기존 라벨링 사례 재연).
- `rsi calibrate`: H0 R ≥ 3 회 평가 → δ, 분산 분해 리포트 → k·|D| 결정.
- 비용 파일럿: 시행당 토큰, 탐색 역할 1라운드 비용 실측.
- 기준선 보고서: Ŝ(H0_rsi) vs Ŝ(pipeline21) on evolve·held-out — **δ 안이면 "교체 자체 회귀 없음"**.

종료 기준
- δ 실측값과 근거 리포트. Ŝ 동등(δ 안) 확인. 라운드 비용 추정이 실측으로 대체됨.
- 34 문서 §3 의 δ 개략 추정과 비교해 evolve 집합 확대(D-9) 필요 여부 결론.

---

## Phase 5 — L1 RRSI 엔진 (규모: 대)

산출물
- `evolve/{history,frontier,propose,critic,analyst,digester,round,driver,gitops}.py` (31 문서 §6.1), 하네스 저장소 worktree 운영.
- XGEN 헌법(SKILL.md)·PATTERNS.md·briefs(I-1), XGEN critic 규칙 7–10(35 문서 §2), precheck 패턴.
- π_E 경로 읽기 전용(P5), 예약 슬롯·수리 라운드·smoke·무효 게이트·attribution.
- `rsi round|run|readjudicate|reevaluate|heldout|status` CLI.
- 파일럿 T=5 → 검토 → 본 실행 T=20.

종료 기준
- 본 실행의 모든 라운드 산출물(directives·proposal·critic·diff·decisions·history·frontier) 완비.
- 임의 라운드를 `readjudicate` 로 재판정 시 같은 결과(재현성).
- held-out/OOD 보고서(판정 미사용). 결과가 H0 대비 어떻든 **수식대로 동작했음**이 종료 기준(성능 목표는 Phase 8 판단).

---

## Phase 6 — 턴 안 탐색 계층 (규모: 중–대)

**선행**: 샌드박스 스냅샷 분기 PoC(R-5).

산출물
- `explore/api.py`(B.2 API: Observation·CellMeta·Question·GridPlan·GridPlanningContext·LLMDesignedMethod·SimResult), `signals.py`.
- `kernel/explore.py`: LiveQuestion, 워커 풀, 시도 = 부모 스냅샷 재개 하위 궤적(Gateway purpose="explore_attempt"), 결정적 검증기 연결, 성공 의미론, 메인 궤적 병합.
- `explore/policies/pi_1_parallel_refine.py`(논문 초기 정책: 병렬 독립 워크스페이스 + 워크스페이스 내 정련).
- control_flow 의 `Explore(task)` 트리거(검증 가능 하위 작업 판정), direction provider.
- recorder 격자 기록(세계 원천).

종료 기준
- 검증 가능 과제(I-3 형 소규모)에서 탐색이 동작하고 트리가 기록됨. 검증기 없는 턴은 퇴화 트리로 기존과 동일(동등성 테스트 재통과).

---

## Phase 7 — L2 Dream 엔진 (규모: 대)

산출물
- `dream/world.py`(트리→불변 World, 풀·메타·개발/선택 분할), `replay.py`(ReplayQuestion: 정보 은닉·결정성·Child 규칙·종료 3조건·지원 밖), `evaluator.py`(Eq.1, pareto.reward v1, parallel_penalty, anytime AUC, β 스윕), `develop.py`(B.2 규칙을 우리 말로 재서술한 프롬프트, 정적 검사), `cycle.py`(M 버전, argmax V, 동점 π^0, 온라인 확인 = RRSI 바닥+비용, β 기본값 규칙), `sandbox.py`.
- `policy_execution_traces.jsonl`, `beta_sweep.json`, `live_cycle_manifest.json` 형식(B.2 이름 그대로).

종료 기준
- 재생 결정성·정보 은닉 테스트 통과(정책이 미공개 점수에 접근 불가 증명).
- 장난감 세계(05 §8.8) 수치 재현, V^{m★} ≥ V^0 불변식.
- 실제 세계 풀(Phase 5 시행 기록 + Phase 6 격자)에서 한 사이클 완주, 온라인 확인까지.

---

## Phase 8 — 융합 운영 (규모: 중)

산출물
- 교대 스케줄러(L1 실행 ↔ L2 사이클, 소유권 잠금).
- `promote/`: 승격 보고서 생성, MR/PR 생성(머지는 규범대로), lineage 레지스트리, canary 범위 설정(설정 사다리), 즉시 롤백.
- 대시보드: lineage 별 Ŝ/Ĉ 추세, 일반화 간극, 원장 비용(정책/탐색 역할 분리), 가드 위반률.

종료 기준
- 첫 진화 H\* 가 사람 승인 후 dev canary 에 lineage 로 배포되고, 롤백이 설정 한 줄로 동작.
- 엔진 기본값 전환(pipeline21 → rsi)은 **별도 사람 결정**.

---

## Phase 9 — 실험·확장 (규모: 지속)

- L1 제어기 Dream 화: 평가 할당 정책(통계적 조기 종료, m_t 조절)을 L1 기록 재생으로 학습 — RRSI 판정 의미가 바뀌므로 `paper`/`xgen` 와 별도 모드 `xgen-adaptive` 로 분리하고 효과·위험 보고.
- I-2(업무 workspace)·I-3(샌드박스 코딩) 구축(D-9), 운영 주력 모델 lineage, 교차 lineage 이전 측정(RRSI Table 4 재현).
- 21-stage 코드 정리 여부(장기 결정).

---

## 2. 단계별 검증 원칙 (공통)

1. **검증한 것만 주장**: 각 단계 보고는 실행한 테스트·측정 결과와 미검증 항목을 구분한다.
2. 수식 변경은 05 문서 개정 → 33 문서 → 테스트 순서로만.
3. 외부 계약에 보이는 변경은 32 문서 결정 표를 거친다.
4. 변경은 사람 승인(MR/PR) 후 CI 로만 운영에 반영한다.
5. 진화 결과는 held-out 을 판정에 쓰지 않는다(보고 전용).

---

## 3. 결정 결과

| ID | 결과 |
|---|---|
| D-1 | (a) 채택 — 새 저장소 `xgen-agent-runtime-rsi`, import `xgen_rsi`. 원격 공개는 별도 결정 |
| D-2 | A, **두 패키지 완전 독립** — geny-rsi 는 자체 진입점 `GenyRSITurnExecutor().run(host, **kwargs)`(같은 계약)와 턴 조립(`xgen_rsi.assembly`)을 갖고, 호스트가 진입점을 고른다. 0.3.0 부터 xgen-agent-runtime 을 import·의존하지 않고 4.80.0 사본 `xgen_rsi.base` 를 쓴다. 한때 런타임에 넣었던 엔진 선택점(4.76.0·4.79.0)은 4.80.0 에서 걷어 냈다 — RSI 코드는 이 저장소에만 둔다 |
| D-3 | (a) — subagent 비활성(𝒦_enabled 8종), 수식은 9종 유지 |
| D-6 | 재현 — `"\n[ERROR] "` 청크를 기존과 같게 |
| D-7 | 기존 유지 — 외부 usage = main + explore_attempt, 정확한 c(τ) 는 원장·기록에만 |
| D-8 | **Harness-Bench 제외** — 자체 스위트 `xgen-core`(8범주 × 4, 오라클 만점·빈 답 ≤ 0.5 를 테스트로 고정)로 대체 |
| D-4·D-5·D-9·D-10 | 미정 — 실제 측정에 쓸 등록 LLM(정책·역할), 평가 세트 확장, 운영 기록의 세계화 |

---

## 4. 구현 현황 (2026-10-02, 0.4.0)

검증한 것(테스트 469개, xgen-agent-runtime 이 설치되지 않은 환경):

- 독립 패키지: 바탕 런타임은 xgen-agent-runtime 4.80.0 사본 `xgen_rsi.base`(519 파일 중 517 바이트 동일, `COPY.json`·`tools/sync_base.py`·테스트로 검사).
- `rsi_math`: 두 논문 수식 전부 + 정확 경계 조기 종료. 공식 RRSI 구현과의 차분 테스트, mypy strict.
- 커널·H0: 기존 엔진과 동등성 9 시나리오 + 실제 응답 재생에서 요청 바이트 동일(1차).
- 스위트: `xgen-core`·`xgen-hard`·`xgen-pro`(천장 아래 모델용, 3회 난이도 보정).
- L1 RRSI: 실제 모델로 진화 — 1차 gpt-6-sol(T=6), 2차 gpt-6-luna(T=5, 역할 gpt-6-sol)·claude-haiku-4-5(2/5, 역할 claude-sonnet-5).
  가드: 측정되지 않은 편집, 환경 단정(검토자·헌법 규칙 11), 분석기 보고 강제.
- L2 Dream: 실제 모델 사이클 — gpt-6-sol 승격(1차), luna 유지·거절(2차).
- XGEN: Agent Geny / Agent Geny RSI 두 노드, 에이전트 단위 패키지 선택(대화·화면 API·예약 작업, workflow !2049·!2050).

검증하지 않은 것 / 남은 것:

- **보류 분할에서 확인된 점수·비용 개선** — luna H\* 는 evolve +0.029 였지만 보류 분할에서 잡음 안이라 패키지에 넣지 않았다.
- haiku 진화 3~5 라운드·보류 분할·Dream(API 크레딧 소진으로 중단).
- 평가 호스트에 코드 실행 도구가 없다(운영과 다름) — 샌드박스 실행을 평가에 넣는 것이 다음 과제.
- 운영 턴 안 탐색과 Phase 8 승격 파이프라인.
