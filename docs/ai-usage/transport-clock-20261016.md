# Cross-host evaluation deadline correction

OBSERVED: manual Linux run `38900733798` completed its three public batches,
but no paid request reached the gateway ledger. The runner clock was about
50 seconds ahead of the Windows gateway clock. Passing a wall-clock deadline
made the gateway reject the requests as outside its 91-second upper bound.
The 47/50 predictions from that run are an invalid infrastructure trial,
not an actual-Qwen acceptance result. Its receipts are retained.

The frozen candidate remains `ea741d8dc731a1817bf6d5ca8ea8fff192f3f008`.
This correction changes only the external evaluation harness and its tests.

SOURCE_VERIFIED: freeze and batch-start replies include the gateway's
monotonic timestamp H0. The client samples C0 after receiving each reply.
A case deadline Cd maps to `H0 + (Cd - C0)`. Reply transit shortens the
available window. Clock origins and wall-clock corrections cannot extend it.
Refreshing each batch prevents rate differences accumulating over the full
campaign. Missing or nonfinite references fail closed.

The gateway checks remaining time after serial queueing, after the price
check, and immediately before dispatch. An expiry after durable reservation
retains the unresolved exposure. The 0.03 USD reservation, three-attempt cap,
per-question identity and campaign ledger remain unchanged. Provider socket
timeouts are bounded by remaining time; this is not a claim that a trickling
HTTP response has a strict whole-response wall-clock bound.

OBSERVED: the complete transport/budget tests pass locally (96 passed) in
the evaluation environment. They cover both 50-second skew directions,
different monotonic origins, reply delay including complete expiry,
per-batch refresh, malformed references, queued/price-check expiry and
post-reservation expiry. A preliminary invocation in the solver-only
environment lacked the transfer dependency; it produced two import failures,
93 passes and one skip. No tests were weakened or skipped to obtain the
evaluation-environment pass.

The same frozen public inputs and candidate will be rerun with the corrected
harness. Sealed inputs and goldens remain closed until the public gate passes.
