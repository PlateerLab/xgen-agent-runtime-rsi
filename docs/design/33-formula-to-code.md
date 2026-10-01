# 33. 수식 → 코드 대응 (`xgen_rsi.rsi_math`) 과 검증 전략

> 정본 수식은 [05-formula-reference.md](../research/05-formula-reference.md). 이 문서는 각 수식을 **어느 함수가, 어떤 시그니처로, 어떤 테스트로** 구현·증명하는지 정한다.
> 규칙: `rsi_math` 는 **순수 함수만**(I/O·시간·전역 상태 없음). 난수는 인자로 받은 `random.Random` 만. 모든 함수는 05 문서 기호를 docstring 에 적는다.

---

## 1. 모듈 지도

```
xgen_rsi/rsi_math/
  types.py      TaskResult, EvalResult, EditRecord, Decision, Candidate, ReplayEpisode … (frozen dataclass)
  rrsi.py       Eq.3–17 + Algorithm 1·2 의 계산 부분
  calibrate.py  δ 추정
  bounds.py     정확 경계 조기 종료(우리 정리, §4)
  dream.py      D-Eq.1, 선택, 평가자(parallel_penalty, pareto.auc v1, pareto.reward), β 규칙
  units.py      단위 환산(δ, β1, w_s 를 검증 단위 수로)
  modes.py      "paper"(논문·공식 코드 재현) / "xgen"(우리 결정 적용) 모드 스위치
```

---

## 2. RRSI 함수 (rrsi.py)

| 수식 | 함수 시그니처 | 비고 |
|---|---|---|
| Eq.3 Ŝ (가중), Ĉ (정의역 D1) | `aggregate(per_task: Mapping[str, TaskResult], k: int) -> EvalResult` | 누락 r=0 분모 포함, Ĉ 는 토큰>0 시행만, 없으면 None. `n_expected=|D|·k` |
| Eq.6 ΔS | `delta_s(S_cand: float, S_inc: float) -> float` | |
| Eq.6 ΔC | `relative_cost_change(C_cand: float|None, C_inc: float|None) -> float` | 결측·0 → 0.0 |
| Eq.4 b_t | `edit_budget(t: int, T: int, b_min: int, b_max: int) -> int` | `ceil(round(v, 9))`, t clamp, T≤0 → b_max |
| Eq.9 | `check_edit_cardinality(n_edits: int, b_t: int) -> bool` | proposer done() 검증에서 사용 |
| Eq.5 | `floor_ok(S_cand: float, S_star: float, delta: float) -> bool` | `S_cand >= S_star - delta` |
| Eq.7/17 | `cost_rule(dS, dC, nu, delta, *, beta0, beta1, w_s, w_c, w_n) -> tuple[bool, str]` | `dS > delta` 분기, Eq.17 엄격 |
| Eq.15/16 ν | `novelty(components: Iterable[str], accepted_counts: Mapping[str,int], *, k_str=K_STR, enabled=K) -> int` | 집합 처리, `enabled` 밖 kind 무시(결정 D-3) |
| N_t(ℓ) | `accepted_counts(records: Sequence[EditRecord], before_t: int|None = None) -> dict[str,int]` | 재판정 시 `before_t=t` |
| 가드 | `GuardFn = Callable[[EvalResult, EvalResult], list[str]]` | 엔지니어링형 예: `rate_guard(max_valid_drop=0.03, max_nosub_rise=0.02)` |
| Alg.2 판정 | `judge(cand: Candidate, inc: EvalResult, S_star, delta, cfg, counts, guard_violations) -> Decision` | 순서: 측정 → 바닥 → c → 가드 |
| Eq.8 / Alg.2 선택 | `select_round(cands, inc, S_star, delta, cfg, counts, guard_fn=None, *, tie="xgen") -> tuple[Candidate|None, list[Decision]]` | tie: `"paper"`=첫 후보, `"xgen"`=Ŝ→Ĉ↓→편집 수↓→라벨 |
| S★ 갱신 | `update_s_star(S_star: float, S_next: float) -> float` | max |
| 결과 레코드 | `outcome_of(cand, winner, decision) -> Literal["ACCEPTED","LOST","REJECTED", gate…]` | a_i = (ACCEPTED) |
| Eq.11 𝒯_t | `tried(records) -> set[str]` | measured(ΔS≠None ∧ ℓ∈𝒦) |
| Eq.11 g_t | `recent_yield(records, t, n_prune) -> dict[str, float]` | 창 포함, 빈 → −inf |
| Eq.14 𝓑_t | `prune_set(records, t, n_prune) -> list[PruneTarget]` | g ≤ 0(−inf 포함), 수락 편집 목록 동봉 |
| Eq.13 σ_t | `stall_flag(traj: Sequence[float], t: int, w: int, delta: float) -> int` | t<w → 0 |
| Eq.13 𝒰_t, 𝓔_t | `exploration(stall: int, tried: set[str], m_draft: int, *, enabled=K) -> Exploration` | 𝒦 순서 유지, 텍스트 생성은 evolve 쪽(수식 아님) |
| 예약 슬롯 | `reserved_variants(m: int, m_draft: int, stall: int, untried: Sequence[str]) -> set[int]` | `v >= m - m_draft` |
| 태그 정규화 | `normalize_component(declared, touched_kinds: set[str], diff: str, signals) -> str` | 1순위 manifest kind 대조, 2순위 정규식 신호(공식 코드 동일 규칙) |
| attribution | `attribute(edit, inc, cand, k) -> AttributionRow` | hit_rate, unpredicted_regressions(임계 1/k) |

