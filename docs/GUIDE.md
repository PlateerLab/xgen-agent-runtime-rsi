# geny-rsi 사용 가이드

[README](../README.md)의 빠른 시작보다 자세한 사용법이다. 라이브러리 API, XGEN 서버 설정, 하네스 형식, 평가·진화·탐색 명령을 다룬다.

---

## 1. 라이브러리 API — `GenyRSI`

`xgen-agent-runtime`의 `PipelinePresets`와 같은 모양이다. 턴 하나는 운영과 **같은 경로**로 돈다. 즉 `GenyRSITurnExecutor().run(host, **kwargs)`로 턴을 조립한 뒤 이 패키지의 실행 코어로 돈다. 호스트는 `LocalHost`이고, 작업 공간·내장 도구·자격증명을 이 프로세스 안에서 제공한다. 이 패키지는 xgen-agent-runtime 을 import 하지 않는다. 바탕 런타임은 4.80.0 사본 `xgen_rsi.base` 다.

```python
from xgen_rsi import GenyRSI

# 질문 하나 → 답 하나
agent = GenyRSI.minimal(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-...")
result = await agent.run("What is the capital of France?")       # 동기: agent.run_sync(...)
print(result.text, result.input_tokens, result.output_tokens)

# 대화 이력 유지 + 시스템 프롬프트
chat = GenyRSI.chat(provider="openai", model="gpt-6-sol", api_key="sk-...", system_prompt="Be concise.")
chat.run_sync("hello"); chat.run_sync("what did I just say?")

# 작업 공간 + 내장 파일 도구(Read·Write·Edit·Glob·Grep) + 내 도구(xgen_agent_runtime.tools.Tool 또는 {"name","func"} dict)
worker = GenyRSI.agent(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-...",
                       workspace="./ws", tools=[MyTool()], max_iterations=20)
for chunk in worker.stream_sync("Summarize report.md into summary.json"):   # 비동기: async for c in worker.stream(...)
    print(chunk, end="")
print(worker.last_result.usage)

# 진화된 하네스로 돌리기 / 같은 호출로 기존 엔진(A/B)
evolved = GenyRSI.agent(..., harness="./H_star")       # rsi evolve export 로 꺼낸 디렉터리
baseline = GenyRSI.agent(..., engine="geny")           # 21-stage 엔진(geny, xgen_rsi.base 사본)
```

| 인자 | 뜻 |
|---|---|
| `provider`, `model`, `api_key`, `base_url`, `credentials` | 정책 π. 런타임이 지원하는 모든 SDK 공급자를 쓸 수 있다(anthropic·openai·google·vllm·bedrock·vertex·azure …) |
| `engine` | `"geny-rsi"`(기본, 이 패키지의 커널 + 하네스) 또는 `"geny"`(기존 21-stage) |
| `harness` | 하네스 디렉터리(기본: 내장 H0) |
| `system_prompt`, `tools`, `builtin_tools`, `workspace` | 턴 구성(`agent()` 는 내장 파일 도구 5개를 켠다) |
| `keep_history` | 객체에 대화 이력을 남겨 다음 턴에 넘긴다(`chat()`·`agent()` 는 켠다) |
| `max_iterations`, `max_tokens`, `temperature`, `thinking`, `output_schema` | 턴 설정(런타임 kwargs 그대로) |
| `record_dir` | 궤적 기록 위치 — `result.record` 에 그 턴의 TrajectoryRecord(정책 토큰 c(τ), 발견 트리)가 실린다 |

`RunResult`: `text`, `engine`, `usage`(입력·출력·캐시 토큰, 호출 수, 비용), `harness`(`name@sha256:…`), `record`, `failed`.

---

## 2. 호스트(XGEN 서버 등)에서 — 진입점 하나

xgen-agent-runtime 과 이 패키지는 서로 의존하지 않는다. 호스트가 둘을 각각 import 하고, 턴을 실행하는 자리에서 클래스를 고른다.
두 진입점은 같은 계약이다 — `run(host, **kwargs)`, `streaming=True` 면 글 조각·사건 dict 이터레이터, `False` 면 최종 글, 조립 실패는
`"[ERROR] geny agent could not start: …"` 출력. 호스트가 턴에 넘기는 도구·기억 객체는 그 에이전트의 패키지(이 패키지는
`xgen_rsi.base`)로 만든다 — `Tool`·`ToolRegistry`·기억 공급자 클래스는 두 패키지에서 서로 다른 클래스다.

