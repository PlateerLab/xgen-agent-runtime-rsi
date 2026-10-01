---
name: table-export
description: Use when a request asks for a cleaned, filtered, deduplicated, or sorted export of source records to CSV or JSON.
---
1. Use the complete source records and the supplied transformation rules, including any source content already provided in the request. Do not reread content merely because it was provided inline.
2. Before writing, work through the source in consecutive, nonoverlapping batches. For each batch, count every input record and classify it as invalid, superseded by another record under the requested precedence rule, or retained. Keep a running set of retained keys so a later batch cannot silently drop an earlier survivor. Do not infer the survivor count from the rows you happen to remember.
3. Reconcile the batch counts before sorting: total input records = invalid records + superseded records + distinct retained records. If they differ, revisit the unaccounted batch once before writing. Normalize and sort every retained record by the requested keys, then write the exact requested file and format. Do not edit the source.
4. Make one bounded output check: compare the written data-row count with the reconciled retained count. If they differ, correct the export once before finishing.
