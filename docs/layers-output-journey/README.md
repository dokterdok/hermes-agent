# Reproduce the named-Bot file-output journey

This recipe reconstructs the published dependencies for a named Bot's first
and subsequent turns, retained-file retrieval, Stop, exact acknowledgment/replay,
and pending output cleanup after service recreation. It does not install a
running gateway or modify existing user data.

The implementation stays in the existing owner PRs:

- Runtime [#111216](https://github.com/NousResearch/hermes-agent/pull/111216),
  `041ae76f80ba2330ef6f1b7b961adc08e59a1877`.
- Output [#99159](https://github.com/NousResearch/hermes-agent/pull/99159),
  `31d00b0ed728e8d580aadf8a143cda3343752441`.
- Route [#100016](https://github.com/NousResearch/hermes-agent/pull/100016),
  `1fa3c0addd0c3eec671f3019c443dd3e449db134`.

`plan.json` records every additional immutable public input, including Input,
Files, Retention, Policy, and Consent. The foundation stays fixed at
`6f2aeb528fc6e8c9cee8ed1fa022a1c50c05829d`; no branch tip is resolved at run time.
The Output implementation pin intentionally predates this documentation-only
recipe commit.

## What is in the recipe

`materialize.py` uses Python's standard library and Git on a POSIX host. It
fetches public Git objects, starts from the fixed foundation, and assembles the
243 declared paths using whole blobs or hashed source-line spans. It verifies
source Git object IDs, span SHA-256 digests, output SHA-256 digests, and modes.
There is no local donor checkout, local patch file, or private object-store input.

This is **not an unmodified checkout of any one owner**. Twenty-one explicit
integration fragments, totaling 3,573 bytes, combine the published contracts.
Every fragment is visible as a `join` with a reason in `plan.json`;
[JOIN_CONTRACTS.md](JOIN_CONTRACTS.md) explains their behavior. In particular,
the composed admission path must execute both existing authorization callbacks.
The recipe must not be represented as merely copying files or as independently
proving the providers' correctness.

Run only a reviewed plan. This is a developer reproduction tool, not a sandbox
for hostile manifests. The destination must be absent and its ancestors must be
existing real directories. Existing destinations are rejected rather than
updated or erased. Reconstruction does not import or execute fetched Python.

## Reconstruct

From the checkout containing this directory, choose a new destination:

```bash
mkdir -p "$HOME/layers-public-replay"
REPLAY_ROOT="$(cd "$HOME/layers-public-replay" && pwd -P)"
python3 docs/layers-output-journey/materialize.py \
  "$REPLAY_ROOT/source"
```

A successful run reports `reconstructed 243 product paths` and the fixed base
and join count. Those are reconstruction results, **not behavioral test results**.
The script reads the adjacent `plan.json` automatically. Inspect any failure;
do not reuse an incomplete destination as a successful reconstruction.

## Exercise the journey

Use the reconstructed checkout's `CONTRIBUTING.md` and independent test
environment. Do not point these commands at a live Hermes home. For a fresh
test environment, using the checkout's bootstrapped Python:

```bash
cd "$REPLAY_ROOT/source"
mkdir -p "$REPLAY_ROOT/home" "$REPLAY_ROOT/hermes-home" \
  "$REPLAY_ROOT/runtime" "$REPLAY_ROOT/tmp"
export HOME="$REPLAY_ROOT/home"
export HERMES_HOME="$REPLAY_ROOT/hermes-home"
export HERMES_RUNTIME_DIR="$REPLAY_ROOT/runtime"
export TEMP="$REPLAY_ROOT/tmp" TMP="$REPLAY_ROOT/tmp" TMPDIR="$REPLAY_ROOT/tmp"
python -m pm.build_env --source . --out .venv --group dev --group test
HERMES_PYTHON="$PWD/.venv/bin/python" PYTHON_CPU_COUNT=2 \
  bash scripts/run_tests.sh -j 1 --file-retries 0 --file-timeout 120 \
  tests/gateway/test_multiplex_named_send_output.py \
  tests/gateway/test_canonical_output_stop_corrections.py \
  tests/gateway/test_root_ack_replay_boundary.py \
  tests/gateway/test_named_output_cleanup_startup.py \
  -k 'terminal_worker_before_first_output_capture_retains_mixed_native_bytes or completed_output_replay_cannot_repair_foreign_input_ack or pending_named_output_cleanup_replays_after_service_registration or (test_named_send_finite_execution_and_retained_publication and (baseline or subsequent))' \
  -q --tb=short
```

The selected cases use synthetic profiles and test-owned execution, with actual
admission, retained-file, acknowledgment, and recovery paths. They do not make
live model-provider requests or prove installed/native Desktop behavior.

## Named coordinator, not only the launch Bot

The current Output pin lets a registered named Bot coordinate the same retained
file journey without borrowing the launch Bot's authority. Exact-home registry
identity replaces the launch-only pointer check; service identity, epoch,
database replacement, admission and retry-readiness checks remain intact.

With the isolated environment above, the decisive extension is:

```bash
HERMES_PYTHON="$PWD/.venv/bin/python" PYTHON_CPU_COUNT=2 \
  bash scripts/run_tests.sh -j 1 --file-retries 0 --file-timeout 120 \
  tests/gateway/test_multiplex_named_send_output.py \
  -k 'requires_exact_registered or baseline or subsequent' -q --tb=short
```

This exercises default and named coordinator first/subsequent turns, retained
bytes and earlier-file retrieval, plus exact-registration removal/replacement
and draining refusal. It does not test every shutdown phase.

The published-input extension passed **5 tests, 0 failed, 1 file**. The two changed
Output blobs were freshly fetched from the public commit into the previously
accepted public reconstruction; all 243 declared product digests matched before
the canonical run. Other public inputs and integration joins stayed unchanged.
This was an incremental public-input replay, not another full reconstruction.
The source delta received a bounded combined correctness/security review.

## Earlier verification and remaining limits

- The public-input reconstruction matched all 243 declared paths of the tested
  candidate, including content and modes; no mismatches remained.
- Parent execution of the original selection on that reconstruction, with the
  earlier Output implementation `4be9cb11031f3001929fd74cadb8f20b097cbf08`:
  **4 files, 5 passed, 0 failed, 14.6 seconds, 1 worker**.
- A separate earlier candidate gate passed 29 tests in 7 files. These overlapping
  selections are not added together and are not a full repository-suite result.
- The parent used an existing isolated test interpreter, serial bytecode
  compilation, and a separately declared test-runner scratch-directory adapter
  to stay inside its permitted workspace. The adapter changed temporary paths,
  not product code or assertions; it is excluded from the 243 product paths and
  from this recipe. The pinned runner otherwise prefers its own temporary root,
  so `TMPDIR` alone is not a guarantee that every runner file stays there.
- A source-only correction re-review approved the owner startup-order repair
  and bounded composition changes. The reconstruction comparison is not a new
  independent security review of this materializer.
- Hosted CI, installed/native Desktop, cross-host operation, and the full Layers
  programme remain outside this result. This recipe authorizes no deployment,
  live takeover, or device action.

Existing owner history and contributor attribution remain unchanged. The recipe
was prepared with AI assistance and parent source inspection; it does not move
lower-owner implementations into this PR's source delta.
