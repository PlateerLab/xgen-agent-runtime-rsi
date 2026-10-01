# geny vs geny-rsi on models below the ceiling — gpt-6-luna · claude-haiku-4-5 × xgen-pro (2026-10-02)

> The first report ([2026-10-01](2026-10-01-geny-vs-geny-rsi.en.md)) concluded that gpt-6-sol and claude-sonnet-5 sit at the ceiling
> (0.9–1.0), so no harness improvement could be shown. This time we measured **models below the ceiling** on a **harder suite (xgen-pro)**.
> Raw summary: [data/2026-10-02.json](data/2026-10-02.json) (`experiments/summarize.py`, no conversation contents or answers).

**Summary**

- **Swapping in loses nothing.** Before evolution, geny-rsi (H0) scores within noise of the existing runtime (geny) for both models and
  both splits (luna 0.919 vs 0.906, haiku 0.826 vs 0.810 — evolve, k=2).
- **luna: RRSI raised the evolve score, but it did not carry over to the held-out split.** Two adoptions in five rounds, evolve 0.919 → 0.948.
  On the held-out split never used for judging (k=4), H\* 0.911 vs H0 0.907 vs geny 0.916 — within noise, with 26% more tokens than H0.
  **So it is not bundled.**
- **haiku: edits that cut cost at the same score level appeared.** The edit adopted in round 0 cut tokens by 10.6% with the score inside the
  band (−0.018). A voided earlier run also produced an edit with the same score and −45.8% tokens (§6, voided for an environment assertion).
  The haiku evolution **stopped in round 2 when the API credit ran out**; it will be updated after it resumes.
- **Measurement and guards did real work.** The cost rule rejected 7 of 10 candidates that only added cost (luna). Three defects were found and
  blocked during the runs — an empty analysis, edits that hard-code the evaluation environment, and missing trials recorded during a credit outage (§8).
- **Dream-RSI: a policy with 36% fewer exploration attempts was found but not promoted, since cost did not fall.** The luna exploration policy
  developed by gpt-6-sol found the same best score with 72 → 46 attempts, but policy tokens were +0.16%, and the confirmation judgement rejected it.
- **Bottom line:** today Agent Geny RSI runs H0 (= the same behaviour as Agent Geny) for every model. What geny-rsi achieved "more strongly" is not
  yet **score** but **measured change control** — rejecting losing changes automatically, recording the evidence for each adoption, blocking
  edits that would harm production — plus cost-saving candidates for haiku.

---

## 1. Why measure again

In the first experiment gpt-6-sol scored 0.99 even on xgen-hard and sonnet-5 was perfect on xgen-core. With no room for the harness to improve,
RRSI only measures noise. This time we used (1) two light, cheap models — **gpt-6-luna** (OpenAI) and **claude-haiku-4-5** (Anthropic) — and (2)
**xgen-pro**, a suite that pushes harder on the points where the harness decides the outcome.

## 2. The xgen-pro suite

8 categories × 4 tasks (evolve 24 · heldout 8 · smoke 2). Every answer is computed in code, and each task is fixed by tests to give the
**oracle full marks and an empty answer ≤ 0.5**.

| Category | What it targets |
|---|---|
| Minutes synthesis | across 20 meeting notes (70k+ characters), track decisions, withdrawals, owner changes, deadline changes and an errata file; ignore drafts |
| Ledger close | match payments, invoices and daily FX rates; normalize references; round to the won; classify paid, partial, over and outstanding |
| Bulk transform | normalize, deduplicate and sort 100+ contact rows into one CSV (long exact output) |
| Config migration | 24 services; change only one of two identically named keys in `[server]`/`[db]`; keep frozen services; remap ports |
| Policy precedence | 12 amendments (3 withdrawals, division and grade scopes), 3 personal exceptions, 12 questions |
| Schedule puzzle | 10–11 meetings · 3 rooms · 4 slots, no attendee overlap, a unique solution |
| Search & recover | find 12 targets among 56+ documents (140+ files with revisions) by content and pick the latest revision (decoy mentions and temp copies excluded) |
| Audit corrections | 140+ sales rows; fix only the 8 wrong items among 16 in a colleague's report |

