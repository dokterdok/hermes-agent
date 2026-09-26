# Files attachment import beside Runtime #10 and Retention safety

This commit does not contain Files bytes. `put_import`, `commit_import_message`, and `recover_import_rollback` stay on Files tip `b353ab32c527bd02e2f606d567da4ca324c3aaff` (NousResearch #98072, branch `feat/bot-mode-roomlink-files-20260829`). Checking out this commit alone leaves `tests/gateway/test_shipped_history_attachment_import.py` red.

Parent is draft #10 tip `0d5cf6fd8ffb51acb01ecacda5ab2e2028c18144`. Promote/demote product is `df890a40941b77a8122293f80f47148a08b9ff2e` (same trees the #10 receipt verified). Runtime series parent remains `879b3a2146eaf0f97443a7d03491756d4af11db7`.

## Overlay

Throwaway tree only. Not committed.

1. Retention safety: `git checkout 004015d6087fe031231c4d7d9e0032cc59b679eb -- gateway/hosted_room_safety.py`. `git hash-object` is `7bfb1bf04b59c52e27603370ef278ae85419ab22`.
2. Audit splice from draft #9 `7bca11a8869a4664ee19a2ce048e70dae10e3bea`, file `review-packages/replicas-audit-splice.patch`, SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90`. `git apply --check` succeeds on this branch's `gateway/hosted_room_replicas.py`.
3. Files store, only after the RED run: `git checkout b353ab32c527bd02e2f606d567da4ca324c3aaff -- gateway/hosted_room_attachments.py tests/gateway/test_hosted_room_attachments.py`.
   - `gateway/hosted_room_attachments.py` blob `99586695410fe478a4fdf3c1b811eddf13a9b900`
   - `tests/gateway/test_hosted_room_attachments.py` blob `f35bed3bc07e9223de899fc8b028d331d6e4d9ac`

`list_published` on that store lazily imports `gateway.hosted_room_attachment_catalog`. The importer does not call it. That module was not checked out.

## RED

Safety and the splice are present. The Files checkout is not. Canonical runner:

```text
HOME=/tmp/files-red3/home TMPDIR=/tmp/files-red3/tmp \
  scripts/run_tests.sh tests/gateway/test_shipped_history_attachment_import.py -q --tb=line
```

3 failed, 0 passed, runner file 1.15s. Each failure is `HostedRoomError: shipped history attachments require the Files import provider` at `gateway/hosted_rooms.py:1459`. No room is written.

## GREEN

Same tree plus the two Files blobs above.

```text
HOME=/tmp/files-final/home TMPDIR=/tmp/files-final/tmp \
  scripts/run_tests.sh \
  tests/gateway/test_shipped_history_attachment_import.py \
  tests/gateway/test_hosted_room_attachments.py \
  tests/gateway/test_hosted_room_replicas.py \
  tests/gateway/test_shipped_group_history_import.py \
  tests/gateway/test_imported_member_retirement.py \
  tests/tui_gateway/test_group_history_import.py \
  tests/gateway/test_hosted_room_viewer_state.py \
  tests/gateway/test_session_hosted_rpc.py \
  -q --tb=line
```

8 files, 71 passed, 0 failed, wall 4.4s.

| File | Result |
|---|---|
| `tests/gateway/test_shipped_history_attachment_import.py` | 3 passed (1.41s) |
| `tests/gateway/test_hosted_room_attachments.py` | 25 passed (3.12s) |
| `tests/gateway/test_hosted_room_replicas.py` | 12 passed (3.15s) |
| `tests/gateway/test_shipped_group_history_import.py` | 7 passed (2.60s) |
| `tests/gateway/test_imported_member_retirement.py` | 1 passed (2.04s) |
| `tests/tui_gateway/test_group_history_import.py` | 4 passed (2.04s) |
| `tests/gateway/test_hosted_room_viewer_state.py` | 12 passed (2.06s) |
| `tests/gateway/test_session_hosted_rpc.py` | 7 passed (4.38s) |

The five #8 files are 31 passed. Replicas stay 12 passed under the Retention triggers. The attachment store file collects 25 tests (17 functions; MIME and zip cases are parametrized).

The importer fixture commits one PNG through `put_import` and `commit_import_message`, checks the row digest, denies a foreign member and a foreign event id, and serves the bytes to the member and to `read_viewer` for `history.imported`. A MIME mismatch leaves no room and no blob. An interrupted import records a `_write_blob` path and requires that path to be gone after `recover_import_rollback`.

Sibling store callers on the same Files blob, before this receipt: `test_hosted_room_expired_commit.py` 1, `test_hosted_discussion_manifests.py` 2, `test_hosted_room_retention_restart.py` 1, `test_hosted_attachment_bridge.py` 2, `test_hosted_file_publication.py` 2, `test_hosted_room_attachment_publication.py` 16, `test_session_hosted_transport.py` 6, `test_hosted_mux_runtime.py` 5. 35 passed, 0 failed. `test_api_media_retention.py` did not collect (`ModuleNotFoundError: aiohttp`); that import happens before the attachment store.

## Adversarial review

Re-review count: **2**.

1. The first green fixture only checked that `sha256` was non-empty and did not bind the read to the history event. The fixture now requires `hashlib.sha256(PNG)` and denies `history:not-this-event`.
2. The interruption test could pass when no blob was written. It now records `_write_blob` targets and requires each path to be absent after the importer's rollback recovery. RED stayed 3 failures at line 1459. GREEN of the eight files stayed 71 passed.

Confirmatory pass: Files bytes are not in this commit. MIME, quota, and recipient checks on the Files tip are unchanged (the 25 store tests include those guards). The importer does not catch `AttachmentError`. Reservation triggers are not edited. Drafts #8, #9, and #10 were not updated. NousResearch #98072, #106742, and #99107 were not published or merged. Fork `main` was not updated. Open findings: none.

## Still later owners

Route `links.route_security_digest` for a remote retirement that succeeds. The attachment catalog module behind `list_published` (not used by this import). Publication of #98072 / #106742 / #99107. Native/device. History rewrite. Merging a fork draft onto `main`.
