# Adversarial review — full Runtime series `d7f5c13..879b3a21`

Review range: `d7f5c13d73784b4e536bf6fe0d20a48089523ec0` .. `879b3a2146eaf0f97443a7d03491756d4af11db7`.
Tip tree: `d36c5415f4c7ce55d300e0a85bd6e48297147443`. Parent: `8548f70e5c642636f33047afd94cdb5e965963db`.
Prior four-path CLEAN on the truncated replay stands for that slice only. This document is the full-delta pass.

## Reconstruction

Product history on this branch is the four Tower commits, David Dudok de Wit author and committer, exact OIDs:

| OID | Tree | Subject |
|---|---|---|
| `e79420fc8fc287805a630660d99ce7ea0a47e3a4` | `a55710f40ee79ad6b9a8f25ceb8ee250f6f99547` | Add owner-scoped inert shipped group importer to canonical Runtime |
| `00c143412d264f0001037643db7f43b19233f970` | `27b724d646bb090860957739861b5eb807a3d960` | Preserve terminal publication and imported source reservations |
| `8548f70e5c642636f33047afd94cdb5e965963db` | `ed06d20f378c0c510a861e9e04b601ce9d32a610` | Backfill imported source reservations after retention copy |
| `879b3a2146eaf0f97443a7d03491756d4af11db7` | `d36c5415f4c7ce55d300e0a85bd6e48297147443` | compose admitted adoption viewer and Stop providers |

`git diff --name-only d7f5c13..879b3a21` is the 18 manifest paths and no others. Supplier blobs match `CURRENT_RUNTIME_UNION_MANIFEST.json`:

- `gateway/hosted_room_viewer_state.py` `3a4c44e9cfc0eaad42a40eec913a6f18dd0343cb`
- `tests/gateway/test_hosted_room_viewer_state.py` `15dd54354a588eccfb7c71896e96586eb593ed91`
- `gateway/session_hosted_rpc.py` `0576a7d59b602e713737624aaeed42d1c1722928`
- `tests/gateway/test_session_hosted_rpc.py` `d1b00a3988dbd7492495273da569668efc34b68b`

The other 14 paths are the reconstructed importer series. They were not invented. Files, Route, and Retention implementations are not in the tree. `gateway/hosted_room_safety.py` is absent.

## Hostile pass

Checked against the full delta, not the four-path slice:

- Import authz. `groups.import_history` requires `session:control` and does not pre-authorize a room that may not exist. `CanonicalHostedRoomService.import_shipped_group_history` passes `authorize_write` into the held writer transaction (`authorize_room(..., create=True, conn=conn)`). Legacy `tui_gateway/methods_groups.py` returns `4124 canonical_owner_required` for ownerless import and member resolve. The TUI `HostedRoomService.import_shipped_group_history` raises before any store write.
- Retention gate. `import_shipped_group_history` calls `find_spec('gateway.hosted_room_safety')` and raises `HostedRoomError` before `_transaction`. A missing provider does not write. Attachment import is a second gate (`put_import` / `commit_import_message` / `recover_import_rollback`) and is not vendored here.
- Source reservations. Conflict on a mismatched marker or an existing reservation raises `RoomConflictError`. `_backfill_history_source_reservations` is `INSERT OR IGNORE` from `hosted_room_history_imports`, at schema init and again after `import_legacy_rooms`. It does not overwrite a different claim.
- Terminal publication. `publication_members` is an optional frozen roster used to publish an already-admitted task. `reconstruct_task_plan` compares the payload with that key excluded, then returns the original payload. New admission still goes through `_policy_room` and `require_member_work_open`.
- Stop ack. Started admissions return `{'interrupted': False, 'status': 'running'}`. Driver `_STOP_ACK_STATUSES` is `{cancelled, interrupted}`. `_dispatch_owned` still requires `authorizer(...) is True`.
- Viewer. `mode=ro`, `PRAGMA query_only`, no schema mint, install id from `_read_existing` (not `read_or_create`), ownership `install:{id}` via `get_default_hermes_root()`. Present quarantine or disband-fence rows, and non-table fence schema, deny. No production caller of this module was added; none is invented here.
- Contract. `gateway-contract.generated.ts` and the OpenRPC document add import and member-resolve types only.
- Import while the driver is stopping. `import_service` is kept when the execution `service` is cleared. The owner bind and the Retention gate still run. This is not an unauthenticated door.
- `HostedRoomError` without `.reason` maps to `RuntimeStoreError('invalid_params')` in `session_group_controls`. The import still does not write. Remapping that error is out of scope and would not make the happy path succeed.

No in-scope product defect. No guard was removed. No product fix commit follows `879b3a21`.

## Tests

Canonical runner `scripts/run_tests.sh`, five files, no shared `--basetemp`. `HOME=/tmp/runtime-full-review/home` for the run only. The runner drops `HERMES_HOME` (conftest sets it per test) and replaces `TMPDIR`.

| File | Result |
|---|---|
| `tests/gateway/test_hosted_room_viewer_state.py` | 12 passed, 1.19s |
| `tests/gateway/test_session_hosted_rpc.py` | 7 passed, 4.71s |
| `tests/gateway/test_shipped_group_history_import.py` | 7 failed, 0.58s |
| `tests/gateway/test_imported_member_retirement.py` | 1 failed, 0.81s |
| `tests/tui_gateway/test_group_history_import.py` | 2 passed, 2 failed, 1.83s |

Runner summary: 21 passed, 10 failed, wall 4.7s. Every importer failure is `HostedRoomError: shipped history import requires the Retention safety provider` at `gateway/hosted_rooms.py:1452`, or the same error mapped to JSON-RPC `invalid_params` (`test_group_history_import.py` lines 172 and 213). The two passing TUI tests are the ownerless-refusal and session-control checks. These 10 failures are not a harness fault and are not fixed by stubbing `find_spec`.

## HELD

1. **Retention safety provider.** Unlock: a separate Retention-owner draft that supplies `gateway.hosted_room_safety` on the import path, then re-run the 10 importer tests. Do not vendor that module into this PR. Do not stub the gate.
2. **Files import provider.** Required only when history carries attachments (`HostedRoomAttachmentStore`). Separate owner.
3. **Route digest.** `hosted_room_member_retirement` refuses with `peer_setup_unavailable` when a link exists and `links.route_security_digest` is not callable. The retirement test never reaches that assertion until Retention lets the import complete. Route stays a separate owner.
4. **Viewer has no production caller** in this 18-path delta. Unlock is a later wiring change with its own review, not a caller invented on this branch.
5. **NousResearch #106742 publication**, native/device surfaces, remote retirement network actions, history rewrite, queued-CAS / full Stop beyond the canonical ack, and programme completion. Maintainer or later-owner work. This PR does not push, comment, fast-forward, or merge that PR, and it does not merge to fork `main`.

## Re-review

Re-review count: **1**.

The confirmatory pass re-read the authz, reservation, publication, Stop, and viewer paths and re-ran the five test files. It found no new in-scope defect. No fix commit. Open findings: none. Status: **HELD**.