## 3. δ 추정 (calibrate.py)

| 함수 | 시그니처 | 비고 |
|---|---|---|
| bootstrap se | `bootstrap_se(ev: EvalResult, reps: int = 2000, rng: random.Random = Random(7)) -> float` | 태스크 내 시행 복원추출, 가중 존중, `pstdev` |
| 합치기 | `pooled(evals: Sequence[EvalResult]) -> EvalResult` | |
| δ | `calibrate(evals: Sequence[EvalResult], z: float = 2.0, reps: int = 2000) -> Calibration` | R≥2: `stdev·√2`(>0 일 때), 아니면 `√2·se·√(k_pooled/k)`. 결과에 방법·관측치 전부 기록 |
| 단위 | `delta_from_units(n_units: int, N_units: int) -> float` (units.py) | 설계 시 참고값 |

## 4. 정확 경계 조기 종료 (bounds.py) — 우리 정리

RRSI 평가 비용(후보 × |D| × k)을 줄이되 **판정을 수학적으로 바꾸지 않는** 조건.

부분 평가 상태: 끝난 시행의 가중 보상 합 `A`, 가중치 합 `B`, 남은 시행 가중치 합 `R`(태스크 가중치는 사전에 알 수 있음; 모르면 상한 사용).

```
Ŝ_max = (A + R) / (B + R)          (남은 시행 전부 r=1)
Ŝ_min =  A      / (B + R)          (남은 시행 전부 r=0 — 누락도 0 이므로 하한 유효)
```

**정리.** `Ŝ_max < min(S★ − δ, Ŝ_t)` 이면 평가를 멈추고 `REJECTED(floor, early_stopped)` 로 기록해도, 전체 평가를 했을 때와 비교해 (i) 이번 라운드 선택 결과, (ii) S★, (iii) 𝒯_t, (iv) 𝓑_t, (v) N_t, (vi) σ_t, 𝒰_t 가 모두 같다.

*검증*: 공식 RRSI 코드(커밋 `be50316`)의 `select_round`·`History` 로 4,000 무작위 시도 중 조기 종료된 1,788 건 모두에서 (i)·(iii)·(iv)·(v) 동일을 확인했다(`research/verification/verify_bound.py`, 2026-10-01).

*증명 개요.* 최종 Ŝ' ≤ Ŝ_max < S★ − δ 이므로 바닥 탈락이 확정 → 허용 집합·argmax·S★ 불변(i, ii). 기록은 ΔS 를 `Ŝ_max − Ŝ_t` (상한)로 남기며 ΔS ≠ None 이라 ℓ 는 전체 평가 때와 똑같이 𝒯_t 에 들어간다(iii). 참 ΔS ≤ ΔS_max < 0 이므로 이 레코드가 g_t(ℓ) 의 부호에 주는 영향은 전체 평가와 같고(양수였다면 다른 레코드 때문), 𝓑_t 는 부호(g ≤ 0)만으로 정해진다(iv). 거부 후보라 N_t 불변(v). σ_t·𝒰_t 는 incumbent 궤적과 𝒯_t 에만 의존(vi). ∎

