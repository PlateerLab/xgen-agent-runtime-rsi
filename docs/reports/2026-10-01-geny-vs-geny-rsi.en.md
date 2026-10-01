# geny vs geny-rsi — detailed comparison report (2026-10-01)

> Subjects: the existing engine **geny** (the 21-stage pipeline of xgen-agent-runtime) and the new engine **geny-rsi** (this repo's kernel + harness + RRSI·Dream-RSI).
> Models: **gpt-6-sol** (OpenAI, Responses API) and **claude-sonnet-5** (Anthropic). Experiments run on 2026-10-01.
> Raw summary: [data/2026-10-01.json](data/2026-10-01.json), produced by `experiments/summarize.py` (no transcripts or answers).
> The Korean version is the primary text: [2026-10-01-geny-vs-geny-rsi.md](2026-10-01-geny-vs-geny-rsi.md).

**Summary**
- **It swaps in.** Replaying real responses, geny-rsi H0 sends byte-identical requests to geny (sonnet-5 81/81, gpt-6-sol 77/77). Score and token differences are model sampling variance.
- **These two models cannot show an improvement.** gpt-6-sol scores 0.99–1.0 even on xgen-hard, and sonnet-5 is perfect on xgen-core. The harness RRSI adopted for gpt-6-sol (H\*) scores 1.000 on the held-out split, the same as H0's 0.997 within noise, and uses 5% more tokens.
- **Dream-RSI found the same best score with less exploration.** An exploration policy developed by gpt-6-sol was promoted after a live check with −25% attempts and −16% tokens.
- **Real runs exposed a defect.** An edit the evaluation never read (turning off memory archiving) was adopted through the novelty bonus. 0.2.0 blocks it with a guard, and the production harness (`sol-xgen-hard`) reverts that edit to the H0 value (§8).
- The next measurements use models below the ceiling (gpt-6-luna, claude-haiku-4-5) and a harder suite.

---

## 1. What was compared

| Name | What |
|---|---|
| **geny** | the existing 21-stage engine of xgen-agent-runtime (`AgentTurnExecutor`) |
| **geny-rsi H0** | this repo's kernel + the starting harness H0 (the production behaviour expressed as components), before evolution |
| **geny-rsi H\*** | the harness adopted after RRSI evolution from H0 (one per model) |
| **π₁ / portfolio / π\*** | live exploration policies: the paper's initial policy (parallel refine), a hand-written portfolio, and the policy promoted by a Dream cycle |

Four questions:

1. **Can it be swapped in?** Given the same policy and input, does geny-rsi H0 do the same thing as geny? (Experiments 1, 2, 3)
2. **Same score at the same cost?** Score, tokens and time. (Experiments 1, 3)
3. **Does it improve itself?** After RRSI evolution, do score and cost improve on the held-out split? (Experiment 4)
4. **Does it spend exploration compute better?** Does a Dream-RSI cycle pick a policy that finds the same best score with less exploration? (Experiment 5)

---

## 2. Method

**Common conditions**
- Runtime: both engines on the xgen-agent-runtime **4.78.0** wheel, with the same provider layer (clients, retries, Responses API).
  (These runs used geny-rsi 0.1.0–0.2.0, when it used the runtime as a library. From 0.3.0 geny-rsi does not depend on the runtime and uses `xgen_rsi.base`, a copy of 4.80.0.)
- Host: `EvalHost` (LocalHost). Each trial gets an isolated workspace, and the five built-in file tools (Read·Write·Edit·Glob·Grep) are confined to it.
  Seed-file modification times are fixed in path order, so Glob's result order is deterministic (see §4).
- Policy settings: model defaults (temperature 0.7 requested; reasoning models follow the provider's rules), memory and self-evolution off, per-task iteration caps (xgen-core default, xgen-hard 30).
- Keys: passed only through policy JSON files; never written to the repo, records or the frontier.

**Suites (both built in; 8 categories × 4 tasks; evolve 24 · heldout 8 · smoke 2)**
- `xgen-core`: one-rule business tasks (CSV aggregation, document field extraction, weekly-report synthesis, config edits, format contracts, computation, Korean meeting notes, file-name recovery).
- `xgen-hard`: tasks aimed at where a harness matters.
  - Volume: orders 90+ rows, survey 55+ rows, customers 70+ rows.
  - Rule documents: correction rows, statuses, notation normalization, returns, VAT-inclusive totals, duplicate responses and customers.
  - Traps: an amendment effective after the trip date, a rescheduled event, a second deferred item, a conditional change, discarded and last-quarter files.
  - Also two-stage consistency, schema compliance and relative dates.
- All answers are computed by code. For every task, tests pin **oracle = full marks and empty answer ≤ 0.5**, so the verifiers themselves are verified.

**Metrics**
- **S** = check-weighted mean reward (passed checks / all checks, weight = number of checks; RRSI Eq.3). Missing (infrastructure) trials count as r 0 and stay in the denominator. ± is the standard error over task means.
- **Tokens** = external usage per trial (input + output), defined the same way by both engines. RRSI's Ĉ is geny-rsi's ledger policy tokens c(τ), counting every call.
- **Time** = wall-clock seconds per trial, mostly provider latency.

---

## 3. Experiment 1 — xgen-core: geny vs geny-rsi H0

64 trials per condition (evolve 24 × k2 + heldout 8 × k2), 0 missing.

| Model | Split | geny S | geny-rsi S | geny tokens | geny-rsi tokens | geny s | geny-rsi s |
|---|---|---|---|---|---|---|---|
| gpt-6-sol | evolve | 0.9189 ±0.030 | 0.9189 ±0.030 | 3,224 | 3,270 | 5.3 | 5.0 |
| gpt-6-sol | heldout | 0.9143 ±0.054 | 0.9143 ±0.054 | 3,663 | 3,477 | 5.6 | 5.5 |
| claude-sonnet-5 | evolve | 1.0000 | 1.0000 | 6,807 | 7,606 | 6.5 | 6.7 |
| claude-sonnet-5 | heldout | 1.0000 | 1.0000 | 7,281 | 7,467 | 7.2 | 6.5 |

- Scores are **identical** in all four conditions. gpt-6-sol fails the same "owner/due" checks of the meeting-notes tasks on both engines, so this is a model weakness, not the engine.
- Token differences go both ways (+1.4%, −5.1%, +11.7%, +2.6%). On single-call tasks the engines are nearly equal (e.g. sonnet-5 computation 2,550 vs 2,552).
  The differences concentrate in multi-step tasks where the number of calls varies. For example, when the model's first search is `Glob("reports/**")`, it finds no files and searches once or twice more, on both engines.
  Experiment 2 answers deterministically whether the engines behave differently given the same responses.
- sonnet-5 is at the ceiling on xgen-core, so experiments 3 and 4 use xgen-hard.

---

## 4. Experiment 2 — deterministic replay equivalence

**Method**
1. Run each task once on geny for real, recording the provider responses in order (including thinking blocks and signatures).
2. Run geny and geny-rsi again with a client that returns those responses as a script. Tools really execute in fresh workspaces.
3. Compare the requests both engines sent to the provider (system, messages, tools, model settings) **call by call**. Only timestamps and workspace paths are normalized (`experiments/replay_equivalence.py`).

| Model | Tasks | Model calls | Identical requests | Same call count | Same answer | Same usage | Same score |
|---|---|---|---|---|---|---|---|
| claude-sonnet-5 | 32 | 81 | **81/81** | 32/32 | 32/32 | 32/32 | 32/32 |
| gpt-6-sol | 32 | 77 | **77/77** | 32/32 | 32/32 | 32/32 | 32/32 |

- The requests are byte-identical even on the 48 multi-step tasks (2–8 calls). This covers sonnet-5's thinking blocks and signatures and gpt-6-sol's Responses API reasoning items.
- The first attempt (155 model calls) diverged on 9 calls. The cause was **environmental nondeterminism**, not the engine. Glob sorts by modification time, and task files written in quick succession share timestamps, so the result order varied between runs.
  Fixing the seed-file modification times made all 158 calls identical. The fix applies to all evaluations, which improves reproducibility.
- Conclusion: **given the same responses, geny-rsi H0 sends the same requests as geny.** The token and time differences in experiments 1 and 3 are model sampling variance and provider latency.

---

## 5. Experiment 3 — xgen-hard: geny vs geny-rsi H0

64 trials per condition (evolve 24 × k2 + heldout 8 × k2), 0 missing.

| Model | Split | geny S | geny-rsi S | geny tokens | geny-rsi tokens | geny s | geny-rsi s |
|---|---|---|---|---|---|---|---|
| gpt-6-sol | evolve | 0.9886 ±0.011 | 0.9924 ±0.007 | 8,721 | 9,113 | 12.3 | 11.8 |
| gpt-6-sol | heldout | 1.0000 | 0.9943 ±0.009 | 9,421 | 9,487 | 12.5 | 13.0 |
| claude-sonnet-5 | evolve | 0.8996 ±0.034 | 0.9053 ±0.044 | 19,914 | 20,498 | 43.8 | 46.1 |
| claude-sonnet-5 | heldout | 0.8295 ±0.114 | 0.8977 ±0.062 | 18,402 | 22,923 | 54.2 | 57.8 |

Mean reward per category (evolve, geny / geny-rsi). Categories not listed are 1.0 for both models and both engines.

| Category | gpt-6-sol | claude-sonnet-5 |
|---|---|---|
| two-stage survey pipeline | 0.857 / 0.905 | 0.714 / 0.762 |
| policy Q&A (amendments) | 1.0 / 1.0 | 0.917 / 0.933 |
| rule-document order aggregation | 1.0 / 1.0 | 0.538 / 0.538 |

- Every score difference between the engines is within the standard error. The largest-looking one, sonnet-5 heldout (0.83 vs 0.90), comes from a single order-aggregation task (geny 0/2 successes, geny-rsi 1/2).
  That task scores either 1.0 or 0.08, so differences like this are easy at k=2. The final comparison of experiment 4 re-measures it at k=4.
- sonnet-5's order-aggregation failures look the same on both engines. The response hits the output cap (8,192 → retried at 16,000 tokens), nothing usable is left, and the turn ends with an empty answer and no report.json (about 160 s).
  The model tries to compute 90–110 rows in text. A harness can address this (write intermediate results to files, output settings, continue after a truncated response).
- xgen-hard lowered the ceiling as intended, especially for sonnet-5 (evolve 0.90, heldout 0.83–0.90). gpt-6-sol stays near 0.99 on xgen-hard too, so it has little room to improve its score.


---

## 6. Experiment 4 — RRSI harness evolution (xgen-hard)

**gpt-6-sol (T=6, two candidates per round, evolve 24 tasks × k2)**
- δ calibration: the base harness evaluated twice (S 0.9924 · 0.9886) → δ = 0.0076.

| Round | Result | What |
|---|---|---|
| r0 | A adopted | a procedure block in the prompt. S 0.992 → 1.000, Ĉ −1.6%. B rejected for higher cost |
| r1 | none | both candidates at S 1.000 but costlier, rejected by the cost rule |
| r2 | none | slightly lower scores (0.994 · 0.992) with little cost gain, rejected |
| r3 | A adopted | turn off `memory.archive`. **An edit the evaluation never reads, adopted through the ν bonus** (§8) |
| r4 | B adopted | a prompt procedure for averages + old-tool-output pruning threshold 30,000 → 24,000 tokens. S 1.000 |
| r5 | none | the proposer ran past its turn limit, no proposal |

Held-out split (8 tasks × k4, never used for judging):

| | S | tokens/trial | s/trial |
|---|---|---|---|
| geny | 0.9858 ±0.011 | 9,479 | 12.5 |
| geny-rsi H0 | 0.9972 ±0.004 | 9,309 | 13.4 |
| geny-rsi H\* | 1.0000 | 9,812 | 14.0 |

- H\*'s score gain is within noise and it uses 5.4% more tokens than H0. One of its three changed addresses (`memory.archive`) is an unmeasured edit, so the bundled harness `sol-xgen-hard` reverts only that item to the H0 value (it was never read in evaluation, so the measured behaviour is the same).

**claude-sonnet-5** — started with the same settings; no adoption in r0, stopped during r1 (decided to redo with models below the ceiling).
Held-out k4 comparison of H0: geny 0.8125 ±0.119, geny-rsi H0 0.8636 ±0.087. The gap comes from two tasks, order aggregation (0/4 vs 1/4 successes) and the survey pipeline (0.54 vs 0.71), and is within the standard error.

---

## 7. Experiment 5 — Dream-RSI exploration policy improvement (gpt-6-sol)

- Worlds: 40 trees explored live with the initial policy π₁ and the portfolio (replayed, no new generation).
- Cycle 1: three candidates compared by replay (Eq.1 V) → the developed policy `r0002_dev` selected.
- Live check (dream8 split, 8 tasks, same exploration grid): best score 1.0 for both, **attempts 72 → 54 (−25%)**, **policy tokens 433,669 → 363,750 (−16.1%)**.
  RRSI judgement admissible (ΔS 0, ΔC −16.1%) → promoted. β stays 0.6 (fewer than three live cycles).
- The claude-sonnet-5 cycle was stopped during cycle 1.

---

## 8. Defects found and fixed during review and real runs

These defects surfaced in the pre-release code review (a read-only audit) and while running real models. All are fixed and have regression tests.

| Defect | How it surfaced | Fix |
|---|---|---|
| Runtime 4.77/4.78 changes (memory for unfinished turns, the turn-context injection point) were missing from the RSI kernel | diffing the runtime | turn-context injection calls the runtime function directly; unfinished-turn recording uses the runtime function as is; equivalence tests added (they fail without the port) |
| Provider errors after retries (`[ERROR] …`) were scored with partial credit instead of as missing | review + measurement | counted as missing (r 0, kept in the denominator), seen by the validity gate and re-measurement; their tokens are excluded from Ĉ |
| On resume, records of the current round leaked into N_t and 𝒯_t and could flip a decision | review | only records before t are counted |
| Result and history files were not written atomically, so a crash could block resume | review | tmp + rename; unreadable outcomes re-run, a truncated last history line is ignored |
| The exploration-policy sandbox's time limit was defeated by one long C call, and it had no memory limit | review + measurement (`sum(range(10**10))` returned past the limit) | policy episodes run in a forked child with CPU and memory limits; the parent kills it and records a timeout |
| Exact rational ties (ΔS = δ, S = S★ − δ) flipped through floating-point error (78 of 175 cases with δ = 3/178) | review + measurement | a 1e-12 tolerance in xgen mode only (paper mode stays 0, like the reference) |
| Credentials in unknown config keys and role blocks were written to frontier.json | review + measurement | key- and token-like values are masked in config dumps |
| The online confirmation skipped the measurement validity gate | review | judged only when both live measurements are valid |
| The role LLM cached one async client and reused it across event loops and threads → "Connection error" | **a real evolution run** (parallel digester calls in round 0) | a client is created and closed inside each call's loop; regression test with a loop-bound fake client |
| A relative run directory from the CLI resolved against the harness repo inside git worktree | **a real evolution run** (baseline step) | the run directory is made absolute |
| The prompt component could not take plain strings in `extra_blocks`, breaking the turn | **a real evolution run** (both gpt-6-sol round-0 candidates failed smoke) | accepts strings and `{"id","text"}` objects; that round was voided and re-run from the same analysis (§6) |
| Glob's result order varied between runs, so requests diverged even with the same responses | first attempt of experiment 2 | fixed seed-file modification times |
| **An edit the evaluation never read was adopted.** gpt-6-sol r3A turned off `memory.archive.params.archive`. The evaluation runs with memory off, so that value is never read and ΔS −0.0019 · ΔC +1.6% are noise, yet the novelty bonus (ν=1) carried it over the within-band rule. Used in production with memory on, this harness would stop conversation archiving (an unmeasured change) | review of a **real evolution run** | components record the parameter addresses they read in the trajectory record; a candidate that edits a parameter no trial of its evaluation read is rejected by a domain guard (0.2.0). The gpt-6-sol H\* from before this fix contains that edit; the bundled harness `sol-xgen-hard` reverts only that item to the H0 value |

---

## 9. Limitations

- **Ceiling.** Both models solve most tasks, so harness differences do not show up in scores. The conclusion goes as far as "equivalent", not yet "better".
- **Sample size.** 8–24 tasks × k 2–4, one evolution run and one Dream cycle per model. A single task's success or failure moves the mean a lot.
- **Suites.** xgen-core and xgen-hard were built in this repo (answers computed in code, verified by oracle and empty-answer tests).
- **Runtime version.** Experiments 1, 3, 4 and 5 ran on runtime 4.78.0. On 4.80.0 only replay equivalence was re-checked.

---

## 10. Reproduction

Keep the policy JSON (`{"provider", "model", "api_key"}`) and the RRSI config (`rrsi.json`, including role models) outside the repo.

```bash
rsi suite build runs/xgen-core
rsi suite build runs/xgen-hard --suite xgen-hard

# experiments 1 and 3 — geny vs geny-rsi H0
python experiments/compare_engines.py --policy sonnet5=policy-sonnet5.json --suite runs/xgen-hard --out runs/e3 --k 2
# experiment 2 — deterministic replay equivalence
python experiments/replay_equivalence.py --policy sonnet5=policy-sonnet5.json --suite runs/xgen-core --tasks $(ls runs/xgen-core/tasks | sed 's/.json//') --out runs/e2b
# experiment 4 — RRSI evolution (baseline and δ can reuse experiment 3's measurements copied to jobs/base and jobs/base_r2)
rsi evolve init runs/evo-sonnet5 --suite runs/xgen-hard --policy policy-sonnet5.json --config rrsi-sonnet5.json
rsi evolve run runs/evo-sonnet5 --T 6
experiments/heldout_final.sh sonnet5 policy-sonnet5.json runs/xgen-hard 4
# experiment 5 — Dream-RSI
experiments/dream_experiment.sh sonnet5 policy-sonnet5.json runs/xgen-hard runs/dream-sonnet5 0.02 runs/e3/sonnet5/geny-rsi/evolve
# tables
python experiments/summarize.py --runs runs --out docs/reports/data/2026-10-01.json
```
