# 02. RRSI 공식 구현(google-research/rrsi) 분석

> 저장소: `https://github.com/google-research/rrsi` (Apache-2.0, 4 commits, 마지막 push 2026-09-23). 2026-10-01 에 clone 해 전체 코어(`rrsi/*.py` 약 2,900줄)와 도메인 설정·헌법·기질(substrate)을 읽었다.
> 목적: 논문 수식이 *실제로 어떻게 계산되는지*, 논문에 없는 공학적 결정이 무엇인지, 우리가 그대로 가져올 것과 바꿀 것을 확정한다.
> 라이선스: Apache-2.0. 코드 일부를 포팅하면 저작권 고지와 NOTICE 를 유지해야 한다. `third_party/`(harbor Terminus-2, archipelago)는 각자 라이선스.

---

## 1. 저장소 구조

```
rrsi.py                 CLI: baseline / calibrate / round / run / readjudicate / reevaluate / heldout / smoke / status
rrsi/
  config.py      97     RRSIConfig (하이퍼파라미터 + 공학 노브)
  schedule.py    54     edit_budget (Eq.4)
  history.py    211     History: 𝓛_t, 𝒯_t, g_t, 𝓑_t, σ_t, 𝓔_t
  components.py 108     𝒦, 𝒦_str, diff→구성요소 분류, novelty ν (Eq.15/16)
  evaluate.py   135     Ŝ, Ĉ (Eq.3), ΔC (Eq.6)
  calibrate.py  119     δ 추정
  selection.py  137     Algorithm 2 (Eq.5/7/17, 가드, argmax)
  loop.py       605     Run.round: Algorithm 1+2 한 라운드, 재판정·재평가
  propose.py    410     P_reg: JSON 행동 프로토콜 proposer 에이전트
  critic.py     147     누설 심사 (결정적 precheck + LLM 리뷰)
  analyst.py    204     ANALYZE: 3-렌즈 보고서 (digester 디스패치)
  digester.py   221     읽기 전용 trace 조사 서브에이전트
  domain.py     124     Domain 인터페이스
  driver.py      99     순차 드라이버(재개, STOP 파일, 연속 인프라 실패 중단)
  gitops.py     126     worktree/branch/fast-forward/tree hash
  llm.py        124     AnthropicVertex 클라이언트(프로젝트 라운드로빈, cache_prefix)
domains/{coding,workspace,eng}/
  adapter.py            DOMAIN = Domain 구현
  rrsi.json             인스턴스 하이퍼파라미터
  SKILL.md              proposer 헌법
  PATTERNS.md           메커니즘 패턴 라이브러리(참고용, allowlist 아님)
  briefs.py             analyst/digester/proposer/critic 역할별 도메인 문단
  render.py             trace 렌더링
tests/test_core.py      API·벤치마크 없이 코어 수식 단위 테스트
third_party/            base 하네스(Terminus-2, archipelago react_toolbelt) + 구조 기질 mechanisms.py
```

README 의 "Method to code" 표가 수식 ↔ 코드 대응을 공식적으로 밝힌다(아래 2절에서 각각 검증).

---

## 2. 수식별 구현 (코드를 직접 읽고 확인)

### 2.1 Ŝ, Ĉ — `evaluate.aggregate` (Eq.3)

```python
num = den = 0.0; toks = []; missing = 0
for tr in per_task.values():
    for r, w in zip(tr.rewards, tr.weights):
        num += r * w; den += w
    toks += [x for x in tr.tokens if isinstance(x, (int, float)) and x > 0]
    missing += tr.missing
S = num/den if den else 0.0
C = sum(toks)/len(toks) if toks else None
n_expected = len(per_task) * k
```

- **누락 시행(crash, timeout, 인프라)은 r = 0 으로 분모에 포함**된다. "후보가 어려운 시행을 망가뜨려서 좋아 보이는 일을 막는다."
- **가중치**: 코딩·엔지니어링은 시행당 가중치 1. Harvey LAB 는 시행 보상 = 통과 criteria/전체 criteria, 가중치 = 전체 criteria 수. 그래서 Ŝ 는 모든 태스크에 걸친 criteria 통과 비율(벤치마크 자체 지표)이 된다. 즉 실제 구현은 Eq.3 의 **가중 일반화**: `Ŝ = Σ r·w / Σ w`. 가중치가 모두 1이면 Eq.3 과 정확히 같다.
- **Ĉ 는 토큰 수가 양수인 시행만 평균**한다(누락 시행 제외). 논문 Eq.3 의 `1/(k|D|) Σ c` 와 정의역이 다르다(→ 4절 차이 D1).
- `TaskResult.mean` = 태스크 내 가중 평균 (trace 선정·attribution 에 사용).

### 2.2 ΔC — `evaluate.relative_cost_change` (Eq.6)