- 달라지는 것은 **정보성 값**뿐이다: 이력에 보이는 ΔS(상한), ΔC(부분 토큰 기준, `partial` 표시), attribution(부분). proposer 에게 `early_stopped` 를 명시해 보여 준다.
- 조건에 `Ŝ_t` 를 넣은 이유: S★ > Ŝ_t 인 경우(incumbent 가 역대 최고보다 낮을 때) 바닥 탈락만으로는 ΔS 부호가 확정되지 않아 𝓑_t 가 바뀔 수 있기 때문이다.
- 평가 순서: 태스크를 *무작위 고정 순서*(seed 기록)로 돌려야 부분 평균에 편향이 없다(조기 종료 판정 자체는 순서와 무관하게 정확하지만, 비용 절감 폭은 순서에 따라 달라진다).
- **재판정 주의**: 나중에 δ 를 키우거나(바닥이 낮아짐) S★ 가 바뀌는 재판정을 하면 조건이 더 이상 성립하지 않을 수 있다. 재판정기는 조기 종료 후보마다 조건을 다시 확인하고, 깨지면 그 후보를 `reevaluate` 대상으로 표시한다(판정을 추측하지 않음).
- 조기 종료한 후보의 시행 기록도 세계 풀에 들어가되 `partial` 표시로 Dream 재생에서 가중치를 낮출 수 있게 한다.

```python
def can_stop_exactly(A: float, B: float, R: float, S_star: float, delta: float, S_inc: float) -> bool: ...
```

## 5. Dream 함수 (dream.py)

| 수식 | 함수 시그니처 | 비고 |
|---|---|---|
| 정규화 s̃ (E8) | `normalize_scores(world: World, *, mode="xgen") -> Callable[[float], float]` | paper 모드 = 항등 |
| D-Eq.1 | `replay_value(ep: ReplayEpisode, *, beta_cost: float, beta_par: float, norm) -> float` | `max s̃ − β1·N + β2·N/max(1,k★)`, N = 공개 비루트 노드 수, 빈 재생 = s̃_r |
| V^m | `mean_value(values: Sequence[float]) -> float` | 1/t Σ |
| 선택 | `select_policy(V: Sequence[float], *, tie="xgen") -> int` | 동점 → 0 우선(E11), paper = argmax 첫 항목 |
| 비감소 보장 검사 | `assert_non_decreasing(V, m_star)` | V[m★] ≥ V[0] |
| 유효 순차 라운드 | `effective_sequential_rounds(batch_sizes: Sequence[int], W: int) -> int` | Σ ceil(|C|/W) |
| 병렬 벌점 | `parallel_penalty(episodes: Sequence[ReplayEpisode], W: int) -> float` | (β, 세계) 평균 of rounds/probes |
| pareto.auc v1 | `pareto_auc_v1(points_by_world: Mapping[str, Sequence[tuple[float,float]]]) -> float` | 세계별 계단 적분 후 평균 |
| pareto.reward | `pareto_reward(auc: float, penalty: float, lam: float) -> float` | auc − λ·penalty |
| anytime AUC | `anytime_auc(curve: Sequence[tuple[int,float]], N_max: int) -> float` | 진단용 |
| β 규칙 | `next_default_beta(live: Sequence[LiveCycle], sweeps: Sequence[BetaSweep], *, step=0.15) -> BetaDecision` | 4갈래 규칙 + 근거 문자열, clamp [0,1], 부족 시 0.6 |
| 격자 검증 | `validate_grid(plan: GridPlan, ctx: GridPlanningContext, *, replay: bool) -> GridValidity` | hard cap, out-of-support |

재생 엔진(`dream/replay.py`)은 수식이 아니라 **전이 규칙**(Child 정의, 종료 3조건, 정보 은닉)을 구현하며, 아래 §7 의 성질 테스트로 검증한다.

---

## 6. 검증 전략

### 6.1 검산 벡터 (05 문서 §8 → 테스트 파일 1:1)

| 05 §8 | 테스트 |
|---|---|
| 8.1 예산표 3종 + b_T + 원값 | `test_edit_budget_tables` |
| 8.2 Ŝ 2건 | `test_aggregate_vectors` |
| 8.3 비용 규칙 5건 | `test_cost_rule_vectors` |
| 8.4 선택 시나리오 | `test_select_round_vectors` |
| 8.5 이력 요약 | `test_history_summaries_vectors` |
| 8.6 정체 플래그 4건 | `test_stall_flag_vectors` |
| 8.7 Table 6 사례 3건 | `test_paper_table6_cases` |
| 8.8 Dream 장난감 세계 | `test_dream_toy_world` (V, penalty, u, q, AUC=0.4556) |

