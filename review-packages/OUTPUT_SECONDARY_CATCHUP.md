# Catch-up after a no-op secondary notify

This commit retries Output secondary publication when primary terminal evidence appears only after a no-op notify. It stacks on draft #21 (`82688b2c80febbae89d791d44557ceb5e6b498ac`). It does not rewrite the #15 secondary contract or the #21 caller’s eligibility rules. Send-consent is not passed and is not publication authority. Primary `publish_terminal` does not call the retry. `prepare_room` does not call the retry.

Parent #21 was not merged. Fork `main` was not updated.

## Call path

`call_settled_invitation_secondary` still returns before any secondary write when the task is not a settled invitation with artifacts and `recipient_member_ids`. When that task is otherwise eligible and the primary `dmessage`/`dterminal` digest is empty, it returns `SecondaryAwaitingPrimary` and writes nothing. That object is not a publication and not consent.

The driver remembers `(TaskIdentity, execution_generation)` only for that exact return. Any other return drops the key. A raised callback does not reach the remember step, so the key stays. A `bool` generation is rejected. The set is process-local. The driver does not scan every settled task and does not add a SQL table.

`_process_room` calls `prepare_room`, and only if that call returns does it call `_catch_up_secondary_after_primary`. A raised `prepare_room` does not catch up. Catch-up is not inside `publish_terminal` or `prepare_room`. It re-enters `_notify_settled_secondary` for the remembered keys of that room. The notify path is the same caller, which still calls `publish_secondary_retained(task)` with no keyword arguments. The consumer’s `_mutate` still rechecks policy and `_output_owner` before a write.

A missing task, a task that is no longer settled, or a generation mismatch drops the key and does not call the callback. A per-task exception is recorded, the room is not added to `_ambiguous_rooms`, the task stays settled, and the key stays pending. A later successful return drops the key. A second catch-up does not call the consumer again. The consumer still owns a later retry or complete of a row that already exists.

`_publish` still only calls `publish_terminal`. History and info still do not call this path. `HostedRoomService.prepare_room` and `HostedRoomService.publish_terminal` are unchanged.

Deferred primary publication and `_harvest_previous_attempt` are the two no-op cases. Harvest settles and notifies without `_publish`. The retry is the next room pass after `prepare_room` returns, or a direct catch-up once events exist.

## Overlay

Throwaway trees only. Not committed. Same lower-owner bytes as `review-packages/OUTPUT_SECONDARY_POST_SETTLEMENT.md`.

Checked out by OID:

| Path | Source | `git hash-object` |
|---|---|---|
| `gateway/hosted_room_safety.py` | Retention `004015d6087fe031231c4d7d9e0032cc59b679eb` | `7bfb1bf04b59c52e27603370ef278ae85419ab22` |
| `gateway/hosted_room_output_completion.py` | Retention `004015d6087fe031231c4d7d9e0032cc59b679eb` | `10134f074e170f6010b7ba22d80abb93e1d34f4b` |
| `gateway/hosted_room_task_scan.py` | Retention `004015d6087fe031231c4d7d9e0032cc59b679eb` | `42f2ff4f5e93014b83cb5afd6390d0c61d5d98d9` |
| `gateway/hosted_room_input_custody.py` | Input `d12f6041191233e7fa4f05bd7c02a02b84a7b85c` | `4690a2970612e1e84878bebcffb2aeeeecf09fb1` |
| `gateway/session_group_state.py` | Route `46c3ce81784406af7a742f9d79e121c3917e584e` | `8634628ee86af069bfe089d96df737dbb0f2295f` |
| `gateway/session_group_disband.py` | Route `46c3ce81784406af7a742f9d79e121c3917e584e` | `8b1f4373697970b433a97576d622182e79ca9ae8` |
| `gateway/session_group_retirement.py` | Route `46c3ce81784406af7a742f9d79e121c3917e584e` | `c9a0b092c2a1a91a0f5e33480a2a49da089603f7` |
| `gateway/hosted_room_route_schema.py` | Route `46c3ce81784406af7a742f9d79e121c3917e584e` | `04dd05e850e36b8710f2c186ce21b11ec1152dfd` |

Splices, not whole-module checkouts:

- `SessionDB.live_read_connection` from `0be79d80d0aab81dae999eb48b9a7b94be2938c1`, inserted before `_read_ctx`.
- `SessionDB.live_write_connection` from `55a032ed1a014986a9d2b339cab47e2ba1708012`, inserted before `_read_ctx`.
- `HostedRoomPolicyCheckpoint.snapshot` from `e535c040f582fec2a3acbbc787d73aeeb5e5365b`, including `held_output_threads` and `read_connection`. `ContextManager` is imported. The rest of the checkpoint file stays the Output tip.
- `route_security_digest` from `46c3ce81784406af7a742f9d79e121c3917e584e:gateway/hosted_room_links.py`. `hashlib` is imported beside it. The links module is not replaced.
- `HostedRoomService.__init__` gains `self.attachments = HostedRoomAttachmentStore(self.db_path)`.