**Difficulty calibration.** Three rounds of pilots on the 8 held-out tasks, one trial each: luna 0.896 (v1) → 0.934 (v2, hardened categories) →
0.932 / 0.908 (v3, scaled up). Even on v3 luna is perfect on config, policy and search and breaks down on the bulk-computation and long-output
categories (ledger, contacts, audit). haiku has room in almost every category. Perfect categories measure "does the harness break an existing ability".

## 3. Method

- **Engines.** geny = the 21-stage engine, run from the copy inside rsi (`xgen_rsi.base`); **517 of 519 files are byte-identical to xgen-agent-runtime
  4.80.0** (the other 2: the version stamp and a docstring path, `COPY.json`). geny-rsi = kernel + harness (H0 or an evolved one).
- **Policies.** gpt-6-luna, claude-haiku-4-5. The evaluation host gives five file tools (Read, Write, Edit, Glob, Grep) — **no code-execution tool** (see §8-2).
- **RRSI role models.** Proposer, critic, analyst and digester: gpt-6-sol for luna, claude-sonnet-5 for haiku (a strong proposer edits the harness of a
  weaker policy). T=5 rounds, two candidates per round, evolve 24 tasks × k2.
- **Noise floor δ.** Calibrated from two evaluations of the base harness (z=2): luna 0.025, haiku 0.032. The second base evaluation is experiment 1's
  geny trials — that both engines send the same requests was measured in the first report's experiment 2 (replay equivalence).
- **Metrics.** S = check-weighted mean reward, ± = standard error of task means. Tokens = external usage per trial (input + output); RRSI's Ĉ is the same value.

## 4. Experiment 1 — before evolution: geny vs geny-rsi H0 (k=2)

| Model | Split | geny S | H0 S | geny tokens | H0 tokens | geny s | H0 s |
|---|---|---|---|---|---|---|---|
| gpt-6-luna | evolve | 0.906 ±0.043 | 0.919 ±0.037 | 41,494 | 39,971 | 40 | 41 |
| gpt-6-luna | heldout | 0.925 ±0.078 | 0.942 ±0.057 | 46,645 | 49,450 | 41 | 48 |
| claude-haiku-4-5 | evolve | 0.810 ±0.058 | 0.826 ±0.058 | 427,815 | 311,274 | 115 | 104 |
| claude-haiku-4-5 | heldout | 0.744 ±0.114 | 0.766 ±0.112 | 523,729 | 570,971 | 138 | 154 |

Mean reward per category (evolve + heldout, geny / H0):

| Category | luna | haiku |
|---|---|---|
| Minutes | 0.87 / 0.85 | 0.57 / 0.75 |
| Ledger | 0.70 / 0.69 | 0.48 / 0.48 |
| Contacts | 0.44 / 0.64 | 0.31 / 0.26 |
| Config | 1.00 / 1.00 | 1.00 / 1.00 |
| Policy | 0.99 / 1.00 | 0.92 / 0.90 |
| Schedule | 0.97 / 0.88 | 0.48 / 0.56 |
| Search | 1.00 / 1.00 | 0.81 / 0.82 |
| Audit | 0.66 / 0.77 | 0.29 / 0.31 |

