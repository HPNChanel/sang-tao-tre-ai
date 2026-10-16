# Excel QA: the new solver experiment

The contest task is natural-language question answering over supplied Excel
workbooks, returning only UTF-8 `id,answer` CSV through the six existing CLI
arguments. Research is limited to evidence selection, question interpretation,
calculation and answer formatting under the contest resource contract.

The user authorized this experiment on 2026-10-17 and clarified that delivery
must contain ONE final solution from this research. Previous approaches belong
in the interview draft; baseline and alternative profiles are internal controls,
not separate products for the user to choose. No merge or contest submission.

## Falsifiable question

Does coordinate-preserving workbook evidence, optionally accompanied by table
structure hints, improve exact answers over answering runtime `d123308`?
Compare unchanged baseline A, complete-context direct answers B, raw-cell plans
C and raw-cell plus structural-hint plans D. B's valid evidence references do
not establish arithmetic correctness. C/D share generation settings.

The independent author owns 360 DEV, 360 sealed main, 120 unseen-layout stress
and 60 portability cases outside Git, in groups of five related variants.
Only DEV is visible to implementation. Freeze all splits/oracles before candidate
feedback. Independent answers are checked from saved workbook bytes. Main uses
known layout families with new worlds/templates; stress holds out layout
families. Ambiguous or stale-formula-cache cases are diagnostic, not accuracy
cases. Eighty deterministic fault/resource cases are separate from accuracy.

Pilot all profiles on the same 60 DEV cases, extend the two best challengers
and A to all DEV, then freeze one challenger. Compare baseline and challenger
on all main and stress cases before opening results. Required evidence:
public 50/50; >=18 additional main correct; world-cluster stratified bootstrap
95% interval strictly positive; stress total correct >= baseline. Report wrong,
blank, new/lost correct, family scores, evidence/plan failures, fallback, actual
time/tokens/cost and all unrun gates. No post-unseal solver/golden changes.

## Interfaces and limits

Separate CellPlanV2 from legacy v1. Up to 16 plan nodes, depth 8, three input
regions and one equality join with a unique right key. Deterministic Decimal
and datetime operations; no model-generated Python execution. Shared dispatch
for direct answers, inspect, planning, repair and HTTP retry. All count toward
three requests, 90 seconds, 24 KiB request bodies and 1,200 output tokens.
Unknown models/prices fail closed; never silently change the supplied model.

Keep the existing durable USD 5 ledger, including prior spending; no reset.
Enforce USD 0.10 per stable question across profiles, versions, batching and
retry. Reserve USD .03 before dispatch, retain unknown billing and halt paid
execution for reconciliation. Preserve the existing cumulative stage caps.

Final runtime gates: Linux amd64, no GPU, 1,000,000,000 byte container limit,
no swap, 2,390 seconds processing plus 10 seconds CSV completion per 20 queries.
The 100 MB rule applies to the submitted ZIP, not workbook input size.
Source freeze target 07:00, handoff target 10:30, official closing 12:00 on
2026-10-17 Asia/Saigon. Incomplete gates remain explicitly incomplete.

RoutingReceipt: {"schema_version":1,"implementation":{"owner":"root"},
"independent_benchmark":{"model":"gpt-6-sol","effort":"xhigh"},
"independent_review":{"model":"gpt-6-sol","effort":"xhigh"}}