```python
from xgen_agent_runtime.host.turn_executor import AgentTurnExecutor   # geny (기존 21-stage)
from xgen_rsi import GenyRSITurnExecutor                             # geny-rsi

executor = GenyRSITurnExecutor() if use_geny_rsi else AgentTurnExecutor()
out = executor.run(host, **kwargs)        # host = HostServices, kwargs = 노드 입력 그대로
```

턴 조립(호스트 계약)은 `xgen_rsi.assembly` 가 맡는다 — 런타임의 조립과 같은 동작의 자체 사본이고, 두 엔진이 같은 요청을
보내는지는 동등성 테스트(각본 모델)와 재생 실험(실제 모델 응답)이 잰다. 공급자 계층·도구·계약 모듈은 사본 `xgen_rsi.base` 다.

geny-rsi 가 쓰는 호스트 훅(선택 — 없으면 그 기능만 꺼진다):

| 훅 | 뜻 |
|---|---|
| `rsi_agent_harness()` | 이 턴 에이전트의 **현재 하네스 페이로드**(`{"manifest", "files", "version"}`, `xgen_rsi.harness.payload`). None 이면 H0. 진화로 채택된 하네스를 호스트가 저장해 두고 돌려준다 |
| `rsi_record(record)` | 턴마다 궤적 요약 dict(하네스 버전·계보·상태·종료 사유·도구 수·정책 토큰·읽힌 파라미터)를 받는다 — 에이전트별 사용 기록 |

호스트 설정(`host.setting`):

| 설정 | 뜻 |
|---|---|
| `XGEN_RSI_HARNESS_DIR` | 모든 에이전트를 하네스 하나로 고정(관리자). 디렉터리 경로 또는 `builtin:h0`. 에이전트 하네스보다 먼저다 |
| `XGEN_RSI_LINEAGE_FILE` | 정책 계열 → 하네스 디렉터리 표(실험·재현용, `{"lineages": {"default": "...", "openai": "..."}}`) |
| (아무것도 없음) | 에이전트 하네스(훅) → 패키지 계열 표(H0 만) → 내장 H0 |
| `XGEN_RSI_HARNESS_CACHE` | 에이전트 하네스 페이로드를 버전별로 풀어 둘 디렉터리(없으면 임시 디렉터리) |
| `XGEN_RSI_RECORD_DIR` | 궤적 기록을 파일로도 남길 위치 |
| `XGEN_RSI_RECORD_CONTENT` | 기록에 전사·최종 글 포함(평가용 — 운영 기본 끔, 개인정보) |

되돌리기: 호스트가 xgen-agent-runtime 의 `AgentTurnExecutor` 로 돌아가면 된다(두 패키지는 서로 영향을 주지 않는다). 진화 중단은 `STOP` 파일.

---

## 2-1. 에이전트 진화 — 호스트가 에이전트마다 부르는 API

설계: [설계 40](design/40-agent-self-evolution.md). 에이전트의 사용 기록을 과제로 만들고, 그 에이전트의 현재 하네스에서 RRSI 를 돌려 채택된
하네스를 돌려준다. 채택은 RRSI 가 정한다(이 API 는 판정을 바꾸지 않는다).

```python
from xgen_rsi.agent_evolution import AgentEvolution
from xgen_rsi.evolve.runner import PolicySpec
from xgen_rsi.roles.llm import RoleModel
from xgen_rsi.usage import AgentSettings, UsageItem

usage = [UsageItem(id="io-123", request="...", answer="...", history=[...],
                   stars=1, issue="데이터 오류", comment="...", expected="", criteria=[])]
evo = AgentEvolution(work_dir, policy=PolicySpec(provider="openai", model="gpt-6-sol", api_key=key),
                     roles={r: RoleModel(provider=..., model=..., api_key=...) for r in
                            ("proposer", "critic", "analyst", "digester", "judge")},
                     agent=AgentSettings(system_prompt=node_system_prompt), T=3, k=2)
result = evo.run(usage, current=agent_current_payload_or_None)
if result.adopted:
    store(result.payload)      # 다음 턴부터 rsi_agent_harness() 가 이것을 돌려준다
```

