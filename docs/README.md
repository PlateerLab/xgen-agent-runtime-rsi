# xgen-agent-runtime-rsi — 조사·설계 문서 모음

기존 21-stage 하네스(`xgen-agent-runtime` 4.75.0)를 **입력·출력 인터페이스만 유지한 채** 기존 방법론 + RRSI + Dream-RSI 를 융합한 새 하네스 프레임워크로 다시 짓기 위한 조사와 계획이다. 작성일 2026-10-01. **현재 단계: 조사·계획 완료, 구현 전.**

원문: RRSI [arXiv:2609.24972](https://arxiv.org/abs/2609.24972) (v2, 코드 `google-research/rrsi` Apache-2.0), Dream-RSI [arXiv:2609.14858](https://arxiv.org/abs/2609.14858) (v1, 코드 미공개). 논문 PDF 는 라이선스상 이 저장소에 포함하지 않는다. arXiv 에서 받는다.

---

## 핵심 결론 10가지

1. **RRSI 와 Dream-RSI 는 층위가 다르다.** RRSI = 하네스(객체 수준) 진화를 *정규화*, Dream-RSI = 탐색 정책(메타 수준)을 *기록 재생*으로 진화. 경쟁이 아니라 보완이다. → [20](research/20-synthesis-three-methodologies.md)
2. **우리는 이미 RRSI 를 손으로 하고 있었다.** CHANGELOG 4.27–4.75 의 Harness-Bench 설계/홀드아웃 판정, 반복 측정, "점수 비열등 + 비용 감소면 채택", "부탁보다 구조"가 RRSI 의 Analyze·분할·δ·Eq.17·헌법 4조의 정성판이다. → [15](research/15-existing-methodology.md)
3. **교체 경계는 `AgentTurnExecutor().run(host, **kwargs)` 하나.** 운영에서 `Pipeline` 을 직접 쓰는 곳이 0건이라, 그 아래 전부를 갈아끼울 수 있다. 계약 모듈·공급자 계층은 기존 패키지를 라이브러리로 import 하고, 기존 런타임은 고치지 않는다. geny-rsi 는 같은 계약의 자체 진입점(`GenyRSITurnExecutor`)을 갖고 호스트가 고른다. → [11](research/11-runtime-io-contract.md), [32](design/32-io-compat-adapter.md)
4. **다중 공급자 계층(`llm_client`, 15 provider, thinking 정규화)은 그대로 재사용 가능**하다(파이프라인 객체에 논리적 비의존). s06/s07/s08 스테이지 코드는 버린다. → [12](research/12-runtime-provider-layer.md)
5. **비용 c(τ) 를 지금 기록으로는 정확히 잴 수 없다.** 잘못된 모델로 가격 산정, $0 모델, CLI 비용 무시, 압축·증류·재시도 사용량 누락, reasoning 칸 없음. 새 커널은 모든 정책 호출을 지나는 단일 관문 원장으로 해결한다. → [12](research/12-runtime-provider-layer.md), [14](research/14-runtime-observability-records.md)
6. **새 구조 = 고정 커널 + 𝒦 9종 타입 구성요소 하네스 + 탐색 정책.** 루프 결정은 한 곳, 측정은 커널만, 하네스 버전은 내용 주소, H 와 π_E 는 블록 좌표 상승으로 번갈아 진화. → [30](design/30-fusion-philosophy.md), [31](design/31-architecture.md)
7. **수식은 전부 정본화했다.** RRSI Eq.1–17 + Algorithm 1·2, Dream Eq.1 + 재생 전이 + 평가자. PDF 수식 페이지는 이미지로 대조했고, 공식 코드를 직접 실행해 검산 벡터를 만들었다. 논문-코드 차이 18건, Dream 미정의 15건을 결정 표로 확정. → [05](research/05-formula-reference.md), [02](research/02-rrsi-reference-code.md), [04](research/04-dream-rsi-replay-policy.md)
8. **새 정리 하나: 정확 경계 조기 종료.** `Ŝ_max < min(S★−δ, Ŝ_t)` 이면 후보 평가를 멈춰도 RRSI 의 선택·𝒯_t·𝓑_t·N_t 가 전체 평가와 같다. 공식 코드로 4,000 무작위 시도 중 1,788 조기 종료에서 전부 일치 확인. → [33](design/33-formula-to-code.md) §4
9. **지금 평가 세트 규모로는 δ 가 크다**(설계 39과제·k=2 에서 개략 δ ≈ 0.1, 논문 0.017). 품질 개선을 받아들이려면 evolve 집합 확대·k 상향·criteria 채점이 필요하다. 실측 보정은 Phase 4. → [34](design/34-evaluation-verifiers.md) §3
10. **결정 항목 10가지**(패키지 위치, 교체 경계, subagent 정책, 첫 lineage, 탐색 역할 모델, 오류 청크 특이 동작, 외부 usage 정의, Harness-Bench, 평가 세트 확장, 운영 기록 사용)와 결과는 [PLAN.md](PLAN.md) §0·§3, 구현 현황은 §4.

---

## 문서 지도 (읽는 순서)

| # | 문서 | 내용 | 분량 |
|---|---|---|---|
| — | [PLAN.md](PLAN.md) | 결정 항목 + Phase 0–9 계획·종료 기준 | 196줄 |
| **논문·코드** | | | |
| 01 | [research/01-rrsi-paper.md](research/01-rrsi-paper.md) | RRSI 요약·분석: 문제·방법·Eq.1–17·Algorithm 1·2·Table 5·실험·사례 재검산 | 258줄 |
| 02 | [research/02-rrsi-reference-code.md](research/02-rrsi-reference-code.md) | 공식 구현 분석, 역할 프롬프트, 구조 기질, 헌법, **논문-코드 차이 D1–D18** | 317줄 |
| 03 | [research/03-dream-rsi-paper.md](research/03-dream-rsi-paper.md) | Dream-RSI 요약·분석: 발견 트리·온라인·재생·Child·Eq.1·선택·실험 | 187줄 |
| 04 | [research/04-dream-rsi-replay-policy.md](research/04-dream-rsi-replay-policy.md) | 정책 API·규칙·β 세 역할·plan_grid·두 목적함수 관계, **미정의 결정 E1–E15** | 241줄 |
| 05 | [research/05-formula-reference.md](research/05-formula-reference.md) | **수식 정본**: 기호표·정의역·경계·검산 벡터 | 428줄 |
| **기존 런타임** | | | |
| 10 | [research/10-runtime-pipeline-core.md](research/10-runtime-pipeline-core.md) | 21-stage 코어 실제 동작, 문서-코드 불일치 | 617줄 |
| 11 | [research/11-runtime-io-contract.md](research/11-runtime-io-contract.md) | **외부 I/O 계약**(49행 표 + 최소 인터페이스) | 926줄 |
| 12 | [research/12-runtime-provider-layer.md](research/12-runtime-provider-layer.md) | 공급자 계층 재사용성, 비용 회계 결함 | 793줄 |
| 13 | [research/13-runtime-harness-components.md](research/13-runtime-harness-components.md) | 𝒦 9종 기준 현재 하네스 인벤토리, 원자 편집 주소 | 752줄 |
| 14 | [research/14-runtime-observability-records.md](research/14-runtime-observability-records.md) | 관측·기록 현황, 필요 신호 격차, 레코드 스키마 초안 | 465줄 |
| 15 | [research/15-existing-methodology.md](research/15-existing-methodology.md) | **기존 방법론**(Harness-Bench 실록) ↔ RRSI·Dream 대응 | 117줄 |
| 20 | [research/20-synthesis-three-methodologies.md](research/20-synthesis-three-methodologies.md) | 세 방법론 종합: 층위·대응·자산/부채·충돌 조정 | 147줄 |
| **설계** | | | |
| 30 | [design/30-fusion-philosophy.md](design/30-fusion-philosophy.md) | 융합 원칙 P1–P12, 커널/하네스 경계, 세 루프, 플랫폼 제약 | 194줄 |
| 31 | [design/31-architecture.md](design/31-architecture.md) | 패키지 구조, 커널, 𝒦 인터페이스, 탐색 계층, 진화 엔진, H0 | 326줄 |
| 32 | [design/32-io-compat-adapter.md](design/32-io-compat-adapter.md) | 계약 항목별 충족 방법, 자체 진입점(런타임 무변경), 특이 동작 결정, 동등성 테스트 | 111줄 |
| 33 | [design/33-formula-to-code.md](design/33-formula-to-code.md) | 수식→함수 시그니처, 정확 경계 정리, 차분·성질 테스트 | 170줄 |
| 34 | [design/34-evaluation-verifiers.md](design/34-evaluation-verifiers.md) | 측정 계약, 검증기·judge, 평가 인스턴스, δ 추정, 비용 | 159줄 |
| 35 | [design/35-risks-safety-governance.md](design/35-risks-safety-governance.md) | 위험 16종과 방어, XGEN critic 규칙, 거버넌스, 중단 스위치 | 77줄 |
| — | [research/verification/](research/verification/) | 검산·정리 검증 스크립트(공식 코드로 실행) | 스크립트 2개 |

---

## 검증 상태

| 항목 | 상태 |
|---|---|
| 논문 수식 페이지 육안 대조(RRSI p.3–6·19–24, Dream p.4–6·11) | 완료 |
| 공식 RRSI 코드 전수 읽기(`rrsi/*.py`, 도메인 설정·헌법·기질·테스트) | 완료 |
| 검산 벡터(예산표, Table 6 사례, Dream 장난감 세계) 공식 코드 실행 | 완료 |
| 정확 경계 조기 종료 정리 무작위 검증 | 완료(1,788/1,788 일치) |
| 기존 런타임 5개 영역 코드 조사(파일:줄 근거) | 완료 — 일부 항목은 각 문서에 "미확인" 표시 |
| Dream-RSI 코드 | **미공개**(논문·프롬프트만 근거) |
| Harness-Bench 실험실 코드 | **사용하지 않음**(D-8 결정) — 자체 스위트 `xgen-core` 로 대체 |
| δ 실측 | 미실시(Phase 4). 34 문서의 δ 는 기록 2쌍 기반 개략 추정 |
