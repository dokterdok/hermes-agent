# Post-settlement caller of Output secondary retained publication

This commit calls `publish_secondary_retained` after a settled invitation→NEW task. It stacks on draft #19 (`cd1440543ba5485cceebeb6a863b29f865943c53`). It does not rewrite the #15 secondary contract (`ece9f17e2d1a2143d563c52140d4e13417f18456`). Send-consent is not passed and is not publication authority. Primary `publish_terminal` does not call it. `HostedRoomAuthorityRPC._terminal` does not call it, so history and info do not either.

Parent #19 was not merged. Fork `main` was not updated.

## Call path

`CanonicalHostedRoomService` assigns `runtime.publish_settled_secondary` after `super().__init__`. The driver calls that callback only after a task has settled:

- `_on_terminal`, after `settle_task` and `_publish` return
- `_execute_attempt`, on the history receipt that settles after `submit` returns
- `_fenced`, after the fenced transition (and after primary `_publish` when that flag is set)
- `_harvest_previous_attempt`, after a recovered settle

`_publish` still only calls `publish_terminal`. `prepare_room` is unchanged.

`call_settled_invitation_secondary` returns without writing when the task is not settled, has no artifacts, has no `recipient_member_ids`, has no primary `dmessage`/`dterminal` digest, or the member transport is not exactly `HostedRoomAuthorityRPC`. It does not insert rows. It calls `publish_secondary_retained(task)` with no keyword arguments. A missing `publish_secondary_retained`, or a missing secondary contract, raises `Group Chat secondary publication is not registered` before any secondary write.

That error is not an observation failure. `_execute_attempt` re-raises it after the settlement has committed, so the room is not added to `_ambiguous_rooms`. The committed task stays `settled`.

An already-published row is left to the consumer. This caller does not skip when a row exists, so a registered-but-unpublished row can still be retried. It does not scan every `prepare_room` poll.

## Overlay

Throwaway trees only. Not committed. Same lower-owner bytes as `review-packages/OUTPUT_SECONDARY_CONSUMER.md`.

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

The runner's `PYTHONPATH` is the tree root so an editable install cannot hide the overlay. That runner edit is not committed.

## RED

Throwaway tree: this caller and its tests on #19, with the runtime callback not assigned. Same overlay. Same uncommitted `PYTHONPATH` pin.

```text
HOME=/tmp/output-secondary-caller-red/home TMPDIR=/tmp/output-secondary-caller-red/tmp \
  scripts/run_tests.sh tests/gateway/test_secondary_retained_publication_caller.py -q --tb=line
```

Measured: 1 file, 4 passed, 1 failed. Pytest 4.22s. Runner wall 9.3s.

`test_settled_invitation_publishes_secondary_outside_terminal_and_primary` finishes the settled turn and sees no secondary row: `([], 0)` against `(('publish', 'pending', 0, 1),)`. Nothing in that tree calls `publish_secondary_retained` from settlement. The deferred, missing-contract, foreign-transport, unsettled, and post-commit observation tests pass without the assignment. The observation test drives `_execute_attempt` itself.

## GREEN

Throwaway tree: the callback assigned, same overlay, same uncommitted `PYTHONPATH` pin.

```text
HOME=/tmp/output-secondary-caller-green/home TMPDIR=/tmp/output-secondary-caller-green/tmp \
  scripts/run_tests.sh tests/gateway/test_secondary_retained_publication_caller.py \
  tests/gateway/test_secondary_retained_publication_consumer.py \
  tests/gateway/test_secondary_retained_publication.py -q --tb=line
```

Measured: 3 files, 15 passed, 0 failed. Runner wall 6.9s. Caller file 5 passed in 5.48s. Consumer file 5 passed in 5.87s. Contract file 5 passed in 6.91s.

A settled invitation→NEW turn with primary evidence inserts one secondary publication (`publish`, `pending`, blocked 0, attempts 1) and no completion. `publish_terminal`, `history`, and `info` do not call the callback. The primary event, retry, and completion fingerprint is unchanged. A second call passes no keyword arguments, so consent is absent, and the consumer publishes again after the test drops the row. A consent object raises `send consent is not publication authority` and does not change `total_changes`. Deferred primary publication leaves secondary counts `(0, 0)`. A missing contract raises `not registered`, writes nothing, and leaves the task `settled`. A non-authority transport returns without writing. An unsettled task returns before the contract is required. A secondary error raised from `submit`'s terminal callback is re-raised; the task stays `settled` and the room is not recorded in `_ambiguous_rooms`.

Contract cases that must start unregistered drop the secondary tables after the settled turn. That drop does not touch primary rows. Consumer lifecycle tests still pass on the row this caller already published.

## Adversarial review

Re-review count: 2. Verdict: CLEAN.

Review 1 found one proof gap. The settled-invitation test required `total_changes` to stay equal across `publish_terminal`, `history`, and `info`. Those calls did not invoke the secondary callback, and the primary fingerprint was not the thing that moved, but `total_changes` went from 54 to 56. The assertion was treating unrelated writes as publication. It now checks the callback spy, the primary fingerprint, and the secondary counts. Consent still asserts `total_changes`.

Checked on this tip and left in place:

- The callback is not invoked from `_publish`, `publish_terminal`, or `_terminal`. History and info stay off this path.
- The consumer is called as `publish_secondary_retained(task)` with an empty keyword dict. Consent is not supplied. The #15 consent method still always raises and writes nothing.
- Eligibility returns before any RPC call when artifacts, recipients, or the primary event digest are missing. Deferred primary publication therefore writes no secondary row.
- A missing contract raises at the consumer's `_require_contract` before register. The caller raises the same error when `publish_secondary_retained` is absent. Neither path inserts a secondary row.
- The caller executes no SQL. Register and publish stay inside the #15 methods.
- A secondary exception after `settle_task` is re-raised from `_execute_attempt` and does not enter the ambiguous-observation branch. `wakeup()` still runs from `_on_terminal`'s `finally`.
- The caller does not skip an existing secondary row. A later consume of the published row keeps the #15 provenance and can retry and complete. Contract tests that need a blank ledger delete only the secondary tables.

Review 2 covered the corrected assertion and the driver sites above. The GREEN command includes them. No remaining in-scope finding.

A notify that runs before primary events exist returns without writing and is not retried from `prepare_room`. Deferred publication and harvest-before-events are that case. Putting the call inside `publish_terminal` or `prepare_room` would publish on reads and fold the secondary ledger into primary publication. A later owner can catch up outside those two functions.

## Still later owners

Catch-up publication when primary terminal evidence appears only after a no-op notify (deferred primary publication, or harvest that settles before events exist). Publication of the upstream output stack this fork has not absorbed. Merging this draft onto fork `main`.
