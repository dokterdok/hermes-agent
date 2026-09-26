# Durable catch-up after a no-op secondary notify

This commit keeps the awaiting-primary keys from draft #24 across process restart. It stacks on draft #24 (`f75a7f3e337d6c02b4a7cfbba0d88d0a483b7542`). It does not rewrite the #15 secondary contract or the #21/#24 caller. Send-consent is not passed and is not publication authority. Primary `publish_terminal` does not read or write the keys. `prepare_room` does not either.

Parent #24 was not merged. Fork `main` was not updated.

## Call path

A no-op notify still returns `SecondaryAwaitingPrimary` and writes no secondary row. The runtime inserts `(room_id, task_id, thread_id, turn_id, execution_generation)` into `hosted_room_secondary_awaiting_primary` and then adds the same key to the in-memory set. Any other return deletes that key. A raised callback still does not reach the remember step. A `bool` generation is still rejected.

The table is created on the first remembered key. Loading a runtime does not create the file or the table. A missing table is an empty set. A table whose columns are not the expected set refuses to start the runtime. Forget of a missing table is a no-op. Catch-up still iterates only that set for the current room. It does not select settled tasks.

`_process_room` still calls `prepare_room`, and only if that call returns does it call `_catch_up_secondary_after_primary`. A raised `prepare_room` does not catch up. A new process loads the keys in `HostedRoomRuntime.__init__` before the next room pass. A failed prepare on that process leaves the key pending. A direct catch-up after that still uses the same notify path.

A durability error is recorded and does not mark the room ambiguous. A failed insert still keeps the in-memory key for this process and leaves no row. A failed delete keeps both the memory key and the row. A missing task, a task that is no longer settled, or a generation mismatch still drops the key when the delete commits. A later successful return drops the key. A second process does not call the callback again.

`_publish` still only calls `publish_terminal`. History and info still do not call this path. `call_settled_invitation_secondary`, `HostedRoomService.prepare_room`, and `HostedRoomService.publish_terminal` are unchanged.

## RED

Product files at #24 `f75a7f3e337d6c02b4a7cfbba0d88d0a483b7542`. Tests for this slice already on the tree.

```text
HOME=/tmp/output-secondary-catchup-durable-red/home TMPDIR=/tmp/output-secondary-catchup-durable-red/tmp \
  scripts/run_tests.sh tests/tui_gateway/test_hosted_room_driver_runtime.py \
  -k 'survives_restart or restarted_catch_up_still_waits or durability_failure_keeps or forget_failure_keeps or noop_secondary_notify or settled_notify_with_evidence or harvest_before_events or catch_up_forgets' \
  -q --tb=line
```

Measured: 1 file, 4 passed, 4 failed. 43 deselected. Pytest 0.75s. Runner wall 2.3s.

The four #24 tests passed. `test_restarted_catch_up_still_waits_for_prepare_room` asserted the loaded key and got an empty set. `test_noop_secondary_notify_survives_restart_without_scanning_settled_tasks` and `test_forget_failure_keeps_the_durable_key` raised `ModuleNotFoundError: No module named 'tui_gateway.hosted_room_secondary_catchup'`. `test_catch_up_durability_failure_keeps_the_key_without_marking_the_room_ambiguous` raised `AttributeError: module 'tui_gateway.hosted_room_driver' has no attribute 'remember_awaiting_primary'`.

## GREEN

Same driver file after the product change, including the lazy table and the corrupt-schema refusal.

```text
HOME=/tmp/output-secondary-catchup-durable-green/home TMPDIR=/tmp/output-secondary-catchup-durable-green/tmp \
  scripts/run_tests.sh tests/tui_gateway/test_hosted_room_driver_runtime.py -q --tb=line
```

Measured: 1 file, 52 passed, 0 failed. Runner wall 7.0s.

A restart after a no-op notify loads that key and not the sibling settled task that already returned a publication. The next room pass calls secondary only for the loaded key. A key for another room stays pending and is not called. After the successful pass, a third runtime does not load the finished key and does not call secondary. Clearing the first runtime's set does not clear the second runtime's set.

A restarted runtime whose `prepare_room` raises does not call secondary, leaves the key, leaves the task settled, and does not mark the room ambiguous. A direct catch-up then publishes without `publish_terminal`, and a later runtime does not load the key.

A store error on insert keeps the in-memory key, writes no row, leaves the task settled, and does not mark the room ambiguous. A store error on delete keeps the row and the key. A wrong key table raises `unsupported secondary catch-up schema` before the runtime starts. The #24 no-op, evidence, harvest, and not-settled cases still pass, including a not-settled memory key that is not resurrected.

The secondary caller, consumer, and contract files were not re-run. They still require the throwaway lower-owner overlay from `review-packages/OUTPUT_SECONDARY_CATCHUP.md`, which is not in this commit. Without that overlay the caller file fails importing `gateway.hosted_room_output_completion`. Those three source files are unchanged in this diff.

## Adversarial review

Re-review count: 2. Verdict: CLEAN.

Review 1 found one defect. `HostedRoomRuntime.__init__` created the key table on every start, including a runtime that had never seen a no-op notify. That writes schema with nothing to restore. Load and forget now leave a missing table absent. Remember creates the table on the first no-op. A wrong table still refuses startup. The full driver file was re-run after that fix: 52 passed.

Review 2 re-read the delta:

- `publish_terminal` and `prepare_room` bodies are not in the diff. `_publish` still only calls `publish_terminal`.
- `call_settled_invitation_secondary` is not in the diff. It still passes no consent and still executes no SQL.
- Catch-up still reads the pending-key set for one room. It does not query `hosted_room_driver_tasks` for every settled row.
- A failed prepare returns before catch-up. The restarted prepare test covers a key that was loaded from disk.
- A missing contract still raises inside the callback before remember. The key stays. The room is not added to `_ambiguous_rooms`. The harvest test still covers that.
- A durability exception is caught in the runtime, recorded, and does not escape the settle callback, so it is not an observation failure.
- A second catch-up after a committed delete does not call the callback. A crash after a committed insert and before the in-memory add is restored by the next process's load.
- The key table has no foreign key into the task table, so task prune is unchanged. A missing task still hits `TaskConflictError` and drops the key when the delete commits.

No open in-scope finding.

## Still later owners

Publication of the upstream output stack this fork has not absorbed. Merging this draft stack onto fork `main`.
