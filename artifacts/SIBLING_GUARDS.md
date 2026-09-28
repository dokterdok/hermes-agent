# Guards a frozen candidate still has to show

R12 in `artifacts/ADVERSARIAL_MATRIX.md` re-checks this list on one frozen revision of assembly target `cb8d6920549ebe9d31f69f187d30b356b5639eed`. A later owner tip is not a superset because its date is later. Execute the behavior. An import or collection error is not a failing counterexample. A green result on supplier foundation `6f2aeb528fc6e8c9cee8ed1fa022a1c50c05829d` is not a cb8 pass.

Symbols below were located on that foundation. On the frozen tree, follow the wire method if the function moved. If the failing guard lives in `gateway/**`, `tui_gateway/**`, `hermes_state*.py`, or `agent/**`, the finding stays assigned to Barry. The consumer lane does not privately patch those trees.

Controller pins, not the older handoff snapshots: Route `8a29b6d13bc558221130b4216a93f373afa68caf` (not `1fa3c0…`); Output recipe `23c5e66d0da21b46cc373993cd39dc2d7ab3929e` and implementation `31d00b0ed728e8d580aadf8a143cda3343752441`; Retention F1 `c9f0029475f085e3b5e66b77df74cd5470925aef` carried explicitly against recipe pin `004015d6087fe031231c4d7d9e0032cc59b679eb`. The older plan tip `4be9cb11…` is not the Output recipe pin.

| Guard | Entry | Pass observation |
|---|---|---|
| Same task id, different payload | `hosted_room_driver.admit_task` | `TaskConflictError`; one `hosted_room_driver_tasks` row; `payload_digest` unchanged |
| Same event id, different body | `hosted_rooms.append_event` | `EventConflictError`; one `hosted_room_events` row |
| Same peer attempt, different `run_id` | `hosted_rooms.upsert_remote_run_receipt` | conflict; the first `hosted_room_remote_runs.run_id` remains |
| Stop ACK for a different generation | `complete_task_cancel` | row stays `stopping`; `cancel_id` unchanged |
| Recovery does not replay unknown work | `recover_room` | foreign `running` becomes `indeterminate`; status is not set back to `queued` |
| Evidence rollback does not pretend catch-up happened | `capture_transition_locked` savepoint inside `_transition` | task row may commit; a rolled-back savepoint leaves work records missing or `availability='unavailable'` |
| Receipt is a second commit | `PeerRunsHTTPClient._admit_dispatch` | kill between peer `run_idempotency` insert and home `hosted_room_remote_runs` insert; restart binds the original `run_id` once |
| Busy-lock retry must not replay settlement | `SessionDB._execute_write` | this base has no `live_read_connection` / `live_write_connection`. A composed Files/Output settlement that runs inside `_execute_write` fails R12. Reporting that journey blocked keeps R12 open |
| Destination is not opened early | `pumpStreamToFile` / `writeBufferToFile` | sentinel bytes at `destPath` survive cancel and mid-write kill; temp uses `wx` |
| Blob replace is the publish | `HostedRoomAttachmentStore._write_blob` | kill before `os.replace`: no downloadable `blob_<id>` for that `upload_id` |
| Promote is explicit | `groups.promote` → `promote_replica` | `confirm` other than true writes nothing; a room id already in `hosted_rooms` is `RoomConflictError` |
| Wrong profile stops first | `dispatch_group_control` | `profile_mismatch` before any insert |
| Desktop Retry keeps the generation | `session_group_controls` `groups.retry` | client `execution_generation` older than the stored row does not requeue. On this base the canonical caller passes `execution_generation` into `retry_room_task`, which accepts only `room_id` and `task_id`, and the runtime fences the loaded row. Dropping the extra keywords and retrying by `task_id` alone fails the row. The TUI `groups.retry` handler is not the Desktop path |
| Client shutdown is not Stop | `HostedRoomRuntime.stop` | accepted task row stays non-terminal; only that runtime's threads are joined |
| Route delegated Stop keeps the admitted generation | Route `8a29b6d…` six paths: `hosted_room_delegated_control`, driver, `hosted_rooms`, both TUI hosted-room modules, `test_hosted_room_delegated_control` | stop snapshots the attempt at fence admission; a later generation is not settled by the earlier ACK; uncertain stays uncertain. The 16/0 public replay is a supplier fixture, not a cb8 pass |
| Retention F1 revocation survives the Output recipe | Retention `c9f0029…` beside recipe pin `004015d…` | a revoked or denied identity gains no execution authority. The historical recipe pass does not retire this row |
| Control consent fails closed at the mutation | `begin_task_cancel` / `complete_task_cancel` / approve, while Permission successor is unpublished | no new task, run, or settled row without the consented mutation. A client that hides the control or fabricates consent fails the guard. Owner is Barry if the fence is missing in gateway or TUI |

Compare input tips by these observations. Keep the stricter result. Do not drop a guard to match the tip that is currently checked out. Do not treat `1fa3c0…` or `4be9cb11…` as the live Route or Output pins.
