# Changelog

geny-rsi(`xgen-agent-runtime-rsi`)의 변경 기록. 0.11.0 부터 적는다. 그 전 판의 내용은 git 기록과 [README](README.md) 의 구현 상태 표에 있다.

## [0.11.0] - 2026-10-12

### Changed: 바탕 런타임을 xgen-agent-runtime 4.89.0 으로 (압축 정책 포함)

geny-rsi 는 runtime 을 `xgen_rsi.base` 로 복사해 쓰고, 한 턴을 도는 harness pipeline 만 자기 것(커널 + 구성요소)이다.
사본을 4.81.0 에서 4.89.0 으로 올리면서, runtime 이 4.82~4.89 에서 턴 조립(`host/turn_executor.py`·`host/runner.py`)과
21-stage 의 Stage 2 · 4 · 6 에 넣은 동작을 geny-rsi 의 조립 사본과 커널 · 구성요소로 옮겼다. 두 엔진에 같은 턴을 돌려 스트림 ·
usage · 모델 요청이 같은지 보는 동등성 시험(`tests/kernel/test_equivalence.py`)이 이번 판의 기준이다. 시험의 판정은 그대로 두고
4.89 의 경우(넘침 복구, 가드 압축, 메모리 노드 문맥 등)를 더했다.

- **사본** (`xgen_rsi.base`, `tools/sync_base.py`): 522개 파일, 기록된 수정 3건(버전 표기 · 머리말 · docstring 경로). 4.82~4.89 의
  요소 계층 변경(기기 폴더 복사 도구, 사용자 PC 접속 `UserPc`, Read 가 그림을 건네는 것, 도구 결과의 토큰 상한, 보조 호출 사용량,
  도구 호출의 능력 · 출처, 압축 재설계, 기억 기록의 압축 대비)은 사본에서 그대로 온다.
- **턴 조립** (`xgen_rsi.assembly`): 4.89.0 의 `AgentTurnExecutor.run` 과 같은 동작이다.
  - 호스트가 `user_pc_connection()` 을 주면 연결 폴더는 `UserPc` 하나로 닿고 기기의 폴더 도구는 보이지 않는다(4.85).
  - 이 턴의 모델이 그림을 보는지(`MODEL_VISION_KEY`)를 SDK 도구 컨텍스트와 CLI 도구 표면의 컨텍스트에 싣는다(4.86).
  - 진행 메모까지 사용자 언어로 쓰라는 원칙(`USER_LANGUAGE_PROMPT_BLOCK`)을 효율 원칙 뒤에 제 조각 `user_language` 로 붙인다.
    조각을 이은 시스템 프롬프트는 runtime 과 같다(4.87.1).
  - 호스트의 `turn_notes()` 훅이 준 글을 이번 턴 안내로 붙인다(4.88).
  - 입력 맞춤(4.89): CLI 백엔드도 창을 해석한다. 메모리 노드가 보낸 이전 대화 문자열(`history_context`)을 창의 몫 안으로 줄이고
    입력 맞춤에서 한 번 더 줄인다. 맞춤 뒤에 붙는 몫(내장 메모리 주입 예약, 날짜 줄 200, claude_code 는 12,000 +
    max(0, 32,000 - max_tokens))과 숨긴 도구 목록(CLI 가 아닐 때) · 이번 턴 안내를 함께 센다. CLI 백엔드는 가장 오래된 이력부터
    빼고 STM · 대화 아카이브 워터마크를 뺀 수만큼 내린다. "입력 일부를 생략" 안내는 사용자 입력이 잘렸을 때만 나간다.
  - 맞춘 이전 대화는 CLI 백엔드면 이번 턴 안내로, API 백엔드면 시스템 프롬프트 끝의 제 조각 `history_context` 로 간다.
