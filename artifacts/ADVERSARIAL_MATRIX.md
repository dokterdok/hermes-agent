# Adversarial matrix — BarryX Layers (#97681)

Independent review artifact only. No product change. No new PR. Symbol locators were read on supplier-proof foundation `6f2aeb528fc6e8c9cee8ed1fa022a1c50c05829d`. The assembly target is public #106742 `cb8d6920549ebe9d31f69f187d30b356b5639eed` (NousResearch/hermes-agent; never push). A green result on 6f2aeb is not a pass of cb8d692. Pins and ownership below follow immutable CONTROLLER_INPUTS v1 and supersede conflicting assignment or handoff pins.

Each row is a falsifier for one frozen candidate revision. Pass means the persisted oracle below holds on that revision after a barrier fault and a real process restart where the row requires one. A green unit test, a success boolean, an in-memory dict, or a `localStorage` write does not pass a row. A missing registered path on the frozen tree fails the row. Renamed helpers still count: follow the wire method to the store it opens, and record that path. Supplier proofs on 6f2aeb, and a fixture replay of a supplier, are not automatic cb8 passes. No cb8 composed behavioral gate has run. Every row below stays OPEN until it is executed on the frozen assembly candidate.

Injection rules live in `artifacts/FAULT_INJECTION.md`. The guard list a composition must still satisfy lives in `artifacts/SIBLING_GUARDS.md`.

## Controller pins and ownership

Authoritative handoff: CONTROLLER_INPUTS v1 (DAVID-LAYERS-RUNNABLE-FINISH-20260928 and DAVID-BARRYX-BRIEF-DELIVERED-20260928-151808). Original controller `20260908_093606_d3e087`. Preserve this snapshot. A changed supplier needs a successor, not a silent pin swap. Direction gist `765f9d551ce88ee01630c18367763e75` revision `70a69b7844871ed696637cecfcfa365ea7ce6e7a`.

### Assembly target vs supplier foundation

| Role | Pin | What a result on it means |
|---|---|---|
| Assembly target #106742 | `cb8d6920549ebe9d31f69f187d30b356b5639eed` | The revision these rows are judged against. Never push to that repository. |
| Supplier proof foundation | `6f2aeb528fc6e8c9cee8ed1fa022a1c50c05829d` | Recipes, prior results, and the symbol names in R1–R13. Proofs of this foundation plus explicit overlays. Not proofs of cb8d692. |
| Integration carrier #98307 | `cb657c411cda8a3b645e9cbfa2a2c8df5ef9d4c5` | Not the new runnable candidate. Permanent draft. Barry alone publishes. Never push local `ec34dfc4f6d122503c5f578922a9525bbbc6c193`. |

Inspected authority, gateway runtime, ACP, hosted-room, and `hermes_state.py` surfaces had no source delta between cb8d692 and 6f2aeb. The trees are not equal: cb8 carries client, build, history, and other Runtime changes. Exactly two Output-recipe product paths intersect that upstream set: `gateway/session_policy.py` and `hermes_state_runtime.py`. Those joins stay Barry/backend-owned. Preserve upstream null-section normalization and canonical row, snapshot, and timestamp annotations (absent stamps are removed, not written by a simple dict update) and retain owner admission and history guards. Do not overwrite either file with an older full copy.

### Supplier pins the rows use

| Owner | Pin | Matrix use |
|---|---|---|
| Runtime #111216 | `041ae76f80ba2330ef6f1b7b961adc08e59a1877` | Admission, lifetime, and terminal metadata. |
| Route #100016 | `8a29b6d13bc558221130b4216a93f373afa68caf` | Supersedes assignment snapshot `1fa3c0…`. Delegated Stop snapshots the exact attempt at fence admission, uses transactional generation guards, persists honest uncertain and settled receipts, and guards physical lifetime before authority reads. Approval captures once and never resends uncertainty. Six declared paths: `gateway/hosted_room_delegated_control.py`, `gateway/hosted_room_driver.py`, `gateway/hosted_rooms.py`, `tui_gateway/hosted_room_driver.py`, `tui_gateway/hosted_room_service.py`, `tests/tui_gateway/test_hosted_room_delegated_control.py`. A public Messaging-recipe replay of those paths is a supplier fixture, not the cb8 or Desktop stack. |
| Output #99159 recipe | `23c5e66d0da21b46cc373993cd39dc2d7ab3929e` | Composition contract. Public `docs/layers-output-journey/` (`materialize.py`, `plan.json`, `JOIN_CONTRACTS.md`, `README.md`), 243 paths. The older plan tip `4be9cb11…` is not this pin. |
| Output #99159 implementation | `31d00b0ed728e8d580aadf8a143cda3343752441` | Owner behavior the recipe composes. Recipe and implementation are each a supplier, not a cb8 pass. |
| Retention #99107 F1 | `c9f0029475f085e3b5e66b77df74cd5470925aef` | Carry explicitly. The Output recipe still pins older `004015d6087fe031231c4d7d9e0032cc59b679eb`. A historical recipe pass does not prove the later F1 union. |
| Input #111362 | `a05c7d2fc9c16f7d1a5d6bf8ecb8e6f3365ca462` | Retained input and custody. |
| Files transport #98072 | `9f143dedd82a6da452d20d1cace4869b23b14ada` | Distinct from classic Files. |
| Classic Files #104198 | `3cc3eb71b011c1d58aa6e87e1bda3e422093a074` | Independent backend owner. |
| Desktop Files #104199 | `3537e93cf89013e234e7980e7cf83fcd44319885` plus test `d99c30e45f2519faa582e4d6b61f2b429411bf77` | Recipe foundation is not cb8 acceptance. The declared inherited availability-test failure stays declared. |
| Preservation #104601 | `ab1414260a79b7a9c4415576f23fb2d1e8dd97fc` | Accepted F1 revocation correction. Not blanket recovery completion. |
| Permission #111939 | baseline `34332b47b3fb3c8394879e7179e157b89be115de` | Read and Send are not Stop, approve, or deny. Control consent is PENDING. No immutable successor. |
| Messaging #98073 | `d4d9f905e8123eea38ad81c4cdd6ac44257315d8` | Published private list, Send, ACK, detail. Thin control commands are in flight. |
| Desktop #97846 | `5211890fb87b626c9ba569910375707530c3a2cc` | Current owner fixes and ancestry, not only an older recipe baseline. |
| Recovery #105079 | `c63f938278b9d6c4072191b0ea936a7762c5881b` | Scoped increment. Host-loss and exclusive successor stay incomplete. |
| Connect #109338 | `acf21665bcecf0edf27e117a35b9338a34f567c3` | History-held. Not a reviewed replacement. Do not import inherited lower changes as owner work. |