| 단계 | 모듈 | 하는 일 |
|---|---|---|
| 과제 | `xgen_rsi.usage` | 기대 답 → "기대 답과 내용이 같다", 낮은 별점·이슈 → "보고된 문제가 없어야 한다", 높은 별점 → "받아들여진 답의 요점을 지킨다", 사용자 기준 그대로. 신호 없는 턴은 빠진다. 해시로 evolve/heldout |
| 판정 | `answer_criteria` 검사 + `xgen_rsi.evolve.judge.CriteriaJudge` | 판정 모델이 답이 기준을 만족하는지 본다. 하네스 밖(하네스는 기준을 보지 못한다). 같은 답은 한 번만 묻는다 |
| 진화 | `xgen_rsi.agent_evolution.AgentEvolution` | 기준선 2회 → δ → RRSI 라운드(가드 그대로) → 채택 페이로드 + 라운드별 판정 + heldout 측정(시작 vs 채택) |

- 평가는 과제마다 독립 작업 공간에서 `AgentSettings.toolset`(기본 파일 도구)으로 돈다 — 사용자의 기억·파일·외부 시스템을 건드리지 않는다.
- 자격증명은 객체로만 받고 디스크에 쓰지 않는다. `work_dir` 하나가 실행 하나이고, 같은 디렉터리로 다시 부르면 이어서 돈다. `evo.stop()` 은
  다음 라운드 전에 멈춘다.
- 과제가 모자라면(`min_evolve`, 기본 4) `status="not_enough_usage"` 로 끝난다.

---

## 3. 하네스 패키지

```json
{
  "schema": "xgen-rsi-harness/1",
  "name": "h0",
  "enabled_kinds": ["prompt", "control_flow", "config", "output_plumbing", "context_mgmt", "client_tool", "skill", "memory"],
  "locked": ["*.params.model", "*.params.provider", "*.params.credentials", "*.params.max_iterations"],
  "components": [
    {"id": "prompt.system", "kind": "prompt", "impl": "xgen_rsi.components.prompt:SystemPromptComponent",
     "params": {"part_overrides": {}, "extra_blocks": [], "part_order": [], "datetime": true}},
    {"id": "context.standard", "kind": "context_mgmt", "impl": "xgen_rsi.components.context:StandardContextComponent",
     "params": {"prune_over_tokens": 30000, "proactive_ratio": 0.8, "target_ratio": 0.7}},
    {"id": "skill.library", "kind": "skill", "impl": "xgen_rsi.components.skills:SkillLibraryComponent",
     "files": ["skills/csv-check/SKILL.md"]}
  ]
}
```

- **편집 주소** `<component_id>.params.<key>` · `<component_id>.files.<path>` — 원자 편집의 대상. 편집이 건드린 주소의 구성요소 kind 가 RRSI 의 태그 ℓ 이다(정규식 추측이 아니라 선언 대조).
- **버전** = manifest 정규형 + 참조 파일 내용의 sha256. 같은 내용이면 같은 버전.
- **잠금** — 정책 π·커널 한도는 하네스가 바꿀 수 없다(RRSI 헌법 hard rule 5 의 구조화).
- **H0** = 기존 운영 경로(런타임 4.75)의 동작을 구성요소로 옮긴 시작 하네스. RRSI 는 H0 대비로 모든 것을 잰다.
- **구조 레버(𝒦_str)**: `skill`(SKILL.md 카탈로그 + `ReadSkill` 점진 공개), `client_tool`(노출·실행 정책), `memory`(기억 정책).
- `rsi harness show [DIR]` · `rsi harness validate [DIR]` · `rsi harness diff A B`.

**패키지에 든 하네스** (`src/xgen_rsi/harnesses/`). **H0 하나뿐**이고 계열 표는 `{"default": "h0"}` 다 — 어떤 모델의 에이전트든 H0 에서 시작한다.
운영 하네스는 XGEN 안에서 에이전트마다 그 사용으로 진화한다([설계 40](design/40-agent-self-evolution.md)). 실험에서 `rsi evolve export` 로 꺼낸
하네스는 그 실험 스위트에 맞춘 것이라 패키지에 넣지 않는다 — 재현·비교할 때 `XGEN_RSI_HARNESS_DIR` 로 고정해 쓴다.

