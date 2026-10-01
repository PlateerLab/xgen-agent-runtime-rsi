---
name: constraint-satisfaction-manual-solving
description: Derive a schedule, assignment or other combinatorial solution by hand when no code-execution tool can run a written solver, so the result actually satisfies every stated constraint rather than just looking plausible.
---
1. If a code-execution tool is listed among your available tools, use it to run a solver (brute-force or backtracking) and take its printed output as the answer; do not hand-derive when execution is available.
2. If no code-execution tool is listed, first list every constraint from the request verbatim in a scratch file: fixed assignments, ordering/precedence rules, exclusivity or conflict rules, and any count or capacity limits.
3. Propagate forced values first: assign any slot/value that is uniquely determined by a single constraint before guessing anything.
4. When more than one value remains possible for a slot, explicitly write down each remaining candidate branch, then test each candidate against every other constraint (not just the one that created the branch) and discard any branch that produces a conflict.
5. Only commit to a branch once it is the single one left that violates no constraint; if several branches still look valid, re-read the constraint list for a rule not yet applied before choosing.
6. Completion check: after writing the final deliverable, go through every constraint in the scratch list one more time against the final values and confirm each one holds; do not finish on a self-affirmation without this item-by-item re-check.