The runner’s clean env imports the working tree. That is not a committed runner edit.

## RED

Product files at #21 `82688b2c80febbae89d791d44557ceb5e6b498ac`. Tests for this slice already on the tree. No overlay required: the driver tests fail before any secondary store.

```text
HOME=/tmp/output-secondary-catchup-red/home TMPDIR=/tmp/output-secondary-catchup-red/tmp \
  scripts/run_tests.sh tests/tui_gateway/test_hosted_room_driver_runtime.py \
  -k 'noop_secondary_notify or settled_notify_with_evidence or harvest_before_events or catch_up_forgets' \
  -q --tb=line
```

Measured: 1 file, 0 passed, 4 failed. 43 deselected. Pytest 0.35s. Runner wall 1.3s.

Three tests raise `ImportError: cannot import name 'SecondaryAwaitingPrimary'`. `test_settled_notify_with_evidence_is_not_retried_on_the_next_poll` raises `AttributeError: HostedRoomRuntime has no attribute _secondary_awaiting_primary`. #21 does not remember a no-op notify and does not retry it.

## GREEN

Same tests, product catch-up restored, including a `prepare_room` that raises.

```text
HOME=/tmp/output-secondary-catchup-green/home TMPDIR=/tmp/output-secondary-catchup-green/tmp \
  scripts/run_tests.sh tests/tui_gateway/test_hosted_room_driver_runtime.py \
  -k 'noop_secondary_notify or settled_notify_with_evidence or harvest_before_events or catch_up_forgets' \
  -q --tb=short
```

Measured: 1 file, 4 passed, 0 failed. Runner wall 0.8s.

The next room pass after a sentinel is `prepare`, then `secondary`, and does not call `publish_terminal` again. A notify that returns a publication is not retried. Harvest notifies with `published == []`. A missing contract on that catch-up writes nothing, keeps the key, leaves the task settled, and does not mark the room ambiguous. A later `_run_cycle` whose `prepare_room` raises does not call secondary. A direct catch-up after that still publishes without `publish_terminal`. A queued task that was inserted into the set by hand is forgotten and the callback is not called.

Secondary store, with the throwaway overlay:

```text
HOME=/tmp/output-secondary-catchup-green/home TMPDIR=/tmp/output-secondary-catchup-green/tmp \
  scripts/run_tests.sh tests/gateway/test_secondary_retained_publication_caller.py \
  tests/gateway/test_secondary_retained_publication_consumer.py \
  tests/gateway/test_secondary_retained_publication.py -q --tb=line
```

Measured: 3 files, 17 passed, 0 failed. Runner wall 8.2s. Caller file 7 passed in 8.17s. Consumer file 5 passed in 6.11s. Contract file 5 passed in 7.24s.

Deferred publication leaves secondary counts `(0, 0)` and stores the pending key. `prepare_room` and `publish_terminal` after that still leave counts `(0, 0)` and leave the key in place. Catch-up then calls `publish_secondary_retained` once with `{}`. The view is `(publish, pending, 0, 1)`. Counts become `(1, 0)`. The primary fingerprint taken after the events exist does not change. The key is gone. The room is not ambiguous. `runner.session_authority` is unchanged. A second catch-up does not call again. A missing contract on catch-up writes nothing, keeps the key, leaves the task settled, records `not registered`, and leaves the primary fingerprint unchanged.

## Adversarial review

Re-review count: 2. Verdict: CLEAN.

Review 1 found one defect. Catch-up ran in a `finally` after `prepare_room`, so a failed prepare still retried secondary publication. That could publish after an auth, quarantine, or prepare failure. The call is now sequential: catch-up runs only after `prepare_room` returns. The harvest test drives a raising `prepare_room` through `_run_cycle` and asserts the callback is not entered.

Review 2 re-ran those four driver tests (4 passed) and the three secondary files (17 passed), then re-read the delta:

- `publish_terminal` and `prepare_room` function bodies are not modified. `_publish` still only calls `publish_terminal`.
- The retry reads the same pending set. It does not scan settled tasks and it does not pass consent.
- A missing contract still raises at `_require_contract` before register. The caller still executes no SQL.
- `_mutate` still takes `_output_policy_read` and `_output_owner` before the secondary write.
- A catch-up exception is recorded per task, clears the secondary TLS error when that error is the exception, and does not mark the room ambiguous.
- A non-sentinel return, including `None` from a non-authority transport after the digest exists, drops the key.
- A successful consumer return is not polled again. The #15 row remains the consumer’s retry and complete path.

No open in-scope finding.

The pending set is process-local. A restart loses it. This slice does not add a table and does not scan every poll to reconstruct it. That durability stays with a later owner.

## Still later owners

Durable catch-up across process restart, still outside `publish_terminal` and `prepare_room`, still without treating send-consent as publication authority. Publication of the upstream output stack this fork has not absorbed. Merging this draft onto fork `main`.
