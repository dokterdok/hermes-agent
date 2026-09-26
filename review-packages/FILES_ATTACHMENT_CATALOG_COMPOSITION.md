# Files `hosted_room_attachment_catalog` beside Route draft #12

This commit does not contain Files catalog bytes. `list_published` stays on Files tip `b353ab32c527bd02e2f606d567da4ca324c3aaff` (NousResearch #98072). The store method on blob `99586695410fe478a4fdf3c1b811eddf13a9b900` imports it lazily. Checking out this commit alone leaves that module absent. The #12 tree's own `gateway/hosted_room_attachments.py` does not define `list_published`.

Parent is draft #12 tip `327b81637fdc35dd2864c18476122f8bc6a45825`.

## Overlay

Throwaway tree only. Not committed. Same stack the #12 receipt already proved, then the catalog.

1. Retention safety: `git checkout 004015d6087fe031231c4d7d9e0032cc59b679eb -- gateway/hosted_room_safety.py`. `git hash-object` is `7bfb1bf04b59c52e27603370ef278ae85419ab22`.
2. Audit splice from draft #9 `7bca11a8869a4664ee19a2ce048e70dae10e3bea`, file `review-packages/replicas-audit-splice.patch`, SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90`. `git apply` succeeds on this branch's `gateway/hosted_room_replicas.py`.
3. Route slice: `git apply review-packages/route-security-digest-overlay.patch`. Patch SHA-256 `9f3d71736a9b1b998a2809b6c45c0223f7ccadf2ad9204ab0b3c7da21e22713e`.
4. Files store: `git checkout b353ab32c527bd02e2f606d567da4ca324c3aaff -- gateway/hosted_room_attachments.py tests/gateway/test_hosted_room_attachments.py`.
   - `gateway/hosted_room_attachments.py` blob `99586695410fe478a4fdf3c1b811eddf13a9b900`
   - `tests/gateway/test_hosted_room_attachments.py` blob `f35bed3bc07e9223de899fc8b028d331d6e4d9ac`
5. Catalog tests, for both the RED and GREEN runs: `git checkout b353ab32c527bd02e2f606d567da4ca324c3aaff -- tests/gateway/test_hosted_room_attachment_catalog.py`. Blob `e641354ff06686e8c6b5400735fab1c12cb1c5bb`. This file is not copied into the commit.
6. Catalog module, only after the RED run: `git checkout b353ab32c527bd02e2f606d567da4ca324c3aaff -- gateway/hosted_room_attachment_catalog.py`. `git hash-object` is `8aab5f2c68d026262c3793428806f4f54f35fb38`. Compared byte-for-byte with that blob.

No other Files path was checked out. `gateway/hosted_room_viewer_state.py` is already on this tree (`viewer_room_state`). The Files tip does not have that module. The store's `_require_viewer_room` imports it, and the catalog calls that method. The import resolves here. `viewer_room_state` still requires one live room, rejects a disbanded room, rejects a non-positive epoch, and denies when a quarantine or disband-fence row is present. `_require_viewer_room` still raises `AttachmentNotFoundError` unless `authority_gateway_id` and `authority_epoch` match that row. Neither function was edited.

`HostedRoomAttachmentStore.list_published` forwards `room_id`, `authority_gateway_id`, `authority_epoch`, `cursor`, `limit`, `query`, `producer_member_id`, and `recipient_member_id`. Those names were not dropped.

## RED

Safety, the splice, the Route patch, and the Files store are present. The catalog module is not. Canonical runner:

```text
HOME=/tmp/catalog-red/home TMPDIR=/tmp/catalog-red/tmp \
  scripts/run_tests.sh tests/gateway/test_hosted_room_attachment_catalog.py -q --tb=short
```

23 failed, 0 passed, runner file 4.6s (pytest 1.89s).

Every failure is `ModuleNotFoundError: No module named 'gateway.hosted_room_attachment_catalog'` at `gateway/hosted_room_attachments.py:1427`, inside `list_published`, reached from `_page`. Collection succeeded. The store object exists before the import. No catalogue page was returned.

## GREEN

Same tree plus catalog blob `8aab5f2c68d026262c3793428806f4f54f35fb38`.

```text
HOME=/tmp/catalog-green/home TMPDIR=/tmp/catalog-green/tmp \
  scripts/run_tests.sh \
  tests/gateway/test_hosted_room_attachment_catalog.py \
  tests/gateway/test_imported_member_retirement.py \
  tests/gateway/test_shipped_history_attachment_import.py \
  tests/gateway/test_hosted_room_attachments.py \
  tests/gateway/test_hosted_room_replicas.py \
  tests/gateway/test_shipped_group_history_import.py \
  tests/tui_gateway/test_group_history_import.py \
  tests/gateway/test_hosted_room_viewer_state.py \
  tests/gateway/test_session_hosted_rpc.py \
  -q --tb=line
```

9 files, 96 passed, 0 failed, runner wall 6.0s.

| File | Result |
|---|---|
| `tests/gateway/test_hosted_room_attachment_catalog.py` | 23 passed (4.13s) |
| `tests/gateway/test_imported_member_retirement.py` | 3 passed (4.47s) |
| `tests/gateway/test_shipped_history_attachment_import.py` | 3 passed (1.41s) |
| `tests/gateway/test_hosted_room_attachments.py` | 25 passed (2.83s) |
| `tests/gateway/test_hosted_room_replicas.py` | 12 passed (3.11s) |
| `tests/gateway/test_shipped_group_history_import.py` | 7 passed (2.74s) |
| `tests/tui_gateway/test_group_history_import.py` | 4 passed (3.08s) |
| `tests/gateway/test_hosted_room_viewer_state.py` | 12 passed (1.83s) |
| `tests/gateway/test_session_hosted_rpc.py` | 7 passed (5.97s) |

The #12 set stays 73 passed. The five #8 files stay 33 (retirement 3, the other four 7 + 4 + 12 + 7). Replicas stay 12. The attachment store file stays 25. The catalog file adds 23.

The catalog file covers empty and bounded pages (including 2000 records), share order, unicode search, cursor freeze and request mismatch, an eight-attachment event without duplicates, a bounded sparse scan that does not read blobs, withholding of unpublished, expired, mismatched, and corrupt rows (actor, payload, manifest, recipient-only, recipients, wrong room, missing event, blob digest, epoch), response and cursor bounds, limit and query bounds, and same-event staging rows that must not bypass the metadata scan bound.

A sibling file on the same tip, `tests/gateway/test_hosted_room_attachment_catalog_v2.py` blob `1a55e602f87b2e52f5d8464381c8ef4f6b6d8ddb`, is not part of this recipe. Checked out beside the same catalog and store blobs it was 18 passed, 0 failed, runner file 4.6s. It did not need another product module. It is not in the 96.

## Adversarial review

Re-review count: **2**.

The first pass looked for a second catalog implementation, a dropped recipient or authority argument, a weakened viewer/epoch/fence check, and a green run that never called `list_published`. The module bytes match blob `8aab5f2c`. The store method still imports that module and still passes `recipient_member_id` and both authority fields. `viewer_room_state` on this tree was not edited. RED is the import error at line 1427 after the store object exists, not an earlier collection error, and it is 0 passed. GREEN is the tip's catalog file, 23 passed, on top of the #12 73. MIME, quota, ACL, cursor, and viewer-access checks in the store file were not edited (the 25 store tests still include those guards). Product modules are not in this commit. Drafts #8, #9, #10, #11, and #12 were not updated. NousResearch #98072, #100016, #106742, and #99107 were not published or merged. Fork `main` was not updated.

Confirmatory pass: the catalog checkout was compared with `cmp` to blob `8aab5f2c` before this receipt was written. The required recipe does not include the v2 test file. Open findings: none.

## Still later owners

Publication of #98072 / #100016 / #106742 / #99107. Native/device. History rewrite. Merging a fork draft onto `main`.
