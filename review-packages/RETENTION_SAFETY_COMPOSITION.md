# Composition receipt — Retention `hosted_room_safety` beside Runtime `879b3a21`

This commit does not change product bytes. Runtime tip `879b3a2146eaf0f97443a7d03491756d4af11db7` (tree `d36c5415f4c7ce55d300e0a85bd6e48297147443`) is the parent. Retention tip `004015d6087fe031231c4d7d9e0032cc59b679eb` (tree `84503b7809bc650e9454d3622393f7204f377dbb`, branch `fix/bot-mode-passive-replicas-20260831`) already contains `gateway/hosted_room_safety.py`. That file is not copied into this commit. It is not applied to draft PR #8 or to `cursor/runtime-complete-owner-draft-1697`.

The importer proof below was run on a throwaway overlay of that parent. Checking out this commit alone does not make `gateway.hosted_room_safety` importable.

## Pins

| Owner | OID | Tree | Role |
|---|---|---|---|
| Runtime product | `879b3a2146eaf0f97443a7d03491756d4af11db7` | `d36c5415f4c7ce55d300e0a85bd6e48297147443` | parent of this receipt |
| Runtime public base | `d7f5c13d73784b4e536bf6fe0d20a48089523ec0` | — | ancestor; not rebased |
| Retention tip | `004015d6087fe031231c4d7d9e0032cc59b679eb` | `84503b7809bc650e9454d3622393f7204f377dbb` | source of the safety blob |
| Safety introduction | `82bf61fdec2b2d25737eed57cfb16c310eabca8b` | `c1bcb15183d8c902efb7ad790e3b04fd24685932` | same safety blob as the tip |
| Safety blob | `7bfb1bf04b59c52e27603370ef278ae85419ab22` | — | `gateway/hosted_room_safety.py` at both Retention commits; matches the attached export (`git hash-object`) |
| Runtime replicas blob | `505404179ccc1ff50182b9a1707f35ef7bfb9a50` | — | unchanged from merge-base `eb9d6887dec958c9042bb79fd65ecbf868263229` |
| Retention replicas blob | `e59558eb2cc1a8ab32b0e2e2fd07a7b9ec9fc5d6` | — | not overlaid; see below |
| `hosted_rooms_common.py` | `5bd58e5c3f24651fe30b842c546aac8104a31796` | — | identical on both tips |

Findings commit `ff18610351f4cd0fc9a69bd3a037bd8516f086f6` is docs on top of the Runtime tip. This composition does not use it.

## Overlay that was tested

1. Detach at `879b3a21`.
2. `git checkout 004015d6087fe031231c4d7d9e0032cc59b679eb -- gateway/hosted_room_safety.py`
   Confirmed `git hash-object` is `7bfb1bf04b59c52e27603370ef278ae85419ab22`.
3. `git apply review-packages/replicas-audit-splice.patch`
   Confirmed `git apply --check` after product paths were restored to `879b3a21` (safety file absent, replicas blob `505404179c`). Patch SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90`.

`hosted_room_safety.py` imports `table_columns` and `table_exists` from `gateway.hosted_rooms_common` (same blob on both tips). During schema init it also imports `_audit_existing_replicas_locked` from `gateway.hosted_room_replicas`. That name is absent on the Runtime replicas blob.

The Retention replicas blob is not a drop-in. Commit `7872c951bfe2d84fee6f0f76796828ca240d40b4` rewrote it and removed `promote_replica` and `demote_room`. The Retention tip still has neither. Runtime `tui_gateway/methods_groups.py` still calls both. Replacing the file would delete that Runtime surface. The splice does not.

The spliced function body is the exact text of `_audit_existing_replicas_locked` in Retention blob `e59558eb` (4014 bytes, identical to the same function in `7872c951`). The patch also binds names that function already calls and that already exist on Runtime `gateway/hosted_rooms.py`: `math`, `time`, `MAX_EVENT_ID_CHARS`, `_validate_actor`, `_validate_event_kind`. No other statement in `hosted_room_replicas.py` changes. `promote_replica` and `demote_room` stay.

`_prune_disbanded_replicas_locked` in the safety module reads `DISBANDED_REPLICA_RETENTION_SECONDS` from `gateway.hosted_rooms`. That name exists on the Retention tip and not on the Runtime tip. The importer path does not call that prune. It was not spliced in.

## Tests

Canonical `scripts/run_tests.sh` (per-file subprocesses, `TZ=UTC`, `LANG=C.UTF-8`, `PYTHONHASHSEED=0`). `HOME=/tmp/retention-compose/home`, `TMPDIR=/tmp/retention-compose/tmp`. The runner clears `HERMES_HOME`; conftest sets it per test. No shared `--basetemp`.

Command:

```text
scripts/run_tests.sh \
  tests/gateway/test_shipped_group_history_import.py \
  tests/gateway/test_imported_member_retirement.py \
  tests/tui_gateway/test_group_history_import.py \
  tests/gateway/test_hosted_room_viewer_state.py \
  tests/gateway/test_session_hosted_rpc.py \
  -q --tb=line
