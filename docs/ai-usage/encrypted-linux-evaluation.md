# Encrypted Linux evaluation

This harness is evaluation infrastructure and is excluded from submission ZIPs.
It does not change the frozen solver's mathematics or load golden answers.

The manually dispatched workflow, host scripts and runtime ZIPs are pinned by
commit/hash. Before the first input decryption, the local controller verifies
the GitHub API's run identity (owner, public repository, manual event, harness
SHA), records Linux amd64 image IDs, dependencies, hardware and configuration,
and acknowledges the environment freeze. Baseline and candidate use the same
pinned candidate build recipe and runtime dependencies; their original run.py
and src bytes remain unchanged. This normalization is recorded separately from
each original source archive hash.

The real provider key remains on the Windows host. CI receives only a temporary
transport token and AES-GCM keys through a repository secret. Each solver runs
as UID 65534 with no network, no capabilities, a read-only root, a decimal
1,000,000,000-byte cgroup cap and no swap. Its only intended communication path
is a narrow Unix socket. The trusted host derives case order from hash-verified
queries, checks case transitions and the question prefix, then inserts the
registered invocation/case identity. All real requests pass through the same
durable local cost ledger. At most three reservations of USD 0.03 are permitted
for one invocation/question. Missing billing information remains reserved.

This boundary assumes reviewed, hash-verified solver source and a trusted
GitHub-hosted runner. It is not a sandbox claim against malicious native solver
code. AES-GCM protects transport and stored results; the runner necessarily sees
plaintext inputs and predictions while executing. Source ZIPs contain no
datasets; encrypted input ZIPs contain only queries and referenced workbooks.
Golden answers never enter CI. Filesystem paths, archive inventory, hashes and
authenticated metadata are checked before use.

CSV, solver logs and captured output share a 64 MiB host tmpfs. Results are
encrypted with authenticated run/source/input/image metadata before returning
to the local controller. The controller authenticates ciphertext but does not
open the contained result ZIP or score predictions. Completed receipts recover
as a contiguous prefix; invocation identities and existing reservations are
never reset. A resumed run requires identical frozen image/dependency metadata.
Interrupted unknown-cost requests keep their reservations. Cleanup removes the
temporary repository secret and stops only this campaign's tunnel/server.

Verification includes archive/cipher/identity/recovery tests, a Linux container
probe of native network denial and permitted Unix transport, and the 80 fault
cases plus runtime deadline tests inside the constrained test container. These
technical checks do not establish public or sealed answer accuracy.

References: [authenticated encryption](https://cryptography.io/en/latest/hazmat/primitives/aead/),
[manual workflow dispatch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
[Docker network none](https://docs.docker.com/engine/network/drivers/none/).