- **커널** (`kernel/model_call.py`, `kernel/engine.py`): runtime 4.89 의 Stage 6 와 같다.
  - 턴 기록으로 지은 요청을 공급자가 `TOKEN_LIMIT` 로 거절하면 엔진이 넘긴 복구 훅(context 구성요소의 `recover_overflow`)을
    부르고, True 면 스트림 재시작을 알린 뒤 요청을 턴 기록에서 다시 지어 한 번만 다시 보낸다. 다시 보낸 요청이 또 거절되면
    `api.error` 를 한 번 내고 그대로 올린다. `api.request` 는 한 번이다.
  - 성공한 호출 뒤 압축 정책의 호출 계수를 올리고(`note_api_call`, 턴 기록 위의 요청), 턴 기록 그대로의 요청이면 공급자가 센 입력
    토큰을 추정의 기준점으로 남긴다(`record_prompt_usage`, 어시스턴트 메시지를 붙이기 전). 다른 재료를 명시해 부른 호출은 둘 다
    하지 않는다.
  - `tool_call` 사건에 도구의 `capabilities` 와 `origin` 을 싣는다(4.88).
- **context 구성요소** (`components/context.py`): runtime 4.89 의 압축 정책(`core.compaction`)을 따른다.
  - 문턱은 `soft_level`: 압축이 줄일 수 없는 몫(시스템 프롬프트 · 도구 · 턴 맥락 · 지금 요청의 첨부)에, 입력 예산이 그 뒤에 남기는
    몫의 비율을 더한 것이다. 예전에는 창 전체의 비율이었다.
  - 호출 전(Stage 2): 비용 트리거 정리(`inputs_trimmed` 도 센다), 압축 요청(`/compact` 는 `manual`, 나머지는 `requested`), 약한 압박
    (`proactive_ratio` 를 넘으면 `target_ratio` 까지).
  - 예산 가드(Stage 4): 같은 약한 압박 점검을 한 번 더 한 뒤 `TokenBudgetGuard()` 기본값(점검마다 정책의 답 몫을 계산)으로 재고,
    모자라면 `guard.compacting` 을 내고 강하게 압축해 한 번 다시 잰다. 그래도 안 되면 거절한다.
  - 넘침 복구(`recover_overflow`): 공급자가 센 토큰으로 보정하고, 공급자가 더 작은 창을 말하면 이 턴의 창을 그 값으로 낮추고,
    `context.overflow` 를 낸다. 보고된 크기가 이미 목표 아래면(답 몫 때문에 넘친 경우) 압축하지 않는다. 압축이 꺼진 턴은 False.
- **진화할 수 있는 것** (`harnesses/h0/manifest.json`, 제안자 헌법): `proactive_ratio` 는 0.5~0.9, `target_ratio` 는
  0.2~(`proactive_ratio` - 0.1) 로 묶는다(소수 6자리). 숫자가 아니거나 불리언 · NaN 이면 기본값(0.8, 0.5)이다. 가드는 더 끌 수 없고
  `guard` · `guard_min_headroom` · `guard_extra` 는 읽지 않는다. 넘침 복구도 고칠 수 없다. H0 의 `target_ratio` 는 0.7 에서 0.5 로
  바뀌고 가드 파라미터는 빠졌다. 그래서 H0 의 버전이 바뀐다. 예전 H0 에서 진화한 하네스에 남은 가드 파라미터는 무시되고(로드는
  된다), 그 하네스의 `target_ratio` 0.7 은 범위 안이라 그대로 쓰인다.
- **memory 구성요소** (`components/memory.py`): 검색기가 저장소에 붙은 hooks 를 쓰고, 주입 상한은 창의 10% 로 묶는다(4.88~4.89 의
  `build_pipeline` 과 같다).
- **하네스 어휘** (`harness/kinds.py`): `ContextComponent` 는 그대로 두고, 선택 프로토콜 `OverflowRecovery` 를 더했다(`PROTOCOLS`
  에는 없다). 커널은 context 구성요소에 `recover_overflow` 가 있을 때만 부른다.
- **의존성 하한**: 사본이 고정하는 판에 맞춰 `xgen-doc2chunk>=0.5.0`, `asyncssh>=2.24.1`.
- `xgen_rsi.__version__` 이 0.9.0 에 머물러 있던 것을 고쳤다.
