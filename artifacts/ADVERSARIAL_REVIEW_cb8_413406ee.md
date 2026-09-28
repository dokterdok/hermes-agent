# Adversarial review — frozen candidate `413406eec16770a182f92ccceb9763b6bd10e5ad`

Independent review only. No product change. No pull request. Matrix is `artifacts/ADVERSARIAL_MATRIX.md` (R1–R13).

| | |
|---|---|
| Candidate | `cursor/barryx-cb8-assemble-52cc` @ `413406eec16770a182f92ccceb9763b6bd10e5ad` |
| Parent | `cb8d6920549ebe9d31f69f187d30b356b5639eed` (#106742). `git merge-base --is-ancestor` holds. |
| Writable delta | `apps/desktop/**`, `docs/layers-integration/**`, `scripts/layers-integration/**`, `tests/layers_integration/**`. Gateway, TUI gateway, `hermes_state*`, and `agent` blobs are the cb8 blobs. |
| Historical 6f2 | `cursor/barryx-layers-integrate-52cc` @ `5202c7d1f214b10d62a08e2e9c09c36c8bc4f2ce`. Sibling-gateway file recovery there is 6f2 evidence. It is not a result for this freeze. |

Lab home was disposable under `/tmp/review-lab`. The probe imported `/tmp/barryx-cb8` (this commit) with `HERMES_HOME` pointed at that lab. SIGKILL was `os.kill(pid, 9)` of the child that had opened the store. Rows were read from a new SQLite connection after the child died.

## What this lab ran

| Check | Result | What it is |
|---|---|---|
| `tests/layers_integration/test_controller_pins.py` | 8 passed after `8a29b6d…` was fetched into the object store. Before that fetch the route-parent test skipped (`route pin is not in this clone`) and the file was 7 passed, 1 skipped. | Blob identity and pin parentage. The route object is not an ancestor of this branch, so a bare clone skips that one check. |
| Desktop vitest, four files (`canonical-group-fresh-client.test.tsx`, `canonical-groups.test.ts`, `canonical-group-history.test.tsx`, `canonical-group-workspace.test.tsx`) | 4 files, 10 passed | Mocked `requestProfile`. Member order and same-name downloads by `attachment_id`. One React key warning in the workspace file. This is client evidence. It does not start two gateway processes and does not read destination bytes from disk. |
| Durable probe | Recorded under each row | Real `hosted_rooms` / attachment / idempotency / `dispatch_group_control` calls on this tree. |

`default_db_path()` on this tree: two homes whose parent directory is `profiles` resolve to the same root `shared-state.db`. Two standalone homes resolve to two files. A pair of profiles under one `profiles` directory is one coordination store.

## Per row

### R1 — Crash before the durable obligation — OPEN

**Reproduction.** Child `put` of `note.txt` (`sha256` `c48dd952…`) then SIGKILL before `commit_message_with_receipt` and before `append_event`. Exit `-9`. New connection: one attachment, `state='uploaded'`, `event_id` NULL; `hosted_room_events` empty; no `hosted_room_driver_tasks` rows.

**Limitation.** `POST /v1/runs` before `RunIdempotencyStore.reserve` was not killed. The later explicit send that must admit that same identity once was not run. No second gateway process. Cut 1 did not leave a message or task row. The row stays open until cut 2 and the single post-restart admit are run on this revision.

**Owner.** Barry, if a later run of cut 2 needs `gateway/**`.

### R2 — Crash after the durable obligation — OPEN

**Reproduction (cut 1).** SIGKILL inside `append_event` after `commit_message_with_receipt` returned. Exit `-9`. New connection: attachment `state='committed'`, `event_id='event-1'`, same `sha256`, zero `hosted_room_events`. A new process retried that `event_id` twice: one `message.user` row. A different `event_id` for the same attachment raised `AttachmentConflictError`. The attachment row stayed on `event-1`.

**Limitation.** Peer receipt (cut 2), work-record savepoint (cut 3), and idempotency reserve before the 202 (cut 4) were not killed. Cut 1 held. The row is not passed on one cut.

**Owner.** Barry for cuts that sit in `gateway/**` / `tui_gateway/**`.

### R3 — Lost ACK, explicit Retry — FAIL

**Severity.** P2. **Owner.** Barry (`gateway/session_group_controls.py`, `tui_gateway/hosted_room_service.py`). The consumer lane must not patch those files.

**Reproduction.** `HostedRoomService.retry_room_task(room, task_id=…, member_id=…, execution_generation=1)` raised `TypeError: retry_room_task() got an unexpected keyword argument 'member_id'`. `_execution_control` for `groups.retry` type-checks `execution_generation >= 1` and then calls `retry_room_task(**params)`, which forwards `member_id` and `execution_generation`. The method accepts only `room_id` and `task_id`. The generation compare against `hosted_room_driver_tasks.execution_generation` does not run.

Same lab, `append_event` for one `event_id` and one payload: one row; a second identical append stayed one row; a different payload raised `EventConflictError`. That is the send identity. It is not the uncertain-task Retry the fresh Desktop client issues through `groups.retry`.

**Why this is a fail.** The Desktop path does not compare the client generation to the stored row. Explicit Retry of uncertain work errors before the fence. A client that dropped the extra keywords and retried by `task_id` alone would be the other fail (requeue whatever row is loaded). This freeze does the first of those.

Journal cold-start of `prepared-submissions-*.json` was not driven. The send-idempotency slice held and does not retire the row.

### R4 — Stale reply after room / profile / owner / generation change — OPEN

**Limitation.** No held `groups.attachment.download` was released onto a second room, profile, owner, or generation. The fresh-client vitest unmounts one binding and mounts another against a mock. It checks member text and download parameters. It does not write a second room's tables.

A6 Connect #109338 (`acf21665…`) stays history-held. The cross-room / epoch half stays open. Owner for a history join is Barry.

### R5 — Wrong-profile credentials — OPEN

**Reproduction.** `dispatch_group_control` with the authority home different from `Path(db_path).parent` raised `RuntimeStoreError: profile_mismatch`. The foreign `hosted_rooms` count stayed 1 and `hosted_room_events` stayed 0.

**Limitation.** Actor `profile_id != authority.profile_id`, `POST /v1/runs` target profile / install id, and two live gateway processes were not run. Two profile homes under `profiles/` share `default_db_path()`. That pair is not two gateways. The 6f2 sibling-gateway pass is not credited here.

**Owner.** Barry if the remaining refusal has to change `gateway/**`.

### R6 — Revocation between preparation and effect — OPEN

**Limitation.** No grant was prepared, revoked, and then released into an effect. Retention F1 `c9f0029…` is not on this tree: `hosted_room_replicas._replica_transaction` takes only `db_path` (no `_authorize`). Authorize-before-audit is still required and is Barry's join. Stop / approve / deny effects stay with R9.

**Owner.** Barry.

### R7 — Same name, two versions, late selection — OPEN

**Reproduction.** Two `put` calls, name `same.txt`, upload ids `u1` and `u2`. A new connection read two rows: `att_ddf241d2…` / `sha256` `83286460…` and `att_0d103cea…` / `sha256` `d11b889d…`.

**Client evidence, separate.** Vitest clicks both downloads on a fresh connection id. The mock returns `att-v1` then `att-v2`. The test asserts those ids in the request. It does not read blob files.

**Limitation.** Late release of the earlier download after the selection changed was not run. No `groups.log` from a live gateway. The 6f2 sibling file-recovery pass is not this row.

### R8 — Partial write, cancel, pre-existing destination — HELD

Native Windows Save/Cancel destination bytes were not executed. No separate admission. Electron `finalizeGatewayDownload` / `pumpStreamToFile` sentinel kill was not run in this lab. Claiming Windows proof from the vitest mock (`saveImageBuffer`) would be an unexecuted pass. The row stays held.

### R9 — Stop generations and control consent — OPEN

**What was checked.** The fresh-client vitest expects no `groups.approve` or `groups.deny` call. `CanonicalGroupWorkspace` still sends `groups.stop` with `room_id` and `cancel_id` only. `actCanonicalGroup` still maps approval to `groups.approve`. No new consent field was added.

**Limitation.** Permission #111939 has no accepted Stop/approve/deny successor. Messaging controls are in flight. A pass requires the mutation itself to fail closed. That fence was not executed, and a client workaround was not added. Inventing consent would be a fail; this tree did not invent it. The row stays open. Owner: Barry.

Route `8a29b6d…` delegated-stop fence is not applied (see R12). Old Stop versus a newer running attempt was not killed on this revision.

### R10 — Gateway absence, return, stale copies — OPEN

**Limitation.** Desktop `before-quit` versus an independent room-gateway pid was not run. `recover_room`, `ingest_page`, and `promote_replica` were not driven. Recovery `c63f9382…` is a scoped supplier and is not on this delta. Host-loss and exclusive successor stay incomplete. No unauthorized takeover was attempted. Local stop-ack `8461351…` is not in the tree and is not treated as green.

The 6f2 sibling-gateway file recovery is not the Desktop-close half of this row.

**Owner.** Barry for promote / host-loss.

### R11 — Missing or replaced stores — OPEN

**Reproduction.** `RunIdempotencyStore` pointed at a path whose parent is a file. It logged the fallback and set `durable` false (`_db_path is None`). A restart claim that used that process memory would not survive. This lab did not then reserve a key or return 202.

**Limitation.** Launch-profile store failure, `parked_profiles` / `served_profiles`, empty `state.db` replacement, and a stale journal `groups.send` were not run.

**Owner.** Barry if boot parking has to change `gateway/run_runtime.py`.

### R12 — Composition drops a guard — OPEN

Blocked. Owner: Barry. This commit does not hand-merge the joins, and it does not report the first journey passed. The stricter oracles are absent on the tree, so the row cannot pass.

Pin test, this lab, 8 passed once `8a29b6d…` was present:

| Path | Blob kept (matches cb8) |
|---|---|
| `gateway/session_policy.py` | `5aa15e8e8fc19d31b7156e93515d59aab94fb52c` |
| `hermes_state_runtime.py` | `48960a91fb2c669bd2aa311960381ca7a4415dc6` |
| `gateway/hosted_rooms.py` | `d959f06ebc22d9c6ad5b459a81dbc4cab9192bde` |
| `gateway/hosted_room_replicas.py` | `505404179ccc1ff50182b9a1707f35ef7bfb9a50` |
| `agent/runtime_session_store.py` | `1392ff1056fcebbc9861d9ef7053777552cd18bb` |

Executed signatures on this tree:

| Required supplier piece | On `413406ee` |
|---|---|
| `session_policy.present_sections` | Present (cb8 null-section behavior kept). |
| `admit_session_input(..., input_custody)` | Parameter absent. |
| `settle_session_input(..., _terminal_write)` | Parameter absent. |
| `room_state(..., conn=)` | Parameter absent. |
| `_replica_transaction(..., _authorize)` | Parameter absent. Retention F1 `c9f0029…` is not applied. Parent of that pin is recipe `004015d…` (pin test). |
| Route `8a29b6d…` six paths | Not applied. Parent of that pin is `1fa3c0…` (pin test, after the object was fetched). Output recipe `23c5e66…` still names the older route pin. Pasting one side drops the other. |

`4be9cb11…` is not the recipe pin. Output recipe `23c5e66…` and implementation `31d00b0…` are not a cb8 pass by being named in `SOURCE_MAP.md`.

### R13 — Teardown touches only task-owned resources — OPEN

**Limitation.** No sentinel pid, sentinel destination, or second-profile gateway was planted before client shutdown or test cleanup. `HostedRoomRuntime.stop` was not driven against a live accepted task.

## First journey

The proving journey (two gateways, file admit, client close, gateway continues, fresh client, exact bytes) was not run on this revision. Desktop vitest 10/10 and the pin test 8/8 do not substitute for it. The 6f2 sibling-gateway file recovery does not substitute for it.

Executed slices that held and still leave the journey open: R1 cut 1 (no event after SIGKILL), R2 cut 1 (one event on retry, conflict on a second id), R7 store identity (two hashes for one name), R5 db-parent `profile_mismatch`.

## Overall verdict

**Not finished.**

| Verdict | Rows |
|---|---|
| FAIL | R3 (P2, owner Barry) |
| HELD | R8 (native Save/Cancel destination bytes unexecuted) |
| OPEN | R1, R2, R4, R5, R6, R7, R9, R10, R11, R12, R13 |

R3 is an open P2. R8 is a required unexecuted gate. R6, R9, R10 (host-loss), R12 (session policy / `hermes_state_runtime` / Route 8a29 six-path / Retention F1 authorize-before-audit), and A6 history publication stay open with owner Barry. A3 was not closed by a client consent workaround. No P1 was observed in the slices this lab ran. The candidate is not a P1/P2/P3-free integrated pass, and #98307 was not published.
