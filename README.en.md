# geny-rsi — Geny + RRSI + Dream-RSI

[![GitHub release](https://img.shields.io/github/v/release/PlateerLab/xgen-agent-runtime-rsi)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml/badge.svg)](https://github.com/PlateerLab/xgen-agent-runtime-rsi/actions/workflows/ci.yml)

> [!IMPORTANT]
> **geny-rsi is built on the two papers below.** The rules for improving a harness by measurement come from **RRSI**; the idea of using
> completed runs as a replay simulator, so alternatives are evaluated without new runs, comes from **Dream-RSI**. We translated both papers'
> formulas and procedures into code ([design 33, Korean](docs/design/33-formula-to-code.md)), and differential tests show the same results as
> the official RRSI implementation. The methodology in this repository is the research of these papers' authors.
>
> **[1] RRSI: Regularized Recursive Self-Improvement of Agent Harnesses** — arXiv:2609.24972 · [PDF](https://arxiv.org/pdf/2609.24972)
> - Authors: Peng Xia, Rujun Han, Zifeng Wang, Yanfei Chen, Yufan Zhuang, Yoonho Lee, Chengsong Huang, Han Yu, Zhongying CuiZhu, Yifei Ming, Huaxiu Yao, Burak Gokturk, Tomas Pfister, Chen-Yu Lee
> - Affiliations: Google Cloud AI Research · UNC-Chapel Hill · Stanford University · Washington University in St. Louis
> - Code: [google-research/rrsi](https://github.com/google-research/rrsi) (Apache-2.0) · [regularized-rsi.com](https://regularized-rsi.com)
> - What we took: the harness as editable components; proposal-side regularization (annealed edit budget, credit assignment over the whole history, exploration of untried components on a stall); selection-side regularization (leakage screening, noise-adjusted floor, cost rule, in-band rule, structural pruning, domain guards).
>
> **[2] Dream-RSI: Recursive Self-Improvement through Evolving Worlds** — arXiv:2609.14858v1 · [PDF](https://arxiv.org/pdf/2609.14858v1)
> - Authors: Tong Zheng, Xidong Wu, Zheng Zhang, Zhankui He, Chaoyi Zhang, Benjamin Coleman, Ruoqiao Wei, Di Bai, Haolin Liu, Rui Liu, Xue Wang, Yue Zhuan, Wang-Cheng Kang, Renkai Xiang, Heng Huang, Xinwu Cheng, Yunsong Guo
> - Affiliations: Google · University of Maryland, College Park · Google DeepMind · University of Virginia
> - Code: [zhengkid/Dream-RSI](https://github.com/zhengkid/Dream-RSI) · [dream-rsi.com](https://dream-rsi.com)
> - What we took: completed runs as a replay simulator (worlds), deterministic evaluation of alternatives on the record, selection that includes the current policy, the policy fixed within a run, and the loop redeploy → new records → a larger simulator. geny-rsi uses it both for the exploration policy on verifiable tasks and for consolidating the harness after every agent turn.
>
> To cite, use the BibTeX in [References](#references).

**geny-rsi** is XGEN's second agent runtime. In XGEN it is used as **Agent Geny RSI** (`agents/geny-rsi`).

- **Geny base** — it carries a copy of the element layer of the existing runtime geny (xgen-agent-runtime). Providers, tools, memory, tasks,
  apps, storage, self-evolution and the host contract are the same as Agent Geny. It is an independent package: it neither imports nor depends
  on xgen-agent-runtime.
- **RRSI** — it splits the harness pipeline that runs one turn into a fixed kernel and an editable harness, and improves the harness by
  regularized measurement. **Every Agent Geny RSI starts from the default of the RSI pipeline (H0).**
- **Dream-RSI** — a completed run is a replay simulator. **Every turn of the agent is kept as a replay world** (what the model saw plus the real
  results its tools returned), and harness candidates are measured on those worlds without running the tools again.
- **Turn consolidation** — the moment a turn ends, a consolidation runs. It takes the correction or complaint in the user's next message, ratings,
  comments and expected answers as signals; when there is something to fix it runs one RRSI round by replay, and the adopted harness is used from
  **the very next turn** ([design 41, Korean](docs/design/41-turn-consolidation.md)).

The two agents differ in **one thing: the harness pipeline**. RSI's strength is fitting the user. Even with the same model, each agent has its own
work, users and material, so the right harness differs too. The package therefore ships H0 only, and the harness is improved per agent inside
XGEN from its users' turns. The methodology experiments and the implementation review of the two runtimes are
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

## How Agent Geny RSI runs inside XGEN

In one sentence: **a turn runs on the current harness; when it ends the turn is kept as a replay record (a world); right away a consolidation
replays that agent's worlds and decides whether to change the harness; the changed harness is read when the next turn starts.** There is no
button or switch. Rationale: [design 41 (Korean)](docs/design/41-turn-consolidation.md).

```
turn N   ┌ pick harness   read the current version from the DB (H0 if none); fixed until this turn ends
         ├ run            kernel + harness; what the model saw and what the tools returned are recorded as they were
         └ turn end       trajectory summary + world stored → start that agent's consolidation in the background (the turn does not wait)
[consol] ┌ signal         does the user's next message in the conversation correct or accept the previous answer + ratings, expected answers
         ├ decide         no new signal that asks for a fix → record only and stop
         ├ measure        replay the worlds that carry signals with the current harness (tools from the record, only the model runs again)
         ├ round          2 candidate harnesses → replay the same worlds → RRSI judgement
         └ adopt          put the new version in the lineage and make it current
turn N+1 pick harness reads the new version
```

**Timeline example** — a correction is only known from the next turn's message, so the changed harness is used from the turn after the correcting one.

| Turn | User | Harness of this turn | Consolidation after the turn |
|---|---|---|---|
| 1 | "Compare the three plans" | H0 | no signal → record only |
| 2 | "No, show it as a table, one-line recommendation" | H0 | turn 2's message judges turn 1's answer = correction → round → H1 adopted |
| 3 | (any request) | **H1** | … |

### What changes

**Only that agent's harness changes.** Harness = `manifest.json` + the files its components reference (skill documents etc.). The version is the
`sha256` of the canonical manifest and the referenced files (same content, same version).

| Can change | Examples |
|---|---|
| Component parameters | `prompt.system.params.extra_blocks` (add an instruction block), `part_overrides` (replace a stock block), compaction thresholds, completion review and repeat-stop, tool exposure, memory policy (retrieve / archive on or off) |
| Component files | write a `skills/<name>/SKILL.md` procedure and list it under a component |
| Enable, disable, add, remove components | registered implementations only (no code can be added) |

| Never changes | Why |
|---|---|
| The kernel — execution order, tool execution, permissions, user denials, sandbox, usage ledger, limits | outside the harness, so the harness cannot touch measurement or safety |
| Locked values — `model`, `provider`, `credentials`, `max_iterations`; the manifest's `locked`, `enabled_kinds`, `exploration_policy` | a proposal that edits them is rejected |
| Agent settings — the node's system prompt, tools, model | input chosen by the user; the harness must work generally on top of it |
| Memory contents (the memory vault), conversation history | user data; consolidation never writes memory |
| The judge and the criteria | outside the harness; the harness never sees criteria while it runs |
| The package's H0 | the starting point of every agent; an agent's changes accumulate in that agent's lineage only |

### When it changes

- **Only three things change it:** an adoption by a consolidation, a rollback by the user, and making a clone or a frozen copy (which copies the
  original's lineage and current version).
- **It applies when the next turn starts.** When a turn is prepared the host hook `rsi_agent_harness()` reads the current version. The pod where
  the adoption happened sees it at once; other pods within 15 seconds (the version cache lifetime).
- **It never changes within a turn.** A turn that started before the consolidation finished runs to the end on the previous version; the turn
  after it uses the new one. Continuation slices of the same turn use the same harness.
- **The conversation (Interaction) does not change.** The earlier messages stay as they are; only the system side of the next turn is built by
  the new harness.

### One consolidation — the exact order

A consolidation starts after every turn, but the expensive steps run only when their condition holds. `status` is kept in the consolidation log
(`geny_rsi_runs`).

| Step | What it does | Ends here when (status) |
|---|---|---|
| 0. Preconditions | it must be an Agent Geny RSI and not a frozen copy; if another pod is consolidating this agent (a `running` record younger than 15 min) it skips | no record |
| 1. Models | policy π = the agent's model; roles (proposer, critic, analyst, judge) = the same model with the key registered in XGEN | Claude Code / Codex → `skipped` |
| 2. Worlds, explicit signals | read the latest 60 worlds; re-read each turn's rating and expected answer from XGEN, update the stored signals when they differ and mark them **fresh** | no worlds → no record |
| 3. Implicit signals | read up to 4 (previous turn, next turn) pairs whose previous answer has no judgement yet, the just-finished turn first. The judge classifies the next message as `correction`, `complaint`, `accept` or `neutral`, and for a correction or complaint writes one sentence "what a better answer must satisfy". A pair is read once | — |
| 4. Criteria | signals → criteria (table below). Replayable worlds with criteria = scorable | none scorable → `recorded` |
| 5. Open a round? | a scorable world must carry a **fresh signal that asks for a fix** (correction or complaint, rating ≤ 2, an issue, an expected answer) | none → `recorded` |
| 6. Evaluation worlds W | fresh signals → signals asking for a fix → signals asking to keep, most recent first within each, at most 8 | — |
| 7. Measure the current harness | 2 trials per world in W. A turn the same model actually ran on the current version is itself one trial; cached replays are reused; the rest are replayed | more than 15% replays failed → `failed` / every criterion passes → `kept` |
| 8. Noise band, directives | δ = max(2·√2 × the trial-bootstrap standard error, one verdict = 1 / sum of criteria), S★ = the current harness's S; edit budget b_t, stall σ_t, untried components, pruning targets | — |
| 9. Analysis | one call over the worst trial of up to 6 failing worlds and up to 3 passing worlds: failure modes and success habits | — |
| 10. Two candidates | in parallel, in git workspaces copied from the current harness: propose (within the edit budget) → leakage review (rejects conversation content, injected conversation summaries, environment claims, disabled safety; one repair) → tag edits by touched addresses → load check | per candidate `no_proposal`, `critic_reject`, `smoke_fail` |
| 11. Measure candidates | replay the same W with each candidate (2 per world, candidates in parallel); stop early once missing the floor is certain | more than 15% replays failed → `eval_invalid` |
| 12. Judge | the RRSI rules as is (below) | no admissible candidate → `kept` |
| 13. Record, adopt | append every candidate's edit records to the history and advance the round number; if there is a winner, turn it into a payload, put it in the lineage and make it current — unless the user changed the harness during the consolidation, then it is not adopted | `adopted` or `kept` |

**Signal → criterion** (the judge passes or fails each criterion on one replayed answer)

| Signal | Criterion |
|---|---|
| Expected answer (quality evaluation) | same facts, numbers and conclusions as the expected answer |
| Rating ≤ 2, or an issue or comment | the problem the user reported (issue + comment) is absent |
| Rating ≥ 4 | keeps every key point of the answer the user accepted (regression guard) |
| Next message is a correction or complaint | the judge's "what a better answer must satisfy" |
| Next message accepts | keeps every key point of that answer (regression guard) |
| Criteria written by the user (when the host passes them — XGEN does not yet) | as written |

**Judgement.** Trial score r = criteria passed / criteria; S = total passed over all trials / total criteria; C = mean policy tokens per trial.
A candidate H′ is admissible only if it meets all of the following.

- Floor: S′ ≥ S★ − δ
- If ΔS > δ, the cost rule: relative token growth ΔC ≤ 0.10 + 35.4·ΔS
- If ΔS ≤ δ, the in-band rule: 100·ΔS − 15·ΔC + 0.5·ν > 0, where ν counts structural kinds (skill, memory, client_tool) being accepted for the first time.
- Guards:
  - Every edited parameter must be read at least once during the replays.
  - The valid-answer rate must not drop by more than 0.15, and the empty-answer rate must not rise by more than 0.15.

Among admissible candidates the highest S wins. Ties go to fewer tokens, then fewer edits. With no admissible candidate the current harness stays.

### The recursion — what carries into the next consolidation

H_{t+1} = consolidate(H_t, world pool, edit history, signals). The adopted H_{t+1} produces the next turns, and those turns become worlds that the
next consolidation evaluates on (Dream-RSI: redeploy → new records → a larger simulator). These five things carry over.

| What carries over | Stored in | Use in the next consolidation |
|---|---|---|
| Current harness and lineage | `geny_rsi_agents.current_version`, `geny_rsi_harnesses` (per version: payload, parent, adopting consolidation, summary) | the starting harness; rollback targets |
| World pool | `geny_rsi_worlds` (latest 60 per agent) | evaluation worlds; turns recorded on the current version are used directly as its trials |
| Edit history 𝓛 | `geny_rsi_agents.state.records` | evidence for the proposer (rejected methods are not redrawn), components already exercised, pruning targets 𝓑_t (components with no gain over the last 4 rounds), first-acceptance (ν) |
| Round number t, cumulative gain | `state.t`, `state.progress` | edit budget b_t = 3 → 1 (shrinks over 20 rounds, 1 afterwards); stall σ_t (if the accepted gains of the last 3 rounds sum to ≤ δ, one candidate slot is reserved for an untried component) |
| Replay and verdict caches | `state.cache`, `state.verdicts` | the same version on the same world is not replayed again; the adopted version's replays become the current harness's trials next time; the same (criterion, answer) is not judged again |

The round number and the edit history grow only in consolidations that actually run a round. `recorded`, `kept` (before a round) and `failed`
consolidations leave only signals and caches.

### Storage — what is where

**DB (core models; an agent = one workflow_id)**

| Table | One row | Main columns | Kept |
|---|---|---|---|
| `geny_rsi_agents` | an agent | `current_version` (empty = H0), `state` (consolidation state JSON) | 1 row |
| `geny_rsi_harnesses` | an adopted version | `version`, `parent_version`, `payload` (manifest + files), `run_id`, `summary` | all |
| `geny_rsi_worlds` | a turn | `io_id` (execution_io), `interaction_id`, `seq` (order in the conversation), `harness_version`, `model`, `world` (JSON), `signals` (JSON), `replayable` | latest 60 |
| `geny_rsi_runs` | a consolidation | `kind` (`consolidate`), `io_id` (the turn that started it), `status`, `start_version`, `adopted_version`, `result` (round summary, no payload), `error` | latest 200 |
| `geny_rsi_trajectories` | a turn | harness version, termination reason, policy tokens, call counts (no content) | all |

**What a world (`world` JSON) holds** — only what replay needs.

- What the model saw: the system parts (the material before the harness builds the prompt), the output schema, the node knobs that are not secret
  (temperature, tokens, thinking level, iterations, window size …), the earlier conversation, this input, turn state left by assembly
- The memory retrieval result of the first iteration
- The tool list (name, description, schema, exposure) and the result of every call (after the host result filter = what the model saw; each cut at
  32,000 characters)
- The turn's answer, status, termination reason, policy tokens and transcript

Not held: keys, credentials, client objects, and what the harness built (candidates rebuild it in replay). Images become placeholders. A world over
2 MB is kept with `replayable=false` and is not used for evaluation.

**Consolidation state (`state` JSON)**

| Field | Content | Bound |
|---|---|---|
| `t` | next round number | — |
| `records` | measured edit records (round, candidate, component, hypothesis, ΔS, ΔC, outcome, part of the diff) | latest 200 rounds |
| `progress` | cumulative accepted ΔS per round | — |
| `analysis` | names of the previous failure modes and success habits (keeps naming stable) | 12 each |
| `scoreboard` | whether the worlds an edit predicted actually moved | latest 40 |
| `cache` | harness version → world → replay results (answer, tokens, status, parameters read; no transcript) | 3 versions (current and adopted are kept), 2 per world |
| `verdicts` | hash of (criterion, reference, request, answer) → pass and reason | 4,000 |

**Pod memory (gone on restart; behaviour is the same without it)**

- The current-harness payload cache: 15 seconds per agent. The pod clears it at once on adoption, rollback or cloning.
- The consolidation table: agent → {running, pending flag, latest turn}.
- Harness directories: unpacked once per version (`XGEN_RSI_HARNESS_CACHE`, or a temp directory).
- One consolidation's temp directory: the harness git repository, the candidate workspaces, a copy of the history file. It is deleted when the
  consolidation ends. Each replay also uses a temp working directory and deletes it.

### Procedures that manage state specially

| Procedure | Why | How |
|---|---|---|
| One consolidation per agent | two consolidations editing the same state would split the history and round numbers | within a pod: a turn that ends while one runs only sets a pending flag, and one more consolidation runs afterwards for the latest turn (not one per accumulated turn). Across pods: a `running` record younger than 15 minutes means skip |
| Re-reading missed signals | skipped consolidations and turns on other pods must still be learned from | every consolidation reads up to 4 unread pairs for implicit signals; explicit signals are compared with XGEN's values every time |
| Ratings or expected answers added later | leaving a rating on the screen does not start a consolidation by itself | the consolidation after that agent's next turn finds it by comparison and uses it as a fresh signal |
| Interrupted consolidations | a pod restarting mid-consolidation leaves a `running` record | a `running` record older than 15 minutes is closed as `interrupted`; its age is computed against the DB's own clock (the column has no time zone, and the DB and the server may differ) |
| Rollback vs adoption | a consolidation must not overwrite a rollback the user made while it ran | just before adopting it checks that the current version is still the one it started from; if not, it does not adopt and records `kept` with the reason |
| Saving state | the next consolidation continues from it | whenever the consolidation returns a result (with or without a round) the whole `state` is overwritten; newly read signals are written to their world rows. If the consolidation ends with an exception (a `failed` record) the state is left as it was |
| Order within a conversation | the previous turn must be found correctly even after old worlds are deleted | `seq` = the conversation's maximum + 1 |
| Replay memory | replay must not touch the user's memory | the replay memory only returns the recorded retrieval and never writes (no execution record, no distillation either); replay is detected by a class attribute only |

### The exact replay rules

- The turn plan is rebuilt from the world; only the model, provider and credentials are switched to the current policy (the agent's current model).
- A tool call is answered from the record in this order:
  1. the same name and the same arguments (canonical JSON)
  2. arguments that differ only in whitespace or case
  3. a guide tool (a gate) gets its recorded text regardless of arguments
  4. anything else gets "no recorded result" (Dream-RSI's Child = ∅) as an error result. The replay continues, and off-support calls are counted.
- A call recorded several times is answered in order; once the records run out, the last result is given again.
- Meta tools that only read the registry (`ToolSearch`, `SelfExtendGuide`) really run on the replay registry. Tools the harness contributes (`ReadSkill` etc.) are contributed again by the replaying harness.
- With the same harness and the same model responses, replay sends the same requests (system, messages, tools) as the real turn (fixed by tests).
- Actions outside the record have no result, so a change that makes the agent use many new tools is at a disadvantage in replay. Such directions are
  verified by the real turns after an adoption, which become the next worlds.

### When no consolidation runs

- Agent Geny (the 21-stage runtime) never calls this hook.
- Turns of a frozen copy leave worlds but are not consolidated. A frozen copy's harness stays at the version it was made with.
- Claude Code / Codex agents leave no world, because the kernel does not own the loop. Their consolidation is recorded as `skipped`.
- If an administrator pins a harness with `XGEN_RSI_HARNESS_DIR`, every turn runs on it (it is checked before the agent harness).
  `XGEN_RSI_RECORD_WORLD=0` stops recording worlds.

**Measured (gpt-6-luna)**:
- A consolidation after a turn without a signal finished in 0.001 s.
- After the correcting turn, a consolidation adopted 70.5 s after the turn ended (XGEN-path E2E), and the very next turn of a new conversation ran
  on the adopted harness.
- With the library alone, correction to adoption took 52.9 s.

**Implementation status**

| Stage | Content | Status |
|---|---|---|
| 1 | Ship H0 only, fix parity defects between the two runtimes | 0.6.0 |
| 2 | Agent harness and trajectory host hooks, criteria checks | 0.7.0 |
| 3 | Turn world recording (`XGEN_RSI_RECORD_WORLD`), world replay, next-message signals, turn consolidation API (`xgen_rsi.consolidate`) | 0.8.0 · 0.8.1 |
| 4 | XGEN — consolidation at the end of each turn, world and state storage, consolidation log in the [Harness] tab | core !916 · workflow !2064 · frontend !2772 merged |


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

RRSI improves the harness in rounds. In production the turn consolidation opens a round (when a new signal says something needs fixing).

1. An analyst turns the trajectories of the worlds the current harness fails into failure modes.
2. A proposer makes harness edits within the edit budget b_t (it sees the agent's continuing edit history, exploration directives and pruning
   targets). A critic screens for leakage (conversation content), environment assertions and kernel intrusion.
3. The same worlds are replayed with each candidate. If the score rises above the noise floor δ the cost rule decides; inside the band an edit is
   adopted only if `100·ΔS − 15·ΔC + 0.5·ν > 0`. An edit to a parameter that was never read during the replays is rejected by a guard.
4. Adoptions form that agent's harness lineage (H0 → H1 → …) and every decision input is recorded. The very next turn runs on the adopted harness.

The same formulas also run offline evolution on a business suite (`rsi evolve`, the methodology experiments).

### 3. Dream-RSI — the record is the simulator

Dream-RSI's core is "a completed run, used as a replay simulator, lets you evaluate alternative policies immediately without expensive online runs".

- **In turn consolidation (production)** — one turn of the agent is one online run, and its world joins the simulator (𝓗_t = 𝓗_{t−1} ∪ {𝒯_t}).
  Harness candidates are measured by replaying those worlds (tool results from the record, actions outside it get ∅). The adopted harness is
  redeployed on the next turn, and that turn becomes a world again. Following §5.1 (history used as directional guidance narrows exploration),
  the critic rejects edits that put conversation summaries or lessons into the harness.
- **In exploration with a verifier (offline)** — when a task is tried several ways (`rsi dream explore`: a branch × attempt grid) the exploration
  policy π_E decides how many branches to open and when to stop. Recorded exploration trees are replayed to compare π_E candidates with Eq.1
  `V = max s − β1·N + β2·N/max(1,k★)`, and a replay winner must explore again for real and pass the **RRSI judgement (floor + cost rule)** to be
  promoted. Production conversation turns do not open branching exploration (there is no verifier).

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
pip install "xgen-agent-runtime-rsi @ https://github.com/PlateerLab/xgen-agent-runtime-rsi/releases/download/v0.7.0/xgen_agent_runtime_rsi-0.7.0-py3-none-any.whl"
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

**2. RRSI** — *Regularized Recursive Self-Improvement of Agent Harnesses* ([arXiv:2609.24972](https://arxiv.org/pdf/2609.24972)). It keeps the edit
space open and **regularizes the search trajectory**. On the proposal side: an annealed edit budget (Eq.4), credit assignment over the full history
(Eq.10/11), exploring untried components when stalled (Eq.13). On the selection side: leakage review, a noise-calibrated floor (Eq.5), the cost
rule on gains (Eq.7), the within-band rule (Eq.17), structural pruning (Eq.14) and domain guards. It produces **the same results as the official
implementation** (google-research/rrsi, Apache-2.0), checked by differential tests.

**3. Dream-RSI** — *Recursive Self-Improvement through Evolving Worlds* ([arXiv:2609.14858v1](https://arxiv.org/pdf/2609.14858v1)). Accumulated
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
- [Design 41 — turn consolidation (Korean)](docs/design/41-turn-consolidation.md) — Dream-RSI × RRSI after every turn: when the papers update, worlds, replay, signals, next-turn application
- [Usage guide (Korean)](docs/GUIDE.md) — library API, server settings, harness format, evaluation, evolution and exploration commands, updating the copy
- [Design docs map (Korean)](docs/README.md) — paper analysis, formula reference, runtime survey, fusion principles, architecture, I/O compatibility, evaluation, risks
- [Plan (Korean)](docs/PLAN.md) — stage plan, decisions, implementation status

## References

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

- PlateerLab, *xgen-agent-runtime* — geny (the 21-stage harness), the multi-provider layer and the host contract. Version 4.81.0 is copied as `xgen_rsi.base` (Apache-2.0)

## License

Apache-2.0. Notices for parts taken and modified from the official RRSI implementation (Apache-2.0) and for the xgen-agent-runtime copy are in [NOTICE](NOTICE).
