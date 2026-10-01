# 04. Dream-RSI 재생 정책·평가자·프롬프트 상세와 미정의 항목의 결정

> 근거: Dream-RSI 논문 Sec 3(형식), Appendix B.1(탐색 프롬프트), B.2(재생 기반 정책 개선 프롬프트). 코드 미공개이므로 B.2 프롬프트가 드러내는 **실제 시스템의 API·평가자 형태**가 구현 사양의 사실상 유일한 단서다.
> 이 문서의 끝(6절)에 "논문이 정하지 않아 우리가 정한 것"을 결정 표로 모았다. 결정에는 모두 근거와 "논문 재현 모드"에서의 대체값을 적었다.
> 2–4절은 Appendix B 의 프롬프트를 옮긴 것이 아니라, 거기서 읽어 낸 인터페이스와 규칙을 우리 말로 요약한 구현 체크리스트다. 원문 인용은 따옴표로 표시했다.

---

## 1. 형식(Sec 3) ↔ 실제 시스템(Appendix B) 대응

| 개념 | 논문 형식 (Sec 3) | 실제 시스템 (Appendix B.2) |
|---|---|---|
| 세계 | 발견 트리 𝒯_i (루트 r, 1차 부모, 리프만 확장) | "frozen, irregular **branch × attempt grid**" |
| 노드 | v: 스냅샷·산출물·진단·점수 s_v | cell `(branch, attempt)`, `CellMeta(.branch .attempt .parent_id .seq .tags)` |
| 루트 확장 | r 선택 → `Child(r)` = 가장 먼저 생성된 미공개 자식(가지 열기) | `legal_roots()` 중 **어느 미개봉 root 든** 선택 가능 ("branch id 가 작다는 이유만으로 root 를 고르지 마라") |
| 가지 확장 | 리프 v 선택 → 유일한 기록 자식 | 열린 가지의 다음 attempt(frontier) |
| 적격 집합 | A(𝒯) = {r} ∪ leaves | `legal_actions()` = 미개봉 roots + 열린 가지 frontier |
| 배치 | C ∈ A(𝒯;W) = {C ⊆ A(𝒯): |C| ≤ W} (r 은 집합 원소라 한 번) | "several roots and/or one frontier from each opened branch", 최대 `max_parallelism`, 중복 금지, 부모·자식 동시 금지 |
| 관측 | 드러난 노드의 저장 관측 | `observed() -> dict[str, Observation]` (드러난 prefix 만) |
| 비용 | N = 드러난 비루트 노드 수 | "Each revealed cell costs one **probe**", total_probes |
| 라운드 | 비어 있지 않은 배치 1개 = 1 라운드, k★ | decision round, `effective_sequential_rounds` |
| 목적함수 | V = max s − β1 N + β2 N / max(1,k★) (Eq.1) | `pareto.reward = pareto.auc − λ · parallel_penalty` (β 스윕) |
| 정지 | 빈 배치 / K2 / 전부 공개 | 배치 미선택 시 종료, 재생은 `budget=None` |
| 격자 크기 | (암묵) | `plan_grid(context) -> GridPlan(branch_count, refine_count, reason)` |

**조정(우리 구현 규칙)**: 형식의 "r 을 선택하면 가장 먼저 생성된 미공개 자식"은 격자 API 의 특수 경우다. 우리는 두 모드를 둔다.

- `root_policy = "earliest"` (**논문 형식 재현 모드**): 배치에 r 을 j 번 넣으면(다중집합 허용) 미공개 가지 중 생성 순서 `seq` 가 가장 이른 j 개를 연다. j = 1 로 제한하면 Sec 3 과 문자 그대로 같다.
- `root_policy = "choose"` (**기본, Appendix B 모드**): 정책이 `legal_roots()` 에서 특정 root 를 고른다. 미공개 root 의 점수는 숨겨지고 `meta().tags`(배정된 방향 등 구조 정보)만 보인다.

---

## 2. 정책 API (Appendix B.2 가 드러내는 인터페이스, 우리 정리)