Local-only, not public, and not copied into the consumer: canonical stop-ack `8461351e856450f33f6ed80a41b0d7e81b47fa59` (a joined-test timeout is retained; that journey is not repeatably green); shipped-history importer `cc69090b8875f6c338fccb09d45f0cf2dfa8440b`; mutable checkout `c7f03671123f24342adc7be8600f72f286ea154f` plus a pending `hermes_constants.py`. That HEAD alone is not the local proof.

### Write-set and finding ownership

The consumer lane must not privately patch `gateway/**`, `tui_gateway/**`, `hermes_state*.py`, `agent/**`, `acp_adapter/**`, `tools/**`, or `hermes_cli/**`. BarryX's isolated write set is client and integration only: `apps/desktop/**`, `apps/shared/**`, and new scripts, tests, and docs under a task-local root. Lab builds may consume public immutable sources. A nontrivial backend merge conflict, including the two cb8 overlaps, returns to Barry.

If an oracle fails inside a reserved tree, the finding stays assigned to Barry. A Desktop-only change that hides Stop, approve, or deny, or that synthesizes consent, does not pass the row. Record one candidate revision per result. Do not sum tests across tuples.

### Rows that stay OPEN

| Surface | Status | What a pass requires |
|---|---|---|
| A3 / R9, and any Stop, approve, or deny effect inside R6 | PENDING control consent | The mutation itself fails closed: no new task, run, or settled row; uncertain work stays uncertain. A client that omits the control or fabricates consent fails the row. |
| A5 / R8 native Save and Cancel destination bytes | HELD / unexecuted | Electron `saveGatewayDownload` remains the falsifier for the Electron path. Windows native proof stays unexecuted unless separately admitted and this oracle is run. |
| A6 / R4 cross-room and history | OPEN. Connect #109338 is history-held | `acf21665…` is not a replacement candidate. |
| A7 / R10 promote and host-loss | OPEN. Recovery `c63f9382…` is scoped only | No unauthorized takeover. Exclusive-successor and host-loss stay open. |

Every other row stays OPEN until executed on the frozen cb8 assembly candidate. A 6f2aeb supplier proof does not close it.

## Index

| ID | ASSIGNMENT §6 attack | Journey |
|---|---|---|
| R1 | Commit-before-response, crash **before** the durable obligation | First vertical |
| R2 | Commit-before-response, crash **after** the durable obligation and before the secondary catch-up / caller ACK | First vertical |
| R3 | Lost ACK, explicit same-command Retry after cold hydration | First vertical |
| R4 | Stale async reply after room / profile / owner / generation change | First vertical (same-room reopen) and later A4/A6 (cross-room write) |
| R5 | Valid credentials for the wrong profile | First vertical |
| R6 | Revocation between preparation and effect | Later A1 / A3 |
| R7 | Two same-name file versions, including the earlier one after reconnect, plus a late selection reply | First vertical |
| R8 | Partial write, cancel, pre-existing destination | Later A5 (native Save/Cancel destination bytes HELD / unexecuted) |
| R9 | Old Stop vs new work; ambiguous Stop vs confirmed stopped | Later A3 / A7 (control consent PENDING; fail closed at the mutation) |
| R10 | Gateway absence, return, unknown accepted work, stale/passive copies | First vertical (continue while Desktop is closed). Promote and host-loss stay OPEN (A2 / A7) |
| R11 | Missing or replaced stores, stale caller state, fail closed | Later A1 / A7 |
| R12 | Branch composition drops an earlier guard; a later sibling is not a superset | Freeze precondition for the first journey, then again for each later group |
| R13 | Teardown and native helpers touch only task-owned resources | First vertical |

