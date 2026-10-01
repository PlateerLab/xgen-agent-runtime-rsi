---
name: manual-computation-without-execution
description: Produce a verified result for a multi-step deterministic computation (normalization, deduplication, currency conversion, classification, constraint satisfaction) when no code-execution tool can run the written logic.
---
1. If a code-execution tool is listed among your available tools, use it to run your processing logic and read its actual output; do not substitute manual computation when execution is available.
2. If no code-execution tool is listed, do not approximate the result in your head or guess values to match a script's intended logic. Instead, open a scratch file (e.g. scratch_calc.md) and process the source records in order, writing one line per record with the intermediate values you derive from it (normalized key, converted amount, running tally) as you go.
3. After processing all records, recompute each aggregate (count, sum, classification bucket) by summing the scratch file entries directly, not from memory of your earlier pass.
4. Cross-check: the number of processed scratch entries must equal the number of source rows/records; any record you could not match must be listed explicitly, not silently dropped.
5. Only after the scratch-file derivation is complete and the row/entry counts reconcile, write the final deliverable from the scratch file's values.
6. Completion check: re-open the scratch file and the final deliverable side by side and confirm every reported number in the deliverable equals a value you can point to in the scratch file.