Appendix B.2 의 정책 개발 프롬프트에서 실제 시스템의 정책 인터페이스 모양을 읽어 낼 수 있다. 아래는 그 의미를 우리 말로 정리한 것이고, 우리 구현은 [`src/xgen_rsi/explore/api.py`](../../src/xgen_rsi/explore/api.py) 의 `Question` 프로토콜과 `LLMDesignedMethod` 다. 온라인 격자(`explore/grid.py`)와 재생 세계(`dream/replay.py`)가 같은 프로토콜을 구현한다.

| `Question` 표면 | 의미 |
|---|---|
| `reset()` | 에피소드 상태를 처음(루트만 보이는 상태)으로 되돌린다 |
| `observed()` | 지금까지 드러난 cell 의 관측 사전. 정책이 결정에 쓸 수 있는 유일한 결과 정보 |
| `legal_actions()` | 이번 배치에 넣을 수 있는 cell: 아직 안 연 root 들 + 열린 가지마다 다음 attempt 하나 |
| `legal_roots()` | 아직 안 연 root 만 |
| `opened_branches()` | 이미 연 가지 번호 |
| `meta(cell_id)` | 점수를 뺀 구조 정보(가지, attempt, 부모, 생성 순서, 방향 태그). 미공개 cell 에도 쓸 수 있다 |
| `probe_batch(cells, on_reveal=…)` | 배치를 실행(온라인)하거나 기록된 결과를 드러낸다(재생). 드러날 때마다 콜백 |
| `baseline_score`, `max_parallelism` | 비교 기준 점수, 배치 크기 상한 W |
| `best_so_far`, `budget_spent` | 장부 전용. 결정 로직에서 읽으면 안 된다 |

- **관측 필드**: 가지·attempt, 점수, 평가 여부, 유효 여부, 실패 분류(`fail_class`), 오류 문자열, baseline 대비·부모 대비 점수 차, 유효/전체 하위 판정 수. 보조 판정 함수(가지가 유망한지, 회복 불가로 실패했는지, 부모·baseline 대비 개선했는지)는 관측에서 계산한다(`explore/signals.py`).
- **성공의 뜻**: 평가가 끝났고 오류가 없으며 실패 분류가 정상이면 성공 평가로 본다. 유효 플래그가 거짓이거나 유효 비율이 비어 있어도 그것만으로 수리 대상으로 분류하지 않는다. 성공 평가 중 최고 점수를 그 가지의 "성공 anchor" 로 쓴다.
- **정보 경계**: 모든 결정 통계는 드러난 관측에서만 만든다. 장부 필드·미공개 점수·기록 원본은 정책에 보이지 않아야 한다(§7 정보 은닉).
- **정책 골격(우리 구현)**: `LLMDesignedMethod.solve(question, budget=None)` 는 `reset` 후, 예산이 남아 있는 동안 관측을 읽고 → 닫을 가지를 갱신하고 → 배치를 고르고 → 배치가 비면 멈추고 → 아니면 `probe_batch` 로 실행하는 반복이다. 배치마다 드러날 때 `_record_curve` 가 호출되어 "probe 수 대비 best-so-far" **anytime 곡선**이 쌓이고(평가자 입력), 끝에 `finalize_result` 가 결과를 정리한다. 배치 합법성은 `check_batch` 가 강제한다. 실제 정책 예시는 `explore/policies/parallel_refine.py`(기준선)와 `portfolio.py`.

## 3. 정책이 지켜야 할 규칙 (B.2 본문 요약 — 우리 정책 검증기의 체크리스트가 된다)

### 3.1 Hard constraints

- `NAME = "OptimalPolicy"`, `class OptimalPolicy(LLMDesignedMethod)` 를 `{method_file}` 에만 구현.
- **Prefix-only**: 결정은 드러난 관측, `baseline_score`, 합법 집합, 구조적 `meta`, 보조 신호만 사용. **미공개 점수, 참 최적값, 하드코딩된 승리 cell id, 절대 점수 목표, 내부 trace 데이터 금지.**
- 모든 prune / widen / deepen / batch / stop 결정은 현재 prefix 로 설명 가능해야 한다. 얕은 약한 점수만으로 가지를 버리지 말 것(깊은 시도가 회복할 수 있음). repairable 한 최근 실패가 역사적 성공 anchor 를 지우거나 그 자체로 영구 기아(starvation)를 일으키면 안 된다.
- 재생은 `budget=None` 으로 호출. 배치를 고르지 않으면 항상 종료. 예산 상한이 있다고 가정하지 말 것.
- 선택된 배치는 합법, 중복 없음, `max_parallelism` 이하.

