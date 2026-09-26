# Shipped Group Chat upgrade continuity

Automatic upgrade is `groups.import_history` on `CanonicalHostedRoomService`. It does not call `groups.create` or `promote_replica`. Unfenced promotion stays non-executable.

## Pins

| Owner | Public commit |
|---|---|
| Runtime series product | `879b3a2146eaf0f97443a7d03491756d4af11db7` |
| Runtime series findings tip | `ff18610351f4cd0fc9a69bd3a037bd8516f086f6` |
| Post-fencing parent (draft #10) | `7b3b6bdbc817da3d7102a20eb5c392e121e31190` |
| Files attachment-import compose | `a36e562f02a69c3651fcee0d2a2bb70d1724c56d` |
| Files tip | `b353ab32c527bd02e2f606d567da4ca324c3aaff` |
| Route digest compose | `327b81637fdc35dd2864c18476122f8bc6a45825` |
| Files catalog compose | `9a1540b0ccb3180c2de25871260784cabb0383e7` |
| Retention safety source | `004015d6087fe031231c4d7d9e0032cc59b679eb` |
| Audit splice source | `7bca11a8869a4664ee19a2ce048e70dae10e3bea` |

Committed blobs (not an undocumented overlay):

| Path | git hash-object |
|---|---|
| `gateway/hosted_room_safety.py` | `7bfb1bf04b59c52e27603370ef278ae85419ab22` |
| `gateway/hosted_room_attachments.py` | `99586695410fe478a4fdf3c1b811eddf13a9b900` |
| `tests/gateway/test_hosted_room_attachments.py` | `f35bed3bc07e9223de899fc8b028d331d6e4d9ac` |
| `gateway/hosted_room_attachment_catalog.py` | `8aab5f2c68d026262c3793428806f4f54f35fb38` |
| `tests/gateway/test_hosted_room_attachment_catalog.py` | `e641354ff06686e8c6b5400735fab1c12cb1c5bb` |

Route overlay patch SHA-256 `9f3d71736a9b1b998a2809b6c45c0223f7ccadf2ad9204ab0b3c7da21e22713e`.
Audit splice SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90`.
`_audit_existing_replicas_locked` matches that splice. Promote and demote are the fenced copies. Triggers are not deleted.

## Product fixes on this branch

- Submit authorization is checked again after `submission_payload` and before `authority.submit`. Preparation can outlive the dispatch-time allow.
- Legacy import does not copy `hosted_room_id_reservations` in table order. Room and replica inserts recreate those rows. Leftover fences are replayed with `INSERT OR IGNORE`. Quarantine copy uses `INSERT OR IGNORE` so an unfenced claim still imports and `append_event` stays quarantined.

## Journey

`tests/gateway/test_group_chat_continuity_journey.py` uses disposable rows and the real canonical dispatch. Import retry is idempotent and queues no driver task. History, members, and PNG bytes survive closing and reopening `SessionDB`. One `groups.send` queues one local task whose prompt contains the shipped history. A second send with the same client id does not duplicate it. Replacing the owner grant makes the next send `permission_denied`. No `authority.claimed` row is written. A second test revokes submit while preparation is blocked and asserts no admission.

`runtime.status` is stubbed to running so dispatch reaches `service.send` without a model worker. Import, authorization, attachment bytes, and task planning are the real methods.

Desktop is not exercised. Desktop does not call `groups.import_history`.

## Adversarial review

Re-review count: **1**.

Pass 1 found one in-scope defect: with the safety schema committed, pre-isolation import aborted on `UNIQUE constraint failed: hosted_room_id_reservations.room_id`, so `test_upgrade_keeps_rooms_from_before_the_shared_state_db_split` and `test_legacy_import_is_a_one_shot_and_skips_driver_liveness_state` lost the room. Fixed without editing triggers. Re-review of that delta plus the submit recheck: no open findings.

`tests/gateway/test_api_media_retention.py` does not collect here (`ModuleNotFoundError: aiohttp`) before it reaches the attachment store. That was already recorded on the Files receipt. It is not a journey failure.

Verdict: **CLEAN**.