## Stores the oracles read

Record the path the live process opened. Do not assume these files are the same.

| Store | Who opens it on this base | What a row counts |
|---|---|---|
| Profile `state.db` | `CanonicalHostedRoomService` sets `db_path=authority.db.db_path` (`gateway/session_hosted_service.py`). Desktop `groups.*` goes through `gateway/session_group_controls.py`. | `hosted_rooms`, `hosted_room_events`, `hosted_room_driver_tasks`, `hosted_room_attachments`, `hosted_room_remote_runs`, `state_meta` owner keys |
| Root `shared-state.db` | `hosted_rooms.default_db_path()`: profile homes whose parent directory is `profiles` share the **root** file. Room-grant revocation uses this path (`gateway/platforms/api_server_room_grants.py`). | `hosted_room_revoked_grants`, `hosted_room_peer_reservations`, replica tables when the TUI service is the writer |
| `runs_idempotency.db` | `RunIdempotencyStore` under the profile home (`gateway/platforms/api_server_run_idempotency.py`) | `run_idempotency` row for `Idempotency-Key` |
| Blob directory | `default_attachment_root(db_path)` → `<db parent>/hosted-room-attachments/blobs/<blob_id>` | file bytes and SHA-256 |
| Desktop journal | `apps/desktop/electron/prepared-submissions.ts` → `<userData>/prepared-submissions-<sha256(origin)>.json` via `writeSecretFileAtomic` | exact `event_id` and payload awaiting Retry |
| Runtime descriptor | `gateway_state.json` `parked_profiles` / `served_profiles` from `gateway/run_runtime.py` | fail-closed profile admission |

Two processes are two gateways only when each `default_db_path()` and each `authority.db.db_path` differ. Two profiles under one `~/.hermes/profiles` share one root `shared-state.db`.

---

## R1 — Crash before the durable obligation

**Failure boundary.** Kill the gateway process after it has accepted the client bytes and before the admitting transaction commits. Two cuts, both required:

1. Desktop `groups.send` → `HostedRoomService.send` → `append_user_event` (`gateway/session_hosted_attachments.py`). Kill before `HostedRoomAttachmentStore.commit_message_with_receipt` commits and before `hosted_rooms.append_event` commits.
2. Peer `POST /v1/runs` (`gateway/platforms/api_server_runs.py` `_handle_runs`). Kill before `RunIdempotencyStore.reserve` commits.

**User invariant.** After a real restart of that gateway, the turn is absent. A later explicit send with the same identity may admit it once. The client shows an unresolved intent, never a delivered message.

**Real entry point.** `groups.send` in `gateway/session_group_controls.py` `_execution_control`, and `POST /v1/runs` with `Idempotency-Key: room:<task_id>:<execution_generation>` (`gateway/platforms/api_server_room_dispatch.py` requires that exact key).

**Observable.** For cut 1: no `hosted_room_events` row with that `event_id`; no `hosted_room_driver_tasks` row for that `thread_id`+`turn_id`; any attachment row is still `state='uploaded'` with `event_id` NULL, or absent. For cut 2: no `run_idempotency` row for that scope+key, and no `hosted_room_remote_runs` row on the caller. Restart the process (new pid, same home, empty memory) and read those tables again.

**Falsifier.** A message, task, or run row exists for that identity. Or the retry after restart creates two tasks or two `run_id`s.

---

## R2 — Crash after the durable obligation, before catch-up or the caller ACK

This is the prior BarryX defect: settlement committed, the secondary obligation not yet durable, and a same-process retry looked green.

**Failure boundary.** Four cuts. Kill only after the first commit is readable from a second connection, and before the second persistence or the HTTP/RPC response is handed to the client.

1. **Attachment commit without the room event.** `commit_message_with_receipt` commits (`state='committed'`, `hold_until_event=True` sets `expires_at`) in its own transaction. `append_event` is a later transaction. `abort_message_commit` runs only if `append_event` raises in the same process. Kill in the gap.
2. **Peer run accepted, home receipt missing.** `PeerRunsHTTPClient._admit_dispatch` (`tui_gateway/hosted_room_peer_http.py`) returns from `POST /v1/runs`, then calls `hosted_rooms.upsert_remote_run_receipt`. The in-memory `self._runs` map is updated after that. Kill the caller after the peer's `run_idempotency` insert and before the home `hosted_room_remote_runs` insert.
3. **Driver row committed, work-record catch-up dropped.** `gateway/hosted_room_driver.py` `_transition` updates `hosted_room_driver_tasks`, then `capture_transition_locked` (`gateway/hosted_room_work_records.py`) writes evidence inside `SAVEPOINT work_transition_capture`. A `WorkRecordError` rolls back only the savepoint. The task row still commits. Force that savepoint rollback, commit, then kill before any later `prepare_delivery_locked` row.
4. **Idempotency reserved, response not delivered.** `RunIdempotencyStore.reserve` commits, then `_handle_runs` builds the 202. Kill before the response bytes reach the client. If `RunIdempotencyStore.durable` is false, the store is `:memory:` and this cut cannot pass.