```

These are the five files named in draft PR #8's RESULT. The first three are the importer files whose happy paths failed there.

| File | #8 on Runtime alone | This overlay |
|---|---|---|
| `tests/gateway/test_hosted_room_viewer_state.py` | 12 passed | 12 passed (runner file 1.25s) |
| `tests/gateway/test_session_hosted_rpc.py` | 7 passed | 7 passed (runner file 3.40s) |
| `tests/gateway/test_shipped_group_history_import.py` | 7 failed | 7 passed (runner file 1.86s) |
| `tests/gateway/test_imported_member_retirement.py` | 1 failed | 1 passed (runner file 1.39s) |
| `tests/tui_gateway/test_group_history_import.py` | 2 passed, 2 failed | 4 passed (runner file 1.83s) |

Runner summary: 5 files, 31 passed, 0 failed, wall 3.4s. The 10 paths that raised `HostedRoomError: shipped history import requires the Retention safety provider` (or that error mapped to JSON-RPC `invalid_params`) completed. The retirement test reached `RuntimeStoreError` / `peer_setup_unavailable` and asserted the member row was unchanged.

An extra file, not in the #8 set, was run because the splice touches replicas:

```text
scripts/run_tests.sh tests/gateway/test_hosted_room_replicas.py -q --tb=line
```

8 passed, 3 failed, file 2.54s. Failures:

- `test_promote_replica_continues_room_at_next_epoch`
- `test_promote_refuses_when_room_exists_locally`
- `test_full_failover_round_trip`

Each is `sqlite3.IntegrityError: room_id is already reserved` at the Runtime replica `INSERT` (`hosted_room_replicas.py` lines 137 and 230 on the overlay). That string is the `RAISE` in `trg_hosted_rooms_reject_reserved_insert` and `trg_hosted_replicas_reject_reserved_insert`, installed by `initialize_safety_schema` once `gateway.hosted_room_safety` imports. The triggers were not removed.

## Hostile pass

- Authz and the pre-write Retention gate are unchanged. The overlay supplies the module `find_spec` looks for. Import still runs inside the held writer transaction. The retirement test still refuses when `links.route_security_digest` is missing, and it still compares the member row before and after.
- The safety file was not edited. Its blob matches the Retention tip and the Tower blob named in the kickoff.
- The audit function was not rewritten. The whole Retention replicas file was not substituted, because that file deletes `promote_replica` and `demote_room`.
- No guard was stubbed. `find_spec` was not patched. Attachment import still requires `put_import`, `commit_import_message`, and `recover_import_rollback`. These fixtures carry no attachments, so that gate did not run.
- This commit's tree does not contain `gateway/hosted_room_safety.py`. A green importer run is a property of the overlay, not of `git checkout` of this receipt.
- NousResearch #106742 and #99107 were not pushed, commented, fast-forwarded, or merged. Fork `main` was not updated. `cursor/runtime-complete-owner-draft-1697` was not updated.

No in-scope product fix follows `879b3a21`.

## HELD

1. **Runtime promote/demote versus Retention reservation triggers.** With the safety module importable, schema init installs the reservation triggers, and the three Runtime failover tests abort. Unlock: a later owner reconciles `promote_replica` / `demote_room` with those triggers. Do not drop the triggers. Do not replace `gateway/hosted_room_replicas.py` with Retention blob `e59558eb` (that deletes promote and demote, which `tui_gateway/methods_groups.py` still calls).
2. **Files import provider.** Still required when history carries attachments. These fixtures do not. Unlock remains a Files-owned `HostedRoomAttachmentStore` import surface, not a change to this gate.
3. **Route digest for a successful remote retirement.** The missing-provider refusal now runs and passes. A retirement that succeeds still needs `links.route_security_digest`. That provider is not in this overlay.
4. **Publication and programme holds.** NousResearch #106742, native/device surfaces, history rewrite, and merging any fork draft onto `main` stay outside this receipt.

## Re-review

Re-review count: **1**.

The confirmatory pass re-read the overlay recipe, the splice patch, and the test table against the commands that were actually run. It corrected two receipt wordings before this commit: `git apply --check` was run on the restored Runtime tip, and the durations are runner file times. The function-body compare against Retention blob `e59558eb` was identical (4014 bytes). Product paths stay restored (`gateway/hosted_room_safety.py` absent, `_audit_existing_replicas_locked` absent). No further in-scope defect. No product fix. Open findings: none. Status: **HELD** (promote/demote versus reservation triggers; Files and Route remain later owners and were not the 10 importer failures).