### 3.2 가지 궤적과 실패 해석

- 열린 가지마다 최신 관측이나 최고 점수만이 아니라 **순서 있는 prefix 궤적**을 재구성한다: 성공 anchor, 점수 추세, 퇴행, 실패/수리 순서, 탐색한 깊이 vs 남은 깊이.
- 실패한 frontier 를 닫거나 우선순위를 낮추기 전에 분류한다:
  1. **hard-unrecoverable**
  2. **repairable implementation failure** — 출력/정확성 불일치, 공유 메모리/자원 한계, 변수/코드, mask/layout/shape 오류는 보통 repairable. 이런 오류 하나로 알고리즘 실패를 추론하지 말 것.
  3. **weak-but-underexplored**
  4. **repeatedly unpromising** (충분한 유효 증거 후)
- `n_valid == 0` 과 `branch_failed_hard(obs)` 는 **신호이지 무조건 폐쇄가 아니다**. `fail_class` 와 `error` 로 repairable 한 zero-valid 와 환경/의존성 실패를 구별한다. `compile_other` 만으로 영구 hard 가 아니다. 현재 실패 *에피소드*를 분류한다 — 이후 성공 결과가 오면 가지를 다시 열고 이전 실패만으로 한 폐쇄를 취소한다.

### 3.3 배치 결정 루프 (매 결정 라운드)

1. prefix 를 읽고 궤적을 재구성한 뒤, hard-unrecoverable 또는 repeatedly unpromising 의 **누적 증거**가 있는 가지만 닫는다.
2. 합법 root 와 합법 frontier 를 prefix 유도 신호로 순위화: 성공 anchor, 부모→자식 이득, 가지 궤적 전체, 실제 성공 vs 실패 증거, 실패 회복 가능성, 이전 수리 결과, 남은 깊이, 교차 가지 비교.
3. 실제 repairable 실패와 덜 탐색된 frontier 를 궤적·회복 가능성·남은 깊이·반복 실패·β 로 **결정적 큐**에 순위화. repairable 실패는 누적 증거가 상대 우선순위를 낮추지 않는 한 적격성을 유지.
4. `max_parallelism` 까지 독립 후보로 **동적 포트폴리오 배치** 하나를 구성: **exploitation**(강한 정상 정련) + **exploration**(새 root 나 덜 탐색된 가지) + **recovery 최대 1개**(실제 repairable 실패). 여러 역할이 적격이면 우선순위로 남은 슬롯을 채우기 전에 exploration 과 정당한 recovery 에 자리를 준다. 고정 할당이 아니라 prefix 증거에 맞춘다. recovery 가 정상 성공 정련을 밀어내거나 워커를 놀리면 안 된다. **무작위 샘플링 금지**, 최상위 후보가 명확하다고 단일 원소 배치로 기본 설정하지 말 것.
5. 드러난 포트폴리오 전체(활성, 덜 탐색됨, 회복 가능, 미개봉, 남은 합법 후보)를 고려한 뒤에만 정지. 적격한 고우선 recovery 나 덜 탐색된 후보가 남아 있으면 정지하지 말 것. 남은 모든 행동은 계속/보류/폐쇄의 증거 기반 결정이 필요.

- 배치는 호출 *전에* 모두 합법인 서로 다른 cell 이어야 한다. 여러 root 와/또는 열린 가지마다 frontier 하나를 담을 수 있다. 부모와 자식을 함께 담지 않는다. **고정된 widen-all / deepen-all 파동 일정 금지** — 드러난 prefix 마다 배치 구성을 적응시킨다.
- 취지(우리 해석): 국소적인 구현 실패를 그 방향 자체가 틀렸다는 증거로 읽지 않는다. 회복 시도의 가치를 새 root·정상 정련과 비교하되 배치는 병렬로 유지한다.