**User invariant.** Restart shows the admitted work exactly once, still unfinished where the effect was unfinished. The same command retries the same identity. Nothing is reported delivered, stopped, or published because a process-local object said so.

**Real entry point.** The four functions named above, reached through `groups.send` / `groups.attachment.upload` and `POST /v1/runs`.

**Observable.**

| Cut | Must be present after restart | Must be absent or unchanged |
|---|---|---|
| 1 | One attachment row, original `event_id`, original `sha256` | No `hosted_room_events` row until a retry of that same `event_id` appends it once. A different `event_id` for that `attachment_id` leaves the row on the first id (`AttachmentConflictError`) |
| 2 | Peer's one `run_idempotency.run_id` | Home `hosted_room_remote_runs` empty for that `task_id`+`execution_generation` until recovery binds **that** `run_id`. A second `run_id` fails the row |
| 3 | `hosted_room_driver_tasks` row, same `payload_digest` | Work-record table either has no revision or `availability='unavailable'`. The task is still listed. It is not a successful turn |
| 4 | Same `run_idempotency.run_id` and fingerprint | A second reserve with the same key and a different fingerprint is `conflict` and writes nothing |

**Falsifier.** Any cut where a same-process object (`self._runs`, the RPC return value, `localStorage` key `hermes.desktop.canonicalGroupSends.v1`) is the only proof. Any second `run_id`, second `task_id` for the same `thread_id`+`turn_id`, or a room event whose payload bytes differ from the pre-crash payload.

---

## R3 — Lost ACK, then explicit Retry after cold hydration

**Failure boundary.** `prepareCanonicalGroupSend` (`apps/desktop/src/plugins/hermes-bots/canonical-group-send.ts`) persists the intent, `groups.send` commits, and the client never observes the ACK. `retireCanonicalGroupSend` therefore leaves the journal in place. Kill the renderer. Start a new client against the same profile home with empty renderer memory.

**User invariant.** The user retries that same command. One task, the original payload, the original artifact bytes. A different payload is refused and stored nowhere.

**Real entry point.** Journal file from `preparedJournal` (`apps/desktop/electron/prepared-submissions.ts`), key `canonical-group-send-v1` + connection + profile + room. Retry calls `groups.send` with the journal `event_id`. `hosted_rooms.append_event` returns the original row when content matches and raises `EventConflictError` when it does not. Uncertain driver work is `groups.retry`. On this base, `session_group_controls._execution_control` requires the client `execution_generation` to be an int `>= 1`, then calls `retry_room_task(**params)` including `member_id` and `execution_generation`. `HostedRoomService.retry_room_task(room_id, *, task_id)` does not take those keywords. `HostedRoomRuntime.retry_indeterminate` then fences on the **loaded row** (`_fences`), not on the client value. The TUI handler `tui_gateway/methods_groups.py` `groups.retry` passes only `task_id`. The frozen Desktop path must compare the client generation to the stored row before requeue, and that compare has to be the path the fresh client actually calls.

**Observable.** After cold start, the journal file still contains that `event_id` and payload. After explicit Retry: exactly one `hosted_room_events` row for that `event_id`; exactly one `hosted_room_driver_tasks` row for that `thread_id`+`turn_id`; `payload_digest` matches the journal payload; attachment `sha256` is unchanged. A second Retry of the same send does not insert another task and does not change `payload_digest`. A `groups.retry` whose client `execution_generation` is older than `hosted_room_driver_tasks.execution_generation` for that `task_id` leaves `status` and `execution_generation` unchanged. A Retry with a different payload leaves the event table unchanged.

**Falsifier.** Two tasks, a substituted `sha256`, a journal that lived only in `localStorage`, or a Retry that requeues whatever row currently has that `task_id` while ignoring the client generation.

---

## R4 — Stale async reply after room, profile, owner, or generation change

**Failure boundary.** Hold `groups.attachment.download` and the driver completion callback after the server has the result and before the client applies it. While held, commit one of: a different `roomId`, a different profile, a different `authority_epoch` / owner, or a newer `execution_generation` on that task. Then release the held reply.

**User invariant.** The reply is discarded. The new room shows none of that work. No destination file is written for the new selection. The old room's bytes stay the old bytes.

**Real entry point.** Client: `CanonicalGroupAttachments` aborts its `AbortController` when `connectionId`, `profile`, or `roomId` changes (`apps/desktop/src/plugins/hermes-bots/canonical-group-attachments.tsx`), and `downloadCanonicalAttachment` returns when the signal is already aborted. Server: `session_hosted_attachments.download` → `HostedRoomAttachmentStore.read` requires `room_id`, `attachment_id`, and `event_id`. Execute-time fence: `CanonicalHostedRoomService._resolve_member_transport` `authorize` and `check_admission` require the current roster, `authority_epoch`, and `execution_generation`. Peer completion is `HostedRoomOwnerRPC.callbacks` keyed by `admission_id`.

