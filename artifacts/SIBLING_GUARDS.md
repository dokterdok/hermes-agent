# Guards a frozen candidate still has to show

R12 in `artifacts/ADVERSARIAL_MATRIX.md` re-checks this list on the frozen SHA. A later owner tip is not a superset because its date is later. Execute the behavior. An import or collection error is not a failing counterexample.

Symbols below exist on composition base `6f2aeb528fc6e8c9cee8ed1fa022a1c50c05829d`. On the frozen tree, follow the wire method if the function moved.

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

Compare input tips by these observations. Keep the stricter result. Do not drop a guard to match the tip that is currently checked out.
