# 05. 수식 정본(Formula Reference) — 기호표·정의역·경계 조건·검산 벡터

> 구현의 정본이다. 새 하네스의 모든 수식 코드는 이 문서의 정의를 따르고, 8절의 검산 벡터를 단위 테스트로 그대로 옮긴다.
> 출처 표기: **[R-Eq n]** = RRSI 논문 식 n, **[R-code]** = google-research/rrsi 구현, **[D-Eq 1]** = Dream-RSI 논문 식 1, **[D-B]** = Dream-RSI Appendix B, **[ours]** = 논문이 정하지 않아 우리가 정한 정의(04 문서 결정 표 E1–E15, 02 문서 D1–D18).
> 8절 숫자는 2026-10-01 에 공식 RRSI 코드를 직접 실행해 확인했다([`verification/verify_examples.py`](verification/verify_examples.py)).

---

## 1. 기호표

### 1.1 RRSI (하네스 진화)

| 기호 | 의미 | 타입/정의역 | 출처 |
|---|---|---|---|
| π | 고정 정책(backbone LLM) | 공급자+모델 식별자 | R §2 |
| H, H_t | 하네스, 라운드 t 의 incumbent | 버전 식별자(트리 해시) | R §2 |
| A=(π,H) | 에이전트 | | R §2 |
| x, D, D_evolve | 태스크, 태스크 집합, evolve 집합 | | R §2 |
| τ, τ_x^(j) | 궤적, 태스크 x 의 j 번째 시행 | | R §2 |
| r(x,τ) | 검증기 보상 | [0,1] | R §2 |
| w(x,τ) | 시행 가중치(criteria 수 등) | > 0, 기본 1 | R-code |
| c(τ) | 정책 토큰 비용 | 정수 ≥ 0 | R-Eq1 |
| k | 태스크당 시행 수 | 정수 ≥ 1 | R-Eq3 |
| S, C | 기대 성능·비용 | | R-Eq1 |
| Ŝ, Ĉ | 경험 추정 | Ŝ∈[0,1], Ĉ>0 또는 결측 | R-Eq3 |
| Ŝ_t, Ĉ_t | incumbent H_t 의 추정 | | R-Alg2 |
| S★ | 역대 최고 evolve 점수 | [0,1] | R-Eq5 |
| δ | 경험적 잡음 대역 | > 0 | R-Eq5 |
| ΔS, ΔC | 점수 차, **상대** 비용 변화 | ΔS∈[−1,1], ΔC∈[−1,∞) | R-Eq6 |
| β0, β1 | 비용 규칙 절편·기울기 | ≥ 0 | R-Eq7 |
| w_s, w_c, w_n | 대역 안 규칙 가중치 | ≥ 0 | R-Eq17 |
| T, t | 총 라운드, 라운드 색인 | t = 0..T−1 | R-C.1 |
| m | 라운드당 후보 수 | 정수 ≥ 1 (기본 2) | R-code |
| b_min, b_max, b_t | 편집 예산 하한·상한·라운드 t 값 | 정수 ≥ 1 | R-Eq4 |
| E_t, z_t | 원자 편집 풀, 선택 지시 벡터 | z_t ∈ {0,1}^{|E_t|} | R-Eq9 |
| 𝒦, 𝒦_str | 구성요소 어휘(9), 구조 부분집합(4) | 고정 집합 | R-Eq12/15 |
| ℓ, h, d | 구성요소 태그, 가설, diff | ℓ∈𝒦 | R-Eq10 |
| 𝓛_t | 편집 이력 | 레코드 집합 | R-Eq10 |
| a_i | 승자 지시자 | {0,1} | R-Eq10 |
| 𝒯_t | 측정된 구성요소 집합 | ⊆ 𝒦 | R-Eq11 |
| g_t(ℓ) | 창 내 최고 최근 이득 | ℝ ∪ {−∞} | R-Eq11 |
| n_prune | 가지치기 창 | 정수 ≥ 0 | R-Eq11 |
| σ_t, w | 정체 플래그, 정체 창 | σ∈{0,1}, w≥1 | R-Eq13 |
| 𝒰_t | 미시도 구성요소 | ⊆ 𝒦 | R-Eq13 |
| m_draft | 예약 탐색 슬롯 수 | 0 ≤ m_draft ≤ m | R-Eq13 |
| 𝓔_t, 𝓑_t | 탐색 지시, 가지치기 대상 | | R-Eq13/14 |
| N_t(ℓ) | ℓ 태그 수락 레코드 수 | 정수 ≥ 0 | R-Eq16 |
| comp(H') | 후보가 건드린 구성요소 집합 | ⊆ 𝒦 | R-Eq16 |
| ν_t(H') | 구조적 새로움 | 0..4 | R-Eq16 |
| g(H_t,H') | 도메인 가드 | {0,1} | R-C.3 |
| 𝓐_t | 허용 후보 집합 | | R-Eq8 |

### 1.2 Dream-RSI (탐색 정책 진화)

| 기호 | 의미 | 정의역 | 출처 |
|---|---|---|---|
| r | 발견 트리 루트(초기 워크스페이스) | | D §3 |
| v, s_v | 노드, 점수(클수록 좋음) | s_v ∈ ℝ | D §3 |
| 𝒯, 𝒯_t, 𝒯_i | 관측 트리, 반복 t 의 온라인 트리, 재생 세계 i | | D §3 |
| A(𝒯) | 적격 노드 = {r} ∪ 리프 | | D §3 |
| W | 병렬 워커 수(`max_parallelism`) | 정수 ≥ 1 | D §3 |
| C, A(𝒯;W) | 배치, 실행 가능 배치 집합 | |C| ≤ W | D §3 |
| K1, K2 | 온라인·재생 라운드 한계 | 정수 | D §3 |
| 𝓗_t | 발견 이력 = 트리 모음 | | D §3 |
| π_t, π_t^m | 탐색 정책, 버전 m | | D §3 |
| M | 버전 수 | 정수 ≥ 1 | D §3 |
| 𝒯_i^{m,k} | 버전 m 이 세계 i 에서 k 라운드 후 드러낸 부분 트리 | ⊆ 𝒯_i | D §3 |
| k_i^{m,★} | 종료 시 완료 라운드 수 | 0..K2 | D §3 |
| N_i^m | 드러난 비루트 노드 수 = |𝒯_i^{m,k★}| − 1 | 정수 ≥ 0 | D-Eq1 |
| β1, β2 | Eq.1 비용·병렬 계수 | ≥ 0 | D-Eq1 |
| V_i^m, V^m | 세계별·평균 재생 점수 | ℝ | D-Eq1 |
| β (β_explore) | 정책 내부 탐색 노브 | [0,1] | D-B |
| λ | parallel_penalty 계수 | ≥ 0 | D-B |
| B, R | 격자 가지 수, 정련 수 (`GridPlan`) | B≥1, R≥0 | D-B |

> **이름 충돌 규칙**: Dream-RSI 의 β1, β2 는 RRSI 의 β0, β1 과 무관하다. 코드에서는 `rrsi.beta0/beta1`, `dream.beta_cost/beta_par`, `policy.beta_explore` 로 이름을 분리한다. 격자의 `branch_count` 는 W 가 아니라 B 로 쓴다.

---

## 2. RRSI — 추정·판정 수식

### 2.1 경험 점수 Ŝ (R-Eq3 의 가중 일반화, R-code)

```
Ŝ(H) = Σ_{x∈D} Σ_{j=1..k} w(x,τ_x^(j)) · r(x,τ_x^(j))  /  Σ_{x∈D} Σ_{j=1..k} w(x,τ_x^(j))
```

- 모든 w = 1 이면 정확히 R-Eq3 `1/(k|D|) Σ Σ r`.
- **누락 시행(크래시·타임아웃·인프라)**: r = 0, w = 해당 태스크의 기본 가중치(=1 또는 기대 criteria 수)로 **분모에 포함**. 후보가 어려운 시행을 망가뜨려 좋아 보이는 것을 막는다.
- 분모 0 이면 Ŝ = 0.
- `n_expected = |D|·k`, `missing = Σ 누락 수`.

### 2.2 경험 비용 Ĉ (R-Eq3, 정의역 명시 [R-code D1])

```
𝒥_c = { (x, j) : c(τ_x^(j)) 가 측정됨 ∧ c(τ_x^(j)) > 0 }
Ĉ(H) = (1/|𝒥_c|) Σ_{(x,j)∈𝒥_c} c(τ_x^(j)),      𝒥_c = ∅ 이면 Ĉ = 결측(None)
```

- 누락 시행을 0 토큰으로 넣으면 크래시 많은 후보가 싸 보이므로 제외한다. 누락률은 2.10 의 무효 게이트가 따로 통제한다.
- c(τ) 의 정의는 [ours, 12 문서 결론]: **정책 π 에 대한 모든 API 호출의 (input + output) 토큰 합**. cache read/write 토큰과 reasoning 토큰은 별도 필드로 기록하고, 기본 c(τ) 에는 input 에 포함된 그대로 둔다(공급자 usage 의 input 정의를 따름). 하네스 내부의 추가 정책 호출(서브에이전트, 요약, 검증 패스)도 **모두 c(τ) 에 포함**한다 — RRSI 의 비용 규칙이 서브에이전트·요약 비용을 잡아야 의미가 있다. 탐색 역할(proposer/critic/analyst)의 토큰은 c(τ) 가 아니다(진화 비용으로 별도 집계).

### 2.3 차분 (R-Eq6)

```
ΔS = Ŝ(H') − Ŝ(H_t)
ΔC = ( Ĉ(H') − Ĉ(H_t) ) / Ĉ(H_t)          Ĉ(H') 또는 Ĉ(H_t) 가 결측/0 이면 ΔC = 0   [R-code D3]
```

### 2.4 잡음 대역 δ (R-code calibrate, D6)

base 하네스 H_0 의 독립 평가 R 회 `Ŝ^(1), …, Ŝ^(R)`, 같은 k:

```
R ≥ 2:   sd_null = stdev(Ŝ^(1..R)) · √2              (표본 표준편차, n−1)
R = 1:   se = pstdev_b( Ŝ*_b ),  b = 1..2000          (태스크 내 시행 복원추출 bootstrap, 가중 존중, seed 고정)
         sd_null = √2 · se · √(k_pooled / k)
δ = z · sd_null,   z = 2.0 (기본)
```

- R ≥ 2 이고 sd_null > 0 이면 직접 관측값, 아니면 bootstrap 값.
- 해석: 변경 없는 하네스가 바닥 S★−δ 를 약 97.5% 통과.
- **단위 환산**(인스턴스 설계 시): δ = n_δ / N_units (예: 코딩 3 pass / 178 trial).

### 2.5 잡음 보정 바닥 (R-Eq5)

```
floor_ok(H') ⟺ Ŝ(H') ≥ S★ − δ
```

경계: 등호 포함(통과).

### 2.6 비용 규칙 / 대역 안 규칙 (R-Eq7, R-Eq17)

```
if ΔS > δ:      c(H') ⟺ ΔC ≤ β0 + β1 · ΔS                                  (Eq.7, 등호 통과)
else:           c(H') ⟺ w_s · ΔS − w_c · ΔC + w_n · ν_t(H') > 0              (Eq.17, 엄격)
```

- 분기 경계: ΔS = δ 는 **대역 안**(Eq.17).
- **단위 환산**: β1 = (허용 상대 비용 per 검증 단위) × N_units (예: 코딩 0.25 × 178 = 44.5). w_s = (가중치 per 검증 단위) × N_units (workspace 0.1 × ≈14,140 ≈ 1414).

### 2.7 새로움 ν (R-Eq15, 16)

```
𝒦_str = {client_tool, skill, memory, subagent}
N_t(ℓ) = |{ i : a_i = 1 ∧ ℓ_i = ℓ ∧ t_i < t }|
ν_t(H') = |{ ℓ ∈ 𝒦_str : ℓ ∈ comp(H') ∧ N_t(ℓ) = 0 }|
```

- comp(H') 는 **diff 증거로 검증된** 태그 집합(2.11). 중복 태그는 한 번만 센다(집합).
- N_t 는 이력 기반이다. 가지치기로 기계장치를 지워도 N_t 는 줄지 않는다(D17).

### 2.8 도메인 가드 (R-C.3)

```
g(H_t, H') = 1  ⟺  위반 목록이 비어 있음
엔지니어링 예:  (valid_rate(H_t) − valid_rate(H')) > 0.03  → 위반
               (no_sub_rate(H') − no_sub_rate(H_t)) > 0.02 → 위반
```

### 2.9 허용과 선택 (R-Eq8, R-Alg2)

```
admissible(H') ⟺ 측정 유효 ∧ floor_ok(H') ∧ c(H') ∧ g(H_t, H')
𝓐_t = { H' ∈ 𝓗_t : admissible(H') }
H_{t+1} = argmax_{H' ∈ 𝓐_t} Ŝ(H')    (𝓐_t = ∅ 이면 H_t)
S★ ← max(S★, Ŝ(H_{t+1}))
```

- 판정 기록 순서(첫 실패 사유): 측정 유효 → 바닥 → c → 가드 [R-code].
- **동점** [ours D7]: Ŝ 동점이면 Ĉ 작은 쪽 → 편집 수 작은 쪽 → 변형 라벨 사전순. (재현 모드: 첫 변형)
- 초기화: `S★ = Ŝ(H_0)` 이며 H_0 평가가 첫 frontier 항목.

### 2.10 측정 유효성 게이트 [R-code D14]

```
valid_measurement(H') ⟺ missing(H') ≤ φ · n_expected(H'),     φ = invalid_missing_frac (코딩 0.2, workspace 0.1, 엔지니어링 0.15)
```

초과 시 1회 재평가, 그래도 초과면 `eval_invalid`(측정 없음 → 이력에 delta_S = None).

### 2.11 구성요소 태그 정규화 [R-code D10, ours]

```
normalize(ℓ_decl, d) = ℓ_decl   if ℓ_decl ∈ 𝒦 ∧ evidence(ℓ_decl, d)
                     = classify(d)  otherwise
classify(d) = prompt  if text_only(d)
            = 첫 매칭 구성요소(선언 타입 맵 → 도메인 신호 → 공통 신호)
            = prompt  if 매칭 없음
```

[ours] 우리 하네스는 모든 파일/모듈이 manifest 에 `kind ∈ 𝒦` 를 선언하므로 `evidence(ℓ, d)` = "d 가 kind=ℓ 인 파일을 건드림"으로 **정확 판정**한다. 정규식 신호는 선언이 없는 새 파일의 보조 분류에만 쓴다.

---

## 3. RRSI — 제안 쪽 수식

### 3.1 편집 예산 (R-Eq4, R-code)

```
b_t = ⌈ round( b_min + (b_max − b_min) · ½ (1 + cos(π t / T)), 9 ) ⌉,      t ← clamp(t, 0, T);   T ≤ 0 이면 b_max
제약:  ‖z_t‖_0 = (후보의 선언 독립 편집 수) ≤ b_t                                   (R-Eq9)
```

- `round(·, 9)` 는 부동소수 오차로 정수 경계를 살짝 넘은 값이 한 칸 올라가는 것을 막는다. 공식 코드 주석은 "t=T 에서 1.0000000002 → 2" 를 예로 들지만 (T=20, 1..4) 의 t=T 원값은 정확히 1.0 이다. 실제로 보정이 필요한 예는 (t=2, T=3, b_min=1, b_max=5): 원값 2.0000000000000004 → 보정 없으면 3, 보정하면 2 (rsi_math 구현 중 발견, 테스트에 고정). **반드시 포함.**
- t ∈ [0, T−1] 에서 b_t > b_min (t = T 에서만 b_min).

### 3.2 편집 이력 (R-Eq10)

```
레코드 = (t_i, variant, edit_id, ℓ_i, h_i, d_i, ΔS_i, ΔC_i, a_i, outcome, Ŝ, Ĉ, bundle, detail, predicted_affected, targets_mode, ts)
outcome ∈ {ACCEPTED, LOST, REJECTED}            (측정됨)
        ∪ {critic_reject, smoke_fail, eval_invalid, no_proposal, not_evaluated}   (측정 없음: ΔS = ΔC = None)
a_i = 1 ⟺ outcome = ACCEPTED
```

후보 하나가 n 개 편집이면 n 개 레코드, 모두 같은 (ΔS, ΔC, a, outcome).

### 3.3 요약 (R-Eq11)

```
measured_t = { i ∈ 𝓛_t : ΔS_i ≠ None ∧ ℓ_i ∈ 𝒦 }
𝒯_t        = { ℓ_i : i ∈ measured_t }
g_t(ℓ)     = max{ ΔS_i : i ∈ measured_t, ℓ_i = ℓ, t − t_i ≤ n_prune },   빈 집합이면 −∞
```

창 경계 포함(t − t_i = n_prune 이면 포함).

### 3.4 탐색 지시 (R-Eq13, R-code)

```
traj[τ] = Ŝ(H_τ)  (τ = 0..t, 기각 라운드는 incumbent 점수 반복)
σ_t = 0                                  if t < w
    = 𝟙[ traj[t] − traj[t−w] ≤ δ ]        otherwise
𝒰_t = 𝒦 \ 𝒯_t                            (𝒦 순서 유지)
예약 변형: σ_t = 1 ∧ 𝒰_t ≠ ∅ 일 때 변형 색인 v ≥ m − m_draft
예약 조건: 예약 변형의 정규화된 태그 중 최소 하나 ∈ 𝒰_t (아니면 수리 라운드)
```

### 3.5 가지치기 대상 (R-Eq14)

```
𝓑_t = { ℓ ∈ 𝒯_t : g_t(ℓ) ≤ 0 }     (g = −∞ 포함)
proposer 입력: 각 ℓ ∈ 𝓑_t 에 대해 (recent_best_gain = g_t(ℓ) 또는 None, accepted_edits_in_incumbent = {a_i = 1 ∧ ℓ_i = ℓ 레코드})
```

---

## 4. Dream-RSI — 재생 수식

### 4.1 적격·배치·전이 (D §3, ours E1)

```
A(𝒯)    = {r} ∪ leaves(𝒯)
A(𝒯;W)  = { C ⊆_multi A(𝒯) : |C| ≤ W,  mult(r) ≤ #unopened_branches,  v≠r 는 최대 1회 }
온라인:  𝒯^{k+1} = 𝒯^k ∪ { new_child(v) : v ∈ C^k }                  (확률적)
재생:    𝒯^{m,k+1} = 𝒯^{m,k} ∪ ⋃_{v∈C} Child(v; 𝒯_i, 𝒯^{m,k})        (결정적)
Child(v≠r) = { v 의 유일한 기록 자식 } 또는 ∅
Child(r)   = root_policy=earliest: 미공개 r-자식 중 seq 최소 (r 을 j 번 넣으면 seq 최소 j 개)
           = root_policy=choose  : 정책이 지정한 미개봉 root cell
종료: C = ∅  ∨  k = K2  ∨  𝒯^{m,k} = 𝒯_i
```

### 4.2 재생 점수 (D-Eq1)

```
N_i^m = |𝒯_i^{m,k★}| − 1
V_i^m = max_{v ∈ 𝒯_i^{m,k★}} s̃_v − β1 · N_i^m + β2 · N_i^m / max{1, k_i^{m,★}}
V^m   = (1/t) Σ_{i=1..t} V_i^m
m★    = argmax_{m ∈ {0..M−1}} V^m      (동점: m=0 우선, 다음 작은 m)   [ours E11]
π_{t+1} = π_t^{m★}                      ⇒  V^{m★} ≥ V^0
```

- s̃ = 정규화 점수 [ours E8]: `s̃_v = clip((s_v − s_base,i)/(s_ceil,i − s_base,i), 0, 1)`, `s_ceil,i = max_{v∈𝒯_i} s_v`, 분모 ≤ 0 이면 `s̃_v = 𝟙[s_v ≥ s_base,i]`. **재현 모드는 원점수 s_v.**
- 비어 있는 재생: N = 0, k★ = 0 → V = s̃_r (루트 점수 없으면 baseline 의 s̃ = 0).

### 4.3 평가자 (D-B)

```
effective_sequential_rounds = Σ_{k: C_k≠∅} ⌈|C_k| / W⌉       ( |C_k| ≤ W 이면 = k★ )
penalty_i(β) = effective_sequential_rounds / max(1, total_probes)
parallel_penalty = mean_{i, β∈𝔅} penalty_i(β)
pareto.reward = pareto.auc − λ · parallel_penalty
```

**pareto.auc v1** [ours E5]:

```
u_i(β) = N_i(β) / N_i^max,     q_i(β) = 최종 best-so-far 의 s̃
AUC_i = ∫_0^1 max{ q_i(β) : β∈𝔅, u_i(β) ≤ u } du   (빈 집합 0, 계단 적분)
pareto.auc = mean_i AUC_i
```

**관계(유도)**: |C_k| ≤ W 이면 `penalty = k★/N = 1/b̄`, Eq.1 병렬성 항 = `β2 · b̄` (b̄ = 평균 배치 크기). 같은 신호의 역수 관계.

### 4.4 β 의 사이클 간 갱신 (D-B 규칙의 수치화) [ours]

```
β_next = β_prev                          if 라이브 최고가 개선 중 ∧ 스윕이 더 나은 근처 β 를 분명히 안 보임
       = clamp(β_prev + Δ, 0, 1)          if 정체 ∧ 스윕에서 더 높은 β 가 합리적 비용으로 더 높은 달성,  Δ ∈ [0.1, 0.2]
       = clamp(β_prev − Δ, 0, 1)          if 높은 β 로 정체 경험 ∧ 높은 β 점이 달성 없이 작업만 추가
       = 0.6                              if 이력 부족 ∨ 증거 충돌
```

"개선 중"·"정체"의 수치 판정은 RRSI 의 σ 와 같은 형태로 정한다 [ours]: 최근 w_D 사이클의 라이브 최고 점수 차가 잡음 대역 δ_D 이하면 정체(δ_D 는 같은 정책의 반복 라이브 사이클로 보정). 판단 주체는 policy-development agent 이고 이 식은 **검증용 기준**이다.

### 4.5 격자 검증 (D-B)

```
1 ≤ B ≤ hard_max_branch_count,    0 ≤ R ≤ hard_max_refine_count
재생 지원: B ≤ trace_branch_count ∧ R ≤ trace_refine_count (아니면 out-of-support → 재생 보상 0)
격자 cell 수 = B · (R + 1)
```

---

## 5. 정의역·경계 조건 일람

| 상황 | 처리 | 출처 |
|---|---|---|
| 시행 누락 | r=0, 분모 포함 / Ĉ 에서 제외 | R-code |
| 토큰 결측 | ΔC = 0 | R-code |
| 누락률 > φ | 1회 재평가 → eval_invalid | R-code |
| ΔS = δ | 대역 안(Eq.17) | R-Alg2 |
| Ŝ' = S★ − δ | 바닥 통과 | R-Eq5 |
| Eq.17 값 = 0 | 불허(엄격 >) | R-Eq17 |
| 𝓐_t = ∅ | H_{t+1} = H_t, trajectory 에 incumbent 점수 반복 | R-Eq8 |
| t < w | σ_t = 0 | R-code |
| g_t(ℓ) = −∞ | 𝓑_t 포함 | R-Eq14 |
| 측정 없는 후보 | 𝒯_t·g_t·N_t 에서 제외 | R-Eq10, R-code |
| 선언 태그 무증거 | diff 로 재분류(거부 아님) | R-code |
| 예약 슬롯 미충족 | 수리 라운드(최대 repair_rounds) | R-code |
| 재생 빈 배치(첫 결정) | k★ = 0, N = 0, V = s̃_r | D-Eq1, ours |
| 기록 안 된 자식 요청 | Child = ∅ | D §3 |
| 격자 계획 > trace 범위 | out-of-support, 재생 보상 0 | D-B |
| V 동점 | 현재 정책 우선 | ours |
| 서로 다른 태스크 세계 평균 | s̃ 정규화 | ours |

---

## 6. 파생 지표 (진단·보고용, 판정에는 쓰지 않음)

| 지표 | 정의 | 용도 |
|---|---|---|
| hit_rate | 편집별 `|predicted ∩ improved| / |predicted|` | attribution scoreboard [R-code] |
| unpredicted_regressions | 예측 밖에서 태스크 평균이 `1/k` 이상 하락한 태스크 | 신용 할당 보강 [R-code] |
| steps/trial | 시행당 정책 호출 스텝 수 | Figure 4b 재현 |
| valid_rate, no_sub_rate | 유효 출력률, 미제출률 | 도메인 가드 입력 |
| generalization gap | Ŝ_evolve − Ŝ_heldout (H_t 와 H_0 각각) | 과적합 감시 |
| anytime AUC | 에피소드 내 best-so-far 곡선 면적 | 재생 진단 [ours] |
| 평균 배치 크기 b̄ | N / k★ | 병렬성 진단 |

---

## 7. 수치 안정성·재현성 규칙

1. Ŝ, Ĉ, ΔS, ΔC 는 float64 로 계산하고 저장 시 Ŝ·ΔS·ΔC 는 소수 6자리, Ĉ 는 1자리 반올림(R-code 와 동일). **판정은 반올림 전 값으로** 한다(저장값 재판정 시 차이가 생기지 않도록 원값도 eval.json 에 보관).
2. b_t 는 `round(v, 9)` 후 천장.
3. bootstrap 은 seed 고정(7), reps 2000.
4. 재생 정책 실행은 결정적(난수·시간 차단).
5. 모든 판정 입력(Ŝ, Ĉ, S★, δ, 가중치, 카운트)을 decisions.json 에 함께 기록해 재판정 가능하게 한다.

---

## 8. 검산 벡터 (단위 테스트로 그대로 옮김)

### 8.1 편집 예산 (공식 코드 실행 결과)

| (T, b_min, b_max) | b_0 … b_{T−1} |
|---|---|
| (20, 1, 4) | 4,4,4,4,4,4,4,4,3,3,3,3,3,2,2,2,2,2,2,2 |
| (20, 1, 3) | 3,3,3,3,3,3,3,3,3,3,2,2,2,2,2,2,2,2,2,2 |
| (40, 1, 4) | 4×16, 3×9, 2×15 |
| b_T (t = T = 20) | 1 |
| (T=3, 1, 5) t=2 원값 | 2.0000000000000004 → `round(·,9)` 후 ⌈⌉ = 2 (보정 없으면 3) |
| (20,1,4) t=19 원값 | 1.01847 → ⌈⌉ = 2 |

### 8.2 Ŝ (공식 테스트)

- 태스크 a: [1,0], b: [1,1], k=2 → Ŝ = 0.75, n_expected = 4.
- 가중: a = [0.5, 1.0] w=[10,10], b = [0,0] w=[90,90] → Ŝ = 15/200 = 0.075.

### 8.3 비용 규칙 (공식 테스트, β0=0.1, β1=40, w_s=100, w_c=15, w_n=0.5, δ=0.02)

| ΔS | ΔC | ν | 결과 | 분기 |
|---|---|---|---|---|
| 0.05 | 0.10+40×0.05−0.01 = 2.09 | 0 | 통과 | Eq.7 |
| 0.05 | 2.11 | 0 | 불허 | Eq.7 |
| 0.0 | −0.10 | 0 | 통과 (1.5 > 0) | Eq.17 |
| 0.0 | 0.10 | 0 | 불허 (−1.5) | Eq.17 |
| 0.0 | 0.0 | 1 | 통과 (0.5 > 0) | Eq.17 |

### 8.4 선택 (공식 테스트)

incumbent Ŝ=0.5, S★=0.55, δ=0.05 (바닥 0.50). A=0.7(prompt), B=0.6(skill), C=0.3(config), D=critic_reject → A·B 허용, C 바닥 탈락, D 불허, **승자 A**. 토큰을 (1+0.1+40×0.2+0.5)배로 늘린 0.7 후보 E → 비용 규칙 탈락. 가드 위반 시 A 도 불허.

### 8.5 이력 요약 (공식 테스트)

레코드: t0 A prompt ACCEPTED ΔS=0.03 / t1 A prompt REJECTED −0.01 / t1 B {skill, memory} REJECTED −0.02 / t2 A config critic_reject.

- 𝒯 = {prompt, skill, memory} (config 제외).
- g_3 (n_prune=4): prompt 0.03, skill −0.02. g_5: prompt −0.01 (t0 레코드가 창 밖). g_6: prompt −∞.
- 𝓑_5 = {prompt, skill, memory}, prompt 의 수락 편집 = h1.
- N(prompt) = 1, ν([client_tool, prompt]) = 1, ν([skill] | N(skill)=2) = 0.

### 8.6 정체 플래그 (공식 테스트)

traj = [0.50, 0.53, 0.53, 0.535, 0.60]: σ(t=3,w=3,δ=0.02)=0 (0.035) / σ(3,2)=1 (0.005) / σ(4,2)=0 (0.07) / σ(1,3)=0 (이력 부족).

### 8.7 논문 사례 재검산 (Table 6, 공식 `cost_rule` 실행)

| 사례 | 입력 | 결과 |
|---|---|---|
| 코딩 R0-A | ΔS = 7/178 = 0.0393 > 0.017, ΔC ≈ 0 | Eq.7 허용량 1.850 → 통과 |
| 코딩 R0-B | ΔS = 3/178 = 0.01685 ≤ 0.017, ΔC = 0.261, w_s=0 | Eq.17 = −3.915 + 0.5ν < 0 (ν = 0..4 모두) → 불허 |
| 엔지니어링 R2 | ΔS = 6/244 = 0.0246 > 0.020, ΔC = 0.016 | 허용량 0.750 → 통과 |

### 8.8 Dream-RSI 장난감 세계 (손계산 + 스크립트 확인)

세계: s_r = 0.40(baseline). 가지 b0: [0.50, 0.62, 0.60], b1: [0.30, 0.70], b2: [0.55]. N_max = 6, W = 2.

| 정책 | 배치 열 | N | k★ | best | V (β1=0.01, β2=0.02, 원점수) | penalty | u | q (s̃) |
|---|---|---|---|---|---|---|---|---|
| A 직렬 탐욕 | {b0a0} → {b0a1} → {b0a2} → ∅ | 3 | 3 | 0.62 | 0.61 | 1.0 | 0.5 | 0.7333 |
| B 병렬 | {b0a0, b1a0} → {b0a1, b1a1} → ∅ | 4 | 2 | 0.70 | 0.70 | 0.5 | 0.6667 | 1.0 |

- A, B 를 한 세계의 두 β 점으로 보면 pareto.auc v1 = 0·0.5 + 0.7333·(0.6667−0.5) + 1.0·(1−0.6667) = **0.4556**.
- B 는 b1 의 첫 시도 0.30(실패처럼 보이는 낮은 점수) 뒤 0.70 이 숨어 있음을 보여 준다 — "얕은 약한 점수만으로 가지를 버리지 말라"(D-B)의 수치 예.