**Observable.** New room: `hosted_room_events` max `seq` and `members_json` unchanged by the released reply; no new `hosted_room_driver_tasks` row with the old `task_id`. Destination path for the post-switch selection: absent, or byte-identical to the sentinel planted before the release. Old blob file: same SHA-256. `hosted_room_policy_cursors.stopped_through_seq` and the task's `execution_generation` change only from the switch itself, not from the stale callback.

**Falsifier.** A blob write, a destination write, or a `message.member` / `message.user` row in the new room caused by the released reply.

---

## R5 — Valid credentials for the wrong profile

**Failure boundary.** Present a credential that is valid for profile B (API key, room grant, or Desktop session actor) on profile A's gateway socket, aimed at A's room.

**User invariant.** A refuses. B's store is unchanged by A's process. A's room membership, tasks, and blobs are unchanged.

**Real entry point.** `dispatch_group_control` raises `profile_mismatch` when `actor.profile_id != authority.profile_id`, when the DB parent is not the authority home, or when `profile_matches_home` fails (`gateway/session_group_controls.py`), and it does so before `_group`. `POST /v1/runs` `_normalize_room_dispatch` rejects `target_profile != active_profile` or `target_install_id != local_authority_gateway_id()`.

**Observable.** Zero new rows in A's `state.db` and A's `shared-state.db` for that attempt (`hosted_rooms`, `hosted_room_events`, `hosted_room_driver_tasks`, `hosted_room_remote_runs`, `run_idempotency`). B's files have the same hashes as before the attempt. The two processes' `default_db_path()` values differ. If they do not differ, the topology is one gateway and the first journey fails here.

**Falsifier.** Any inserted row on A, any byte change on B, or a shared `shared-state.db` counted as two gateways.

---

## R6 — Revocation between preparation and effect

**Failure boundary.** A grant or membership passes signature / roster preparation. A barrier then commits revocation. The effect runs after that commit.

**User invariant.** The effect does not admit, does not write a destination, and does not mark an uncertain in-flight attempt as succeeded.

**Real entry point.** `api_server_room_grants._decode_request_grant` checks the signature only. `_room_grant_claims` then calls `hosted_rooms.room_grant_is_revoked` and `peer_room_grant_is_current` on `default_db_path()`. Revocation writer: `revoke_room_grant_scope` (row in `hosted_room_revoked_grants`, `revoked_at` on `hosted_room_peer_reservations`). Local execute fence: `authorize` inside `_resolve_member_transport` and `check_remote_hosted_admission` (`gateway/session_hosted_transport.py`), which re-read the owner and the payload digest.

**Observable.** After the barrier, the effect leaves no new `run_idempotency` row, no new `hosted_room_driver_tasks` row, and no new `hosted_room_remote_runs` row. `hosted_room_revoked_grants.revoked_before` is at or after the grant's `issued_at`. An attempt that had already passed the fence stays `running` or moves to `indeterminate` / `stopping`. It does not become `settled` with a new `settlement_id`.

**Falsifier.** A new task or run row, a `settled` status written by the post-revocation effect, or a revocation row read from a different file than the one the effect opened (that setup is a failed injection, and the row stays open).

---

## R7 — Same displayed name, two versions, late selection

**Failure boundary.** Admit two files with the same `name` and different bytes, on one room, through the real client. Reconnect. Separately, hold the download response for the earlier `attachment_id`, change the client selection to the later `attachment_id`, then release the earlier response.

**User invariant.** The user can retrieve both versions. The earlier version is still the earlier bytes after a fresh client opens. A late reply for the earlier id does not replace the later selection.

**Real entry point.** `groups.attachment.upload` → `HostedRoomAttachmentStore.put` (identity is `upload_id` / `attachment_id`, not `name`). `groups.attachment.download` → `download` → `read(room_id, attachment_id, event_id)`. Client selection is `CanonicalGroupAttachments.download`, which sends `attachment_id` and `event_id`.

**Observable.** Two `hosted_room_attachments` rows, same `name`, distinct `attachment_id`, distinct `sha256`, distinct files under `hosted-room-attachments/blobs/`. After a new client, `groups.log` still lists both, and download of the earlier `attachment_id` returns the earlier SHA-256. The late reply does not change the later blob, the later row, or the destination bytes chosen for the later id.

**Falsifier.** One row keyed only by `name`, the earlier SHA-256 gone after reconnect, or the destination hash equal to the earlier blob after the user selected the later id.

---

## R8 — Partial write, cancel, pre-existing destination

**Failure boundary.** Three cuts on the byte path.

1. Kill during `HostedRoomAttachmentStore._write_blob` after the `.tmp-*` file has bytes and before `os.replace` onto `blob_<id>`.
2. User cancels the save dialog in `finalizeGatewayDownload` (`apps/desktop/electron/gateway-file-download.ts`) after a sentinel file already exists at the chosen destination.
3. Kill `pumpStreamToFile` / `writeBufferToFile` after the `.hermes-download-*.part` temp exists and before `rename` onto `destPath`. The pump opens that temp with `flags: 'wx'`.