### 3.4 β — 실행 안에서는 고정, 사이클 간에는 적응

`__init__` 에서 스칼라 하나만 읽는다: `beta = float(self.config.get("beta", <sensible_default>))`. β 의 **세 역할**(혼동 금지):

1. **한 재생/라이브 에피소드 안**: β 고정. 모든 행동 임계를 `_schedule(beta) -> dict` 하나로 라우팅. 높은 β = 더 넓게, 더 깊은 인내, 더 약한 가지치기. 낮은 β = 더 적은 probe, 더 이른 정체 정지, 더 강한 가지치기. **`solve()` 안에서 관측으로 β 를 바꾸지 말 것.** recovery 적격성, 보류 임계, 대기도 같은 일정으로 라우팅(높은 β 는 더 참고, 낮은 β 는 선택적이되 repairable 실패 하나를 자동 폐쇄로 취급하지 않음).
2. **오프라인 평가 중**: 평가자가 고정 β 격자를 스윕. 정책이 실제 달성/작업/병렬성 교환을 노출하는지 측정(온라인 β 적응이 아님).
3. **다음 정책 버전 제안 시**: 이전 *라이브* 사이클과 그 β 스윕의 증거로 기본 β 를 한 번 정해 굽는다(다음 라이브 에피소드 내내 고정).

모든 임계는 prefix 상대값. 절대 점수 컷오프 금지.

**사이클 간 기본 β 규칙** — 최근 2–3개의 라이브 `trace_pool/iter*/live_cycle_manifest.json`(있으면 `_current` 포함)에서 반복별 최종 최고 점수와 실제 구운 β 를 읽고, 짝이 되는 보관 `beta_sweep.json`(pareto.reward, AUC, parallel penalty, β 별 frontier)을 읽는다. 점수만으로는 β 가 변화를 일으켰다고 할 수 없으니 두 출처를 함께 쓴다:

| 상황 | 행동 |
|---|---|
| 라이브 최고가 아직 개선 중 | 이전 기본 β 유지(스윕이 근처의 더 나은 β 를 분명히 보이지 않는 한) |
| 라이브 최고가 정체 + 스윕에서 더 높은 β 가 합리적 작업/병렬 비용으로 더 높은 달성 | 기본값을 **0.1–0.2 올림**, [0, 1] 로 clamp |
| 높은 기본 β 를 이미 정체 구간에서 시도 + 높은 β 스윕 점이 달성 없이 작업만 추가 | 작은 폭으로 **내림** |
| 이력 부족 또는 증거 충돌 | 적당히 탐색적인 기본값 **약 0.6** (재생 천장을 라이브 정지 신호로 착각하지 말 것) |

- β 스윕은 β 가 달성/작업 교환을 바꿀 때만 비퇴화다. 스윕은 정책이 배치하는지도 드러낸다. **동결 trace 의 알려진 천장에 닿는 가장 작은 β 를 기본값으로 고르지 말 것.**

### 3.5 다음 사이클 격자 계획 `plan_grid`

우리 구현에서는 `LLMDesignedMethod.plan_grid(context: GridPlanningContext) -> GridPlan` 이다(타입은 `src/xgen_rsi/rsi_math/types.py`).

- 새 라이브 격자를 만들기 **전에** 실행. 에피소드 내 결정이 아니며 현재 에피소드 결과를 절대 보지 않는다. 항상 non-None `GridPlan` 반환(템플릿 stub 상속·러너 fallback 위임 금지). 이력이 없거나 부족하면 context 의 fallback/hard-cap 필드에서 유도한 **명시적 보수적 bootstrap 계획**과 사실적 이유를 반환.
- `GridPlan(branch_count=B, refine_count=R)`: 임의 정수(프리셋 아님). 가지 `0..B−1`, attempt `0..R` 생성. R = 각 root 이후 허용되는 정련 수. 러너 검증: `1 ≤ B ≤ context.hard_max_branch_count`, `0 ≤ R ≤ context.hard_max_refine_count`. **재생에서 동결 trace 의 `context.trace_branch_count`/`trace_refine_count` 를 넘는 계획은 지원 밖(out of support)이라 재생 보상을 받을 수 없다.**
- 사용 가능한 prefix-safe 사실: `history`(완료된 이전 라이브 manifest: 계획·유효 격자, 실제 연 폭/깊이, probe 작업, 결정 라운드, 점수, β), fallback/hard cap, worker cap, 재생 구조 지원 필드. raw trace 결과나 현 사이클 결과는 읽지 말 것.
- 폭 vs 깊이(기본 선호가 아니라 증거로):

