# Runtime promote/demote beside Retention reservation triggers

Product parent: Runtime `879b3a2146eaf0f97443a7d03491756d4af11db7`.
This branch: `cursor/runtime-promote-reservation-9d17`.

Retention safety is a test overlay only. Blob `7bfb1bf04b59c52e27603370ef278ae85419ab22` from tip `004015d6087fe031231c4d7d9e0032cc59b679eb`, confirmed with `git hash-object` after `git checkout 004015d6087fe031231c4d7d9e0032cc59b679eb -- gateway/hosted_room_safety.py`. It is not in this commit.

Audit splice: draft #9 `7bca11a8869a4664ee19a2ce048e70dae10e3bea` file `review-packages/replicas-audit-splice.patch`, SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90`. `git apply --check` succeeds on this branch's `gateway/hosted_room_replicas.py`. The splice is not copied into this commit. Retention replicas blob `e59558eb` is not used.

## RED

On `879b3a21` plus that overlay, before this fix:

```text
HOME=/tmp/retention-compose/home TMPDIR=/tmp/retention-compose/tmp \
  scripts/run_tests.sh tests/gateway/test_hosted_room_replicas.py -q --tb=short
```

8 passed, 3 failed, runner file 3.34s. Failures:

- `test_promote_replica_continues_room_at_next_epoch` — `IntegrityError: room_id is already reserved` at the `hosted_rooms` insert
- `test_promote_refuses_when_room_exists_locally` — same error at the new-replica insert
- `test_full_failover_round_trip` — same error at the `hosted_rooms` insert

Triggers were not dropped. A reservation-only probe then failed the two promote tests with `IntegrityError: room authority is quarantined` from `append_event`, because the claim still carried `promoted_from_replica` and `trg_hosted_events_quarantine_unsafe_lineage` matched it.

## Fix

`promote_replica` deletes only a replica-owned `hosted_room_id_reservations` row, then inserts the room so `trg_hosted_rooms_reserve_insert` records `authority`. Any other owner is refused and left in place. A new replica insert is refused when a reservation already exists.

The claim keeps `previous_gateway_id`, `authority_gateway_id`, `authority_epoch`, and `reason`. It does not set `promoted_from_replica`. That fragment is what the quarantine trigger and the safety backfill both classify as an unverified takeover. `claim_authority` already writes `authority.claimed` without it. The triggers are unchanged.

Replica events are deleted before they are inserted into `hosted_room_events`. Copy-then-delete counts the same bytes twice against `trg_hosted_events_shared_budget` and aborts a history that already fits.

`demote_room` still appends `authority.lost`. The same-lineage idempotent return happens before the quarantine check. A later demote that would insert into an already quarantined room raises `RoomQuarantinedError`.

## Tests

Canonical `scripts/run_tests.sh` (`TZ=UTC`, `LANG=C.UTF-8`, `PYTHONHASHSEED=0`). The runner clears `HERMES_HOME`.

Product tree, safety module absent:

```text
HOME=/tmp/nosafety/home TMPDIR=/tmp/nosafety/tmp \
  scripts/run_tests.sh tests/gateway/test_hosted_room_replicas.py -q --tb=line
```

12 passed, 0 failed, runner file 2.57s, wall 2.6s. The shared-budget case returns without allocating the large history when `hosted_room_event_budget` is absent.

Overlay (safety checkout + splice) at `f05b23f6c0e8c1265397dfa141c270aa5e3e6ba4`:

```text
HOME=/tmp/green-compose/home TMPDIR=/tmp/green-compose/tmp \
  scripts/run_tests.sh \
  tests/gateway/test_hosted_room_replicas.py \
  tests/gateway/test_shipped_group_history_import.py \
  tests/gateway/test_imported_member_retirement.py \
  tests/tui_gateway/test_group_history_import.py \
  tests/gateway/test_hosted_room_viewer_state.py \
  tests/gateway/test_session_hosted_rpc.py \
  -q --tb=line
```

6 files, 43 passed, 0 failed, wall 3.8s.

| File | Result |
|---|---|
| `tests/gateway/test_hosted_room_viewer_state.py` | 12 passed (1.79s) |
| `tests/gateway/test_imported_member_retirement.py` | 1 passed (1.89s) |
| `tests/tui_gateway/test_group_history_import.py` | 4 passed (2.20s) |
| `tests/gateway/test_shipped_group_history_import.py` | 7 passed (2.60s) |
| `tests/gateway/test_hosted_room_replicas.py` | 12 passed (2.87s) |
| `tests/gateway/test_session_hosted_rpc.py` | 7 passed (3.80s) |

The five #8 files are 31 passed. The three reservation failures and the half-budget promote are inside the 12 replica passes. After promote, the test sees `owner_kind='authority'`, no quarantine row, and both reject triggers still installed.

## Adversarial review

Re-review count: **2**.

1. Reservation transfer alone still quarantined the continued room, so the next append aborted. Fixed by not writing the unsafe-takeover fragment. Triggers and the backfill stay.
2. Copy-then-delete double-charged the shared event budget. Fixed by deleting the replica events first. The overlay replica file includes a history just over half the ordinary budget and checks the committed budget equals the authority events only.

Confirmatory pass: no trigger was dropped or edited; a non-replica reservation is not deleted; demote still writes `authority.lost` (that trigger quarantines the demoted room, and the failover test still fences the stale epoch); safety bytes are not in the commit; #8/#9 branches, NousResearch #106742, #99107, and fork `main` were not updated. Open findings: none.

## Still later owners

Files import provider when history carries attachments. Route `links.route_security_digest` for a retirement that succeeds. Publication of #106742 / #99107. Native/device. History rewrite. Merging a fork draft onto `main`.