**User invariant.** A cancel or a torn write leaves the sentinel bytes in place and creates no successful destination. A partial blob is not downloadable as the attachment.

**Real entry point.** `_write_blob`; `saveGatewayFile` in `apps/desktop/electron/main.ts` → `saveGatewayDownload` → `finalizeGatewayDownload`. Native Windows Save/Cancel destination-byte proof is HELD / unexecuted (commit-headroom). It is not green unless separately admitted and this oracle is run on that host. The Electron path on the foundation remains the falsifier for the shipped Electron save. Claiming Windows proof without the destination-byte oracle fails the row as unexecuted. A missing guard inside `gateway/**` stays assigned to Barry.

**Observable.** Sentinel file bytes and size unchanged. No new file at `destPath` on cancel (`saved: false` is insufficient; the directory listing is the oracle). No `hosted_room_attachments` row in `committed` for the killed `upload_id`. `read` of a missing blob raises `AttachmentIntegrityError` and returns no bytes. A leftover `.tmp-*` or `.part` file is not served. An exclusive-create collision (`EEXIST`) leaves the foreign file at that temp path in place.

**Falsifier.** Truncated sentinel, a destination file whose bytes are a prefix of the body, or a download that returns those prefix bytes with a success result.

---

## R9 — Stop generations

**Status.** OPEN. Permission #111939 baseline `34332b47…` covers Read and Send, not Stop, approve, or deny. Messaging #98073 `d4d9f905…` is the published list→Send→ACK→detail proof. Independent control consent is PENDING, with no immutable successor. A pass requires the mutation itself to fail closed while that successor is unpublished. A client that hides Stop, approve, or deny, or that synthesizes consent, fails the row. If `begin_task_cancel`, `complete_task_cancel`, or the approve path lacks that fence, the finding stays assigned to Barry. Route pin `8a29b6d…` is the delegated-stop supplier, not a cb8 pass.

**Failure boundary.** Two cuts.

1. A task is `running` at `execution_generation=N`. `groups.stop` commits. A newer `groups.send` then admits work at a higher event `seq`. Hold the old task's peer stop ACK until after the new task is `running`.
2. Kill after `begin_task_cancel` and before `complete_task_cancel` (ambiguous). Separately, deliver the matching stop ACK (confirmed).

**User invariant.** The old Stop stops the old attempt only. The new turn keeps running. "Stopping" stays visible as unfinished until the matching acknowledgement. A confirmed stop is the `cancelled` row.

**Real entry point.** `groups.stop` → `HostedRoomService.stop_room` → `hosted_rooms.request_room_stop` (event kind `room.stop_requested`, id derived from `cancel_id`) → `HostedRoomRuntime.cancel`. Queued work uses `cancel_task`. Running work uses `begin_task_cancel` (`status='stopping'`, `cancel_generation+1`) and only then `complete_task_cancel` when `status`, `cancel_id`, and `cancel_generation` match. `HostedRoomPolicyCheckpoint._apply_stop_requested` sets `hosted_room_policy_cursors.stopped_through_seq` to that event `seq`. `prepare_room` cancels a task whose `source_event_seq` is below that fence. Peer stop of one attempt is `PeerRunsHTTPClient.stop_receipt(task_id, execution_generation)`.

**Observable.** Old row: `stopping` until the ACK, then `cancelled` with that `cancel_id`. New row: its own `task_id`, `status='running'`, `execution_generation` independent of the old row's `cancel_generation`. `room.stop_requested` seq is less than the new message seq. The ambiguous cut, after restart, is still `stopping` (or `indeterminate` if the run fence was lost). It is not `cancelled` and not `settled`. `groups.stop`'s returned count is not the oracle. `groups.disband` with `require_acknowledged=True` leaves the room row live while any task is still `stopping`.

**Falsifier.** The new task is `cancelled` by the old `cancel_id`, the ambiguous row is `cancelled` or `settled`, or a stop ACK for generation N completes generation N+1.

---

## R10 — Gateway absence, return, unknown work, stale copies

**Status.** The Desktop-close and member-absence half is part of the first journey and stays OPEN until executed on the frozen cb8 candidate. The promote, replica, and host-loss half stays OPEN. Recovery #105079 `c63f9382…` is a scoped increment, not takeover approval and not exclusive-successor completion. Connect history publication does not close this row. Do not copy local-only stop-ack `8461351…` into the consumer or describe that journey as repeatably green. A finding that needs `gateway/**`, `tui_gateway/**`, or `hermes_state*.py` stays assigned to Barry.

**Failure boundary.** After a task is admitted, kill the member gateway or drop its socket. Leave the home gateway up. Close the disposable Desktop. Later, start the member gateway again from the same home, and also offer a stale replica page to the home.

**User invariant.** Closing Desktop leaves the admitted task on the home gateway. The member's absence leaves that attempt unknown, not successful and not replayed. When the member returns, it is the same member. A replica does not become the room and does not replace the bot.

