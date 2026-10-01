<!--
Portions adapted from google-research/rrsi (commit be50316, domains/*/PATTERNS.md: the
pattern-library format), Copyright 2026 The rrsi Authors / Google LLC, Apache License 2.0;
modified by PlateerLab.
-->
# Pattern Library (reference, not an allowlist)

Known mechanism families for business-agent harnesses, expressed in what this
harness can change (params, prompt blocks, skill files, component presence),
with concrete examples and generic traps. You may adopt, adapt, ignore, or
invent mechanisms not listed here.

## 1. Deliverable routing (computed content must land in the file)

The most common business-agent tax: the agent computes the right values in
its reasoning or chat and then writes an incomplete deliverable, the wrong
file name, or no file at all. Mechanisms: a prompt block that ties a
condition to a concrete procedure ("when the request names an output file,
write it with exactly that name and format, then Read it back once and compare
it with the request"); keeping completion review enabled so a final answer
without the promised deliverable gets one more pass; a skill holding the
procedure for a recurring deliverable format (CSV with a header, JSON with
required keys, an edited document that keeps unrelated content).

Generic trap: "always double-check everything" — unbounded verification burns
the iteration budget. Bound it: one read-back pass, then finish.

## 2. Request coverage (every requested item, nothing invented)

Agents often answer the first half of a multi-part request. Mechanisms: a
block that makes the policy enumerate the requested items before acting and
tick them off before the final answer; a skill for decomposing a request into
a checklist. Prefer conditions the policy can check from the request text.

Generic trap: a checklist template keyed to THIS evolve set's request wording
is leakage. Describe the procedure, not the tasks.

## 3. Tool surface and execution

Failures where the agent could not reach a tool it needed, re-opened the same
tool repeatedly, or interleaved dependent tool calls. Mechanisms: restoring
previously used tools, gate reachability, sequential execution when calls
depend on each other; concurrency only when calls are independent.

Generic trap: exposing more tools "just in case" grows every prompt (cost)
and dilutes tool choice. Change the surface only against clear evidence.

## 4. Context budget

Long tasks degrade when early instructions or computed values are compacted
away. Mechanisms: raising or lowering the proactive compaction ratio and
target, the prune threshold, keeping memory blocks and turn notes as the
evidence suggests.

Generic trap: "context collapse" — compaction that summarizes a summary loses
the task. Compaction-path changes affect every long task; avoid wholesale
increases that blow the budget on every trial.

## 5. Loop control and termination

Agents that stop with work left, loop on a refused action, or exhaust the
turn budget. Mechanisms: completion review, repeat-stop threshold, the soft
and hard turn input budget.

Generic trap: anything that makes finishing wait for a confirmation that may
never come. Every added pass needs an explicit exit.

## 6. Executable-feeling skills (procedures that do work)

Skills work when they turn a recurring wrong method into a short, checkable
procedure: numbered steps, the tool to use at each step, and an explicit
completion check. A skill the policy loads but that does not change what it
does is dead weight in the catalog.

```
---
name: tabular-deliverable
description: Produce a CSV or JSON deliverable that matches the requested schema exactly.
---
1. Extract the required columns or keys from the request, in order.
2. Build the rows from the source files; do not invent missing values.
3. Write the file with the exact requested name.
4. Read the file back once and compare header and row count with the request.
```

Generic trap: a skill keyed to one task's data (its column names, its values)
is memorization; keep skills entity-free and format-level.

## 7. Memory policy

Memory content is user data and is never part of the harness. The harness only
decides whether memory is retrieved and archived. Turning retrieval on or off
is a policy edit; storing lessons from evaluation tasks is forbidden.