| 증거 | 행동 |
|---|---|
| 의미적으로 다른 root 들이 초기에 개선되고 깊은 정련은 정체 | 폭↑, 깊이 유지/↓ |
| 큰 이득이 작고 반복 가능한 방향 집합에서 늦게 도착 | 폭 유지/↓, 깊이↑ |
| 충분한 깊이 후 모든 탐색 방향이 정체하고 의미 있는 방향 부류가 미개척 | 폭↑ |
| 반복된 hard·회복 불가 실패 또는 강하게 중복된 방향 | 폭·깊이 보수적으로↓ |
| 충돌·부족한 이력 | context 에서 유도한 보수적 bootstrap 계획 + "증거 부족" 명시 |

- 모든 계획에 짧고 사실적인 `reason`. `plan_grid` 는 *몇 개의 방향을 제공할지*에 답하고, **direction provider** 가 새 root 에 방향을 배정하며, `solve` 가 여전히 어떤 root/frontier 를 열고·정련하고·가지치고·멈출지 정한다. 런타임 격자는 hard bound 다(제어기 임계는 덜 쓸 수는 있어도 유효 계획을 넘어 가지·시도를 만들 수 없다).
- **기호 충돌 주의**: `GridPlan(branch_count=W, …)` 의 W 는 *가지 수*이고 논문 Sec 3 의 W 는 *병렬 워커 수*(`max_parallelism`)다. 우리 문서는 **B = 가지 수, R = 정련 수, W = 워커 수** 로 쓴다.
- 예산 대응: Gemini-3.1 Pro "10 workspaces × up to 11 refinement steps = 110" ⇒ B = 10, 가지당 cell 11 = attempt 0..R ⇒ **R = 10**, 총 110 cell. Gemini-3.7-Flash 32 × 20 = 640 ⇒ B = 32, R = 19. Fig 6 E0 의 110 = 전체 격자.

### 3.6 이력 학습 규칙 (결과 누설 없이)

- 이전 라운드는 `{history_dir}/r####_*/`. 정책 코드와 `proposal_results/beta_sweep.json` 을 읽는다. **강한 최근 정책에서 시작**하고, pareto.reward 를 올린 메커니즘을 유지하며, 진척이 멈추면 구체적 변경을 한다. 레거시 AUC-only 스윕은 코드 이력으로는 유용하지만 현재 보상과 수치 비교 불가. `{history_dir}/baseline/` 의 baseline = 이겨야 할 **parallel-refine 바닥**.
- 현재 목적함수 라운드마다 `proposal_results/policy_execution_traces.jsonl` 보관: **(동결 trace, β) 당 재생 에피소드 하나**. 결정 라운드마다 prefix 상태·선택 배치·드러난 결과로 직렬 배치, 조기 정지, 과잉 가지치기, 낭비 probe 같은 일반 행동을 진단. **라운드 간 피드백 전용** — `solve()` 안에서 읽지 말 것, trace 특화 가지·cell id·점수·목표를 정책 로직에 복사하지 말 것.
- `{trace_pool}` 은 `solve()` 밖에서만 읽을 수 있다. 반복별 라이브 추세는 raw 재생 결과보다 `live_cycle_manifest.json` sidecar 를 선호.

### 3.7 산출물

`{method_file}` 에 완전한 적응 정책. 모듈 docstring 에 prefix 신호, 배치 규칙, β 일정, 기본 β 근거, 격자 계획 규칙, **과잉 가지치기·과잉 정지·repairable 실패 후 영구 기아·직렬 probe** 에 대한 안전장치를 기술. 마치기 전 검증: 궤적 기반 순위, 명시된 성공 의미론, zero-valid 비자동 폐쇄, 결정적 recovery 경쟁, 포트폴리오 수준 정지.