**Real entry point.** Room work runs in `HostedRoomRuntime` inside the gateway process (`tui_gateway/hosted_room_service.py` `start_hosted_room_service`), independent of the renderer. Electron `before-quit` `backendShutdown.run()` (`apps/desktop/electron/main.ts`) stops the app-attached serve child only. `recover_room` sets foreign `running` rows to `indeterminate` and does not requeue. `groups.replicate` → `ingest_page` writes `hosted_room_replica_events` only. `groups.promote` → `promote_replica` requires `confirm: true` and inserts a new `hosted_rooms` authority at `epoch+1` plus `authority.claimed`. `groups.list` reads `hosted_rooms`, not the replica tables.

**Observable.** After Desktop quits: the independent gateway pid is alive; the task row is still `queued` / `running` / `stopping` / `indeterminate`; `members_json` is byte-identical; event `seq` is unchanged by the quit. After member loss: that task is `indeterminate` or still `running` under its own lease, with the same `payload_digest`, and no second member row exists. After return: the same `member_id` and `profile` are in `members_json`; `authority_gateway_id` is unchanged. The replica's `last_seq` may advance. `hosted_rooms.authority_epoch` does not. A fresh client `groups.state` / `groups.log` returns that same seq, the same members, and the same attachment SHA-256 as before the close.

**Falsifier.** The room gateway pid died with Desktop, a new bot member appeared, the task was requeued or marked `settled` because the member was gone, or `promote_replica` / an `authority.claimed` row ran from the stale page without the previous owner being fenced.

---

## R11 — Missing or replaced stores, stale caller state

**Failure boundary.** With a stale Desktop journal and a stale socket still holding the old room id: remove the profile `state.db`, replace it with an empty file, or replace `shared-state.db` with the other gateway's file. Also open `runs_idempotency.db` as an unwritable path so `RunIdempotencyStore` would fall through to `:memory:`.

**User invariant.** The gateway does not serve that profile and does not mint authority for the stale caller. The other gateway's file is unchanged. A retry is refused rather than admitted into a memory-only idempotency map.

**Real entry point.** `initialize_gateway_runtime` / `_park_reserved_profile` (`gateway/run_runtime.py`): the launch profile's store failure aborts boot; a secondary is recorded under `parked_profiles` and omitted from `served_profiles`. `dispatch_group_control` refuses a DB whose parent is not the authority home. `authorize_room` inserts a `state_meta` owner only when `create=True` and no historical room exists; a missing owner is `permission_denied`. `probe_hosted_room` on a missing file returns false and creates no room; a SQLite error raises `RoomProbeUnavailableError`. A missing blob raises `AttachmentIntegrityError`. `RunIdempotencyStore.__init__` logs and sets `durable` false when the file cannot be opened.

**Observable.** `gateway_state.json` lists the broken secondary under `parked_profiles` and not under `served_profiles`. No new `hosted_rooms` row and no new `state_meta` owner for the stale room id. The foreign file's hash is unchanged. `RunIdempotencyStore.durable` is true for any run the candidate claims will survive restart; a memory fallback that still returns 202 fails the row. `groups.list` on the empty replacement is empty: the previous `room_id` is not reconstructed with the old `members_json`.

**Falsifier.** The stale journal's `groups.send` inserts a room, a task, or an owner row; the other gateway's file changes; or a restart of a memory-backed idempotency store admits a second `run_id` for the same key.

---

## R12 — Composition drops a guard; a later sibling is not a superset

**Failure boundary.** Freeze one candidate revision of assembly target `cb8d692…` plus the declared supplier deltas. Re-run R1–R11 on that revision. A result recorded on `6f2aeb…` does not transfer. Compare the composed behavior with each controller pin the candidate claims, and keep the stricter oracle:

- Route `8a29b6d13bc558221130b4216a93f373afa68caf`, not assignment snapshot `1fa3c0…`.
- Output recipe `23c5e66d0da21b46cc373993cd39dc2d7ab3929e` and implementation `31d00b0ed728e8d580aadf8a143cda3343752441`. The older plan tip `4be9cb11…` is not the recipe pin.
- Retention F1 `c9f0029475f085e3b5e66b77df74cd5470925aef` carried explicitly against Output-recipe pin `004015d6087fe031231c4d7d9e0032cc59b679eb`. The historical recipe pass does not prove the later F1 union.
- Desktop `5211890fb87b626c9ba569910375707530c3a2cc` and Desktop Files `3537e93cf89013e234e7980e7cf83fcd44319885`.

A later sibling is not a superset. Commit dates and PR titles are not evidence. Backend guard loss inside `gateway/**`, `tui_gateway/**`, `hermes_state*.py`, or `agent/**` is a finding for Barry, not a consumer patch.

**User invariant.** Every guard that was true on an input tip the candidate claims to include is still true on the frozen tree. A newer tip that lost a guard does not erase the older tip's obligation.

