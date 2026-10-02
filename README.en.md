# geny-rsi — Geny + RRSI + Dream-RSI

[![GitHub release](https://img.shields.io/github/v/release/PlateerLab/xgen-agent-runtime-rsi)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml)

**geny-rsi** is XGEN's second agent runtime. In XGEN it is used as **Agent Geny RSI** (`agents/geny-rsi`).

- **Geny base** — it carries a copy of the element layer of the existing runtime geny (xgen-agent-runtime). Providers, tools, memory, tasks,
  apps, storage, self-evolution and the host contract are the same as Agent Geny. It is an independent package: it neither imports nor depends
  on xgen-agent-runtime.
- **RRSI** — it splits the harness pipeline that runs one turn into a fixed kernel and an editable harness, and evolves the harness by
  measurement. **Every Agent Geny RSI starts from the default of the RSI pipeline (H0) and evolves its own harness from how its users use it
  (conversations, feedback, expected answers).**
- **Dream-RSI** — in exploration that tries several attempts at a task with a verifier, it evolves the exploration policy (how many branches,
  when to stop) by replaying recorded history.

The two agents differ in **one thing: the harness pipeline**. RSI's strength is fitting the user. Even with the same model, each agent has its own
work, users and material, so the right harness differs too. The package therefore ships H0 only, and the harness evolves per agent inside XGEN
([design 40, Korean](docs/design/40-agent-self-evolution.md)). The methodology experiments and the implementation review of the two runtimes are
in one [**comparison report (Korean)**](docs/reports/geny-vs-geny-rsi.md).

[한국어](README.md) · [**Comparison report (Korean)**](docs/reports/geny-vs-geny-rsi.md) · [Usage guide (Korean)](docs/GUIDE.md) · [Design docs (Korean)](docs/README.md) · [Plan (Korean)](docs/PLAN.md)

---

## Agent Geny and Agent Geny RSI

| | **Agent Geny** (`agents/geny`) | **Agent Geny RSI** (`agents/geny-rsi`) |
|---|---|---|
| Runtime · package | geny · xgen-agent-runtime | geny-rsi · xgen-agent-runtime-rsi. **Neither imports nor depends on the other** |
| Entry point | `AgentTurnExecutor().run(host, **kwargs)` | `GenyRSITurnExecutor().run(host, **kwargs)` — the same contract |
| Ports, settings, credentials, model | the same | the same |
| Memory, tasks, tools, apps, storage, evolution history | runtime's element layer | **the same code** — runtime 4.81.0 copied as `xgen_rsi.base` (file hashes in `COPY.json`, checked by tests) |
| **Harness pipeline** | the 21-stage engine. People measure it and change it by release | fixed kernel K₀ + harness H. **Starts from H0 and is evolved by RRSI from that agent's use** (each agent has its own harness) |
| Exploration policy | none | π_E, evolved by **Dream-RSI** (used in exploration with a verifier) |

```
agent A = (π, K₀, H, π_E)
  π    fixed policy        the provider and model the user picked                        the same for both agents
  K₀   fixed kernel        I/O contract · provider gateway · usage ledger · tool permissions · limits · records   not editable by the harness
  H    harness             content-addressed package of 𝒦-typed components (9 kinds)    starts at H0, evolved per agent by RRSI
  π_E  exploration policy  code that decides branching, batching and stopping            evolved by Dream-RSI
```

The XGEN server handles everything tied to an agent **through that agent's package**: conversation turns, the [Memory], [Tasks], [Tools],
[Apps], [Storage] and [Evolution history] screen APIs, scheduled jobs, frozen copies and clones. An Agent Geny RSI's data is handled by rsi code
from start to end, and the two agents differ only in the harness pipeline. Platform features that belong to no agent (personal SSH connection
tests, the general LLM service, and so on) use XGEN's default package (runtime).

**Which to use**

- **Agent Geny** — when the harness should change only through runtime releases that people changed and verified.
- **Agent Geny RSI** — when the harness should evolve to fit how that agent is used. It first runs on H0 (which sends the same requests as Agent
  Geny, measured by replay equivalence), and only changes that evolution adopts accumulate on that agent. Every decision is recorded and can be
  rolled back.

---

## Each agent has its own harness — self-evolution inside XGEN

```
user ──turn──► Agent Geny RSI ── kernel + [this agent's current harness H_t] ──► answer     H0 at first
                    │ trajectory summary (harness version, termination, tools, cost)
                    ▼
               [usage records] ◄── feedback (stars, issue, comment) · expected answers (quality evaluation) · tasks added by hand
                    ▼ turned into tasks with judgement criteria
               [this agent's tasks] evolve / heldout
                    ▼ evolution run (started by the user, or when tasks accumulate)
               RRSI rounds: analyse → propose → review → evaluate → judge (δ, cost rule, guards)
                    ▼ adopt
               [this agent's harness lineage] H0 → H1 → …          H_{t+1} from the next turn, roll back at any time
```

- **The harness belongs to the agent.** The package ships H0 only. It does not ship harnesses fitted to a model or a suite; harnesses evolved in
  experiments are fitted to those experiment suites and are not used in production.
- **The material is that agent's use.** The turn records XGEN already keeps (`execution_io`), user feedback (`user_feedbacks`) and expected answers
  (quality evaluation) become tasks. Judgement uses deterministic checks and criteria judged by a judge model (`answer_criteria`). Judgement sits
  outside the harness.
- **Evaluation does not touch the user's data or external systems.** Each task runs in its own workspace with that agent's settings, and tools with
  side effects are blocked.
- **One agent's evolution applies only to that agent.** Clones and frozen copies take the harness lineage with them.

**Implementation status** ([design 40](docs/design/40-agent-self-evolution.md) §4)

| Stage | Content | Status |
|---|---|---|
| 1 | Ship H0 only, fix parity defects between the two runtimes, pin in XGEN | 0.6.0 |
| 2 | Agent harness and trajectory host hooks, criteria checks, usage records → tasks, agent evolution API | in progress |
| 3 | XGEN storage, hooks, feedback → tasks, evolution worker, API | in progress |
| 4 | [Harness] tab in the Agent Geny RSI detail view | in progress |

An administrator can pin every agent to one harness with `XGEN_RSI_HARNESS_DIR` (a directory path or `builtin:h0`).

---

## The three parts of geny-rsi

### 1. Geny base — the elements stay the same

geny-rsi copies runtime 4.81.0 as `xgen_rsi.base`. 517 of its 519 files are byte-identical; the other 2 (version stamp, a docstring path) are listed
with reasons in `tools/sync_base.py`. The provider layer (15: anthropic, openai, google, vllm, bedrock, vertex, azure, ollama, claude_code_cli,
codex_cli …), tools, memory, tasks, apps, storage, self-evolution, the host contract and the 21-stage baseline engine all come from this copy. Turn
assembly (the 26-step host contract) is also a copy with the same behaviour as the runtime (`xgen_rsi.assembly`). When the runtime changes, the copy
is updated separately (`tools/sync_base.py`).

### 2. RRSI — improving the harness by measurement

The kernel fixes the order of a turn (`context → prompt → client_tool → guard → config → call → parse → tools → control`) and the harness components
decide what each step does. Components come in 9 𝒦 kinds — `prompt`, `context_mgmt`, `control_flow`, `output_plumbing`, `client_tool`, `skill`,
`memory`, `config` (and `subagent`, disabled) — and a harness is a content-addressed package (`manifest.json`) of their parameters and files.

RRSI improves the harness in rounds.

1. An analyst turns failing trajectories into failure modes.
2. A proposer makes harness edits within an edit budget. A critic screens for leakage, environment assertions and kernel intrusion.
3. The candidates are measured. If the score rises above the noise floor δ the cost rule decides; inside the band an edit is adopted only if
   `100·ΔS − 15·ΔC + 0.5·ν > 0`. An edit to a parameter that was never read during evaluation is rejected by a guard.
4. Adoptions form that agent's harness lineage (H0 → H1 → …), and every decision input is recorded so it can be judged again. The next turn runs on
   the adopted harness.

The material is that agent's use — turn inputs and outputs, user feedback (stars, issue, comment), expected answers (quality evaluation). These
become tasks with judgement criteria, split into evolve and heldout ([design 40](docs/design/40-agent-self-evolution.md) §2.3).

### 3. Dream-RSI — saving exploration compute

A task with a verifier can be tried several ways (`rsi dream explore`: a branch × attempt grid). The exploration policy π_E decides how many
branches to open and when to stop. Recorded exploration trees become replay worlds, and π_E candidates are compared by replay without new
generation, using Eq.1 `V = max s − β1·N + β2·N/max(1,k★)`. A replay winner is promoted only after it explores the same tasks for real and passes
the **RRSI judgement (floor + cost rule)**. The evaluation trials that an agent's evolution runs leave behind are that agent's replay worlds.

Production conversation turns have no verifier to score them, so they do not use π_E and run a single path.

---

## Geny's elements and RSI

Geny's philosophy is "agent = model + elements (memory, tasks, tools, apps, storage) + self-evolution". geny-rsi keeps the elements as they are and
improves only **how they are used** (the harness), by measurement. The elements' contents (user data) and safety mechanisms (permissions, denials,
sandbox) cannot be changed by the harness.

| Element | Same in both agents | Decided by the harness (RRSI edit target) | Kept by the kernel and host |
|---|---|---|---|
| **Memory** | vault, session (STM) and long-term memory storage and format, memory tools, [Memory] screen, end-of-turn distillation | `memory` component: injecting pinned facts and relevant knowledge at the first iteration (`retrieve`), recording and rolling up the conversation at slice end (`archive`). The memory guidance block in `prompt` | memory contents, provider lifetime (open, close), distillation, write blocking for guests and frozen copies |
| **Tasks** | task tools (schedule, stop, list), scheduler, [Tasks] screen. Scheduled jobs run as that agent's turns | exposure of task tool schemas (`client_tool`) | who runs and with what permissions, task tools removed for guests and frozen copies |
| **Tools** | built-in tools (files, Bash, web), forged and shared tools, connector device tools, tools attached as nodes | `client_tool`: schemas shown per call, restoring tools from earlier turns, progressive-disclosure gates, sequential or parallel execution. `skill`: the harness's procedure documents (progressive disclosure) | tool implementations, permissions, HITL, user denials, repeat-failure blocking, sandbox, the run test before registration |
| **Apps** | app create, deploy and delete tools, app runner, app LLM, [Apps] screen | exposure of app tools | app execution, deployment and addresses, app LLM policy and limits |
| **Storage** | agent workspace (cloud original ↔ runner session), file sync, [Storage] screen | nothing | workspace restore and publish, the file-path fence |
| **Evolution history** | self-evolution tools (the agent edits its own prompt, tools and connections) and their record | exposure of self-evolution tools | what can be edited, and the record |

**The two evolutions are different axes.**

| | Self-evolution (evolution history, both agents) | Harness evolution (RRSI, Agent Geny RSI) |
|---|---|---|
| What changes | **what** the agent is — prompt, tools, connected nodes | **how** it runs a turn — context management, prompt assembly, loop decisions, tool exposure, procedures, memory policy |
| Who and when | the agent, during a conversation | a proposer model, on tasks built from that agent's use, in evolution-run rounds |
| Adoption rule | the agent's judgement (recorded in [Evolution history]) | measurement that passes the noise floor, cost rule and guards (recorded in [Harness]) |
| Scope | that one agent | that one agent |
| Takes effect | immediately (that workflow) | from the next turn after adoption (can be rolled back) |

They complement each other. When self-evolution changes the agent's work, later use becomes tasks, and harness evolution finds the way of running
that fits that work.

---

## Results summary

[Comparison report (Korean)](docs/reports/geny-vs-geny-rsi.md) — four models (gpt-6-sol, claude-sonnet-5, gpt-6-luna, claude-haiku-4-5) × three
suites (xgen-core, xgen-hard, xgen-pro). These experiments measure **whether the methodology works**. Harnesses evolved in them are fitted to those
suites and are not shipped.

| Question | Result (measured) |
|---|---|
| Is anything lost by swapping | Replaying the same responses, H0 sends byte-identical requests to geny (158/158). Before evolution, score differences are within noise for every model and suite |
| Does RRSI raise the score | On evolve, luna 0.919 → 0.948 (above δ), sol 0.992 → 1.000. **Within noise on the held-out split** (luna 0.911 vs 0.907, sol 1.000 vs 0.997) |
| Does RRSI cut cost | haiku −10.6% tokens at the same score level (evolve). The luna and sol adoptions used +26% and +5% tokens on the held-out split |
| Does Dream-RSI save exploration | sol: same best score with −25% attempts and −16% tokens, promoted. luna: a −36%-attempts candidate used +0.16% tokens and was rejected |
| Do measurement and guards work | Cost-only candidates rejected automatically (luna 7/10). Two kinds of harmful edits found in real runs (unmeasured edits, edits asserting the evaluation environment as fact) and blocked by guards |

**Summary.** Nothing is lost by swapping (H0 = geny); RRSI improved score and cost on the tasks it evolved on, rejects harmful changes automatically
and records why. No score or cost gain has been confirmed on the held-out split yet — the experiment suites are small (8 held-out tasks) and synthetic,
unrelated to any user, which is why production evolution uses each agent's real use. The experiment evaluation ran with file tools only, without
memory, tasks, self-evolution or code execution.

---

## Quick start

### Install

Install the wheel from GitHub Releases (not on PyPI). This one package is enough — xgen-agent-runtime is not needed.

```bash
pip install "xgen-agent-runtime-rsi @ https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases/download/v0.6.0/xgen_agent_runtime_rsi-0.6.0-py3-none-any.whl"
```

### As a library — the same feel as `PipelinePresets`

```python
from xgen_rsi import GenyRSI

agent = GenyRSI.minimal(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-...")
print((await agent.run("What is the capital of France?")).text)

worker = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...", workspace="./ws")  # file tools + history
for chunk in worker.stream_sync("Read reports/ and write summary.md"):
    print(chunk, end="")

baseline = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...", engine="geny")  # the 21-stage engine (geny, the xgen_rsi.base copy)
```

### In a host (the XGEN server etc.) — one entry point

The two packages do not know each other; the host imports each. Tool and memory objects the host passes into a turn are built from that agent's
package (an Agent Geny RSI turn uses `Tool`, `ToolRegistry` and memory providers from `xgen_rsi.base`) — objects from the two packages are never mixed
in one turn.

```python
from xgen_agent_runtime.host.turn_executor import AgentTurnExecutor   # geny — Agent Geny
from xgen_rsi import GenyRSITurnExecutor                             # geny-rsi — Agent Geny RSI

executor = GenyRSITurnExecutor() if use_geny_rsi else AgentTurnExecutor()
out = executor.run(host, **kwargs)   # chunk iterator (streaming) or the final text
```

### Evolving a harness · evolving an exploration policy

```bash
rsi suite build ./suites/xgen-pro --suite xgen-pro
rsi evolve init runs/evo --suite ./suites/xgen-pro --policy policy.json --config rrsi.json
rsi evolve run runs/evo                  # H0 baseline → δ calibration → RRSI rounds
rsi evolve export runs/evo ./H_star      # adopted harness → a directory (for experiments; pin it with XGEN_RSI_HARNESS_DIR)

rsi dream explore runs/pool --suite ./suites/xgen-pro --policy policy.json --explorer builtin:parallel_refine --iteration 0
rsi dream cycle runs/cycle1 --worlds worlds.json --incumbent builtin:parallel_refine --iteration 1 ...
```

Commands and the harness format are in the [usage guide (Korean)](docs/GUIDE.md).

---

## Three references

**1. XGEN's philosophy** — "No LangChain. No LangGraph. A pipeline where every step can be observed, changed and replaced." We keep this and change
the unit from "21 numbered stages" to **kinds of edit units (𝒦)**. The evidence-based decisions of CHANGELOG 4.27–4.75 ("adopt if the score is not
worse and cost drops", "structure over requests", "intervene only on deterministic signals") were **RRSI done by hand**. Permissions, HITL, user
denials and the sandbox are owned by the kernel and cannot be turned off by the harness.

**2. RRSI** — *Regularized Recursive Self-Improvement of Agent Harnesses* ([arXiv:2609.24972](https://arxiv.org/abs/2609.24972)). It keeps the edit
space open and **regularizes the search trajectory**. On the proposal side: an annealed edit budget (Eq.4), credit assignment over the full history
(Eq.10/11), exploring untried components when stalled (Eq.13). On the selection side: leakage review, a noise-calibrated floor (Eq.5), the cost
rule on gains (Eq.7), the within-band rule (Eq.17), structural pruning (Eq.14) and domain guards. It produces **the same results as the official
implementation** (google-research/rrsi, Apache-2.0), checked by differential tests.

**3. Dream-RSI** — *Recursive Self-Improvement through Evolving Worlds* ([arXiv:2609.14858](https://arxiv.org/abs/2609.14858)). Accumulated
discovery history is the **replay world**. Exploration-policy candidates are replayed deterministically over recorded trees (no new generation),
compared by Eq.1 and chosen by argmax including the incumbent. geny-rsi promotes a replay winner only after it explores for real and **passes the
RRSI judgement once more**.

---

## Architecture

```
host ──GenyRSITurnExecutor().run(host, **kwargs)──► xgen_rsi.assembly (turn assembly — a copy with the runtime's behaviour) ──► kernel
       (Agent Geny uses AgentTurnExecutor — same contract)  providers, tools, memory, tasks, apps, storage: the copy xgen_rsi.base (4.81.0)  │
 ┌──────────────────────── K₀ kernel (fixed) ──────────────────────────────────────────────────────────────────────────▼──┐
 │ engine     context → prompt → client_tool → guard → config → call → parse → tools → control (one decision point)        │
 │ model_call request assembly, streaming, retries (base provider layer) · ledger every policy call and c(τ) · tools perms │
 │ stream     chunk grammar, usage once, continuation, cancel, unfinished-turn memory · recorder trajectories + trees     │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 ┌──────────────────────── H harness (manifest.json, 𝒦-typed) ── one per agent, H0 at first ────────────────────────────┐
 │ prompt · context_mgmt · control_flow · output_plumbing · client_tool · skill · memory · config   (subagent disabled)   │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 rsi_math formulas · evolve RRSI · explore/dream Dream-RSI · discovery live exploration · agent GenyRSI · host LocalHost
```

---

## Formulas → code

| Paper | Code (`xgen_rsi.rsi_math`) |
|---|---|
| RRSI Eq.3 Ŝ, Ĉ (weighted generalization, missing = 0) | `aggregate` |
| Eq.4/9 annealed edit budget b_t | `edit_budget`, `check_edit_cardinality` |
| Eq.5 noise-calibrated floor | `floor_ok` |
| Eq.6/7/17 ΔS·ΔC, cost rule, within-band rule | `delta_s`, `relative_cost_change`, `cost_rule` |
| Eq.10/11 history, 𝒯_t, g_t | `edit_records`, `tried`, `recent_yield` |
| Eq.13 σ_t, 𝒰_t, reserved slot | `stall_flag`, `exploration`, `reserved_variants` |
| Eq.14 𝓑_t | `prune_set` |
| Eq.15/16 𝒦_str, ν_t | `K_STR`, `novelty`, `accepted_counts` |
| Algorithm 2 judge, select, S★ | `judge`, `select_round`, `update_s_star` |
| δ calibration | `calibrate`, `bootstrap_se` |
| Exact-bound early stopping (our theorem) | `can_stop_exactly`, `early_stop_record_delta` |
| Dream-RSI Eq.1, V^m, selection | `replay_value`, `mean_value`, `select_policy`, `assert_non_decreasing` |
| Appendix B evaluator | `parallel_penalty`, `pareto_auc_v1`, `pareto_reward`, `anytime_auc` |
| Default β rule across cycles, grid validation | `next_default_beta`, `validate_grid` |

`paper` mode gives bit-for-bit the same results as the official RRSI implementation; `xgen` mode applies documented decisions (tie rules, the active
𝒦 set, score normalization, an exact rational tie tolerance, etc.). **Exact-bound early stopping**: even if every remaining trial scored full marks,
if `Ŝ_max < min(S★−δ, Ŝ_t)` evaluation stops — we prove selection, 𝒯_t, 𝓑_t and N_t equal those of a full evaluation, and checked it randomly
against the official code (1,788/1,788).

---

## Safety and governance

- Permissions, HITL, user denials, the sandbox, limits, verifiers and the ledger are **owned by the kernel** — harness edits cannot turn them off.
- Leakage review (before evaluation): the 6 RRSI rules + XGEN rules (inducing permission or denial bypass, kernel intrusion, customer or task
  specificity, user-facing copy rules, no environment assertions).
- An edit to a parameter never read during evaluation is not adopted (since 0.2.0).
- Exploration-policy code runs only in a separate process (CPU and memory limits) with a restricted namespace.
- Production records keep **structure, scores and cost only** by default (no contents). Credentials are never written to config dumps or the frontier.

---

## Development

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q        # tests (no real model calls)
.venv/bin/python -m mypy             # rsi_math strict
.venv/bin/ruff check src tests tools experiments
.venv/bin/python tools/sync_base.py --check   # xgen_rsi.base is still an exact copy of runtime 4.81.0
```

## Documents

- [Comparison report (Korean)](docs/reports/geny-vs-geny-rsi.md) — implementation review of the two runtimes, swap equivalence, RRSI and Dream-RSI methodology experiments, production use, limits
- [Design 40 — each agent has its own harness (Korean)](docs/design/40-agent-self-evolution.md) — self-evolution inside XGEN (the production structure)
- [Usage guide (Korean)](docs/GUIDE.md) — library API, server settings, harness format, evaluation, evolution and exploration commands, updating the copy
- [Design docs map (Korean)](docs/README.md) — paper analysis, formula reference, runtime survey, fusion principles, architecture, I/O compatibility, evaluation, risks
- [Plan (Korean)](docs/PLAN.md) — stage plan, decisions, implementation status

## References

- Xia et al., *RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*, arXiv:2609.24972, 2026. Code: github.com/google-research/rrsi (Apache-2.0)
- Zheng et al., *Dream-RSI: Recursive Self-Improvement through Evolving Worlds*, arXiv:2609.14858, 2026
- PlateerLab, *xgen-agent-runtime* — geny (the 21-stage harness), the multi-provider layer and the host contract. Version 4.81.0 is copied as `xgen_rsi.base` (Apache-2.0)

## License

Apache-2.0. Notices for parts taken and modified from the official RRSI implementation (Apache-2.0) and for the xgen-agent-runtime copy are in [NOTICE](NOTICE).