```python
if not C_cand or not C_inc: return 0.0
return (C_cand - C_inc) / C_inc
```

어느 쪽이든 토큰 수가 없으면 ΔC = 0 (비용 중립 취급).

### 2.3 b_t — `schedule.edit_budget` (Eq.4)

```python
if T <= 0: return int(b_max)
t = max(0, min(int(t), int(T)))
v = b_min + (b_max - b_min) * 0.5 * (1.0 + math.cos(math.pi * t / T))
return int(math.ceil(round(v, 9)))      # 코드 주석: "t=T 에서 1.0000000002 → 2" (실제 예는 05 문서 §3.1)
```

- `round(v, 9)` 로 부동소수 잡음을 깎은 뒤 천장. 우리 구현도 반드시 이 보정을 넣어야 한다.
- 실측 예산표(2026-10-01, 이 코드를 직접 실행):
  - 코딩 T=20, 1..4: `[4,4,4,4,4,4,4,4,3,3,3,3,3,2,2,2,2,2,2,2]`
  - workspace T=20, 1..3: `[3,3,3,3,3,3,3,3,3,3,2,2,2,2,2,2,2,2,2,2]`
  - 엔지니어링 T=40, 1..4: 4×16, 3×9, 2×15
  - **b_min=1 은 실행 중 한 번도 나오지 않는다**(t=T 에서만).
- 예산은 proposer 의 `done()` 에서 강제된다: 선언 편집 수 > b_t 이면 반려하고 다시 done 을 요구한다(거부가 아니라 재시도).

### 2.4 𝓛_t, 𝒯_t, g_t, 𝓑_t — `history.History` (Eq.10/11/14)

- **편집 하나당 JSONL 레코드 하나**(`runs/<domain>/history.jsonl`). 레코드 필드:

```
t, variant, edit_id, component, hypothesis, targets_mode, predicted_affected, diff(경로),
delta_S, delta_C, accepted, outcome, S, C, bundle(=후보의 편집 수), detail(≤600자), ts
```

- `outcome` ∈ {`ACCEPTED`, `LOST`(허용됐지만 더 높은 허용 후보에 짐), `REJECTED`(허용 안 됨)} = 측정된 결과. 측정 전 탈락은 `critic_reject` / `smoke_fail` / `eval_invalid` / `no_proposal` / `not_evaluated` 로 `delta_S = None` 기록.
- `measured()` = `delta_S is not None and component in K` 인 레코드.
- `tried()` = 𝒯_t = measured 레코드의 component 집합. **critic 에 걸린 구성요소는 tried 가 아니다**(측정 없음).
- `yield_g(t, n_prune)`: tried 구성요소마다 −∞ 로 시작, `t − t_i ≤ n_prune` (**경계 포함**) 인 measured 레코드의 delta_S 최댓값.
- `prune_set(t, n_prune)` = 𝓑_t: `g ≤ 0` 인 구성요소 + 그 구성요소의 **현재 incumbent 에 들어 있는 수락 편집 목록**(`accepted_edits_in_incumbent`). proposer 에게 "무엇을 지울지"까지 넘긴다.
- `accepted_edits()` 는 `accepted=True` 레코드를 구성요소별로 모은다. `incumbent_component_counts()` = N_t(ℓ).
- `render(n=40)`: proposer 컨텍스트용 최근 레코드 축약. **측정 없는 게이트 실패는 최근 4개만** 남긴다("abort 의 벽은 증거가 아니라 피드백 루프").

### 2.5 σ_t, 𝓔_t — `history.stall_flag`, `history.exploration` (Eq.13)

```python
def stall_flag(trajectory, t, w, delta):
    if t < w or t >= len(trajectory) or t - w < 0: return 0
    return int(trajectory[t] - trajectory[t - w] <= delta)
```

- `trajectory[t]` = **H_t(라운드 t 시작 시 incumbent)의 Ŝ**. frontier 의 trajectory 는 `t=0`(baseline)부터 라운드가 끝날 때마다 `t+1` 항목을 추가하며, 기각된 라운드는 incumbent 점수를 그대로 반복 기록한다.
- t < w 이면 0 (이력 부족).
- `exploration(t, stall, tried, m_draft)` → `{sigma, untried(=𝒰_t, 𝒦 순서 유지), m_draft, text}`. text 는 proposer 에게 주는 문장: 정체+미시도 있음 → "RESERVED", 미시도만 있음 → "필수 아님, 증거는 아직 없음", 전부 시도 → "모든 구성요소가 최소 1회 시도됨".
- **예약 슬롯 배정**(`loop.round`): `reserved = sigma and untried and v >= m − m_draft`. m=2, m_draft=1 이면 정체 시 **B 변형이 예약 슬롯**을 갖는다.
- 예약 슬롯 강제는 세 겹: proposer `done()` 검증 → critic 수락 후 diff 기반 재태깅 결과로 재검사(재태깅 후 미시도 구성요소가 하나도 없으면 critic 판정을 reject 로 바꿔 수리 라운드로 보냄).

