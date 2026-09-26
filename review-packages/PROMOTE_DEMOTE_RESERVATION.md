# Runtime promote/demote beside Retention reservation triggers

Product parent: Runtime `879b3a2146eaf0f97443a7d03491756d4af11db7`.
Retention safety blob used only as a test overlay: `7bfb1bf04b59c52e27603370ef278ae85419ab22` from `004015d6087fe031231c4d7d9e0032cc59b679eb`.
Audit splice: `review-packages/replicas-audit-splice.patch` from draft #9 `7bca11a8869a4664ee19a2ce048e70dae10e3bea` (SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90`). `git apply --check` succeeds on this commit's `gateway/hosted_room_replicas.py`. The splice and `gateway/hosted_room_safety.py` are not part of this commit.

## RED (pre-fix overlay)

`scripts/run_tests.sh tests/gateway/test_hosted_room_replicas.py -q --tb=short` on `879b3a21` plus the safety checkout and the splice.

8 passed, 3 failed. Failures:

- `test_promote_replica_continues_room_at_next_epoch` — `IntegrityError: room_id is already reserved` at the `hosted_rooms` insert (`promote_replica`)
- `test_promote_refuses_when_room_exists_locally` — same error at the new-replica insert (`_store_replica`)
- `test_full_failover_round_trip` — same error at the `hosted_rooms` insert

Triggers were not dropped.

A reservation-only probe (delete `owner_kind='replica'` before the room insert; refuse a new replica when any reservation exists) moved the two promote failures to `IntegrityError: room authority is quarantined` inside `append_event`. That is `trg_hosted_events_quarantine_unsafe_lineage` matching `"promoted_from_replica":true` on the claim, then `trg_hosted_events_reject_quarantined_insert` on the next write. The conflict test became `RoomConflictError: room_id is already reserved` at ingest.

## Fix

`promote_replica` deletes only a replica-owned `hosted_room_id_reservations` row, then inserts the room so `trg_hosted_rooms_reserve_insert` records `authority`. Any other owner is left in place and the insert is refused. A new replica insert is refused when a reservation already exists, instead of colliding with `trg_hosted_replicas_reject_reserved_insert`.

The claim keeps `previous_gateway_id`, `authority_gateway_id`, `authority_epoch`, and `reason`. It does not set `promoted_from_replica`. That fragment is the unsafe-takeover marker the quarantine trigger and the safety backfill both match. `claim_authority` already writes `authority.claimed` without it, and that shape is what the trigger leaves writable. The trigger is unchanged.

`demote_room` still appends `authority.lost`. The same-lineage idempotent return happens before the quarantine check. A later demote that would insert into an already quarantined room raises `RoomQuarantinedError` instead of the trigger abort.

## Not in this commit

Wholesale replacement with Retention replicas blob `e59558eb` (that file deletes `promote_replica` / `demote_room`). Edits to #8 / #9 branches. Publication of NousResearch #106742 or #99107. Merge onto fork `main`.