---

## 4. 평가

```bash
# 내장 스위트: xgen-core(규칙 하나짜리 업무 과제) · xgen-hard(양·규칙 문서·함정) · xgen-pro(천장 아래 모델용: 긴 문맥·대량 출력·
# 여러 파일 편집·겹친 규정·제약 퍼즐·도구 검색·검증) — 각 8범주 × 4, evolve 24 · heldout 8 · smoke 2
rsi suite build ./suites/xgen-pro --suite xgen-pro

# 하네스 하나를 평가 (정책 = provider·model·키를 담은 JSON, 저장소 밖에 둔다)
rsi eval --harness src/xgen_rsi/harnesses/h0 --suite ./suites/xgen-pro --split evolve --k 2 \
         --policy policy.json --out runs/h0-evolve [--engine geny-rsi|geny]
```

- 평가는 **실제 엔진·실제 호스트 프로토콜**로 돈다(`EvalHost`: 시행마다 독립 작업 공간, 파일 도구는 그 안으로만, 시작 파일의 수정 시각 고정).
- 검증기는 결정적이고 하네스 밖에 있다. 보상 = 통과 검사 수 / 전체, 가중치 = 검사 수.
- 모든 과제는 **오라클(정답 풀이)이 만점, 빈 답이 0.5 이하**임을 테스트로 고정한다 — 검증기 자체를 검증한다.
- 누락(인프라 실패·재시도를 다 쓴 공급자 오류)은 r 0·분모 포함이고 유효성 게이트·재측정이 본다. 누락 시행의 토큰은 비용에서 뺀다.
- 시행 순서는 고정 난수로 섞고, 바닥에 못 미침이 **확정**되면 남은 시행을 돌리지 않는다(정확 경계 조기 종료 — 판정 불변).

---

## 5. RRSI 하네스 진화 (L1)

```bash
rsi evolve init runs/evo --suite ./suites/xgen-pro --policy policy.json --config rrsi.json
rsi evolve run runs/evo                 # baseline(H0) → δ 보정(반복 평가 2회) → 라운드 0..T-1 (STOP 파일로 멈춤)
rsi evolve status runs/evo
rsi evolve heldout runs/evo final       # 보류 분할 — 보고 전용, 어떤 판정에도 쓰지 않는다
rsi evolve export runs/evo ./H_star     # 채택된 하네스를 디렉터리로 → XGEN_RSI_HARNESS_DIR 또는 GenyRSI(harness=...)
rsi evolve readjudicate runs/evo 3      # 저장된 측정으로 라운드 3 재판정(평가 0 회)
```

`rrsi.json`(키가 들어가니 저장소 밖에): RRSIParams(T, k, m, b_min, b_max, w, m_draft, β0, β1, w_s, w_c, w_n, …), 엔지니어링 값, 역할 모델.

```json
{"T": 6, "k": 2, "m": 2, "mode": "xgen", "n_fail_traces": 10, "n_success_traces": 4,
 "roles": {"proposer": {"provider": "anthropic", "model": "claude-sonnet-5", "api_key": "..."},
           "critic": {"...": "..."}, "analyst": {"...": "..."}, "digester": {"...": "..."}}}
```

라운드 하나(Algorithm 1·2)의 흐름:
1. 분석: digester → analyst. 실패 양상·능력 공백·성공 습관을 뽑는다.
2. b_t·σ_t·𝒯_t·𝒰_t·𝓑_t 를 계산한다.
3. 후보 m 개를 제안한다. 엄격 JSON 행동, 편집 수 ≤ b_t, 예약 탐색 슬롯.
4. 누설 심사: 결정적 사전 검사 + LLM 10규칙, 수리 라운드.
5. 건드린 주소로 태그를 정규화한다.
6. 스모크 → 평가(정확 경계 조기 종료).
7. `select_round`: 바닥·비용 규칙·띠 안 규칙·가드.
8. frontier 를 먼저 쓰고 브랜치를 옮긴다.