### 6.2 공식 구현과의 차분 테스트(differential testing)

공식 `google-research/rrsi` 를 **테스트 의존성으로 커밋 해시 고정**(Apache-2.0, 실행 코드에는 포함하지 않음)하고, `hypothesis` 로 무작위 입력을 생성해 우리 함수와 출력이 같은지 확인한다(`modes.paper`).

| 대상 | 생성 입력 | 비교 |
|---|---|---|
| `edit_budget` | T∈[1,200], b_min≤b_max∈[1,10], t∈[−5,T+5] | 정수 동일 |
| `aggregate` | 태스크 1–50, k 1–5, 보상·가중·토큰(일부 None·0) | S, C, n_expected, missing 동일 |
| `relative_cost_change`, `cost_rule` | 실수 범위 + 경계값(ΔS=δ, 값=0) | bool 동일 |
| `judge`/`select_round` | 후보 0–8, 게이트 실패 섞기 | 승자·결정 사유 분류 동일(tie=paper) |
| `stall_flag`, `exploration` | 궤적·w·δ | 동일 |
| `History` 요약 | 무작위 레코드열(측정/비측정 섞기) | tried, g, prune, counts 동일 |
| `novelty`, `normalize`(신호 경로) | 구성요소 열, diff 문자열 | 동일 |
| `calibrate` | 평가 1–4개 | delta 동일(같은 seed·같은 난수 소비 순서를 보장하도록 구현) |

### 6.3 성질 기반 테스트(불변식)

| 불변식 | 대상 |
|---|---|
| b_t 는 t 에 대해 비증가, b_0 = b_max, b_t ∈ [b_min, b_max], b_T = b_min | edit_budget |
| Ŝ ∈ [0,1]; 시행 하나를 누락으로 바꾸면 Ŝ 는 증가하지 않음 | aggregate |
| ΔC 를 줄이면 c 는 참→거짓으로 바뀌지 않음; ν 를 늘리면 대역 안 c 는 참→거짓으로 바뀌지 않음 | cost_rule |
| 승자는 허용이며 허용 중 Ŝ 최대; 허용 없으면 None; S★ 비감소 | select_round |
| ΔS ≤ 0 레코드 추가는 𝓑_t 의 원소를 빼지 않음 | prune_set |
| `can_stop_exactly` 가 참인 모든 경우, 남은 시행을 임의로 채운 전체 평가와 (선택, 𝒯, 𝓑, N) 동일 | bounds (무작위 채우기 반복) |
| V[m★] ≥ V[0] | select_policy |
| 같은 (정책, β, 세계) → 같은 에피소드 | replay 결정성 |
| N ≤ |𝒯_i| − 1; 배치는 합법·중복 없음·|C| ≤ W·부모-자식 비동시 | replay 전이 |
| |C| ≤ W 이면 penalty = k★/N ∈ [1/W, 1] | parallel_penalty |
| pareto.auc ∈ [0,1], 점 추가는 auc 를 줄이지 않음 | pareto_auc_v1 |

### 6.4 재판정 재현 테스트

저장된 `decisions.json` + 판정 입력으로 `select_round` 를 다시 돌려 같은 결과가 나오는지(원값 저장 규칙 05 §7). δ·가중치를 바꾼 재판정은 공식 코드의 `readjudicate` 와 같은 결과여야 한다.

---

## 7. 모드 스위치 (`modes.py`)

| 항목 | `paper` (재현) | `xgen` (기본) |
|---|---|---|
| argmax 동점 | 첫 후보 | Ŝ → Ĉ↓ → 편집 수↓ → 라벨 |
| 𝒦 활성 | 9종 전부 | 결정 D-3 반영(`enabled`) |
| Dream 점수 | 원점수 s_v | 정규화 s̃ (E8) |
| Dream 루트 | earliest, 1회 | choose, 다중 |
| V 동점 | 첫 m | π^0 우선 |
| 조기 종료 | 없음 | 정확 경계(§4) |
| 바닥·비용 규칙 비교 허용오차 | 0 (공식 구현 그대로) | 1e-12 — 정확한 유리수 동점(ΔS = δ, S = S★ − δ)을 부동소수 오차 없이 "띠 안"·"통과"로 판정 |

- `paper` 모드 테스트가 통과해야 `xgen` 모드 변경이 "논문 수식 위의 명시적 결정"임이 보장된다.