- H0 is slightly higher in all four cells, all within δ. Before evolution geny-rsi performs like the existing runtime (consistent with the first report's replay equivalence).
- haiku spends 300k–570k tokens per trial (about 10× luna), rereading the same content in long loops. The cheaper the model, the larger the share
  of **cost** a harness can cut.

## 5. Experiment 2 — RRSI harness evolution

### gpt-6-luna (T=5, δ=0.025, base S 0.919)

| Round | Candidate | Edits (components) | S | ΔS | ΔC | Result |
|---|---|---|---|---|---|---|
| r0 | A | prompt, skill | 0.934 | +0.015 | +16.4% | rejected by the cost rule |
| r0 | B | prompt ×4 | 0.925 | +0.007 | +29.9% | rejected by the cost rule |
| r1 | A | skill | 0.932 | +0.013 | +24.4% | rejected by the cost rule |
| r1 | **B** | skill (record-accounting procedure) | 0.928 | +0.009 | +1.2% | **adopted** (within band, new structure ν=1) |
| r2 | A | skill ×2 | 0.924 | −0.003 | −0.7% | rejected by the cost rule |
| r2 | B | prompt | 0.912 | −0.016 | +4.3% | rejected by the cost rule |
| r3 | **A** | skill revision, context_mgmt (prune later) | 0.948 | +0.020 | +6.7% | **adopted** (within band) |
| r3 | B | context_mgmt, prompt | 0.935 | +0.008 | −1.4% | admissible, lost on score |
| r4 | A | skill ×2 | 0.930 | −0.018 | +20.0% | rejected by the cost rule |
| r4 | B | skill | 0.939 | −0.009 | +18.6% | rejected by the cost rule |

- H\* = both adoptions: a table-export skill (account for every source row as retained, excluded or superseded, and check the written row count in
  one bounded pass) + a higher threshold for pruning old tool output (`prune_over_tokens`). Evolve S 0.919 → 0.948 (+0.029, above δ).
- The failure modes the analyst found: omitted or misordered contact exports, payment reconciliation errors, unnecessary audit corrections, premature
  "impossible" conclusions, and later withdrawals not applied. The adopted edits target the first (omissions) and the fifth (early content pruned away
  in long documents).

### claude-haiku-4-5 (T=5, δ=0.032, base S 0.826) — stopped in round 2

| Round | Candidate | Edits | S | ΔS | ΔC | Result |
|---|---|---|---|---|---|---|
| r0 | **A** | prompt (write, read back and check the format, once) + skill ×2 (conditional: worksheet and constraint propagation when no code-execution tool is listed) | 0.808 | −0.018 | **−10.6%** | **adopted** (within band, lower cost) |
| r0 | B | prompt, skill | 0.839 | +0.013 | +16.0% | rejected by the cost rule |
| r1 | A, B | — | — | — | — | stopped during evaluation: **Anthropic API credit exhausted** |

The 22 trials after the credit ran out were recorded as missing. Missing trials count as 0, so leaving them would corrupt the judgement — the evolution
was stopped and those trials deleted (they rerun on resume).

## 6. Experiment 3 — final held-out comparison (gpt-6-luna, 8 held-out tasks × k4)

| | S | tokens per trial | s |
|---|---|---|---|
| geny | 0.916 ±0.071 | 43,707 | 39 |
| geny-rsi H0 | 0.907 ±0.074 | 50,619 | 43 |
| geny-rsi H\* | 0.911 ±0.067 | 63,576 | 44 |

Paired per-task differences (task means): H\*−H0 +0.018 ±0.036, H\*−geny −0.015 ±0.050, H0−geny −0.033 ±0.037. By category H\* is above H0 on
ledger (+0.19) and audit (+0.13) and below on schedule (−0.15) — with one task per category, a single task's success swings the mean.

**Interpretation.** The evolve gain of +0.029 did not carry over to the held-out split, and cost rose. A likely reason: both adoptions came in through the
within-band rule (a gain inside the noise plus cost and novelty terms) — neither edit cleared δ on its own. So this H\* is **not bundled** (the README's
bar: a gain above noise, or lower cost at the same score, confirmed on the held-out split).

## 7. Experiment 4 — Dream-RSI exploration policy (gpt-6-luna)

Worlds: 16 trees explored live on the first task of each category (dream8) by the initial policy π₁ and the portfolio + 24 experiment-1 trials = 40.

| Developer | Replay selection | Live check (same grid) | Result |
|---|---|---|---|
| luna itself (the paper's setting) | both candidates have the same V as the incumbent (1.0253) | — | incumbent kept |
| gpt-6-sol | r0002 (V 1.0284 > 1.0253) | best 1.0 = 1.0, **attempts 72 → 46 (−36%)**, policy tokens 1,767,375 → 1,770,254 (+0.16%) | **rejected** (cost rule) |

- The two policies luna wrote kept exploring after finding a perfect score and opened every world to the end — the same behaviour as π₁, hence the same
  value. Stopping after the first batch scores higher in replay (1.043 vs 1.030), so there was room to improve.
- gpt-6-sol's policy cut attempts by 36%, but each attempt was longer, so tokens did not fall. Replay looks at attempt counts while the confirmation
  judgement looks at tokens; the two disagreed, and the confirmation step blocked the promotion — as designed.
- haiku Dream was not run (credit).

## 8. Defects found and fixed in the real runs

| Defect | How it surfaced | Fix |
|---|---|---|
| **The analyst produced an empty report** — on a suite with long trajectories it kept requesting more digests until the 30-turn cap and ended with 0 failure modes; the proposer edited without analysis | luna round 0 log `top modes: []` | tell the analyst how many turns remain and **force the report** at the cap; keep the analyst transcript. That round was voided and rerun (afterwards: 5 failure modes in 13 turns) |
| **An edit hard-coding the evaluation environment was adopted** — "this workspace has no code-execution tool; scripts never run". True for the evaluation host, false in XGEN production (sandboxed Bash). The edit gave the same score with −45.8% tokens | review of haiku round 1's adopted edit | critic and constitution rule 11: no statements about what the environment has or lacks; tool guidance only as conditionals ("if listed …, otherwise …"). That run was voided and restarted (all later adopted edits are conditional) |
| **Credit-outage trials piled up as 0-score missing trials** | 400 errors during haiku round 1 evaluation | stopped the evolution and deleted the 22 outage trials so they rerun on resume |

The defect fixed earlier (adopting an unmeasured edit → the read-parameter guard, 0.2.0) did not recur in these runs.

## 9. What it achieved more strongly — and what it has not yet

| | Shown by measurement | Not yet shown |
|---|---|---|
| Swap-in | before evolution, geny-rsi performs like the runtime for both models and splits (differences within δ) | — |
| Score | luna +0.029 on evolve (above δ) | a score gain that carries over to the held-out split |
| Cost | haiku: −10.6% tokens at the same score level (adopted), −45.8% (voided run) | a cost cut confirmed on the held-out split |
| Change control | automatic rejection of cost-only candidates (luna 7/10), every judgement input recorded, harmful edits (unmeasured, environment assertions) blocked | — |
| Exploration | a policy reaching the same best score with −36% attempts | token savings — rejected by the confirmation |

**Production state:** no adopted harness is bundled yet. In XGEN, Agent Geny RSI runs H0 for every model, which behaves like Agent Geny.

## 10. Limitations

- **Evaluation vs production.** The evaluation host gives no code-execution tool for safety. XGEN's production Agent Geny has a sandboxed Bash, so the
  bulk-computation categories (ledger, contacts, audit) are much easier in production. These numbers are for an environment without code execution.
- **Sample size.** The held-out split has one task per category (8 tasks), so a single task moves the mean a lot (± 0.07); differences of 0.02–0.03 are hard to resolve.
- **Within-band adoption.** RRSI's within-band rule (with the novelty term) adopts gains inside the noise. That helps exploration; the bar for production
  is a separate held-out confirmation.
- **Selection rule.** The highest score among admissible candidates wins (the paper's Algorithm 2). In round 0 of the voided haiku run, a −30.5% cost
  candidate lost by a 0.003 score difference (within noise).
- **haiku incomplete.** Evolution 2/5 rounds; held-out comparison and Dream not run (credit).

## 11. Reproduction

```bash
rsi suite build runs/xgen-pro --suite xgen-pro
python experiments/compare_engines.py --policy luna=policy-luna.json --suite runs/xgen-pro --out runs/p1 --k 2      # experiment 1
experiments/start_evo_after_e1.sh luna policy-luna.json rrsi-luna.json runs/xgen-pro runs/p1                    # experiment 2
E1=runs/p1 experiments/heldout_final.sh luna policy-luna.json runs/xgen-pro 4                                   # experiment 3
experiments/dream_experiment.sh luna policy-luna.json runs/xgen-pro runs/dream-luna 0.025 runs/p1/luna/geny-rsi/evolve   # experiment 4
```

Policy and role JSON files contain keys; keep them outside the repository.