---

## 4. 탐색 프롬프트 (B.1) — discovery agent 용

변수(`$node_dir`, `$history_dir`, `$baseline_dir`, `$eval_program`, `$problem_file`, `$direction_guidance`)는 호출 시스템이 채운다. 요지:

1. **이력 전체를 먼저 읽어라** — 형제 `attempt_*/` 디렉터리, `$history_dir`, `$baseline_dir` 의 모든 `proposal.md` 를 *전부*(표본·최근만·현재 가지만이 아니라). 각각의 `eval/score.json`(실패면 `error.txt`)을 읽어라. **제안서의 자기 주장보다 측정 결과를 믿어라.**
2. **성공과 실패 모두에서 배워라** — 실패면 *왜*: 결함 있는 핵심 아이디어인가, 버그·나쁜 파라미터·구현 실수에 발목 잡힌 좋은 아이디어인가? 전자는 반복하지 마라. 후자는 재시도할 가치가 있지만 **코드에서 버그를 실제로 찾았고(제안서로 추측한 게 아니라) 구체적 수정이 있을 때만**.
3. **지역 최적에 수렴하지 마라** — 대부분의 시도가 한 메커니즘의 작은 변형에 몰리고 수익이 평탄해지면 지역 최적이다. 또 하나의 작은 조정을 거부하라. 구조적으로 다른 메커니즘이나 시도 안 된 조합을 안전한 한계 정련보다 의도적으로 선호하라. **탐색 다양성은 다음 증분 이득만큼 중요하다.**
4. **제안하고 구현하라** — 새 아이디어는 진정으로 새로운 메커니즘, 이전 성공 조각의 새 조합, 또는 2단계에서 찾은 특정 버그의 표적 수정이어야 한다. 이미 시도한 것의 반복·개명은 금지. `$eval_program` 에 구현. 실제 평가 전에 컴파일·정확성·SOTA 를 주장하지 마라.
- 파일: `$node_dir/proposal.md`(메커니즘, 이력 근거, 반복이 아닌 이유, 기대 이득/위험)와 `$node_dir/$eval_program` 만 쓴다. 나머지는 읽기 전용.
- **"Never execute pkill, kill, killall, or terminate unrelated processes."**

→ RRSI proposer 프롬프트와의 공통점: 이력 전체 조건화, 측정 우선, 반증된 가설 재사용 금지, 새 메커니즘 선호. 차이: Dream-RSI 탐색 에이전트는 **과제의 해(프로그램)** 를 만들고, RRSI proposer 는 **하네스**를 만든다.

---

## 5. 평가자 목적함수 두 가지와 그 관계

### 5.1 논문 Eq.1 (정책 선택의 형식 목적)

```
V_i^m = max_{v ∈ 𝒯_i^{m,k★}} s_v − β1 · N_i^m + β2 · N_i^m / max{1, k_i^{m,★}}
V^m   = (1/t) Σ_i V_i^m,      m★ = argmax_m V^m   (현재 정책 포함 → V^{m★} ≥ V^0)
```

### 5.2 Appendix B 평가자 (개발 에이전트 피드백·β 스윕)

```
pareto.reward   = pareto.auc − λ · parallel_penalty
parallel_penalty = mean over the sweep of ( effective_sequential_rounds / total_probes )
effective_sequential_rounds = Σ_{nonempty batches C_k} ⌈ |C_k| / W ⌉
```

- "직렬 정책은 penalty ≈ 1, 유용한 꽉 찬 배치는 1/W 에 접근."
- `pareto.auc` = "적은 **총 probe** 로 높은 trace 별 달성에 보상" (정확한 식은 미공개 → 6절 결정 E5).

### 5.3 두 병렬성 항은 같은 양의 역수다 (우리 유도)

배치 크기 상한 |C_k| ≤ W 가 hard constraint 이므로 비어 있지 않은 모든 배치에서 ⌈|C_k|/W⌉ = 1 이다. 따라서

