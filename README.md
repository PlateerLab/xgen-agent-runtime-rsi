# geny-rsi — Geny + RRSI + Dream-RSI

[![GitHub release](https://img.shields.io/github/v/release/PlateerLab/xgen-agent-runtime-rsi)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml)

> [!IMPORTANT]
> **geny-rsi 는 아래 두 논문을 바탕으로 만들었다.** 하네스를 측정으로 고치는 규칙은 **RRSI** 에서, 실행 기록을 재생 시뮬레이터로 써서 대안을
> 새 실행 없이 평가하는 방식은 **Dream-RSI** 에서 가져왔다. 두 논문의 수식과 절차를 코드로 옮겼고([설계 33](docs/design/33-formula-to-code.md)),
> RRSI 공식 구현과는 차분 테스트로 같은 결과를 낸다. 이 저장소의 방법론은 두 논문 저자들의 연구다.
>
> **[1] RRSI: Regularized Recursive Self-Improvement of Agent Harnesses** — arXiv:2609.24972 · [PDF](https://arxiv.org/pdf/2609.24972)
> - 저자: Peng Xia, Rujun Han, Zifeng Wang, Yanfei Chen, Yufan Zhuang, Yoonho Lee, Chengsong Huang, Han Yu, Zhongying CuiZhu, Yifei Ming, Huaxiu Yao, Burak Gokturk, Tomas Pfister, Chen-Yu Lee
> - 소속: Google Cloud AI Research · UNC-Chapel Hill · Stanford University · Washington University in St. Louis
> - 코드: [google-research/rrsi](https://github.com/google-research/rrsi) (Apache-2.0) · [regularized-rsi.com](https://regularized-rsi.com)
> - 가져온 것: 하네스 = 편집 가능한 구성요소, 제안 쪽 정규화(담금질 편집 예산 · 전체 이력 신용 할당 · 정체 시 미시도 구성요소 탐색), 선택 쪽 정규화(누설 심사 · 잡음 보정 바닥 · 비용 규칙 · 띠 안 규칙 · 구조 가지치기 · 도메인 가드).
>
> **[2] Dream-RSI: Recursive Self-Improvement through Evolving Worlds** — arXiv:2609.14858v1 · [PDF](https://arxiv.org/pdf/2609.14858v1)
> - 저자: Tong Zheng, Xidong Wu, Zheng Zhang, Zhankui He, Chaoyi Zhang, Benjamin Coleman, Ruoqiao Wei, Di Bai, Haolin Liu, Rui Liu, Xue Wang, Yue Zhuan, Wang-Cheng Kang, Renkai Xiang, Heng Huang, Xinwu Cheng, Yunsong Guo
> - 소속: Google · University of Maryland, College Park · Google DeepMind · University of Virginia
> - 코드: [zhengkid/Dream-RSI](https://github.com/zhengkid/Dream-RSI) · [dream-rsi.com](https://dream-rsi.com)
> - 가져온 것: 완료된 실행 기록 = 재생 시뮬레이터(world), 기록 위에서 대안을 결정적으로 평가, 현재 정책을 포함한 선택, 실행 중 정책 고정, 재배치 → 새 기록 → 시뮬레이터가 커지는 순환. geny-rsi 는 이를 검증기 있는 탐색 정책과, 에이전트 턴마다의 하네스 정리 둘 다에 쓴다.
>
> 인용은 [참고문헌](#참고문헌)의 BibTeX 를 쓴다.

**geny-rsi** 는 XGEN 의 두 번째 에이전트 런타임이다. XGEN 에서는 **Agent Geny RSI**(`agents/geny-rsi`)로 쓴다.

- **Geny 기본** — 기존 런타임 geny(xgen-agent-runtime)의 요소 계층을 그대로 복사해 가진다. 공급자·도구·기억·작업·앱·스토리지·자기 진화·
  호스트 계약이 Agent Geny 와 같다. xgen-agent-runtime 을 import 하지도 의존하지도 않는 독립 패키지다.
- **RRSI** — 한 턴을 실행하는 harness pipeline 을 고정 커널과 편집 가능한 하네스로 나누고, 하네스를 정규화된 측정으로 고친다. **모든 Agent Geny RSI
  는 RSI 파이프라인의 기본값(H0)에서 시작한다.**
- **Dream-RSI** — 완료된 실행 기록이 곧 재생 시뮬레이터다. 에이전트의 **턴 하나하나가 재생 세계**(모델이 본 입력 + 도구가 돌려준 실제 결과)로 남고,
  하네스 후보를 그 세계들 위에서 도구를 다시 실행하지 않고 잰다.
- **턴 정리** — 턴이 끝나는 즉시 [정리]가 돈다. 다음 사용자 메시지의 정정·불만, 별점·코멘트, 기대 답을 신호로 삼아, 고칠 것이 있으면 RRSI 라운드
  하나를 재생으로 돌리고, 채택된 하네스를 **바로 다음 턴**부터 쓴다([설계 41](docs/design/41-turn-consolidation.md)).

두 에이전트의 차이는 **harness pipeline 하나**다. RSI 의 강점은 사용자 fit 이다 — 같은 모델이라도 에이전트마다 하는 일·사용자·자료가 다르므로
맞는 하네스도 다르다. 그래서 패키지는 H0 만 싣고, 하네스는 XGEN 안에서 에이전트마다 그 사용자의 턴으로 고쳐진다.
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

## XGEN 안에서 Agent Geny RSI 가 도는 방식

한 문장으로: **턴은 지금 하네스로 돌고, 턴이 끝나면 그 턴이 재생 기록(세계)으로 남고, 곧바로 [정리]가 그 에이전트의 세계들을 다시 돌려
보며 하네스를 고칠지 정하고, 고친 하네스는 다음 턴이 시작할 때 읽힌다.** 버튼·스위치는 없다. 근거는 [설계 41](docs/design/41-turn-consolidation.md).

```
턴 N    ┌ 하네스 고르기  지금 버전을 DB 에서 읽는다(없으면 H0). 이 턴이 끝날 때까지 고정
        ├ 실행           커널 + 하네스. 모델이 본 입력과 도구가 돌려준 결과를 그대로 기록한다
        └ 턴 끝          궤적 요약 + 세계 저장 → 그 에이전트의 [정리]를 백그라운드로 시작(턴은 기다리지 않는다)
[정리]  ┌ 신호           같은 대화의 다음 사용자 메시지가 앞 답을 고치라는지 받아들였는지 읽는다 + 평가·기대 답
        ├ 판단           고칠 것을 말하는 새 신호가 없으면 기록만 하고 끝
        ├ 재생 평가      신호가 있는 세계를 지금 하네스로 다시 돌린다(도구는 기록된 결과, 모델만 새로)
        ├ 라운드         후보 하네스 2개 → 같은 세계로 재생 → RRSI 판정
        └ 채택           새 버전을 계보에 넣고 지금 버전으로 바꾼다
턴 N+1  하네스 고르기에서 새 버전을 읽는다
```

**시간 순서 예** — 정정은 그다음 턴 메시지로만 알 수 있으므로, 고친 하네스는 정정한 턴의 다음 턴부터 쓰인다.

| 턴 | 사용자 | 이 턴의 하네스 | 턴 뒤 [정리] |
|---|---|---|---|
| 1 | "세 요금제를 비교해 줘" | H0 | 신호 없음 → 기록만 |
| 2 | "아니, 표로 보여 주고 추천은 한 줄로" | H0 | 턴 2 메시지로 턴 1 답 판정 = 정정 → 라운드 → H1 채택 |
| 3 | (아무 요청) | **H1** | … |

### 무엇이 변하나

**변하는 것은 그 에이전트의 하네스 하나다.** 하네스 = `manifest.json` + 그 구성요소가 참조하는 파일(스킬 문서 등). 버전은 manifest 정규형과
참조 파일 내용의 `sha256` 이다(내용이 같으면 같은 버전).

| 바뀔 수 있는 것 | 예 |
|---|---|
| 구성요소의 파라미터 | `prompt.system.params.extra_blocks`(지시 블록 추가), `part_overrides`(기본 블록 교체), 압축 임계, 완료 점검·반복 정지, 도구 노출, 기억 정책(검색·기록 켜기) |
| 구성요소의 파일 | `skills/<이름>/SKILL.md` 절차 문서를 쓰고 구성요소에 등록 |
| 구성요소의 켜기·끄기·추가·제거 | 등록된 구현만(코드는 추가할 수 없다) |

| 바뀌지 않는 것 | 이유 |
|---|---|
| 커널 — 실행 순서, 도구 실행·권한·사용자 거부·샌드박스, 사용량 원장, 한도 | 하네스 밖. 측정·안전을 하네스가 건드릴 수 없게 |
| 잠긴 값 — `model`·`provider`·`credentials`·`max_iterations`, manifest 의 `locked`·`enabled_kinds`·`exploration_policy` | 제안이 고치면 후보가 거절된다 |
| 에이전트 설정 — 노드의 시스템 프롬프트·도구·모델 | 사용자가 정한 입력이다. 하네스는 그 위에서 일반적으로 돌아야 한다 |
| 기억 내용(메모리 vault), 대화 기록 | 사용자 데이터. 정리는 기억에 쓰지 않는다 |
| 판정 모델·판정 기준 | 하네스 밖. 하네스는 실행 중 기준을 보지 못한다 |
| 패키지의 H0 | 모든 에이전트의 출발점. 에이전트의 변화는 그 에이전트의 계보에만 쌓인다 |

### 언제 변하나

- **바뀌는 순간은 세 가지뿐이다:** 정리의 채택, 사용자의 되돌리기, 복제·고정본 만들기(원본의 계보와 지금 버전을 복사).
- **적용은 다음 턴이 시작할 때다.** 턴이 준비될 때 호스트 훅 `rsi_agent_harness()` 가 지금 버전을 읽는다. 채택이 일어난 파드는 즉시, 다른 파드는
  최대 15초(버전 캐시 수명) 안에 반영된다.
- **턴 안에서는 바뀌지 않는다.** 정리가 끝나기 전에 시작한 턴은 끝까지 이전 버전으로 돌고, 그다음 턴부터 새 버전이다. 같은 턴의 이어가기 조각도
  같은 하네스다.
- **대화(Interaction)는 바뀌지 않는다.** 앞 대화 기록은 그대로이고, 다음 턴의 시스템 쪽 구성만 새 하네스가 만든다.

### [정리] 한 번 — 정확한 순서

정리는 턴이 끝날 때마다 시작하지만, 비싼 단계는 조건이 맞을 때만 돈다. `status` 는 정리 기록(`geny_rsi_runs`)에 남는다.

| 단계 | 하는 일 | 여기서 끝나는 경우(status) |
|---|---|---|
| 0. 시작 조건 | Agent Geny RSI 이고 고정본이 아니어야 한다. 다른 파드가 이 에이전트를 정리 중이면(15분 안의 `running` 기록) 건너뛴다 | 기록 없음 |
| 1. 모델 | 정책 π = 그 에이전트의 모델, 역할(제안·검토·분석·판정) = 같은 모델과 XGEN 에 등록된 키 | Claude Code·Codex → `skipped` |
| 2. 세계·명시 신호 | 최근 세계 60개를 읽고, 각 턴의 평가·기대 답을 XGEN 에서 다시 읽어 저장된 신호와 다르면 갱신하고 **새 신호**로 표시 | 세계가 없으면 기록 없음 |
| 3. 암묵 신호 | 앞 답에 대한 판정이 아직 없는 (앞 턴, 다음 턴) 쌍을 방금 끝난 턴부터 최대 4쌍 읽는다. 판정 모델이 다음 메시지를 `correction`·`complaint`·`accept`·`neutral` 로 분류하고, 정정·불만이면 "더 나은 답이 지킬 것" 한 문장을 쓴다. 한 번 읽은 쌍은 다시 읽지 않는다 | — |
| 4. 판정 기준 | 신호 → 기준(아래 표). 기준이 있는 재생 가능 세계 = 채점 대상 | 채점 대상 없음 → `recorded` |
| 5. 라운드를 열까 | 채점 대상 중 **새 신호이면서 고칠 것을 말하는**(정정·불만, 별점 2 이하, 이슈, 기대 답) 세계가 있어야 한다 | 없음 → `recorded` |
| 6. 평가 세계 W | 새 신호 → 고칠 것을 말하는 신호 → 지키라는 신호 순, 같은 순위 안에서는 최근 순, 최대 8개 | — |
| 7. 지금 하네스 평가 | W 의 세계마다 시행 2번. 지금 버전으로 같은 모델이 실제로 한 턴은 그 자체가 시행 1번이고, 캐시된 재생이 있으면 쓰고, 모자라면 재생한다 | 재생 실패가 15% 넘음 → `failed` / 모든 기준 통과 → `kept` |
| 8. 잡음 띠·지시 | δ = max(2·√2 × 시행 부트스트랩 표준오차, 판정 한 칸 = 1 / 기준 수 합), S★ = 지금 하네스의 S. 편집 예산 b_t, 정체 σ_t, 아직 안 건드린 구성요소, 가지치기 대상 | — |
| 9. 분석 | 실패한 세계의 가장 나쁜 시행 6개와 통과한 세계 3개를 보고 실패 양상·성공 습관을 정리(1회 호출) | — |
| 10. 후보 2개 | 지금 하네스를 후보마다 복사한 폴더에서 병렬로(git 을 쓰지 않는다): 제안(편집 예산 안) → 누설 검토(대화 내용·대화 요약 주입·환경 단정·안전장치 해제 거절, 수리 1회) → 바뀐 주소로 편집 태그 → 적재 확인 | 각 후보의 `no_proposal`·`critic_reject`·`smoke_fail` |
| 11. 후보 평가 | 같은 W 를 후보로 재생(세계 × 2회, 후보끼리도 병렬). 바닥에 못 미침이 확정되면 남은 재생을 멈춘다 | 재생 실패 15% 초과 → `eval_invalid` |
| 12. 판정 | RRSI 규칙 그대로(아래) | 허용 후보 없음 → `kept` |
| 13. 기록·채택 | 후보마다 편집 기록을 이력에 더하고 라운드 번호를 올린다. 승자가 있으면 페이로드로 만들어 계보에 넣고 지금 버전으로 바꾼다. 단, 정리하는 동안 사용자가 하네스를 바꿨으면 채택하지 않는다 | `adopted` 또는 `kept` |

**신호 → 판정 기준**(판정 모델이 재생 답 하나에 기준마다 통과·실패를 낸다)

| 신호 | 기준 |
|---|---|
| 기대 답(품질평가) | 기대 답과 사실·숫자·결론이 같다 |
| 별점 2 이하 또는 이슈·코멘트 | 사용자가 보고한 문제(이슈 + 코멘트)가 없다 |
| 별점 4 이상 | 그때 받아들여진 답의 요점을 빠짐없이 담는다(회귀 방지) |
| 다음 메시지가 정정·불만 | 판정 모델이 쓴 "더 나은 답이 지킬 것" |
| 다음 메시지가 수용 | 그 답의 요점을 빠짐없이 담는다(회귀 방지) |
| 사용자가 쓴 기준(호스트가 넘길 때 — XGEN 은 아직 넘기지 않는다) | 그대로 |

**판정 규칙.** 시행 점수 r = 통과한 기준 수 / 기준 수, S = 모든 시행의 통과 수 합 / 기준 수 합, C = 시행당 평균 정책 토큰.
후보 H′ 는 다음을 모두 만족해야 허용된다.

- 바닥: S′ ≥ S★ − δ
- ΔS > δ 이면 비용 규칙: 상대 토큰 증가 ΔC ≤ 0.10 + 35.4·ΔS
- ΔS ≤ δ 이면 띠 안 규칙: 100·ΔS − 15·ΔC + 0.5·ν > 0. ν 는 처음 채택되는 구조 구성요소(skill·memory·client_tool) 수다.
- 가드:
  - 고친 파라미터가 재생 중에 한 번이라도 읽혀야 한다.
  - 유효 답 비율이 0.15 넘게 떨어지거나, 빈 답 비율이 0.15 넘게 오르면 안 된다.

허용 후보 중 S 가 가장 높은 것이 이긴다. 같으면 토큰이 적은 쪽, 그다음 편집 수가 적은 쪽이다. 허용 후보가 없으면 지금 하네스를 유지한다.

### 재귀 — 다음 정리로 무엇이 이어지나

H_{t+1} = 정리(H_t, 세계 풀, 편집 이력, 신호). 채택된 H_{t+1} 이 다음 턴들을 만들고, 그 턴들이 다시 세계가 되어 다음 정리의 평가 재료가 된다
(Dream-RSI: 재배치 → 새 기록 → 시뮬레이터가 커진다). 다음 정리로 이어지는 것은 아래 다섯 가지다.

| 이어지는 것 | 저장 | 다음 정리에서의 쓰임 |
|---|---|---|
| 지금 하네스와 계보 | `geny_rsi_agents.current_version`, `geny_rsi_harnesses`(버전마다 페이로드·부모·채택한 정리·요약) | 다음 정리의 출발 하네스. 되돌리기 대상 |
| 세계 풀 | `geny_rsi_worlds`(에이전트당 최근 60개) | 평가 세계. 지금 버전으로 기록된 턴은 지금 하네스의 시행으로 바로 쓴다 |
| 편집 이력 𝓛 | `geny_rsi_agents.state.records` | 제안자가 읽는 증거(거절된 방법은 다시 내지 않는다), 이미 건드린 구성요소, 가지치기 대상 𝓑_t(최근 4라운드 동안 이득이 없던 구성요소), 처음 채택 여부(ν) |
| 라운드 번호 t·누적 이득 | `state.t`, `state.progress` | 편집 예산 b_t = 3 → 1(20라운드에 걸쳐 줄고 그 뒤 1), 정체 σ_t(지난 3라운드 채택 이득 합 ≤ δ 이면 아직 안 건드린 구성요소에 후보 자리 하나를 예약) |
| 재생·판정 캐시 | `state.cache`, `state.verdicts` | 같은 버전·같은 세계는 다시 재생하지 않는다. 채택된 버전의 재생 결과가 다음 정리에서 지금 하네스의 시행이 된다. 같은 (기준, 답)은 다시 판정하지 않는다 |

라운드 번호와 편집 이력은 라운드를 실제로 돌린 정리에서만 늘어난다. `recorded`·`kept`(라운드 전)·`failed` 정리는 신호·캐시만 남긴다.

### 저장 — 어디에 무엇이

**DB(core 모델, 에이전트 = workflow_id 하나)**

| 테이블 | 한 행 | 주요 칼럼 | 남기는 양 |
|---|---|---|---|
| `geny_rsi_agents` | 에이전트 | `current_version`(빈 값 = H0), `state`(정리 상태 JSON) | 1행 |
| `geny_rsi_harnesses` | 채택된 버전 | `version`, `parent_version`, `payload`(manifest + 파일), `run_id`, `summary` | 전부 |
| `geny_rsi_worlds` | 턴 하나 | `io_id`(execution_io), `interaction_id`, `seq`(대화 안 순서), `harness_version`, `model`, `world`(JSON), `signals`(JSON), `replayable` | 최근 60개 |
| `geny_rsi_runs` | 정리 하나 | `kind`(`consolidate`), `io_id`(정리를 시작한 턴), `status`, `start_version`, `adopted_version`, `result`(라운드 요약, 페이로드 없음), `error` | 최근 200개 |
| `geny_rsi_trajectories` | 턴 하나 | 하네스 버전·종료 사유·정책 토큰·호출 수(내용 없음) | 전부 |

**세계(`world` JSON)에 들어가는 것** — 재생에 필요한 것만.

- 모델이 본 입력: 시스템 조각(하네스가 짓기 전의 재료), 출력 스키마, 노드 손잡이(온도·토큰·생각 수준·반복 수·창 크기 등 비밀 아닌 것), 앞 대화,
  이번 입력, 조립 단계가 남긴 턴 상태 값
- 첫 반복의 기억 검색 결과
- 도구 목록(이름·설명·스키마·노출 상태)과 호출마다의 결과(호스트 결과 필터를 지난 것 = 모델이 본 것, 하나당 32,000자에서 자름)
- 그 턴의 답·상태·종료 사유·정책 토큰·전사

들어가지 않는 것: 키·자격증명·클라이언트 객체, 하네스가 지은 완성본(재생에서 후보가 다시 짓는다). 이미지는 자리 표시로 바뀐다. 2MB 를 넘으면
`replayable=false` 로 남고 평가에 쓰지 않는다.

**정리 상태(`state` JSON)**

| 필드 | 내용 | 상한 |
|---|---|---|
| `t` | 다음 라운드 번호 | — |
| `records` | 측정된 편집 기록(라운드·후보·구성요소·가설·ΔS·ΔC·결과·diff 일부) | 최근 200라운드 |
| `progress` | 라운드마다 채택된 ΔS 누적 | — |
| `analysis` | 앞 분석의 실패 양상·성공 습관 이름(이름을 안정시킨다) | 각 12개 |
| `scoreboard` | 편집이 예측한 세계가 실제로 움직였는지 | 최근 40개 |
| `cache` | 하네스 버전 → 세계 → 재생 결과(답·토큰·상태·읽힌 파라미터, 전사 없음) | 3버전(지금·채택 버전은 남김), 세계당 2개 |
| `verdicts` | (기준, 참조, 요청, 답) 해시 → 통과·이유 | 4,000개 |

**파드 메모리(재시작하면 사라지고, 사라져도 동작은 같다)**

- 지금 하네스 페이로드 캐시: 에이전트마다 15초. 채택·되돌리기·복제 때 그 파드에서 바로 지운다.
- 정리 실행 표: 에이전트 → {돌고 있음, 대기 표시, 마지막 턴}.
- 하네스 디렉터리: 버전별로 한 번만 풀어 둔다(`XGEN_RSI_HARNESS_CACHE`, 없으면 임시 디렉터리).
- 정리 한 번의 임시 디렉터리: 지금 하네스 사본, 후보마다의 사본, 이력 파일 사본. 정리가 끝나면 지운다. 재생도 한 번마다 임시 작업 디렉터리를 쓰고 지운다.

### 상태를 특수하게 다루는 절차

| 절차 | 왜 | 어떻게 |
|---|---|---|
| 에이전트당 정리 하나 | 같은 상태를 두 정리가 동시에 고치면 이력·번호가 갈린다 | 파드 안: 도는 동안 끝난 턴은 대기 표시만 하고, 끝나면 마지막 턴 기준으로 한 번 더 돈다(쌓인 턴 수만큼 돌지 않는다). 파드 사이: 15분 안의 `running` 기록이 있으면 건너뛴다 |
| 놓친 신호 다시 읽기 | 건너뛴 정리·다른 파드의 턴도 배워야 한다 | 암묵 신호는 정리마다 아직 읽지 않은 쌍을 최대 4개 읽는다. 명시 신호는 정리마다 XGEN 값과 저장값을 비교한다 |
| 나중에 남긴 평가·기대 답 | 화면에서 평가를 남기는 것만으로는 정리가 시작되지 않는다 | 그 에이전트의 다음 턴이 끝날 때 정리가 비교로 찾아 새 신호로 쓴다 |
| 끊긴 정리 | 파드가 정리 도중 재시작하면 `running` 이 남는다 | 15분 넘은 `running` 은 `interrupted` 로 닫는다. 나이는 DB 의 현재 시각과 비교한다(칼럼에 시간대가 없고, DB 와 서버의 시간대가 다를 수 있다) |
| 되돌리기와 채택의 경합 | 정리 도중 사용자가 되돌렸는데 정리가 덮으면 안 된다 | 채택 직전에 지금 버전이 정리 시작 때와 같은지 본다. 다르면 채택하지 않고 `kept` 로 남긴다(사유를 기록) |
| 상태 저장 | 다음 정리가 이어 쓴다 | 정리가 결과를 돌려주면(라운드를 돌렸든 아니든) `state` 전체를 덮어쓴다. 새로 읽은 신호는 그 세계 행에 쓴다. 정리가 예외로 끝나면(`failed` 기록) 상태는 그대로다 |
| 대화 안 순서 | 오래된 세계를 지워도 앞 턴을 정확히 찾아야 한다 | `seq` = 같은 대화의 최댓값 + 1 |
| 재생 기억 | 재생이 사용자 기억을 더럽히면 안 된다 | 재생 기억 자리는 기록된 검색 결과만 돌려주고 쓰지 않는다(실행 기록·증류도 없음). 재생 여부는 클래스 속성으로만 판정한다 |

### 재생의 정확한 규칙

- 턴 계획은 세계에서 다시 만든다. 모델·공급자·자격증명만 지금 정책(에이전트의 현재 모델)으로 바꾼다.
- 도구 호출은 기록과 맞춰 답한다. 순서는 다음과 같다.
  1. 같은 이름과 같은 인자(정규화 JSON)
  2. 공백·대소문자만 다른 인자
  3. 안내 도구(문)는 인자와 무관하게 기록된 글
  4. 그 밖은 "기록된 결과 없음"(Dream-RSI 의 Child = ∅)을 오류 결과로 돌려준다. 재생은 계속되고, 지원 밖 호출 수를 센다.
- 같은 호출이 여러 번 기록됐으면 차례대로 답하고, 다 쓰면 마지막 결과를 다시 준다.
- 레지스트리만 읽는 메타 도구(`ToolSearch`·`SelfExtendGuide`)는 재생 레지스트리에서 실제로 돈다. 하네스가 기여하는 도구(`ReadSkill` 등)는 재생하는 하네스가 다시 기여한다.
- 같은 하네스·같은 모델 응답이면 재생은 실제 턴과 같은 요청(시스템·메시지·도구)을 보낸다(테스트로 고정).
- 기록 밖 행동은 결과가 없으므로, 새 도구를 많이 쓰게 하는 변경은 재생에서 불리하다. 그런 방향은 채택된 뒤의 실제 턴이 다음 세계가 되어 검증된다.

### 정리하지 않는 경우

- Agent Geny(21-stage 런타임)는 이 훅을 부르지 않는다.
- 고정본 턴은 세계를 남기지만 정리하지 않는다. 고정본의 하네스는 만든 때의 버전으로 굳는다.
- Claude Code·Codex 에이전트는 커널이 루프를 갖지 않아 세계를 남기지 않는다. 정리는 `skipped` 로 남는다.
- 관리자가 `XGEN_RSI_HARNESS_DIR` 로 하네스를 고정하면 모든 턴이 그 하네스로 돈다(에이전트 하네스보다 먼저 본다). `XGEN_RSI_RECORD_WORLD=0` 이면
  세계를 남기지 않는다.

**실측(gpt-6-luna)**:
- 신호 없는 턴의 정리는 0.001초에 끝났다.
- 정정 턴의 정리는 턴이 끝나고 70.5초 만에 채택했다(XGEN 실제 경로 E2E). 새 대화의 바로 다음 턴이 채택된 하네스로 돌았다.
- 라이브러리 단독으로는 정정부터 채택까지 52.9초였다.

**구현 상태**

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 패키지를 H0 만으로, 두 런타임 동일성 결함 수정 | 0.6.0 |
| 2 | 에이전트 하네스·궤적 호스트 훅, 기준 판정 검사 | 0.7.0 |
| 3 | 턴 세계 기록(`XGEN_RSI_RECORD_WORLD`), 세계 재생, 다음 메시지 신호, 턴 정리 API(`xgen_rsi.consolidate`) | 0.8.0 · 0.8.1 |
| 4 | XGEN — 턴 끝 정리, 세계·정리 상태 저장, [하네스] 탭의 정리 기록 | core !916 · workflow !2064 · frontend !2772 머지 |


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

RRSI 는 라운드로 하네스를 고친다. 운영에서는 턴 정리가 라운드를 연다(고칠 것을 말하는 새 신호가 있을 때).

1. 지금 하네스가 틀리는 세계의 궤적을 분석기가 실패 유형으로 정리한다.
2. 제안자가 편집 예산 b_t 안에서 하네스 편집을 낸다(에이전트의 이어진 편집 이력·탐색 지시·가지치기 대상을 본다). 검토자가 누설(대화 내용)·
   환경 단정·커널 침범을 심사한다.
3. 같은 세계들을 후보 하네스로 재생해 잰다. 점수가 잡음 바닥 δ 를 넘어 오르면 비용 규칙, 띠 안이면 `100·ΔS − 15·ΔC + 0.5·ν > 0` 일 때만
   채택한다. 재생에서 한 번도 읽히지 않은 파라미터를 고친 편집은 가드가 거른다.
4. 채택은 그 에이전트의 하네스 계보로 남고(H0 → H1 → …), 판정 입력은 전부 기록된다. 바로 다음 턴부터 채택된 하네스로 돈다.

같은 수식으로 업무 스위트에서 오프라인 진화도 돌릴 수 있다(`rsi evolve`, 방법론 실험).

### 3. Dream-RSI — 기록이 곧 시뮬레이터다

Dream-RSI 의 핵심은 "완료된 실행 기록을 재생 시뮬레이터로 쓰면, 비싼 온라인 실행 없이 대안 정책을 즉시 평가할 수 있다"이다.

- **턴 정리에서(운영)** — 에이전트의 턴 하나가 온라인 실행 하나이고, 그 턴의 세계가 시뮬레이터에 더해진다(𝓗_t = 𝓗_{t−1} ∪ {𝒯_t}). 하네스 후보는
  그 세계들을 재생해 잰다(도구 결과는 기록에서, 기록 밖 행동은 ∅). 채택된 하네스는 다음 턴에 재배치되고, 그 턴이 다시 세계가 된다.
  기록을 방향 지침으로 프롬프트에 넣으면 탐색이 좁아진다는 §5.1 에 따라, 대화 요약·교훈을 하네스에 넣는 편집은 검토자가 거절한다.
- **검증기가 있는 탐색에서(오프라인)** — 과제를 여러 갈래로 풀어 볼 때(`rsi dream explore`: branch × attempt 격자) 탐색 정책 π_E 가 몇 갈래로
  나누고 언제 멈출지 정한다. 쌓인 탐색 트리를 재생해 π_E 후보를 Eq.1 `V = max s − β1·N + β2·N/max(1,k★)` 로 비교하고, 재생 승자는 실제로
  다시 탐색해 **RRSI 판정(바닥 + 비용 규칙)** 을 통과해야 승격한다. 운영 대화 턴은 분기 탐색을 열지 않는다(검증기가 없다).

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
pip install "xgen-agent-runtime-rsi @ https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases/download/v0.7.0/xgen_agent_runtime_rsi-0.7.0-py3-none-any.whl"
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

**2. RRSI** — *Regularized Recursive Self-Improvement of Agent Harnesses* ([arXiv:2609.24972](https://arxiv.org/pdf/2609.24972)).
편집 공간은 열어 두고 **탐색 궤적을 정규화**한다. 제안 쪽은 담금질 편집 예산(Eq.4)·전체 이력 신용 할당(Eq.10/11)·정체 시 미시도 구성요소
탐색(Eq.13), 선택 쪽은 누설 심사·잡음 보정 바닥(Eq.5)·이득 시 비용 규칙(Eq.7)·띠 안 규칙(Eq.17)·구조 가지치기(Eq.14)·도메인 가드.
공식 구현(google-research/rrsi, Apache-2.0)과 **차분 테스트로 같은 결과**를 낸다.

**3. Dream-RSI** — *Recursive Self-Improvement through Evolving Worlds* ([arXiv:2609.14858v1](https://arxiv.org/pdf/2609.14858v1)).
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
- [설계 41 — 턴 정리](docs/design/41-turn-consolidation.md) — 턴마다 Dream-RSI × RRSI: 두 논문의 갱신 시점, 세계·재생·신호, 다음 턴 적용
- [사용 가이드](docs/GUIDE.md) — 라이브러리 API, 서버 설정, 하네스 형식, 평가·진화·탐색 명령, 사본 갱신
- [설계 문서 지도](docs/README.md) — 논문 분석, 수식 정본, 기존 런타임 조사, 융합 원칙, 아키텍처, I/O 호환, 평가, 위험
- [계획](docs/PLAN.md) — 단계 계획, 결정 항목, 구현 현황

## 참고문헌

- **[1]** Peng Xia, Rujun Han, Zifeng Wang, Yanfei Chen, Yufan Zhuang, Yoonho Lee, Chengsong Huang, Han Yu, Zhongying CuiZhu, Yifei Ming, Huaxiu Yao, Burak Gokturk, Tomas Pfister, Chen-Yu Lee. *RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*. arXiv:2609.24972, 2026. [PDF](https://arxiv.org/pdf/2609.24972)
- **[2]** Tong Zheng, Xidong Wu, Zheng Zhang, Zhankui He, Chaoyi Zhang, Benjamin Coleman, Ruoqiao Wei, Di Bai, Haolin Liu, Rui Liu, Xue Wang, Yue Zhuan, Wang-Cheng Kang, Renkai Xiang, Heng Huang, Xinwu Cheng, Yunsong Guo. *Dream-RSI: Recursive Self-Improvement through Evolving Worlds*. arXiv:2609.14858v1, 2026. [PDF](https://arxiv.org/pdf/2609.14858v1)

```bibtex
@article{xia2026rrsi,
  title   = {RRSI: Regularized Recursive Self-Improvement of Agent Harnesses},
  author  = {Xia, Peng and Han, Rujun and Wang, Zifeng and Chen, Yanfei and Zhuang, Yufan and Lee, Yoonho and Huang, Chengsong and
             Yu, Han and CuiZhu, Zhongying and Ming, Yifei and Yao, Huaxiu and Gokturk, Burak and Pfister, Tomas and Lee, Chen-Yu},
  journal = {arXiv preprint arXiv:2609.24972},
  year    = {2026},
  url     = {https://arxiv.org/pdf/2609.24972}
}

@article{zheng2026dreamrsi,
  title   = {Dream-RSI: Recursive Self-Improvement through Evolving Worlds},
  author  = {Zheng, Tong and Wu, Xidong and Zhang, Zheng and He, Zhankui and Zhang, Chaoyi and Coleman, Benjamin and Wei, Ruoqiao and
             Bai, Di and Liu, Haolin and Liu, Rui and Wang, Xue and Zhuan, Yue and Kang, Wang-Cheng and Xiang, Renkai and Huang, Heng and
             Cheng, Xinwu and Guo, Yunsong},
  journal = {arXiv preprint arXiv:2609.14858},
  year    = {2026},
  url     = {https://arxiv.org/pdf/2609.14858v1}
}
```

- PlateerLab, *xgen-agent-runtime* — geny(21-stage 하네스), 다중 공급자 계층, 호스트 계약. 4.81.0 을 `xgen_rsi.base` 로 복사해 쓴다(Apache-2.0)

## 라이선스

Apache-2.0. RRSI 공식 구현(Apache-2.0)에서 옮기고 고친 부분과 xgen-agent-runtime 사본의 고지는 [NOTICE](NOTICE).
