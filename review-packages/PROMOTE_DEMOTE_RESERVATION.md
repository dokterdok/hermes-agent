# Runtime promote/demote beside Retention reservation triggers

Product parent: Runtime `879b3a2146eaf0f97443a7d03491756d4af11db7`.
Branch: `cursor/runtime-promote-reservation-9d17`. Draft PR #10.

Retention safety is a test overlay only. Blob `7bfb1bf04b59c52e27603370ef278ae85419ab22` from tip `004015d6087fe031231c4d7d9e0032cc59b679eb`, confirmed with `git hash-object`. It is not in this commit. Retention replicas blob `e59558eb` is not used as a file replacement.

Audit splice: draft #9 `7bca11a8869a4664ee19a2ce048e70dae10e3bea` file `review-packages/replicas-audit-splice.patch`, SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90`. Applied only on the overlay. Not copied into this commit.

## Reservation behavior (kept)

`promote_replica` deletes only a replica-owned `hosted_room_id_reservations` row, then inserts the room so `trg_hosted_rooms_reserve_insert` records `authority`. Any other owner is refused. Replica events are deleted before the authority copy so `trg_hosted_events_shared_budget` does not count them twice. `demote_room` still appends `authority.lost`. Triggers are not edited.

## Fencing (this commit)

Barry's flag is confirmed. `c82b8148` dropped `promoted_from_replica` from the promote claim so `trg_hosted_events_quarantine_unsafe_lineage` would not quarantine, and the tests then required `quarantine is None` plus a successful post-promote append. `groups.promote` admits that call when `confirm` is true. That flag, the new epoch, and the reservation transfer are not proof the previous host stopped. Authority A's database stays writable. This is not host-loss recovery.

The claim again sets `promoted_from_replica: true` (canonical compact JSON). `claim_authority` still omits it. Admission refuses the room when any `authority.claimed` payload has that boolean, including after a later claim that omits it:

- `append_event`, `rename_room`, and `claim_authority`
- `admit_task` and `_require_room_authority` (lease acquire, renew, release, start)
- `HostedRoomService.bindings` via `execution_blocked_room_ids` (parsed JSON, not a substring match)

`promote_replica` returns `executable: false`. When the safety schema is installed, the quarantine trigger still writes `unsafe_replica_promotion` and both reject triggers stay installed. The Python check also refuses when that module is absent.

## RED

Product tree, before the history scan (current-epoch check only). A later `authority.claimed` without the marker admitted a write:

```text
HOME=/tmp/promotion-fence/home TMPDIR=/tmp/promotion-fence/tmp \
  scripts/run_tests.sh tests/gateway/test_hosted_room_replicas.py \
  -k test_later_claim_cannot_wash_unfenced_promotion -q --tb=short
```

1 failed, 13 deselected. `test_later_claim_cannot_wash_unfenced_promotion` — `Failed: DID NOT RAISE RoomQuarantinedError` at the post-wash `append_event`.

Earlier, before the marker was restored, the same runner on `test_unfenced_promotion_does_not_admit_execution` and `test_promote_requires_confirm_and_takes_over` failed the same way: promotion admitted `append_event`, and the confirm=true claim had no `promoted_from_replica` key.

## GREEN

Product tree, safety module absent. `scripts/run_tests.sh`, `TZ=UTC`, `LANG=C.UTF-8`, `PYTHONHASHSEED=0`. The runner clears `HERMES_HOME`.

```text
HOME=/tmp/promotion-fence/home TMPDIR=/tmp/promotion-fence/tmp \
  scripts/run_tests.sh \
  tests/gateway/test_hosted_room_replicas.py \
  tests/tui_gateway/test_groups_replication_methods.py \
  tests/gateway/test_hosted_rooms.py \
  tests/gateway/test_session_hosted_service.py \
  -q --tb=line
```

4 files, 70 passed, 0 failed, wall 4.5s. Replicas 14, groups replication 4, hosted rooms 48 (`claim_authority` on an ordinary room still appends), session hosted service 4 (ordinary `bindings()` still returns `owned`).

Overlay (safety checkout + splice), same three room files:

```text
HOME=/tmp/promotion-fence/home TMPDIR=/tmp/promotion-fence/tmp \
  scripts/run_tests.sh \
  tests/gateway/test_hosted_room_replicas.py \
  tests/tui_gateway/test_groups_replication_methods.py \
  tests/gateway/test_hosted_rooms.py \
  -q --tb=line
```

Replicas 14 passed. Groups replication 4 passed. Hosted rooms 46 passed, 2 failed: `test_upgrade_keeps_rooms_from_before_the_shared_state_db_split` and `test_legacy_import_is_a_one_shot_and_skips_driver_liveness_state`, both `UNIQUE constraint failed: hosted_room_id_reservations.room_id` during legacy import. That collision is pre-existing on this safety overlay and is not this slice.

## Adversarial review

Re-review count: **2**.

1. Restoring the marker and checking only the current epoch still let a later `authority.claimed` without the marker admit `append_event`, `admit_task`, and `acquire_lease`. `claim_authority` is that writer. Fixed by scanning every claim in the log, and by refusing `claim_authority` and `rename_room` while the marker remains. The scheduler parses JSON (`is True`), so a reason string that contains the fragment does not hide an ordinary room.
2. Overlay re-review: promote still quarantines as `unsafe_replica_promotion`; both reject triggers remain; an ordinary room in the same database still binds and accepts `claim_authority`; the safety reject trigger aborts a direct wash insert and the test still requires `RoomQuarantinedError` on the original epoch. No trigger was dropped. Safety bytes and the splice are not in the commit. NousResearch #106742 and #99107 were not touched. Fork `main` was not updated.

Open findings: none. Accepted residuals: a direct `UPDATE` of the stored claim payload can strip the marker when the safety schema is absent (the same writer can delete rows; with the schema the quarantine row still blocks); replaying an already-copied `event_id` returns the stored event and does not allocate a new sequence; `disband_room` can still tombstone on the product tree; `groups.promote` returns success with `executable: false` and the admission APIs raise. None of those start driver work. This slice does not implement host-loss recovery.

## Still later owners

Do not treat `groups.promote` success, `confirm=true`, or epoch+1 as a live host. Files import provider when history carries attachments. Route `links.route_security_digest` for a retirement that succeeds. The two legacy-import reservation collisions on the safety overlay. Publication of #106742 / #99107. Native/device. History rewrite. Merging a fork draft onto `main`.
