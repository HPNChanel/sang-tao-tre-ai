# Qwen evaluation campaign: implementation contract

Baseline: `8188df35c181992fd0aa940dd4bb827370084cb4`. User authorized a USD 5
total campaign, source changes and separate draft PRs, but not a new main merge
or contest upload. The baseline package is retained.

The target is higher raw Exact Match, followed by elapsed time and actual
tokens. The new benchmark is synthetic internal evidence, not the organizer's
hidden test. A separate authoring context owns 1,200 cases: 840 development and
360 sealed, grouped by scenario before splitting. The author freezes the seal
before solver changes. Old QA/v2/v3 files are not inspected or reused. Goldens,
private workbooks, queries, credentials and evaluation outputs stay outside Git
and the allowlisted submission package.

The initial falsifiable hypothesis is that generic fixes plus selective Qwen
planning improve correctness without regressing known public cases. Four DEV
configurations compare fallback/selective review and 600/1,200 output tokens.
There are at most three repair waves and a stop after two plateaus. The final
selected source/configuration is frozen before one paired sealed comparison.
Neither implementation nor golden answers may change after unsealing.

API stage ceilings: baseline/public USD 0.50; development USD 2.00; sealed
USD 1.50; performance USD 0.50; reserve USD 0.50. A dedicated non-resetting key
has a USD 5 provider limit. The gateway serializes calls, reserves USD 0.03
durably before dispatch, permits at most three attempts per question/run, and
retains unresolved reservations after timeouts or missing usage. Only finite
`usage.cost` is a billed amount. A 24 KiB body limit, disabled reasoning and
extensions, provider allowlist/price ceilings, and max 1,200 output tokens
bound exposure operationally; this is not a provider-certified per-question
dollar guarantee. Billing anomalies halt paid execution.

Each contest invocation has 20 questions and a 2,400-second total external
deadline, including loading, retries and CSV output. The 360 sealed cases form
18 invocations per version. Linux acceptance measures the whole container with
a 1,000,000,000-byte limit and no swap. Hardware is recorded rather than
assumed to equal the organizer's 6 vCPU machine.

Acceptance requires targeted/full applicable tests, all 50 public answers
correct with actual Qwen, resource/timeout evidence, and frozen paired sealed
results. Promotion requires more sealed correct answers; ties require stable
time/token improvements on an open performance pack. Otherwise the baseline
remains the recommended submission. Reports distinguish wrong/blank answers,
new/lost correct cases, grouped uncertainty, actual/unresolved costs and skipped
or unrun gates. Final artifacts include a candidate AI26C0101.zip, hashes,
evaluation report and updated oral-defense notes.

Implementation is in progress. No benchmark, correctness, cost, runtime or
release result is claimed by this contract.

The Windows development adapter verifies all frozen source hashes, strips the
provider credential from the solver environment, binds API identity at the
enclosing question call, and denies Python socket egress except its exact
loopback gateway. This is containment for reviewed Python source, not an OS
sandbox against malicious native code. Linux acceptance requires container
network isolation independently.

RoutingReceipt (schema version 1): benchmark custody and independent cost
review are bounded research subtasks, routed to `gpt-6-sol` / `xhigh` under the
user's research profile. Both use separate contexts; implementation stays with
the root. The source benchmark author may see the sealed data; implementation
receives only DEV and the frozen metadata. No inference result is fabricated.
