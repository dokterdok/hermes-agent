# Adversarial review — consumer tip `abc36db5d21a92ca9e1fab6314f6d581429efb22`

Delta review of CONTROLLER_INPUTS v2 private-controls wiring. v1 stays immutable. No product change. No pull request. Matrix is `artifacts/ADVERSARIAL_MATRIX.md`. Baseline remains `artifacts/ADVERSARIAL_REVIEW_cb8_413406ee.md`.

| | |
|---|---|
| Tip | `cursor/barryx-cb8-assemble-52cc` @ `abc36db5d21a92ca9e1fab6314f6d581429efb22` |
| Parent | `7d332b558f5d4a82a6125c6d7761bfe35135e96c` |
| Foundation | `#106742` `cb8d6920549ebe9d31f69f187d30b356b5639eed` (ancestor) |
| Prior freeze | `413406eec16770a182f92ccceb9763b6bd10e5ad` is an ancestor. Its unfinished rows still apply where the gateway blobs did not change. |
| v2 pins | Permission `3b0d88e044f4a689def4ae1fee45186aafb51e11`. Messaging head/recipe `eea4a0c96d321bfd9a03705627f7f0d6a6280f40`. Messaging product `8f5338e6a7699c615112e2e2ef50136f31f8e466`. Route still `8a29b6d13bc558221130b4216a93f373afa68caf`. |

Commits since `413406ee`: `7d332b558f` (R3 handback, no gateway shim) and `abc36db5d2` (Desktop Stop / approval / grant-revoke calls). Both messages carry `Co-authored-by: David Dudok de Wit <dokterdok@users.noreply.github.com>`. Noted only. History was not rewritten.

## Write-set

Paths since `413406ee` are only `apps/desktop/**`, `apps/shared/**`, `docs/layers-integration/**`, `scripts/layers-integration/**`, and `tests/layers_integration/**`. No `gateway/`, `tui_gateway/`, `hermes_state*`, `agent/`, `acp_adapter/`, `tools/`, or `hermes_cli/` path changed.

Blobs identical to cb8: `gateway/session_policy.py` `5aa15e8e…`, `hermes_state_runtime.py` `48960a91…`, `gateway/hosted_rooms.py` `d959f06e…`, `gateway/hosted_room_replicas.py` `50540417…`, `agent/runtime_session_store.py` `1392ff10…`, `gateway/session_group_controls.py` `275ef390…`, `tui_gateway/hosted_room_service.py` `484b933e…`.

`groups.messaging.room.stop.grant`, `groups.messaging.room.stop.revoke`, `groups.messaging.room.approval.grant`, and `groups.messaging.room.approval.revoke` are not registered on this tree.

## What this lab ran

| Check | Result | Scope |
|---|---|---|
| Desktop vitest: `private-controls-parity.test.ts`, `canonical-groups.test.ts`, `canonical-group-workspace.test.tsx`, `canonical-group-fresh-client.test.tsx`, `canonical-group-history.test.tsx` | 5 files, 19 passed | Mocked `requestProfile`. Client contract only. |
| `tests/layers_integration/test_r3_retry_keywords.py` | 2 passed | The tests assert the cb8 `TypeError`. They do not fix it. |
| `tests/layers_integration/test_controller_pins.py` | 8 passed | Route object `8a29b6d…` was already in this clone from the prior review fetch. A bare clone still skips the route-parent check. |

The workspace restore test still prints a React missing-key warning for a journal attachment that has neither `attachment_id` nor `name`. The integrator left that key alone. It is not a new control-wiring defect.

The 6f2 sibling-gateway file recovery on `5202c7d1f2` is not a result for this tip.

## A3 consumer contract (executed)

Shared module `apps/shared/src/private-controls.ts` and the plugin copy `apps/desktop/src/plugins/hermes-bots/private-controls.ts` made the same decisions in `private-controls-parity.test.ts`.

| Rule | Evidence on this tip |
|---|---|
| Allow once / Deny need exactly one displayed `pa-` + 64 lowercase hex selector | `exactDisplayedApproval` throws unless one row matches. A second copy of the same selector throws. `pa-short` and `1` throw. The workspace renders Allow once / Deny only when `isApprovalSelector` is true, and the button name includes that selector. A pending approval with no selector (`bare`) renders no button. |
| Selector is not sent on `groups.approve` | `groupsApproveParams` returns `room_id`, `member_id`, `task_id`, `execution_generation`, `request_id`, `choice`. The vitest click on `Allow once pa-bb…` recorded that body and `not.toHaveProperty('selector')`. |
| Stop is not grant/revoke | Stop calls `groups.stop` with `room_id` and a UUID `cancel_id`. `groupsStopParams` rejects a `pa-` token and a digit list position. The Stop click sent no `groups.messaging.*` method. |
| Grant/revoke are the four room methods | `controlMethod('stop','grant')` is `groups.messaging.room.stop.grant`. Approval revoke is `groups.messaging.room.approval.revoke`. Neither equals `groups.stop`. The consent click test sent stop-grant then approval-revoke and no `groups.stop` or `groups.approve`. Revoke carries `mrc-` + 32 hex. Grant does not send `binding_id`. |
| List position is not a selector | `1` is rejected by `exactDisplayedApproval` and by `groupsStopParams`. The workspace does not use the map index as the selector. Approval rows are keyed by the `pa-` token. |
| Fail closed while cb8 has no selectors and no consent methods | Production mount `group-chat-view.tsx` renders `CanonicalGroupWorkspace` without `operatorControl`, so Grant/Revoke are absent. cb8 does not emit `pa-` selectors, so Allow once / Deny stay unrendered. The four consent methods are unregistered, so a caller that did pass `operatorControl` has no cb8 implementation to treat as success. `PRIVATE_CONTROLS.md` and `JOURNEY.md` say this wiring is not an A3 pass. |