**Real entry point.** The registered methods in R1–R11, plus the guard list in `artifacts/SIBLING_GUARDS.md`. On this base, `SessionDB` has `_read_ctx`, `_execute_write`, and `_write_sql`. It does not have `live_read_connection` or `live_write_connection`. `_execute_write` retries the whole callback when `BEGIN IMMEDIATE` is busy, and the callback must stay idempotent under that retry. Diagnosis of public Files/Output says those owners need a non-replaying owner-fenced transaction. A composed path that performs settlement inside `_execute_write` or `_write` fails this row. Leaving that handoff blocked, and not claiming the file journey, keeps the row open rather than passed.

**Observable.** The same table and byte oracles as R1–R11 on the frozen SHA. For each dropped guard, the pre-composition tree shows the failing counterexample and the frozen tree shows the same counterexample still failing, or the guard's oracle passing. An import error or a collection error is not that counterexample.

**Falsifier.** The candidate reports a journey passed while any R1–R11 oracle fails on that SHA, or while a merged tip's stricter oracle is absent and the loss is explained by the tip being older.

---

## R13 — Teardown touches only task-owned resources

**Failure boundary.** Before teardown, plant a sentinel process that is not the task gateway, a sentinel file at a destination the task must not own, and a second profile gateway pid. Run client shutdown (`before-quit`) and the test's cleanup.

**User invariant.** The sentinel process, the sentinel file, and the other profile gateway are still there. Only the task's own pid, temp files, and journal entries are gone.

**Real entry point.** `HostedRoomRuntime.stop` joins the `hosted-room-driver-supervisor` thread and that runtime's room threads, with the caller-supplied timeout, and does not interrupt an accepted turn. `pumpStreamToFile` `discardTemp` unlinks the temp only after the write stream emits `open`. `awaitClosed` in `gateway-file-download.ts` is armed by the stream `close` event; its grace timer is not the race oracle. Electron `before-quit` waits on the attached serve child via `backendShutdown.run()`.

**Observable.** Sentinel pid still alive. Sentinel file bytes unchanged. Other profile `state.db` hash unchanged and its gateway pid alive. The task gateway pid is the only process the harness signaled, and that pid was recorded at spawn. No protocol-handler registry key was written. `HostedRoomRuntime.stop` returned because its own threads ended or the deadline fired, and the accepted task row is still non-terminal if the stop was a client shutdown rather than `groups.stop`.

**Falsifier.** The sentinel pid is dead, the sentinel file changed, a helper blocked with no deadline, or a kill matched a process by a name substring (`hermes`, `gateway`, `electron`) rather than the recorded pid.

---

## First vertical journey vs later coverage

The first proving journey is: two independently identified gateways, a hosted group, file work admitted through the real client, Desktop closed, gateway work continues, a fresh client recovers the same ordered events, the same members, and the exact file versions.

**Required before that journey can be called shown**

- R1 and R2 on the admitted file turn (restart cuts, not a same-process retry).
- R3 for that turn's lost ACK and explicit Retry after a fresh client.
- R4 for the fresh client's own binding (a late download from the closed client must not write).
- R5 so the two gateways are actually two stores and two credentials.
- R7 so "exact file versions" includes two same-name blobs and the earlier SHA-256 after the fresh client opens.
- R10's Desktop-close and member-absence half (task row and `members_json` survive; no replacement bot).
- R13 so closing that Desktop does not kill the room gateway or the other profile.
- R12 as a precondition: the frozen SHA has already been named, and these oracles were run on it.

**Later A1–A7, not implied by the first journey**

| Rows | Group | Why it stays later |
|---|---|---|
| R6 | A1 foundations, A3 messaging | Revocation at the effect boundary is a permission journey, not the reopen journey. Stop, approve, and deny effects stay PENDING with R9: fail closed at the mutation |
| R8 | A5 Desktop/Files | Cancel, partial write, and sentinel destinations. Native Save/Cancel destination bytes are HELD / unexecuted |
| R9 | A3 messaging, A7 recovery | OPEN. Control consent is PENDING. Stop generation fences and ambiguous vs confirmed stop. The first journey does not Stop. A client workaround is not a pass |
| R10 promote / replica half | A2 peer, A7 recovery | OPEN. Passive copy must not become authority. Host-loss is incomplete. Recovery `c63f9382…` does not approve takeover |
| R11 | A1, A7 | Missing and replaced stores. The first journey uses live disposable stores |
| R4 cross-room / foreign-owner half | A4 session, A6 connect | OPEN. Same-room reopen is in the first journey. Connect #109338 `acf21665…` is history-held, not a reviewed replacement |
| R12 again | Each group as it is claimed | Re-run the oracles for the guards that group owns, on the frozen cb8 revision. A pass of the first journey, or a 6f2 supplier proof, does not retire R12 |

Shipped Group Chat upgrade, native Windows Save/Cancel destination bytes, A3 control consent, A6 Connect history publication (#109338), and A7 host-loss / exclusive-successor fencing stay outside a shown first journey. They remain OPEN. They are not passed by omission, by a supplier proof on `6f2aeb…`, or by a client workaround. Findings that need `gateway/**`, `tui_gateway/**`, `hermes_state*.py`, or `agent/**` stay assigned to Barry.
