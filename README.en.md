# geny-rsi — a self-improving harness for XGEN agents

[![GitHub release](https://img.shields.io/github/v/release/PlateerLab/xgen-agent-runtime-rsi)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml)

**geny-rsi** is XGEN's second agent runtime. It shares the input/output contract of the existing runtime **geny** (the 21-stage
pipeline of xgen-agent-runtime) and Geny's elements: memory, tasks, tools, apps, storage and self-evolution. What differs is the
**harness pipeline that executes a turn**. geny-rsi's pipeline is split into a frozen kernel and an editable harness; the harness is
improved by **RRSI** and the allocation of exploration compute by **Dream-RSI**, both only through measurement. It turns the
evidence-driven harness engineering XGEN did by hand into formulas and records.

[한국어](README.md) · [**Detailed report (2nd, luna·haiku)**](docs/reports/2026-10-02-luna-haiku-xgen-pro.en.md) · [1st report](docs/reports/2026-10-01-geny-vs-geny-rsi.en.md) · [Usage guide (Korean)](docs/GUIDE.md) · [Design docs (Korean)](docs/README.md) · [Plan (Korean)](docs/PLAN.md)

---

## Agent Geny and Agent Geny RSI — only the harness pipeline differs

XGEN has two Geny agents. They are used the same way on the canvas, in chat and on every screen; what you choose is **how a turn runs**.

| | **Agent Geny** (`agents/geny`) | **Agent Geny RSI** (`agents/geny-rsi`) |
|---|---|---|
| Package | xgen-agent-runtime | xgen-agent-runtime-rsi — **neither imports nor depends on the other** |
| Ports, settings, credentials, model | same | same |
| Memory, tasks, tools, apps, storage, evolution history | runtime's element layer | **the same code** — runtime 4.80.0 copied as `xgen_rsi.base` (file hashes recorded in `COPY.json`, checked by tests) |
| **Harness pipeline** | 21-stage pipeline, changed by people through releases | frozen kernel K₀ + harness H (9 component kinds 𝒦); uses **the harness RRSI measured and adopted for the model family** |
| How the harness changes | runtime releases | adopted harnesses shipped in rsi releases (`harnesses/` + `lineages.json`); with no adopted harness for the model, H0 = the same behaviour as Agent Geny |

The XGEN server handles everything that belongs to an agent **with that agent's package**: chat turns, the [Memory], [Tasks],
[Tools], [Apps], [Storage] and [Evolution history] screen APIs, scheduled jobs, freezing and cloning. So an Agent Geny RSI's data is
handled by rsi code end to end, and the two agents differ only through the harness pipeline. Platform features that belong to no
agent (personal SSH connection tests, the general LLM service, …) use XGEN's default package (runtime).

**When to use which**

- **Agent Geny** — the default. Its harness changes only through runtime releases (edited and verified by people).
- **Agent Geny RSI** — when you want a harness **adopted by measurement** for the chosen model. Only harnesses with a record of a
  score gain above noise, or of lower cost at the same score, on a held-out split never used for judging enter the package. For a model with
  no adopted harness it behaves like Agent Geny. **As of 2026-10-02 no adopted harness is bundled** — see [results](#results).

---

## Geny's elements and RSI

Geny's philosophy is "agent = model + elements (memory, tasks, tools, apps, storage) + self-evolution". geny-rsi keeps the elements
as they are and changes only **how they are used** (the harness), by measurement. The elements' contents (user data) and the safety
mechanisms (permissions, denials, sandbox) cannot be changed by a harness.

| Element | Same for both agents | Decided by the harness (RRSI edit targets) | Kept by the kernel/host (not changeable by a harness) |
|---|---|---|---|
| **Memory** | vault, session (STM) and long-term stores and formats, memory tools (`memory_write`, `memory_pin`), screens | the `memory` component: inject pinned facts and relevant knowledge on the first iteration, record and roll up the conversation at the end of a slice; the memory block of `prompt`; the retrieval time limit of `context_mgmt` | memory **contents** (user data), provider lifecycle (open/close), end-of-turn distillation, write blocking for guest and frozen turns |
| **Tasks** | task tools (schedule, stop, list), the scheduler, the [Tasks] screen; prompt jobs run as that agent's turn (with the RSI harness for an RSI agent) | `client_tool` exposure: when task-tool schemas are shown | who runs and with what permission; task tools removed for guest and frozen turns |
| **Tools** | built-in tools (files, Bash, web), self-made tools (ForgeTool) and shared tools, connector device tools, tools wired as nodes | `client_tool`: which schemas each call shows, restoring tools used in earlier turns, progressive-disclosure entry points, running one step's calls sequentially or in parallel; `skill`: general procedure documents owned by the harness (progressive disclosure) | tool implementations, permissions, HITL and user denials, blocking repeated failures, the sandbox, pre-registration test runs |
| **Apps** | app create/publish/delete tools, the app runner, the LLM apps use (the agent's model), the [Apps] screen | exposure of app tools (`client_tool`) | app execution, publishing and addresses, app LLM policy and limits |
| **Storage** | the agent workspace (cloud original ↔ runner session), file sync, the [Storage] screen | nothing | restoring and publishing the workspace, the file-path fence |
| **Evolution history** | self-evolution tools (WorkflowSelf — the agent edits its own prompt, tools and connections) and their log | exposure of the self-evolution tools | what can be edited, and the log |

**The two kinds of evolution are different axes.**

| | Self-evolution (evolution history, both agents) | Harness evolution (RRSI, Agent Geny RSI) |
|---|---|---|
| What changes | **what** the agent is — prompt, tools, connected nodes (the workflow) | **how** a turn runs — context management, prompt assembly, loop decisions, tool exposure, procedures |
| Who and when | the agent, during a conversation, on request or by its own judgement | a proposer model, in offline rounds on a business suite |
| Acceptance | the agent's judgement (logged in [Evolution history]) | measurement passing the noise-adjusted floor, the cost rule and the guards |
| Scope | that one agent | a model family (every Agent Geny RSI using that model) |
| Applied | immediately (that workflow) | in an rsi release (human approval and CI) |

Harness evolution never touches a user's workflow, memory or files. A workflow changed by self-evolution runs with the same harness
from the next turn.

---

## What RSI is for

**Goal: better and cheaper with the same model — and proof of it in the record.** The right harness (way of executing turns) differs
per model. Instead of people re-tuning a harness for each model, only changes the measurement allows are accumulated.

- **RRSI improves the harness.** A proposer that has analysed failed trajectories edits the harness (within the leakage screen and the
  edit budget) and the edit is measured on a business suite. A score rise **above the noise floor (δ)** is adopted subject to the cost
  rule; within the band, an edit is adopted only when the combined score, cost and novelty value (Eq.17) is positive — edits that only add cost
  are rejected. Edits the evaluation never read and edits asserting facts about the environment are rejected. Adoptions form a harness lineage
  per model family, and every judgement input is recorded so a round can be re-adjudicated. **Shipping (the package) is a separate bar** — only
  harnesses whose gain is confirmed on a held-out split never used for judging are bundled.
- **Dream-RSI saves exploration compute.** Accumulated exploration records serve as replay worlds, so candidate exploration policies
  (how many branches, when to stop) are compared without new generation. A replay winner is promoted only after **exploring live again
  and passing the RRSI judgement**.
- **What it does not do.** It does not train the policy (the model). It does not change user data, permissions or safety mechanisms. It
  does not change production without measurement and human approval.

## Harness pipelines compared — geny vs geny-rsi

| | **geny** (existing) | **geny-rsi** (this repo) |
|---|---|---|
| Execution core | 21-stage pipeline (fixed stage numbers) | frozen kernel K₀ + harness H of 𝒦-typed components |
| Input/output | `AgentTurnExecutor().run(host, **kwargs)` | **identical** — chunks, usage, and model requests (measured by deterministic replay) |
| Providers | 15 registered provider names (incl. aliases) | **same** (the copied provider layer `xgen_rsi.base.llm_client`) |
| Harness improvement | people measure and edit | **RRSI**: propose → leakage screening → evaluate → accept by noise floor and cost rule |
| Exploration | one path at a time | branch × attempt grid; the exploration policy π_E improves by **Dream-RSI** |
| Unit of measurement | turn logs | trajectory record (discovery tree) + policy tokens c(τ) — directly a replay world |
| How to use | `AgentTurnExecutor().run(host, **kwargs)` | `GenyRSITurnExecutor().run(host, **kwargs)` or `GenyRSI(...)` |

```
Agent A = (π, K₀, H, π_E)
  π    frozen policy      — the provider/model the user picked
  K₀   frozen kernel      — I/O contract · provider gateway · usage ledger · tool execution & permissions · limits · recording   (never evolved)
  H    harness            — a content-addressed package of components typed by 𝒦 (9 kinds)                                       (evolved by RRSI)
  π_E  exploration policy — code that decides branching, batching and stopping                                                    (evolved by Dream-RSI)
```

---

## Results

**Second round (2026-10-02 · gpt-6-luna · claude-haiku-4-5 · xgen-pro)** — [detailed report](docs/reports/2026-10-02-luna-haiku-xgen-pro.en.md)

| Question | Result (measured) |
|---|---|
| Does swapping in lose anything? | Before evolution geny-rsi (H0) is within noise of the existing runtime for both models and splits (luna 0.919 vs 0.906, haiku 0.826 vs 0.810, evolve) |
| Does the score rise? (RRSI) | luna: 2 adoptions in 5 rounds, evolve 0.919 → 0.948 (above δ 0.025). But **on the held-out split (k=4): H\* 0.911 vs H0 0.907 vs geny 0.916 — within noise**, tokens +26% → not bundled |
| Does cost fall? (RRSI) | haiku: an edit with −10.6% tokens at the same score level adopted (−45.8% in a voided run). Evolution stopped at round 2/5 when the API credit ran out — to be updated after resuming |
| Do measurement and guards work? | 7/10 cost-only candidates rejected automatically (luna). Three defects found and blocked in the runs: an empty analysis, **edits hard-coding the evaluation environment** (false in production), outage trials recorded as zero |
| Is exploration saved? (Dream-RSI) | the luna exploration policy developed by gpt-6-sol: same best score with −36% attempts, but tokens +0.16% → the confirmation blocked the promotion |

**First round (2026-10-01 · gpt-6-sol · claude-sonnet-5)** — [detailed report](docs/reports/2026-10-01-geny-vs-geny-rsi.en.md): replaying real responses,
both engines send byte-identical requests (81/81, 77/77). Both models sit at the ceiling (0.9–1.0), so no harness improvement could be shown.
gpt-6-sol's Dream policy was promoted with 25% fewer attempts and 16% fewer tokens.

**In short:** what geny-rsi reliably does today is **swap in without loss and keep only the changes measurement allows**. No score or cost gain has
yet been confirmed on a held-out split. The biggest limitations are that the evaluation host has no code-execution tool (unlike production) and the
small held-out split (8 tasks).

---

## Quick start

### Install

Wheels are published as GitHub Release assets (not on PyPI). This one package is all you need; xgen-agent-runtime is not required.

```bash
pip install "xgen-agent-runtime-rsi @ https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases/download/v0.4.0/xgen_agent_runtime_rsi-0.4.0-py3-none-any.whl"
```

### As a library — the same feel as `PipelinePresets`

```python
from xgen_rsi import GenyRSI

agent = GenyRSI.minimal(provider="anthropic", model="claude-sonnet-5", api_key="sk-ant-...")
print((await agent.run("What is the capital of France?")).text)

worker = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...", workspace="./ws")  # file tools + history
for chunk in worker.stream_sync("Read reports/ and write summary.md"):
    print(chunk, end="")

baseline = GenyRSI.agent(provider="openai", model="gpt-6-sol", api_key="sk-...", engine="geny")  # same call, the 21-stage engine (geny, copied in xgen_rsi.base)
```

### In a host (an XGEN server, etc.) — one entry point

The two packages do not know each other; the host imports each one. Both entry points share the same contract (`run(host, **kwargs)`
→ an iterator of text chunks, or the final text). The tool and memory objects a host hands to a turn are built from that agent's own
package (an Agent Geny RSI turn gets `xgen_rsi.base`'s `Tool`, `ToolRegistry` and memory providers); one turn never mixes objects
from the two packages.

| XGEN node | Runtime | Package | Entry point |
|---|---|---|---|
| Agent Geny (`agents/geny`) | geny | xgen-agent-runtime | `AgentTurnExecutor` |
| Agent Geny RSI (`agents/geny-rsi`) | geny-rsi | xgen-agent-runtime-rsi | `GenyRSITurnExecutor` |

XGEN's Agent Geny RSI node has the same ports, settings and server wiring as Agent Geny, and its turn runs on this package from start to finish.

```python
from xgen_agent_runtime.host.turn_executor import AgentTurnExecutor   # geny (existing)
from xgen_rsi import GenyRSITurnExecutor                             # geny-rsi

executor = GenyRSITurnExecutor() if use_geny_rsi else AgentTurnExecutor()
out = executor.run(host, **kwargs)
```

**Harnesses ship inside the package.** Without settings, the bundled lineage table (`harnesses/lineages.json`) picks the harness
for the model, falling back to the built-in H0 (the same behaviour as Agent Geny). Harnesses adopted by RRSI are added to this repo's
`harnesses/` and that table and released, so a host picks them up by **bumping the version**, as with the runtime. Administrators can
override with `XGEN_RSI_HARNESS_DIR=builtin:<name>` (one bundled harness) or a directory path, or `XGEN_RSI_LINEAGE_FILE` (a lineage table).

### Evolve a harness

```bash
rsi suite build ./suites/xgen-pro --suite xgen-pro
rsi evolve init runs/evo --suite ./suites/xgen-pro --policy policy.json --config rrsi.json
rsi evolve run runs/evo                  # H0 baseline → δ calibration → RRSI rounds
rsi evolve export runs/evo ./H_star      # adopted harness → XGEN_RSI_HARNESS_DIR=./H_star
```

Live exploration, Dream cycles, evaluation commands and the harness format: [usage guide](docs/GUIDE.md).

---

## Three references

**1. XGEN's philosophy** — "No LangChain. No LangGraph. Every step observable, mutable and swappable." We keep that principle and
change the unit from "21 numbered stages" to **kinds of editable units (𝒦)**. Configuration is an artifact (a content-addressed
manifest), and the evidence-driven decisions of CHANGELOG 4.27–4.75 ("accept if the score is non-inferior and cost goes down",
"change structure instead of asking in the prompt", "intervene only on deterministic signals") were **RRSI done by hand**.
Permissions, HITL, user denials and the sandbox are kernel-owned and cannot be switched off by a harness.

**2. RRSI** — *Regularized Recursive Self-Improvement of Agent Harnesses* ([arXiv:2609.24972](https://arxiv.org/abs/2609.24972)).
Keep the edit space open and **regularize the search trajectory**: an annealed edit budget (Eq.4), credit assignment over the full
history (Eq.10/11), exploring untried components when stalled (Eq.13); a leakage screen, a noise-adjusted floor (Eq.5), a gain-dependent
cost rule (Eq.7), a within-band rule (Eq.17), structural pruning (Eq.14) and domain guards. Our implementation matches the reference
(google-research/rrsi, Apache-2.0) under **differential tests**.

**3. Dream-RSI** — *Recursive Self-Improvement through Evolving Worlds* ([arXiv:2609.14858](https://arxiv.org/abs/2609.14858)).
The accumulated discovery history already is a **replay world**. Candidate exploration policies are replayed deterministically on
recorded trees (no new generation), compared by Eq.1 `V = max s − β1·N + β2·N/max(1,k★)`, and selected by an argmax that includes the
current policy. geny-rsi additionally requires the replay winner to **pass the RRSI judgement on a live run** before promotion.

---

## Formulas → code

| Paper | Code (`xgen_rsi.rsi_math`) |
|---|---|
| RRSI Eq.3 Ŝ, Ĉ (weighted; missing trial = 0) | `aggregate` |
| Eq.4/9 annealed edit budget b_t | `edit_budget`, `check_edit_cardinality` |
| Eq.5 noise-adjusted floor | `floor_ok` |
| Eq.6/7/17 ΔS·ΔC, cost rule, within-band rule | `delta_s`, `relative_cost_change`, `cost_rule` |
| Eq.10/11 history · 𝒯_t · g_t | `edit_records`, `tried`, `recent_yield` |
| Eq.13 σ_t, 𝒰_t, reserved slots | `stall_flag`, `exploration`, `reserved_variants` |
| Eq.14 𝓑_t | `prune_set` |
| Eq.15/16 𝒦_str, ν_t | `K_STR`, `novelty`, `accepted_counts` |
| Algorithm 2 judging · selection · S★ | `judge`, `select_round`, `update_s_star` |
| δ calibration | `calibrate`, `bootstrap_se` |
| exact-bound early stopping (our theorem) | `can_stop_exactly`, `early_stop_record_delta` |
| Dream-RSI Eq.1, V^m, selection | `replay_value`, `mean_value`, `select_policy`, `assert_non_decreasing` |
| Appendix B evaluator | `parallel_penalty`, `pareto_auc_v1`, `pareto_reward`, `anytime_auc` |
| cross-cycle default-β rule, grid validation | `next_default_beta`, `validate_grid` |

`paper` mode reproduces the reference RRSI implementation bit for bit; `xgen` mode applies documented decisions (tie rule, enabled 𝒦,
score normalization, a tolerance for exact rational ties, …). **Exact-bound early stopping** stops evaluating a candidate once even
all-perfect remaining trials could not lift it above `min(S★−δ, Ŝ_t)` — selection, 𝒯_t, 𝓑_t and N_t are provably identical to a full
evaluation, checked against the reference code on 1,788/1,788 random cases.

---

## Safety and governance

- Permissions, HITL, user denials, the sandbox, limits, verifiers and the ledger are **kernel-owned** — no harness edit can disable them.
- Leakage screening before evaluation: RRSI's six rules plus XGEN rules (inducing permission/denial bypass, touching kernel territory,
  customer/business-specific content, user-facing copy rules).
- Exploration-policy code runs only in a separate process (CPU and memory limits) inside a restricted namespace.
- Production recording keeps **structure, scores and cost only** by default; credentials are never written to config dumps or the frontier.
- Production changes always go through **human approval** (MR/PR) and CI. Formulas decide; people approve and own the constitution.

---

## Development

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q        # tests (no real model calls)
.venv/bin/python -m mypy             # rsi_math strict
.venv/bin/ruff check src tests experiments
```

## References

- Xia et al., *RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*, arXiv:2609.24972, 2026. Code: github.com/google-research/rrsi (Apache-2.0)
- Zheng et al., *Dream-RSI: Recursive Self-Improvement through Evolving Worlds*, arXiv:2609.14858, 2026
- PlateerLab, *xgen-agent-runtime* — geny (the 21-stage harness), the multi-provider layer and the host contract. Version 4.80.0 is copied as `xgen_rsi.base` (Apache-2.0)

## License

Apache-2.0. Attribution for parts adapted and modified from the RRSI reference implementation (Apache-2.0) is in [NOTICE](NOTICE).
