# geny-rsi — a self-improving harness for XGEN agents

[![GitHub release](https://img.shields.io/github/v/release/PlateerLab/xgen-agent-runtime-rsi)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml)

**geny-rsi** is XGEN's second agent runtime. It has **the same input/output contract** as the existing runtime **geny** (the
21-stage pipeline of xgen-agent-runtime). It improves its own harness with **RRSI** (regularized harness evolution) and its
allocation of exploration compute with **Dream-RSI** (replay-based policy improvement), turning the evidence-driven harness
engineering XGEN did by hand into formulas and records.

In XGEN the two run side by side as two agents: **Agent Geny** (`agents/geny`, geny) and **Agent Geny RSI** (`agents/geny-rsi`, geny-rsi).

**It is an independent package.** geny-rsi neither imports nor depends on xgen-agent-runtime. Its base runtime (provider layer,
tools, memory, host contract, the 21-stage engine) is `xgen_rsi.base`, a copy of xgen-agent-runtime 4.80.0 owned by this repo and
updated separately when the original changes.

[한국어](README.md) · [**Detailed comparison report**](docs/reports/2026-10-01-geny-vs-geny-rsi.en.md) · [Usage guide (Korean)](docs/GUIDE.md) · [Design docs (Korean)](docs/README.md) · [Plan (Korean)](docs/PLAN.md)

---

## At a glance — geny vs geny-rsi

| | **geny** (existing) | **geny-rsi** (this repo) |
|---|---|---|
| Execution core | 21-stage pipeline (fixed stage numbers) | frozen kernel K₀ + harness H of 𝒦-typed components |
| Input/output | `AgentTurnExecutor().run(host, **kwargs)` | **identical** — chunks, usage, and model requests (measured by deterministic replay) |
| Providers | 15 registered provider names (incl. aliases) | **same** (the copied provider layer `xgen_rsi.base.llm_client`) |
| Harness improvement | people measure and edit | **RRSI**: propose → leakage screening → evaluate → accept by noise floor and cost rule |
| Exploration | one path at a time | branch × attempt grid; the exploration policy π_E improves by **Dream-RSI** |
| Unit of measurement | turn logs | trajectory record (discovery tree) + policy tokens c(τ) — directly a replay world |
| How to use | `AgentTurnExecutor().run(host, **kwargs)` | `GenyRSITurnExecutor().run(host, **kwargs)` or `GenyRSI(...)` |
| Package | xgen-agent-runtime | xgen-agent-runtime-rsi — **neither depends on the other** |

```
Agent A = (π, K₀, H, π_E)
  π    frozen policy      — the provider/model the user picked
  K₀   frozen kernel      — I/O contract · provider gateway · usage ledger · tool execution & permissions · limits · recording   (never evolved)
  H    harness            — a content-addressed package of components typed by 𝒦 (9 kinds)                                       (evolved by RRSI)
  π_E  exploration policy — code that decides branching, batching and stopping                                                    (evolved by Dream-RSI)
```

---

## Results (2026-10-01 · gpt-6-sol · claude-sonnet-5)

| Question | Result (measured) |
|---|---|
| Can it be swapped in? | **Replaying real model responses, both engines send byte-identical requests** — sonnet-5 81/81, gpt-6-sol 77/77 calls (32 tasks each); answers, usage and scores identical 64/64 |
| Same score? | xgen-core: identical scores in all four conditions (gpt-6-sol 0.919/0.914, sonnet-5 1.000). xgen-hard: every difference within the standard error |
| Does it improve itself? (RRSI) | gpt-6-sol adopted three edits in six rounds, but on the held-out split H\* scores 1.000 vs H0 0.997 (within noise) with +5% tokens. Both models sit at the ceiling, so no improvement can be shown → to be re-measured with models below the ceiling (gpt-6-luna, claude-haiku-4-5) and a harder suite. A defect found in the real runs (adopting an unmeasured edit) is blocked in 0.2.0 |
| Better use of exploration? (Dream-RSI) | an exploration policy developed by gpt-6-sol found the same best score (8/8) live with **25% fewer attempts and 16% fewer tokens**, passed the RRSI judgement and was promoted |


Numbers, methods and limitations: [detailed comparison report](docs/reports/2026-10-01-geny-vs-geny-rsi.en.md).

---

## Quick start

### Install

Wheels are published as GitHub Release assets (not on PyPI). This one package is all you need; xgen-agent-runtime is not required.

```bash
pip install "xgen-agent-runtime-rsi @ https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases/download/v0.3.0/xgen_agent_runtime_rsi-0.3.0-py3-none-any.whl"
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
rsi suite build ./suites/xgen-hard --suite xgen-hard
rsi evolve init runs/evo --suite ./suites/xgen-hard --policy policy.json --config rrsi.json
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