하네스 버전마다 git 커밋이 남고, `history.jsonl`·`decisions.json`·`attribution.jsonl` 로 모든 판정을 다시 계산할 수 있다. 커밋 신원은 `XGEN_RSI_GIT_AUTHOR_NAME/EMAIL` 로 바꾼다.

---

## 6. 라이브 탐색과 Dream-RSI 사이클 (L2)

```bash
# 검증기가 있는 과제를 branch × attempt 격자로 실제로 푼다 — 셀 하나 = 에이전트 턴 하나
rsi dream explore runs/pool --suite ./suites/xgen-pro --policy policy.json \
                  --explorer builtin:portfolio --iteration 0 --branches 3 --refine 2 --W 3

# 탐색 트리 + 평가 시행 → 재생 world 풀
rsi dream build-worlds runs/worlds.json --trace-pool runs/pool --eval-job h0=runs/evo/jobs/base --suite ./suites/xgen-pro

# 사이클: π^0..π^{M−1} 개발·재생 평가 → argmax V → 온라인 확인(RRSI 판정) → promoted_policy.py
rsi dream cycle runs/cycle1 --worlds runs/worlds.json --incumbent builtin:parallel_refine --iteration 1 \
                --llm policy_dev.json --trace-pool runs/pool \
                --confirm-suite ./suites/xgen-hard --policy policy.json --delta 0.02
rsi dream status runs/cycle1
```

- 정제 셀은 부모 셀의 작업 공간을 복사해 이어서 풀고, 부모의 점수와 실패한 검사 이름·사유를 피드백으로 받는다. 루트 r 의 점수는 시작 작업 공간 그대로를 같은 검증기로 채점한 값이다.
- 같은 과제는 라이브와 재생에서 **같은 관측 매핑**으로 보인다(부분 점수도 점수 — 검사 가중 보상이므로).
- 탐색 정책 코드는 **별도 프로세스**(CPU 시간·메모리 한도) + 제한된 namespace(허용 import 목록, 결정성, 시간 제한, 접두부 밖 정보 접근 차단)에서만 돈다.
- 재생 승자는 같은 과제를 실제로 탐색해 `rsi_math.judge`(측정 유효성 + 바닥 Eq.5 + 비용 규칙 Eq.7/17)를 통과해야 승격된다.
- 운영 대화 턴은 검증기가 없어 π_E 를 쓰지 않는다.

---

## 7. 바탕 런타임 사본(`xgen_rsi.base`) 갱신

geny-rsi 와 geny 의 차이는 harness pipeline 하나여야 한다. 그래서 기억·도구·작업·앱·스토리지·자기 진화 같은 요소 계층은
xgen-agent-runtime 의 코드를 그대로 복사해 쓰고(`xgen_rsi.base`), 원본 버전과 파일별 해시를 `src/xgen_rsi/base/COPY.json` 에 둔다.
`tests/test_base_copy.py` 가 사본이 그 기록과 같은지 본다 — 사본을 손으로 고치면 실패한다.

```bash
python tools/sync_base.py /path/to/xgen-agent-runtime/src/xgen_agent_runtime 4.81.0   # 새 버전으로 복사 + COPY.json
python tools/sync_base.py --check                                                      # 사본이 기록과 같은가
```

사본에서 꼭 고쳐야 하는 곳은 `tools/sync_base.py` 의 `LOCAL_EDITS` 에 이유와 함께 적는다(지금은 버전 표기·머리말·docstring 경로 세 곳).
runtime 을 올릴 때는 복사 → 전체 테스트(동등성 포함) → 릴리스 순서다. XGEN 은 두 패키지를 각각 같은 버전대로 올린다.

## 8. 실험 재현

`experiments/` 의 스크립트가 보고서의 모든 수치를 만든다(정책 JSON 은 저장소 밖에).

| 스크립트 | 실험 |
|---|---|
| `compare_engines.py` | 같은 정책·과제·검증기로 geny vs geny-rsi(H0) |
| `replay_equivalence.py` | 실제 모델 응답을 녹음해 두 엔진에 다시 먹이고 모든 호출의 요청을 비교(결정적 동등성) |
| `dream_experiment.sh` | 라이브 탐색(π₁ vs portfolio) → world 풀 → 정책 개발 사이클 → 온라인 확인 |
| `summarize.py` | `runs/` → 보고서 표(JSON, 대화 내용 제외) |
