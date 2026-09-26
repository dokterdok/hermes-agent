# Route `route_security_digest` beside Files draft #11

This commit does not contain Route product bytes. `links.route_security_digest`, `PeerRunsHTTPClient` `target_profile`, and `revoke_grant_exact` stay on Route tip `46c3ce81784406af7a742f9d79e121c3917e584e` (NousResearch #100016). Checking out this commit alone leaves `test_remote_retirement_succeeds_with_route_security_digest` red once Retention safety is importable. Without that safety module the importer never reaches the digest check.

Parent is draft #11 tip `a36e562f02a69c3651fcee0d2a2bb70d1724c56d`.

Tower's local restore `e2264620933163946937b4e10c7b8a49ff147c16` is not in this clone and not on GitHub. The success test was not copied from it. The digest function text was.

## Overlay

Throwaway tree only. Not committed.

1. Retention safety: `git checkout 004015d6087fe031231c4d7d9e0032cc59b679eb -- gateway/hosted_room_safety.py`. `git hash-object` is `7bfb1bf04b59c52e27603370ef278ae85419ab22`.
2. Audit splice from draft #9 `7bca11a8869a4664ee19a2ce048e70dae10e3bea`, file `review-packages/replicas-audit-splice.patch`, SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90`. `git apply` succeeds on this branch's `gateway/hosted_room_replicas.py`.
3. Route slice, only after the RED run: `git apply review-packages/route-security-digest-overlay.patch`.
   - Patch SHA-256 `9f3d71736a9b1b998a2809b6c45c0223f7ccadf2ad9204ab0b3c7da21e22713e`.
   - `route_security_digest` is the exact function from `46c3ce81:gateway/hosted_room_links.py` (health fields `status` and `updated_at` excluded).
   - `PeerRunsHTTPClient.__init__` gains the tip's `target_profile` argument and profile-prefix block. The retirement writer passes `target_profile`; the current constructor rejects that keyword.
   - `_request` uses the tip's `{base_url}{_profile_prefix}{path}` form. An empty prefix leaves other callers unchanged.
   - `revoke_grant_exact` is the exact method from `46c3ce81:tui_gateway/hosted_room_peer_http.py`.
   - The whole links module is not checked out. That blob imports `gateway.hosted_room_link_records`, which this tree does not have.
4. Files store, for the attachment files in the GREEN run: `git checkout b353ab32c527bd02e2f606d567da4ca324c3aaff -- gateway/hosted_room_attachments.py tests/gateway/test_hosted_room_attachments.py`.
   - `gateway/hosted_room_attachments.py` blob `99586695410fe478a4fdf3c1b811eddf13a9b900`
   - `tests/gateway/test_hosted_room_attachments.py` blob `f35bed3bc07e9223de899fc8b028d331d6e4d9ac`

## RED

Safety and the splice are present. The Route patch is not. The Files checkout is not. Canonical runner:

```text
HOME=/tmp/route-red/home TMPDIR=/tmp/route-red/tmp \
  scripts/run_tests.sh tests/gateway/test_imported_member_retirement.py -q --tb=short
```

1 failed, 2 passed, runner file 4.9s (pytest 1.76s).

| Test | Result |
|---|---|
| `test_missing_route_digest_provider_refuses_remote_retirement_without_mutation` | passed |
| `test_disbanded_room_refuses_remote_retirement_without_mutation` | passed |
| `test_remote_retirement_succeeds_with_route_security_digest` | failed |

The failure is `pytest.fail` at line 120, after `RuntimeStoreError` / `peer_setup_unavailable` and `after == before`. No member write. The disband test raises `room_unavailable` and leaves membership `active`.

## GREEN

Same tree plus the Route patch and the two Files blobs.

```text
HOME=/tmp/route-green/home TMPDIR=/tmp/route-green/tmp \
  scripts/run_tests.sh \
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

8 files, 73 passed, 0 failed, runner wall 4.3s.

| File | Result |
|---|---|
| `tests/gateway/test_imported_member_retirement.py` | 3 passed (3.52s) |
| `tests/gateway/test_shipped_history_attachment_import.py` | 3 passed (1.69s) |
| `tests/gateway/test_hosted_room_attachments.py` | 25 passed (3.30s) |
| `tests/gateway/test_hosted_room_replicas.py` | 12 passed (3.30s) |
| `tests/gateway/test_shipped_group_history_import.py` | 7 passed (2.79s) |
| `tests/tui_gateway/test_group_history_import.py` | 4 passed (2.57s) |
| `tests/gateway/test_hosted_room_viewer_state.py` | 12 passed (2.20s) |
| `tests/gateway/test_session_hosted_rpc.py` | 7 passed (4.33s) |

The five #8 files are 33 passed. Retirement grew from 1 test to 3; the other four files are unchanged (7 + 4 + 12 + 7). Replicas stay 12 passed. The attachment store file stays 25 passed.

The success test calls the real `revoke_grant_exact` and the real `_request`. The peer socket is `open_credentialed_url`, replaced with a body `{"revoked": true}`. The captured URL is `https://original.example/p/builder/v1/room-members/grants/revoke-exact`, method POST, `Authorization: HermesRoom <grant>`, body `{}`. Membership becomes `former`, availability `retired` / `former_member`. `member_id`, `profile`, `handle`, `display_name`, and `source` stay put. The grant string stays put and the link status becomes `needs_reauthorization`. The room has no `disbanded_at`. Local members are unchanged. After `disband_room` with the room's own authority, retiring the other remote member raises `room_unavailable` and that member stays `active`.

`route_security_digest` on the saved link record ignores `status` and `updated_at` and changes when `grant` changes.

The missing-digest test deletes `route_security_digest` for that test only (`monkeypatch`, `raising=False`). On the RED tree the attribute is already absent, so the delete is a no-op and the test still passes. On the GREEN tree the attribute exists, the delete removes it, and retirement still refuses before mutation. The other two tests in the same file then see the attribute restored.

## Adversarial review

Re-review count: **2**.

The first pass looked for a stubbed revoke, a weakened missing-digest guard, a disband bypass, and a second digest implementation. The success test does not install `revoke_grant_exact`. The URL assertion fails unless the tip method and the profile-prefix line ran. The refusal test still requires `peer_setup_unavailable` and an unchanged member. The disband test requires `room_unavailable` while membership stays `active`, including on the GREEN tree where the digest function is present. The function text in the patch matches `46c3ce81`. `hosted_room_link_records` is not introduced. Product modules are not in this commit.

Confirmatory pass: RED output is the `pytest.fail` after the no-mutation assert, not an earlier setup error. GREEN is 73 passed. Slice bytes were compared to the tip before the tree was restored. Drafts #8, #9, #10, and #11 were not updated. NousResearch #100016, #98072, #106742, and #99107 were not published or merged. Fork `main` was not updated. Open findings: none.

## Still later owners

The attachment catalog module behind `list_published` (not used by history import or by this retirement). Publication of #100016 / #98072 / #106742 / #99107. Native/device. History rewrite. Merging a fork draft onto `main`.