```
effective_sequential_rounds = k★,       parallel_penalty(한 에피소드) = k★ / N = 1 / b̄,
Eq.1 병렬성 보너스 = β2 · N / max(1, k★) = β2 · b̄,        b̄ = N / k★ = 평균 배치 크기
```

즉 Eq.1 은 평균 배치 크기에 **선형 보너스**, B.2 평가자는 그 **역수에 선형 벌점**을 준다. 같은 신호(평균 배치 크기)의 두 단조 변환이다. 배치가 W 를 넘을 수 있는 변형(예: 큐잉 허용)에서만 ⌈|C|/W⌉ > 1 로 두 정의가 갈라진다. 우리 구현은 두 값을 모두 계산·기록하고 각각의 용도(5.4)에 쓴다.

### 5.4 우리 구현에서의 사용 원칙

| 용도 | 쓰는 목적함수 | 이유 |
|---|---|---|
| 정책 버전 선택 m★ | **Eq.1 의 V^m** (각 버전의 *구운 기본 β* 에서) | 논문 Sec 3 의 형식 보장(V^{m★} ≥ V^0)을 그대로 유지 |
| 개발 에이전트 피드백 | pareto.reward, AUC, parallel_penalty, β 별 frontier, Eq.1 항별 분해 | Appendix B 의 실제 피드백 형태 |
| 다음 기본 β 결정 | 사이클 간 β 규칙(3.4) | Appendix B |
| 회귀 감시 | 두 목적함수 모두 | 한쪽만 오르고 다른 쪽이 크게 떨어지면 경고 |

---

## 6. 논문이 정하지 않아 우리가 정한 것 (결정 표)

| ID | 항목 | 결정 | 근거 | 재현 모드 대체값 |
|---|---|---|---|---|
| E1 | 루트 다중 선택 | 배치에 r 다중 허용(미개봉 가지 수까지) + `root_policy ∈ {earliest, choose}` | 형식은 집합, 실제 시스템은 "several roots" | `earliest`, r 1회 |
| E2 | K1 (온라인 라운드 한계) | `K1 = B·(R+1)` (직렬일 때 가능한 최대 라운드) + 별도 probe 예산 `N_max = B·(R+1)` | 격자 hard bound 가 실질 한계 | 동일 |
| E3 | K2 (재생 라운드 한계) | `K2 = |𝒯_i| − 1` (= 사실상 무제한, 모든 노드 공개 시 종료) | B.2 "Replay calls with budget=None… do not assume a budget cap" | 동일 |
| E4 | M | 기본 4 (π^0 + 개발 3회), 설정값 | 개발 에이전트 LLM 비용이 지배, 재생은 저렴 | 설정 |
| E5 | pareto.auc | 아래 6.1 정의 v1 | "적은 총 probe 로 높은 trace 별 달성" + "β 스윕 곡선을 순위화" | 동일(정의 버전 기록) |
| E6 | λ | 0.1 (설정값, 보정 대상) | parallel_penalty ∈ [1/W, 1] → 최대 감점 0.1 로 AUC 를 지배하지 않게 | 설정 |
| E7 | β 격자 | {0.0, 0.2, 0.4, 0.6, 0.8, 1.0} | β 는 [0,1] clamp, 기본 ≈0.6, 조정폭 0.1–0.2 | 동일 |
| E8 | s_v 정규화 | 세계별 `s̃_v = clip((s_v − s_base,i)/(s_ceil,i − s_base,i), 0, 1)`, 분모 ≤ 0 이면 s̃ = 1[s_v ≥ s_base,i] | 서로 다른 태스크의 세계를 평균하려면 필수. 논문 실험은 한 태스크의 여러 라운드라 원점수로 충분했음 | 원점수 s_v |
| E9 | β1, β2 | 정규화 점수 기준 `β1 = 0.25 / N_ref`, `β2 = 0.05 / W` (N_ref = 표준 격자 cell 수, 예 110) | 격자 전부를 써도 달성 0.25 만큼만 감점, 꽉 찬 배치는 0.05 가산 → 품질이 주항 유지. **보정 대상** | 설정 |
| E10 | M 개정 해석 | 개발 M−1 회 → π^0..π^{M−1}, 그 안에서 argmax | 마지막 개정본이 평가 없이 버려지지 않게(03 문서 3.6) | 동일 |
| E11 | V 동점 | 같은 V 면 **현재 정책 π^0 우선**, 다음 작은 m | 불필요한 정책 교체 방지(보수적) | argmax 첫 항목 |
| E12 | 재생 결정성 | 정책 실행은 seed 고정, 시간·난수·파일 접근 차단 샌드박스 | "never sample randomly", "prefix-only" | 동일 |
| E13 | Observation 의 fail_class 어휘 | `ok, compile_other, compile_error, runtime_error, wrong_output, resource_limit, timeout, env_failure, …` 를 우리 검증기가 표준화 | B.2 는 `"ok"`, `compile_other` 만 명시 | — |
| E14 | direction provider | 방향 = 새 root 에 붙는 구조 태그(`meta.tags`) + 탐색 프롬프트 `$direction_guidance`. 생성은 별도 LLM 단계(이력의 미개척 방향 부류) | B.2 의 역할 분담 서술 | — |
| E15 | 비어 있는 재생의 V | N=0, k★=0 → `V = s_r`(루트 점수; 없으면 baseline) | Eq.1 의 max 정의역에 r 포함 | 동일 |

