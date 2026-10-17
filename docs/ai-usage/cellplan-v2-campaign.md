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

## Outcome on 2026-10-17

The final solution is baseline A, runtime `d123308` with the `selective` review
policy and 600 output tokens. The challenger did not pass the pre-registered
public gate, so no sealed result could promote it.

- Pilot on 60 DEV cases: A 0, B 14, C 41 and D 43 correct.
- Full DEV (360): A 0 correct, 153 wrong, 207 blank. V2.1 `f7fdd12` raw 316,
  dual 313. V2.2 `c2b1e07` hybrid 337 correct, 18 wrong, 5 blank. These are
  development numbers: the implementer saw DEV answers while fixing failures.
- Frozen challenger V2.3 `9b36478` is V2.2 plus the hybrid Dockerfile defaults
  and a value-scale percent correction. An offline re-execution of every logged
  CellPlanV2 plan changed 0 of the V2.2 DEV answers (V2.1 raw/dual DEV: 8/7
  fixed, 0 broken; `0c980ca` public: 2 fixed, 0 broken). Its own DEV API run was
  halted by an HTTP 429 after 4 of 18 batches, so this exact revision has no DEV
  API score.
- Public Linux gate: `0c980ca` 48/50 (GitHub run 38965773502, percent scale).
  Two executions of `9b36478` were halted by the ledger after provider 429s with
  unknown billing and do not count (48/50 each). The first halt-free execution,
  harness `92631c9`, scored 49/50 (run 38976982800). The miss, `p4_q016`, went
  through the legacy review and adjudication path that is shared with A; the
  same question kept the rule answer in A's 16/10 run.
- The source freeze records `promotion.possible=false`; the configuration freeze
  records the controls and the rerun rule.
- Sealed MAIN and stress, informational only: MAIN (repetition 1, run
  38978617638): A 0/360, V2.3 314/360, paired difference +314 (V2.3 only 314, A
  only 0), world-cluster bootstrap 95% [+301, +326] questions (proportions 0.836
  to 0.906); stress (repetition 1, run 38988044952): A 0/120, V2.3 116/120,
  difference +116. The MAIN requirement was met and the stress requirement was
  met; neither can promote.
- A on the final handoff ZIP bytes, public Linux 50: 49/50 (run 38987219465);
  CSV contract, memory (peak 106266624 bytes), swap and time limits pass.
  Public2 question 25 was left blank: the rule path rejected its own plan and
  both Qwen plans failed schema validation, while in the 16/10 run the second
  Qwen plan was valid and the answer correct. The 50/50 check of the final
  package therefore fails; the rule decides only whether a challenger replaces
  A, so the final stays A and the re-run is recorded as a limitation.
- Not run: portability and a second model.
- Ledger: USD 0.922942498 actual cost across all campaigns, USD 0.615279586 of
  it on 17/10, plus USD 0.18 retained reservations (exposure USD 1.102942498,
  cap USD 5). Six attempts with unknown billing keep their USD 0.03
  reservations; none was recorded as zero.

The benchmark author and sealed custodian was a separate subagent with its own
context, used in place of the `gpt-6-sol` named in the RoutingReceipt; on 17/10
a separate Claude subagent held custody to amend the scorer, re-wrap the sealed
transport and score the paired runs. No `gpt-6-sol` review was run either; the
custodian checked the freeze files against its six sealed packets without
opening any sealed result (all three items PASS: 11/11, 3/3 and 5/5 checks, plus
5/5 pre-checks; receipt dated 2026-10-16T22:59:10Z, after the freeze files at
2026-10-16T22:53:53Z). A second Claude subagent, which did not open sealed data,
fact-checked the Vietnamese handoff drafts against the evidence files before
assembly. Before the freeze the implementation side saw the MAIN `main_question`
template code, but no world values or answers; the MAIN wording was rewritten
before the freeze (recorded in the benchmark freeze manifest `199d8375…`). MAIN
is therefore not fully unexposed. The custodian also notes that
`benchmark/author.py`, outside the encrypted area, holds the MAIN question
templates; the implementer did not open it from 01:26 on 17/10 (per that
session's own log, not in the handoff), and for the time before, the record is
the `main_question` note above. MAIN and stress have now been used to measure
V2.3: a revised V2 needs a new sealed set.

This branch head keeps the challenger defaults from `0c980ca` in the Dockerfile
(`hybrid`, 1200 output tokens). The final package runs `d123308`, so it must not
be rebuilt from this branch head.

The handoff folder is `deliverables/ban-giao-pdu-team-20261017/`, which is
ignored by Git (not tracked). Nothing was merged or submitted.
