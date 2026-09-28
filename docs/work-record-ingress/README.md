# Reject withdrawn work-record access before maintenance

Removing a participant's access must prevent a delayed request from modifying
its passive copy. This reproduction covers exact-grant withdrawal after HTTP
body reading and between read-only admission and the writer, plus stale-store
refusal without schema repair. Valid authorized preservation still succeeds.

## Two owners, one admission boundary

- Retention [#99107](https://github.com/NousResearch/hermes-agent/pull/99107),
  `c9f0029475f085e3b5e66b77df74cd5470925aef`, provides the optional authorization
  callback inside the existing writer, before replica schema/audit maintenance.
  Existing callers without a callback retain their behavior.
- Preservation [#104601](https://github.com/NousResearch/hermes-agent/pull/104601),
  `2135a0d9f2beaa58c6fe9fa34ad112d52f6185a9`, requires root-schema readiness and
  exact-grant admission through SQLite read-only access, then revalidates the
  current replica identity, roster and grant inside that writer.

The callback alone is not a promise that the shared connector never initializes
the root database. Preservation must reject a missing or stale root in its
read-only preflight. The provider owns only the pre-replica-maintenance callback;
it does not acquire execution authority or enable recovery/takeover.

`compose.py` fetches the exact public Preservation commit and two Retention
blobs. It replaces only `_replica_transaction` with the verbatim published
provider function, preserving the consumer's other replica extensions and its
existing `Callable` import. It also copies the provider regression test.
No provider implementation or provider commit is bundled into Preservation's
source delta. The composition is a test checkout, not a deployment artifact.

## Reproduce

Requires Git, Python's standard library, public GitHub access, and a prepared
project test interpreter. Follow the pinned checkout's `CONTRIBUTING.md` for
dependencies; set `HERMES_PYTHON` to that isolated interpreter's absolute path.
Do not use a live Hermes home or install experimental code into a running agent.
The reconstruction destination must not exist. A failed reconstruction is left
for inspection; the script never deletes or overwrites an existing destination.

From the checkout containing this document:

```bash
mkdir -p "$HOME/work-record-replay"
REPLAY_ROOT="$(cd "$HOME/work-record-replay" && pwd -P)"
python3 docs/work-record-ingress/compose.py "$REPLAY_ROOT/source"
mkdir -p "$REPLAY_ROOT/home" "$REPLAY_ROOT/hermes" \
  "$REPLAY_ROOT/tmp" "$REPLAY_ROOT/pycache" "$REPLAY_ROOT/cache"
cd "$REPLAY_ROOT/source"
env HOME="$REPLAY_ROOT/home" HERMES_HOME="$REPLAY_ROOT/hermes" \
  TEMP="$REPLAY_ROOT/tmp" TMP="$REPLAY_ROOT/tmp" TMPDIR="$REPLAY_ROOT/tmp" \
  XDG_CACHE_HOME="$REPLAY_ROOT/cache" PYTHONPYCACHEPREFIX="$REPLAY_ROOT/pycache" \
  HERMES_PYTHON="${HERMES_PYTHON:?set an absolute isolated test interpreter}" \
  PYTHON_CPU_COUNT=2 bash scripts/run_tests.sh \
  -j 1 --file-retries 0 --file-timeout 120 \
  tests/gateway/test_replica_transaction_authorization.py \
  tests/gateway/test_work_record_ingress_admission.py -q --tb=short
```

The cases use synthetic SQLite stores, grants and loopback HTTP, not real
conversations or model-provider requests. Environment overrides are scoped to
the test command rather than exported into subsequent shell operations.

## Recorded result and limits

- Fresh reconstruction from the public inputs above matched the four affected
  source/test files of the reviewed composition exactly.
- The canonical command above on that reconstruction passed **10 tests across
  2 files, zero failures**, in 2.3 seconds on Linux with an existing isolated
  Python 3.13 test environment. No runner source modification was needed.
- The broader affected candidate selection passed **62 tests across 4 files**;
  this includes the existing work-record and HTTP API tests. These overlapping
  results are not added together.
- The stale-root regression first failed on real schema repair, while its
  current-root control passed. The corrected admission path passed both.
- One combined source review required that correction; one changed-area
  re-review approved it. Earlier failure evidence remains retained.
- This is not a full-suite, hosted-CI, native-device, cross-host failover or
  complete #97681 acceptance result. No live activation, deployment, history
  rewrite or global execution authority is introduced.

Prepared with AI assistance and parent source inspection. Existing layer
history, contributor attribution and previously accepted recipes are retained.
