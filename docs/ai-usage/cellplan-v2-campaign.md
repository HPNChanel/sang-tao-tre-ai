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

## Round 2 on 2026-10-17

The submission deadline moved from 12:00 to 19:00, and the user approved a
second round at 10:01 local time. The rule was written to
`PREREGISTRATION_R2.json` at 10:03 (03:03:12Z), before any V2.4 result: V2.4
`7bd2b8c` (hybrid, selective, 1200 output tokens) replaces A only if it passes
five gates in order; a failed gate, or a gate without a scored execution by
16:30, keeps A. No solver change after the pre-registration; re-runs only for
infrastructure failures, with the same frozen bytes and configuration.

V2.4 is V2.3 plus four changes designed from DEV and public results. R1 keeps
the checked rule answer when the legacy rule planner resolves a question
natively, instead of a Qwen adjudication (the public `p4_q016` miss). R2
rebuilds a smaller workbook view when a repair request exceeds 24 KiB. R3 names
the offending fields in rejection feedback. R4 asks once more after an
abstention, within the three-call budget.

- G0 DEV (at least 330/360): 338 correct, 22 wrong, 0 blank. PASS. During the
  first execution a ledger read while the gateway was saving made the local
  gateway refuse attempt 7866 (never sent; settlement on record), and batch 10
  ran its remaining questions without Qwen. That batch was re-run once with the
  same bytes; the scored folder is the 17 clean batches plus the re-run.
- G1 public Linux (50/50 on the first halt-free execution): 50/50, run
  38998752779, six model calls. PASS.
- G2 portability, paired A against V2.4 in one dispatch (D at least 3 and a
  world-bootstrap 95% lower bound above 0): V2.4 52/60, A 0/60, D +52, interval
  [41, 60]. PASS. This was the first execution of the portability split.
- G4 stress, a single V2.4 arm against A's frozen round-1 result (at least
  112/120 and not below A): V2.4 116/120, A 0/120, repetition 2, run
  39002405225. PASS. Repetition 1 (run 39001457587) stopped
  after 3 of 6 receipts when the tunnel's single connection dropped; it was not
  scored, its results were not opened, and the whole split was re-run as
  repetition 2.
- G3 MAIN, a single V2.4 arm against A's frozen round-1 result (at least
  303/360, D at least 18 and an r3 stratified bootstrap lower bound above 0):
  V2.4 308/360 (28 wrong, 24 blank), A 0/360, D +308, interval [295, 320] over
  72 worlds stratified by family, run 39004742811. PASS. 42 of the 52 MAIN
  answers that were not correct sit in two families: F03 (weighted mean) 1/30,
  with 23 blank, and F11 (unit conversion) 17/30. The same families scored
  29/30 and 27/30 on DEV and 10/10 each on stress. In round 1, V2.3 scored
  314/360 on MAIN, above V2.4, with F03 5/30 and F11 19/30. The cause was not
  examined, because that needs the MAIN questions, and no code was changed
  after the sealed results.
- Decision by the pre-registered rule: all five gates passed, so V2.4
  `7bd2b8c` is the final solution and replaces A.
- Final-bytes check, planned at 04:27:19Z before it ran: the 50 public
  questions re-ran on GitHub Actions Linux from the exact bytes of the handoff
  `AI26C0101.zip`, run 39009958725: 50/50, five model calls, USD 0.00064345,
  longest invocation 21.2 s, memory peak 106,291,200 bytes, no swap, no OOM,
  every CSV within the contract. PASS.
- Ledger after round 2: 8,988 attempts, USD 1.122855884 actual cost plus
  USD 0.21 retained for seven attempts with unknown billing (5331, 5903, 5906,
  5907, 5911, 5912, 7866), exposure USD 1.332855884 against the USD 5 cap,
  `halt_reason` null. Round 2 alone: 1,326 attempts (7663-8988), USD
  0.199913386 actual plus the USD 0.03 retained for attempt 7866. The costliest
  MAIN question cost USD 0.00100185, far below the USD 0.10 per-question cap.

The custodian subagent of round 1 again collected and scored every sealed
execution and returned aggregates only. G3 and G4 reuse sets on which the
implementer had already seen V2.3's aggregates, and compare one new V2.4 arm
with A's frozen executions, so they are not independent measurements; the
pre-registration records this. Portability had never been run, but
`benchmark/author.py`, outside the encrypted area, also holds its generator; the
implementer did not open that file from 01:26 on 17/10, and for the time before,
the record shows only the `main_question` scaffolding noted above. At 11:26 a
`find` over the campaign folder descended into `benchmark/private/` and printed
one file name; no file there was opened, and later searches exclude
`benchmark/`.

The final package `AI26C0101.zip` (SHA-256
`3207bdbd089123ea2d3d52c5ea3baabea3b58e374a2f4ab882d18651cfb44cd9`, 402,840
bytes) is built from `7bd2b8c`: 37 of its 38 members are byte-identical to the
evaluated ZIP `55f9703c…`, and only `README.md` was rewritten in Vietnamese. Its
Dockerfile sets `DS2_SOLVER_PROFILE=hybrid`, `QWEN_REVIEW_POLICY=selective` and
`QWEN_MAX_OUTPUT_TOKENS=1200`, so the round-1 note above, that the package must
not be rebuilt from this branch head, no longer applies. The README template in
`scripts/build_submission_package.py` still described V2.3's route (natively
resolved questions go to the baseline Qwen review) and was not used for the
final README. The round-1 handoff folder, which kept A, is archived unchanged as
`deliverables/ban-giao-pdu-team-20261017-truoc-vong-2.zip`; the round-2 handoff
replaces it in `deliverables/ban-giao-pdu-team-20261017/`, still ignored by
Git. At 13:46 the user approved merging V2.4 into `main` through PR #65 with a
merge commit, and closing the other open pull requests with a note while
keeping their branches. At 14:26, also with the user's approval, PR #66 (merge
commit `b9b92c9`) brought the route text in the README template and in the
repository README in line with V2.4 and added a test; the solver code, the
Dockerfile and `AI26C0101.zip` did not change, and the package was not
rebuilt. Nothing was submitted or deployed.