### 6.1 `pareto.auc` 정의 v1 (우리 정의)

β 격자 𝔅 의 각 β 와 각 재생 세계(동결 trace) i 에 대해 재생 에피소드를 하나 돌린다.

```
u_i(β) = N_i(β) / N_i^max               정규화 작업 (N_i^max = 세계 i 의 비루트 노드 수)
q_i(β) = s̃ 기준 best-so-far 최종값        정규화 달성 (E8)

AUC_i  = ∫_0^1  max{ q_i(β) : β ∈ 𝔅,  u_i(β) ≤ u }  du        (집합이 비면 0; 계단 함수 적분)
pareto.auc = (1/|I|) Σ_i AUC_i
```

- 해석: "작업 u 이하로 달성 가능한 최고 달성"의 Pareto 경계 아래 면적. 적은 probe 로 높은 달성을 내는 β 가 하나라도 있으면 면적이 커진다(β 스윕이 교환을 노출해야 점수가 남).
- 보조로 에피소드 anytime 곡선 AUC(`∫ best-so-far(n) dn / N_max`, 정지 후 값 유지)를 함께 기록해 "얼마나 빨리 좋은 해에 도달했는가"를 진단한다.
- 정의 버전을 `pareto_auc_version="v1"` 로 결과에 남겨, 레거시 결과와 수치 비교하지 않는다(B.2 "레거시 AUC-only 스윕은 수치 비교 불가"와 같은 원칙).

---

## 7. 재생 시뮬레이터 구현 요건 (정확성 조건)

1. **정보 은닉**: 정책은 `ReplayQuestion` 객체만 받는다. 기저 트리·미공개 점수에 접근할 경로가 없어야 한다(객체 능력 기반 + 샌드박스).
2. **결정성**: 같은 (정책, β, 세계)는 항상 같은 에피소드. 정책 코드의 비결정성(시간, 난수, 해시 순서)을 차단·고정.
3. **지원 밖 처리**: 기록되지 않은 자식 요청 → `Child = ∅` (보상 없음). 격자 계획이 trace 범위를 넘으면 out-of-support 로 표시하고 재생 보상 0.
4. **장부**: probe 수, 결정 라운드 수, 배치 크기 열, effective_sequential_rounds, anytime 곡선, 종료 사유(빈 배치 / K2 / 전부 공개)를 에피소드마다 기록 → `policy_execution_traces.jsonl` 형식.
5. **세계 수명**: 세계는 불변(append-only). 같은 태스크의 새 온라인 실행은 새 세계를 추가한다(𝓗_t = 𝓗_{t−1} ∪ {𝒯_t}).
6. **비용**: 재생은 LLM·도구·평가기 호출이 0 이어야 한다(Dream-RSI 의 전제). 정책 코드는 순수 Python 이고 재생 한 번의 비용은 밀리초–초 단위여야 수천 회 평가가 가능하다.