### 2.6 𝒦, 𝒦_str, 태그 검증, ν — `components.py` (Eq.12/15/16)

```python
K     = ["prompt","control_flow","config","output_plumbing","context_mgmt","client_tool","skill","memory","subagent"]
K_STR = ["client_tool","skill","memory","subagent"]
```

- `text_only(diff)`: 바뀐 모든 줄이 문자열 리터럴 또는 주석이면 True → `prompt`. "모델이 보는 텍스트 편집은 내용이 무엇이든 prompt."
- `classify_diff(diff, domain_signals)`: text_only 면 prompt, 아니면 (도메인 신호 → 공통 신호) 순서로 첫 매칭 구성요소, 없으면 prompt.
- 공통 신호(`GENERIC_SIGNALS`): memory=`Memory(`, `.remember(`, `.recall(`, `_STATE_DIR` / skill=`skills/`, `SkillRegistry`, `skill_use`… / client_tool=`ToolRegistry`, `register_tool`, `tool_spec`, `CLIENT_TOOLS` / subagent=`subcall(`, `sub_agent`, `subagent`.
- `has_evidence(component, diff)`: 선언 태그가 diff 에 그 구성요소의 신호를 가질 때만 유지. "proposer 가 기계장치를 실제로 넣지 않고 skill/memory/tool/subagent 라고 이름 붙이거나, 예약 탐색 슬롯을 채우려고 control-flow 변경에 미시도 라벨을 붙일 수 있다. 검증 안 된 태그는 𝒯_t, 𝒰_t, novelty 항을 오염시킨다."
- `normalize(declared, diff)`: 선언이 K 안에 있고 증거가 있으면 유지, 아니면 diff 로 재분류. 재태깅은 거부 사유가 아니다("라벨로 다투면 후보 하나를 잃고 얻는 게 없다").
- `novelty(edit_components, incumbent_counts)` = `|{c ∈ set(edit_components) : c ∈ K_STR ∧ counts.get(c,0) == 0}|`. 즉 ν_t(H').
- **주의**: 태그 검증은 *한 후보의 전체 diff* 에 대해 한다(편집별 diff 가 아님). 여러 편집이 한 후보에 있으면 편집 A 의 태그 증거가 편집 B 의 코드에서 올 수 있다. 우리 설계에서는 구성요소가 파일/모듈 단위로 타입 선언되므로 **편집별 diff 범위로 정확히 검증**할 수 있다(31 문서).

### 2.7 δ — `calibrate.calibrate`

```
delta = z · sd(null ΔS),   z = cfg.delta_z (기본 2.0)
```

- base 평가가 R ≥ 2 회면 직접 관측: `sd_null = stdev(scores) · √2` (두 독립 평가 차의 표준편차).
- 1회면 **태스크 내 시행 bootstrap**: `se = pstdev(bootstrap S)` (reps=2000, seed=7, 가중치 존중), `sd_boot = √2 · se · √(k_pooled / k_single)`.
- R ≥ 2 이고 sd_null > 0 이면 직접 관측값을 쓰고, 아니면 bootstrap.
- 결과 `calibration.json`: delta, z, sd_null, sd_null_bootstrap, se_bootstrap, method, n_evals, k, n_tasks, S_base, C_base (+ S_per_eval, max_abs_diff).
- 해석: "변경 없는 하네스는 바닥 S★−δ 를 약 97.5% 확률로 통과하고, 이득은 대역을 넘어야 L1 비용 규칙이 진짜로 취급한다."
- 논문 인스턴스 값은 `rrsi.json` 에 고정(0.017/0.004/0.020). `delta: null` 이면 `calibrate` 결과를 쓴다.

### 2.8 Algorithm 2 — `selection.judge`, `selection.select_round`

```python
def cost_rule(dS, dC, nov, delta, cfg):
    if dS > delta:  return dC <= cfg.beta0 + cfg.beta1 * dS          # Eq.7
    return cfg.w_s*dS - cfg.w_c*dC + cfg.w_n*nov > 0                 # Eq.17

def judge(cand, incumbent, S_star, delta, cfg, incumbent_counts, guards):
    if cand.ev is None: → 불허(gate_failure)
    dS = ev.S - incumbent.S;  dC = relative_cost_change(ev.C, incumbent.C)
    nov = novelty(cand.components, incumbent_counts)
    if ev.S < S_star - delta: → 불허 "below noise-adjusted floor"
    if not cost_rule(...):    → 불허 "cost rule failed"
    if guards:                → 불허 "domain guard violated"
    → 허용

select_round: 허용 후보 중 S 최대(동점이면 max() 의 첫 항목 = 앞 변형) → winner, 없으면 None
```

- 경계: 바닥은 `S' < S★−δ` 이면 탈락(즉 `≥` 통과), 이득 분기는 `ΔS > δ`(엄격), Eq.7 은 `≤`, Eq.17 은 `> 0`(엄격). 논문과 일치.
- **동점 처리**: Python `max` 는 첫 최댓값을 반환하므로 동점이면 변형 A 가 이긴다. 논문은 동점 규칙을 명시하지 않는다(→ 차이 D7).

### 2.9 라운드 오케스트레이션 — `loop.Run.round(t)`

1. frontier 검증: `incumbent.harness_tree == tree_hash(evolve/<domain>)` (하네스 디렉터리의 git tree 해시). 어긋나면 중단.
2. **𝓕_t ← Analyze**: incumbent 자체 평가에서 trace 선정 — 점수 낮은 태스크 `n_fail_traces` 개의 *최악 시행* + 점수 높은 태스크 `n_success_traces` 개의 *최선 시행*(proposer 가 깨면 안 되는 성공 습관). trace 가 기대치 절반 미만이면 중단. `analysis_report.json` 재사용(재개 안전). `global_analysis.json` 에 failure_modes/success_habits 를 누적해 다음 라운드의 안정적 명명에 씀.
3. b_t, σ_t, 𝒯_t, 𝓔_t, 𝓑_t 계산 → `directives.json` 저장.
4. m 개 변형(A, B, …) 각자 **자기 worktree + 브랜치 `<domain>/r{t}{v}`** 에서 `_draft`:
   - propose → (critic → 수리) 최대 `1 + repair_rounds` 회 → diff 기반 태그 정규화 → 커밋 → **smoke**(컴파일·생성·소수 태스크 liveness, 선택 규칙 아님) → `prep.json`.
5. 게이트 통과 후보만 `Evaluate(H', D_evolve, k)` (ThreadPool, `eval_parallel`). 누락 시행이 `invalid_missing_frac × n_expected` 를 넘으면 1회 재시도, 그래도 넘으면 `eval_invalid`.
6. **Algorithm 2** → `decisions.json`. 각 후보의 편집 레코드를 history 에 기록, attribution 기록.
7. 승자가 있으면 `evolve/<domain>` 을 승자 커밋으로 **fast-forward**, frontier.incumbent 갱신, `S_star = max(S_star, new_S)`. trajectory 에 `t+1` 항목 추가.

**재판정**(`readjudicate t`): δ 나 가중치를 바꾼 뒤, 새 평가 없이 저장된 측정으로 Algorithm 2 를 다시 적용한다. 이후 라운드가 남아 있으면 거부(그 후보들은 옛 H_{t+1} 에서 나왔으므로). N_t(ℓ) 는 `t_i < t` 수락 레코드로 다시 센다.
**재평가**(`reevaluate t`): 인프라 실패로 오염된 후보만 다시 측정한 뒤 재판정.

### 2.10 Attribution scoreboard — `loop.Run.attribute` (논문에 없음)

편집마다 `predicted_affected`(proposer 가 선언한 영향 태스크) 중 실제로 평균이 오른 태스크(`predicted_hit`), `hit_rate`, 예측 밖에서 `regression_threshold(k) = 1/k` 이상 떨어진 태스크(`unpredicted_regressions`, 최대 12)를 `attribution.jsonl` 에 남긴다. 최근 20행을 proposer 에게 다시 보여 준다("과대 주장은 불리하게 작용"). 신용 할당을 보강하는 공학 장치다.

---

## 3. 탐색 역할(proposer / critic / analyst / digester) 상세

### 3.1 Proposer — `propose.propose` (P_reg)

- **strict-JSON 행동 프로토콜**, 턴당 정확히 한 행동. MAX_TURNS=40, MAX_EDITS=80(파일 수정 횟수), TRACE_READ_CAP=60,000자.
- 행동: `list_files`, `read_file`, `list_traces`(이번 라운드 trace: task_id, score, status, steps, modes), `read_trace`(읽기 전용, 스텝 범위·detail, 채점 판정 항상 포함), `edit_file`(old 는 정확히 1회 등장), `write_file`(새 파일만, 허용 확장자), `done`.
- **abort 행동이 없다**(3회까지 "abort 는 없다"며 반려, 그 뒤에야 abort 반환). "아무것도 내보내지 않는 라운드는 아무것도 시험하지 않는다. 이력은 *측정된 것*을 기록한다."
- `done(edits=[…])` 의 편집 필드: `id, component, hypothesis, targets_mode, why_not_lower_lever, trigger_condition, predicted_affected, retroactive_check(교정·보존·이전 3부 반사실), regression_risk`. 필수: id, component, hypothesis, targets_mode, predicted_affected, retroactive_check.
- done 검증: 편집 수 > b_t, 필수 필드 누락, component ∉ K, 예약 슬롯 위반, 파일 변경 0인데 편집 선언 → 모두 반려 후 재시도.
- 컨텍스트 순서: 변형 브리프 → 편집 이력 𝓛_t → attribution scoreboard → 탐색 지시 𝓔_t → 가지치기 𝓑_t → 3-렌즈 보고서 𝓕_t → 태스크별 digest → **현재 하네스 소스 전체** → b_t → 과업(또는 수리 라운드 지시 + 리뷰어 이의).
- 안정 블록(헌법 SKILL.md + 패턴 라이브러리 PATTERNS.md)은 `cache_prefix` 로 ephemeral 캐시.
- 시스템 프롬프트 요지: "정책 LLM 은 고정이며 *너와 다른 모델*이다. 너의 능력·습관·판단을 공유한다고 가정하지 마라. trajectory 를 증거로 *그 모델의 관점에서* 하네스를 개선하라. 바꿀 수 있는 것은 작업 디렉터리의 스캐폴드 코드뿐이다. 도구 환경·채점기·태스크 집합은 고정이다." "인터페이스 계약(모듈 진입점, 클래스명, trajectory 출력 스키마)은 그대로다. 모델명·스텝 예산·타임아웃은 외부 주입이라 편집해도 효과가 없다."

### 3.2 Critic — `critic.review` (누설 심사)

두 겹:

1. **결정적 precheck**: 공통 패턴(자격증명: `AIza…`, `sk-…`, `api_key="…"`) + 도메인 패턴(코딩: `/tests`, `test_outputs`, `reward.(txt|json)`, `ctrf`, `/solution`, `task.toml`, `/logs/verifier` + **evolve 태스크 이름 전부**를 단어 경계 정규식으로). 걸리면 즉시 reject. 빈 diff 도 reject.
2. **LLM 리뷰**: 다음 중 *하나라도* 해당하면 REJECT.
   1. 누설/태스크 특화: 태스크명·id, 엔티티·회사·사건명, 태스크 특화 파일명, 기대 출력·정답, 한 태스크에만 의미 있는 매직 상수, 태스크별 템플릿, 스위트 도메인 목록, 태스크 식별 특징에 대한 분기. **리트머스: "같은 종류의 다른 스위트의 낯선 태스크에서도 말이 되고 도움이 되는가?"**
   2. 퇴화: 메커니즘을 주장하지만 사실상 no-op(죽은 코드, 미사용 플래그, 주석뿐), 또는 대체 없이 기존 안전장치(컨텍스트 압축, 출력 절단, 오류 처리, 종료 가드) 삭제·비활성화.
   3. 채점기 게이밍: 런타임에 검증기·judge·rubric 을 읽기·탐지·재구성·영향, 채점기에서만 나올 수 있는 임계·참조값 하드코딩, 완료 신호·출력 형식을 채점기에 맞춰 게이밍.
   4. 미선언 번들링: 선언 편집 어디에도 속하지 않는 독립 변경, 또는 선언 편집 하나가 여러 독립 메커니즘을 몰래 묶음.
   5. 런타임 메모리/스킬 누설: 메모리·상태·스킬 메커니즘이 시행 간에 태스크 특화 런타임 데이터(파일 내용, 도구 출력, 계산된 정답, 태스크/엔티티/파일명, 태스크 식별자로 키잉된 해)를 저장·주입할 수 있으면 거부.
   6. 무한 작업: 포기 경로 없는 추가 검사·재시도·"계속 개선" 루프, 확인이 올 때까지 완료를 기다리게 하는 것.
   - "런타임 정확성(정의 안 된 이름, 크래시, 문법)은 네 일이 아니다. 결정적 compile/constructor/smoke 검사가 뒤에서 처리한다."
   - 출력 `{"verdict": "accept"|"reject", "reasons": […], "risk_notes": […]}`, 파싱 3회 실패 시 reject.
- reject 면 이의(reasons, risk_notes, 선언 편집)를 proposer 에게 돌려 **최대 repair_rounds(=5)회 수리**. 수리 불가 후보는 측정 없이 이력에 기록하고 탈락.

### 3.3 Analyst — `analyst.analyze` (𝓕_t)

- analyst 는 **raw trace 를 직접 읽지 않는다**. 읽기 전용 digester 서브에이전트에 trace 하나씩 맡기고(`digest_many`, 호출당 ≤8 요청, 병렬 6), 구조화된 digest 를 모아 3-렌즈 보고서를 낸다. MAX_TURNS=30.
- 3-렌즈: `failure_modes`(점수를 잃게 한 차단 요인, 태스크에 걸쳐 군집) / `capability_gaps`(시도했지만 못 한 것) / `success_habits`(통과 태스크가 깔끔히 끝난 이유 — 라운드 간 유지되는 회귀 가드).
- 집계 규칙: 같은 메커니즘은 병합·다른 메커니즘은 분리, 총 영향(태스크 수 × 태스크당 잃은 점수)으로 순위, 설명은 엔티티 없는 태스크 무관 서술(증거 인용은 예외), 이전 이름 유지(안정 명명), **코드 변경을 처방하지 말고 모델 vs 하네스 탓을 하지 말 것**, 모순 시 후속 digest 후 한 번만 보고.

### 3.4 Digester — `digester.digest_task`

- 렌더된 trace 디렉터리에 갇힌 읽기 전용 도구(read_file, glob, grep, 허용 목록 bash: grep/head/tail/wc/cat/ls/find/cut/sort/uniq/awk/jq/sed(-i 금지)/tr/paste, 리다이렉션·`$( )`·rm/mv/cp/tee/python 금지). MAX_TURNS=15, digest ≤6,000자.
- 렌즈별 스키마: failure(`blocker, narrative, evidence[where,quote], verifier_evidence, capability_note, needed_instead`), capability_gap(`wanted, why_couldnt, evidence, workaround_seen`), success(`habits[habit, where_shown], risk_if_removed`).
- "채점/검증 섹션부터 읽고, 앵커를 grep 한 뒤 관련 조각만 읽어라. 파일을 통째로 읽지 마라." "인용은 정확하고 짧게, 모든 주장에 where, trace 가 보여 주는 것 이상 추측 금지."

### 3.5 LLM 클라이언트 — `llm.generate`

AnthropicVertex, GCP 프로젝트 라운드로빈 + 실패 시 재시도·회전(최대 6, 지수 backoff ≤30s), MAX_TOKENS=20,000, `json_only` 면 시스템 프롬프트에 "단일 JSON 객체만 출력" 접미사 + `extract_json`(코드펜스·앞뒤 잡문 제거). `cache_prefix` 는 ephemeral 캐시 블록. → 우리는 이 자리를 **기존 llm_client 다중 공급자 계층**으로 대체한다(12 문서).

---

## 4. 논문 ↔ 코드 차이 목록 (우리가 결정해야 할 것)

| # | 항목 | 논문 | 공식 코드 | 우리 결정(제안) |
|---|---|---|---|---|
| D1 | Ĉ 정의역 | `1/(k|D|) Σ_x Σ_j c(τ)` (모든 시행) | 토큰 > 0 인 시행만 평균, 누락 제외 | **코드 방식 채택 + 명시**. 누락 시행을 0 토큰으로 넣으면 크래시가 많은 후보가 싸 보인다. 누락률은 `invalid_missing_frac` 게이트로 따로 통제. 05 문서에 정의역을 수식으로 적는다 |
| D2 | Ŝ 가중치 | 가중 없음 | `Σ r·w / Σ w` (Harvey criteria 가중) | 일반화 채택(가중 1이면 Eq.3 과 동일). 검증기가 criteria 수를 함께 보고하게 한다 |
| D3 | ΔC 의 결측 | 정의 없음 | 어느 쪽이든 결측이면 0 | 채택. 단, 정책 토큰은 커널이 항상 계측하므로 결측은 인프라 오류로 기록 |
| D4 | w_s, w_c, w_n 값 | "Table 5 에 보고"라 했으나 없음 | rrsi.json: 코딩 0/15/0.5, workspace 1414/15/0.5, 엔지니어링 244/2/0.5. 기본값 100/15/0.5 | 인스턴스 설정으로 외부화. 코드 주석이 밝힌 단위: 엔지니어링 = pass 1개당 1 (= 244 per unit S), workspace = criterion 1개당 0.1 (≈ 1414 per unit S), 코딩 = 0(대역 안 점수 상승은 무가치). w_c 는 상대 비용 1 단위당. 우리 인스턴스는 "검증 단위 1개당 w_s" 로 정의하고 N(검증 단위 총수)으로 환산 |
| D5 | b_min 도달 | "final-round edit budget" | t=0..T−1 에서 미도달(천장) | 수식 그대로 구현하고, 문서·UI 에 "실제 마지막 라운드 예산"을 표시 |
| D6 | δ 추정법 | "base 하네스 반복 평가" | z=2 × sd(null ΔS), R≥2 직접/1회 bootstrap | 채택. R≥2 를 기본으로(정확), bootstrap 은 대체 경로 |
| D7 | argmax 동점 | 미정의 | 첫 변형 승 | **결정적 규칙 명시**: Ŝ 동점이면 Ĉ 작은 쪽, 그다음 편집 수 작은 쪽, 그다음 변형 라벨. Ridge 정신과 일치. *단, 논문 재현 모드에서는 코드 방식* |
| D8 | m (라운드당 후보 수) | m_t, Table 5 에 없음 | m=2 | 설정값. Dream-RSI 식 할당 정책의 대상(30 문서) |
| D9 | 측정 전 탈락 처리 | "유효 측정 전에 실패한 후보는 무시" | 이력에 delta_S=None 으로 기록, 𝒯_t·g_t 에서 제외, render 에서 최근 4개만 | 채택 |
| D10 | 태그 검증 | 언급 없음(태그만) | 선언 태그가 diff 증거 없으면 재분류 | 채택하되 **구성요소 타입 선언(manifest) 기반으로 정확 검증**(정규식 신호는 보조) |
| D11 | 예약 슬롯 위치 | m_draft 슬롯 | 뒤쪽 m_draft 개 변형 | 채택 |
| D12 | 수리 라운드 | 언급 없음 | critic reject → 최대 5회 수리 | 채택 |
| D13 | smoke | 언급 없음 | 평가 전 liveness(선택 규칙 아님) | 채택: 커널 계약 테스트 + 소수 태스크 |
| D14 | 누락 시행 게이트 | 언급 없음 | 누락 > frac×n_expected → 1회 재시도 → eval_invalid | 채택 |
| D15 | attribution | 언급 없음 | predicted_affected 적중률·예측 밖 퇴행 | 채택(신용 할당 보강) |
| D16 | g_t 창 경계 | `t − t_i ≤ n_prune` | 동일(포함) | 동일 |
| D17 | N_t(ℓ) | "라운드 t 이전 수락 레코드 수" | 현재 history 의 accepted 레코드(재판정 시 t_i<t 로 재계산) | 동일. 가지치기로 메커니즘을 제거해도 N_t 는 줄지 않는다(이력 기반) — 의도된 동작인지 논문 미명시, 우리도 이력 기반 유지 |
| D18 | Figure 2 라벨 | F "(L1-style)", G "(L0-style)" | config docstring "L1 cost rule" | 본문 정의(L0 예산 / L1 가지치기 / L2 비용 규칙)를 정본으로 |

---

## 5. 구조 기질(structural substrate) — `third_party/*/mechanisms.py`

코딩 base 하네스(Terminus-2)에 함께 들어 있는 **비활성 primitive**. import 만으로는 아무것도 바뀌지 않고, proposer 가 `terminus_2.py` 에 *배선*해야 레버가 켜진다. 그래야 𝒦_str 편집이 "실제 기계장치"로 diff 에 드러난다(태그 증거).

| 레버 | primitive | 요점 |
|---|---|---|
| skill | `upload_skills(environment, repo_skills_dir)` + `skills/<name>/SKILL.md` | YAML frontmatter(name, description) + 절차 본문. 카탈로그(name+description+위치)만 광고하고 정책이 `cat` 으로 전문을 끌어옴 = 점진 공개. "순수 텍스트 참조 스킬은 씻겨 나가기 쉽다 — 절차를 구체적·검증 가능하게, 또는 실행 가능한 helper 로 뒷받침" |
| memory | `Memory`: fcntl 잠금 host 측 JSONL, `semantic`/`episodic` | `_LEAKY` 정규식(`/app/`, `/tests?/`, `.py::`, expected, reference, answer[:=])에 걸리는 노트는 쓰기 거부, 중복 제거, 읽기는 항상 필터+limit. `digest()` 로 주입. **엔티티 없는 일반 절차만** |
| client_tool | `ToolRegistry.register/spec/call` | host 측 callable, 결과 4000자 절단, 예외는 문자열로(롤아웃을 죽이지 않음). 검증기 접근 금지 |
| subagent | `subcall(llm, brief, max_tokens=800)` | 같은 고정 정책에 대한 *한 번의* 제한된 추가 호출. "작은 정책에서는 서브콜이 해롭기 쉽다(조기 종료). 측정하라, 가정하지 마라." 오류 시 '' |

→ 우리 설계 시사점: **𝒦_str 각각에 대해 커널이 안전한 기질을 제공**하고, 하네스 편집은 그것을 "배선/설정"하는 형태가 되어야 한다. 그래야 (1) 누설·무한 루프 같은 위험이 기질 수준에서 막히고, (2) 편집이 원자적이며, (3) 태그 검증이 정확해진다.

---

## 6. 헌법(SKILL.md)과 패턴 라이브러리(PATTERNS.md) 요지 (코딩 인스턴스)

**"How your work is judged"** — 선택 규칙 전체(바닥, 이득 시 비용 규칙, 대역 안 규칙)와 두 정규화(편집 예산, 이력·탐색·가지치기)를 proposer 에게 그대로 설명한다. 보상 구조를 숨기지 않는다.

**과적합 함정** — "이 하네스는 *점수를 매기는 바로 그 태스크*로 진화된다. 그래서 태스크 특화 수정은 유혹적이면서 무가치하다(최종 판정은 held-out SWE-bench Verified)." 리트머스: "낯선 터미널 태스크를 *많이* 다루는 유능한 인간 운영자에게 도움이 되는가?"

**Hard rules (위반 시 자동 거부)**

1. 편집은 줄 수가 아니라 **독립성**으로 센다. 독립적으로 작동하고 "어떤 태스크를 뒤집을지" 스스로 답할 수 있는 변경 하나 = 편집 하나. 의존 부분은 한 편집. 같은 라운드 내 의존 사슬 금지. 큰 서브시스템은 *여러 라운드에 걸쳐* 짓되 가설에 "phase 1 of N" 을 선언.
2. 태스크 특화 내용 금지(이름, 파일명, 기대 출력, 숫자 정답, 태스크 식별 트리거). 오류의 *원인*과 일반 절차를 인코딩하고, 정답은 인코딩하지 않는다.
3. 검증기를 건드리지 마라(/tests, 테스트 파일, reward 경로, pytest 출력 참조 금지, 완료 신호 게이밍 금지).
4. 문구보다 메커니즘(제어 흐름, 출력 배관, 정보 라우팅, 상태). 프롬프트 편집은 메커니즘을 구현해야 한다(예: 계산된 컨텍스트 주입). 동기부여 문구는 아니다.
5. 계약을 깨지 마라(AgentHarness 클래스, BaseAgent API, trajectory 출력 스키마). 모델명·온도·타임아웃·동시성은 외부 주입.
6. 대체 없이 안전장치를 끄지 마라(요약, 출력 절단, 파싱 오류 복구, 완료 이중 확인).
7. 무인 견고성: 핫패스의 미처리 예외는 후보 하네스 전체를 무효로 만든다.
8. 종료를 위태롭게 하지 마라: 검사·철저함을 부추기는 메커니즘은 제한되어야 하고(예: "목표 검증 패스는 최대 1회") 완료가 기다려야 한다고 암시하면 안 된다.
9. 코드·주석·프롬프트·상태는 영어만.

**레버(행동 공간)** — 1 설정(기존 상수 조정) / 2 제어 흐름 / 3 프롬프트·템플릿 메커니즘 / 4 추가 모델 호출·서브에이전트 / 5 컨텍스트 관리 / 6 새 모듈, 그리고 구조 레버 7 skill / 8 memory / 9 client_tool / 10 subagent. "증거에 레버를 맞추고, 패턴을 고치는 가장 작은 움직임을 선호하라. 병렬 구성요소를 추가하기보다 기존 구성요소를 편집하라."

**제안 규율** — retroactive check 3부(교정·보존·이전), predicted_affected 필수(scoreboard 로 적중 확인), "지식을 주입하기보다 메커니즘을 고쳐라".

**PATTERNS.md (코딩)** — 1 대기/폴링, 2 출력 배관, 3 워크플로/상태 기계(지속성 없음), 4 파싱 오류 복구, 5 컨텍스트 예산, 6 추가 정책 호출. 각 항목에 **일반 함정**(무한 폴링, 캡 전면 확대, 무한 재확인 루프, JSON 조용한 수정, 요약의 요약으로 인한 컨텍스트 붕괴, 서브콜 지연)이 붙어 있다.

→ 우리 설계 시사점: 헌법·패턴은 **도메인(인스턴스) 데이터**다. XGEN 용 헌법(예: "Agent-XGeny 업무 에이전트", "문서 검토", "코드 샌드박스")을 각각 작성해야 하며, 계약 조항(hard rule 5)은 **우리의 I/O 계약(11 문서)** 을 그대로 옮긴다.

---

## 7. 그대로 가져올 것 / 바꿀 것

**그대로(수식·의미 보존)**: `edit_budget`(round(v,9) 포함), `aggregate`(가중·누락=0), `relative_cost_change`, `History` 레코드 스키마와 요약 함수, `stall_flag`, `exploration`, `novelty`, `cost_rule`, `judge`/`select_round`(판정 순서), `calibrate`(z·sd null, bootstrap), 재판정·재평가, 예약 슬롯, 수리 라운드, smoke, 누락 게이트, attribution, 헌법·critic 규칙의 내용.

**바꿀 것**:
- LLM 호출 → 기존 `llm_client` 다중 공급자(역할별 모델 지정: proposer/analyst/critic/digester).
- git worktree 기반 후보 격리는 유지하되, 하네스를 **타입 선언된 구성요소 패키지**로 만들어 태그 검증·diff 범위를 정확히(정규식 신호는 보조).
- Domain 인터페이스 → XGEN 평가 도메인(검증기·trace 렌더러·가드·헌법)으로 구현.
- 단일 CLI 프로세스 → 장기 실행 작업(재개 가능) + 운영 배포 게이트(사람 승인)와 결합.
- 동점 규칙, Ĉ 정의역, 하이퍼파라미터 출처를 명문화.