That client evidence does not join Permission `3b0d88e044` or Messaging `8f5338e6` / `eea4a0c96d` onto cb8. A3 stays **OPEN**, owner Barry.

## Per row

Prior SIGKILL cuts (R1 cut 1, R2 cut 1) were executed on `413406ee` against these same gateway blobs. They are not re-labeled as a journey pass. Unexecuted cuts stay open.

| ID | Verdict | This tip |
|---|---|---|
| R1 | OPEN | Gateway admit path unchanged. Crash-before cut 2 and the post-restart single admit are still unexecuted. No second gateway. |
| R2 | OPEN | Catch-up cuts 2–4 still unexecuted. Owner Barry where the cut sits in `gateway/**` or `tui_gateway/**`. |
| R3 | **FAIL P2**, owner Barry | Re-executed on this tip: `_execution_control(..., 'groups.retry', {room_id, member_id, task_id, execution_generation})` raises `TypeError` for `member_id` on `HostedRoomService.retry_room_task`. Desktop Retry still sends those fields (`explicit Retry keeps the pending member and generation on groups.retry`). The client does not drop them and retry by `task_id` alone. `CanonicalHostedRoomService.retry_room_task` is `HostedControls.retry_room_task`, which does compare generation; the base method and the JSON-RPC handler do not forward that call. No gateway shim. |
| R4 | OPEN | Fresh-client vitest is mocked `requestProfile`, including log paging. No held reply released onto another room, profile, owner, or generation. A6 Connect #109338 stays history-held. |
| R5 | OPEN | No new two-gateway credential run. Profile homes that share `default_db_path()` are still one coordination store. 6f2 sibling recovery is not credited. |
| R6 | OPEN | Revocation-before-effect was not run. Retention F1 `c9f0029…` is not applied (`_replica_transaction` has no `_authorize`). Owner Barry. |
| R7 | OPEN | Same-name downloads in vitest are mock bytes keyed by `attachment_id`. Late selection after a held reply was not run. Not the 6f2 file-recovery pass. |
| R8 | HELD | Native Save/Cancel destination bytes were not executed. |
| R9 | OPEN | A3 control consent is not closed. See the contract table. Stop generation versus a newer running attempt was not killed. Route `8a29b6d…` is not applied. Owner Barry. Inventing a client consent success would be a fail; this tip documents the missing methods and does not claim the pass. |
| R10 | OPEN | Desktop close versus an independent gateway pid was not run. Host-loss and exclusive successor stay incomplete. Owner Barry. |
| R11 | OPEN | Missing-store boot and a stale journal send were not re-run. |
| R12 | OPEN | Session policy, `hermes_state_runtime` custody, Route six paths, and Retention F1 are still unjoined. Pin test confirms the cb8 blobs. A supplier replay is not a cb8 pass. Owner Barry. |
| R13 | OPEN | Sentinel teardown was not run. |

## New consumer findings

No new consumer P1, P2, or P3. The private-controls wiring matches the v2 client rules that were executed, and it stays inside the consumer write set.

## Remaining Barry blockers

- R3 P2: base `retry_room_task` rejects the Desktop generation keywords; the JSON-RPC `groups.retry` handler does not forward them. The compare that exists on `HostedControls` is not the path this call hits.
- Join Permission `3b0d88e044` and Messaging product `8f5338e6` (recipe `eea4a0c96d`) onto cb8, including any `gateway/session_policy.py` overlap, keeping `present_sections`.
- `hermes_state_runtime.py` absent-annotation clearing versus `input_custody` / `_terminal_write`, with `agent/runtime_session_store.py` kept.
- Route `8a29b6d…` six paths versus the output recipe's older route pin.
- Retention F1 authorize-before-audit versus recipe `004015d…`.
- A6 history publication (#109338) and A7 host-loss.
- Native Windows Save/Cancel (R8) stays held.

## Overall

**Not finished.** R3 remains an open P2 owned by Barry. R8 remains held. R9 / A3 remains open: the consumer can form the public calls and does not claim they pass on cb8. The two-gateway journey was not run. The 6f2 sibling recovery is not a cb8 result. #98307 was not published.
