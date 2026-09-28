# R3 returned to Barry — lost ACK, explicit Retry

Review: `artifacts/ADVERSARIAL_REVIEW_cb8_413406ee.md` on
`cursor/adversarial-matrix-fa6c` at `d8cf1908d19ad998ab76fbe84fea48c9823e12bb`.
Candidate under review: `413406eec16770a182f92ccceb9763b6bd10e5ad`.

**FAIL. P2. Owner: Barry.** No gateway file was edited for this note.

## Desktop surface

`CanonicalGroupWorkspace` renders each `groups.state` `driver_status.pending_actions`
entry. For `kind: 'retry'` the Retry button calls `actCanonicalGroup`.

`actCanonicalGroup` (`apps/desktop/src/plugins/hermes-bots/canonical-groups.ts`)
sends `groups.retry` with:

```text
profile, room_id, member_id, task_id, execution_generation
```

`canonicalGroupRequest` adds `profile` from the binding. The workspace test
`explicit Retry keeps the pending member and generation on groups.retry`
clicks that button and records this call:

```text
connectionId: fresh-client
targetProfile: reviewer
method: groups.retry
params: {
  profile: reviewer,
  room_id: room-one,
  member_id: two,
  task_id: task-uncertain,
  execution_generation: 4
}
```

The client leaves those fields in place when the gateway answers
`invalid_params`. It does not drop `member_id` or `execution_generation` and
retry by `task_id` alone. That drop would requeue whatever row the service
loads. A3 consent is unchanged: this click does not send `groups.approve` or
`groups.deny`.

## Call that raises before the generation compare

`dispatch_group_control` strips `profile`, then `_group` calls
`_execution_control`. For `groups.retry`, `_execution_control` checks
`execution_generation >= 1`, `member_id`, and `task_id`, then:

```text
service.retry_room_task(**params)
```

with `params` equal to `{room_id, member_id, task_id, execution_generation}`.

`tui_gateway.hosted_room_service.HostedRoomService.retry_room_task` is
`(self, room_id, *, task_id)`. Executed on this tree:

```text
_execution_control(service, 'groups.retry', {
  room_id: 'room-one', member_id: 'one', task_id: 'task-1', execution_generation: 1
})
TypeError: HostedRoomService.retry_room_task() got an unexpected keyword argument 'member_id'
```

`tests/layers_integration/test_r3_retry_keywords.py` is that call.
`dispatch_group_control` turns the `TypeError` into
`RuntimeStoreError('invalid_params')`. `HostedRoomService.retry_room_task`
never reads `hosted_room_driver_tasks.execution_generation`. The same class
is what `tui_gateway/methods_groups.py` `start_hosted_room_service` constructs.

The JSON-RPC handler `groups.retry` in `tui_gateway/methods_groups.py` calls
`service.retry_room_task(room_id, task_id=...)` and does not pass `member_id`
or `execution_generation`. That is the other failure: a `task_id` retry with
no generation compare. The Desktop payload above still contains both fields;
this handler does not forward them. Neither shape was given a client or
gateway shim.

## What does compare generation

`gateway.session_hosted_service.CanonicalHostedRoomService` resolves
`retry_room_task` to `gateway.session_hosted_controls.HostedControls.retry_room_task`
`(self, room_id, *, member_id, task_id, execution_generation)`.
`ensure_hosted_service` assigns that class. `_control_task` compares
`execution_generation` and `member_id` to the stored task before requeue.
The keyword `TypeError` is the base `HostedRoomService` method, which the
MRO of the canonical class does not select.

Widening only the base signature, or stripping the Desktop fields, would
still skip that compare on the base method and on the JSON-RPC handler.
The compare that already exists on `HostedControls` is the contract the
forwarded Desktop call needs. That join stays with Barry.

## Still blocked on Barry

- This R3 pair (base `retry_room_task` keyword rejection, and the JSON-RPC
  handler that omits the generation).
- `gateway/session_policy.py` null-section join versus the recipe whole file.
- `hermes_state_runtime.py` removal-based annotations versus
  `input_custody` / `_terminal_write`.
- Route `8a29b6d` six paths versus the output plan's 1fa3 spans.
- Retention F1 `c9f0029` authorize-before-audit versus the recipe double-init.
- Permission #111939 and Messaging controls. A3 stays open.

The two-gateway file journey was not run on cb8. The 6f2 sibling recovery is
not a result for this candidate.

## Consumer observations beside this fail

These do not move R3 off Barry, and they do not close the other review rows.

- The Desktop Retry button is the surface above. When the gateway answers
  `invalid_params`, that text stays on screen. The client does not send a
  second `groups.retry` with only `task_id`.
- `groups.stop` from the same workspace still sends `room_id` and `cancel_id`
  only. No approve or deny field was added. A3 stays open. R9 stays open.
- A fresh connection pages `groups.log` (`has_more`, next `since_seq` = last
  `seq`) and downloads each version by `attachment_id`. A cursor that does
  not advance leaves the log and the member list unpublished. A later binding
  on another room does not download the previous room's attachment. That is
  mocked `requestProfile`. It is not two gateway processes, not bytes on
  disk, and not the 6f2 sibling recovery. R1, R2, R4, R7, and R8 stay open
  or held on the review's terms.
- The pin test skips the route-parent check until `8a29b6d` is in the local
  object store. That commit is not an ancestor of this branch. The five kept
  backend blobs are unchanged. R12 stays open.
- A restored composer attachment that only has `path` and `mime_type` still
  renders under `key={attachment_id ?? name}`. Both are missing for that
  journal shape, so React warns in the existing restore test. The test still
  passes. This note does not retarget that key.
