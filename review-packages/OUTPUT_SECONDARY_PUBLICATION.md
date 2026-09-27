# Output secondary retained publication

This commit is the Output-owned secondary contract only. It does not contain Retention, Input, Route, Files, or Policy bytes. `_output_owner` and `_output_policy_read` are unchanged. Send-consent is not publication authority. Secondary rows are `hosted_room_secondary_publications` and `hosted_room_secondary_publication_completions`. Primary artifact retry and completion rows are not reused, and `runner.session_authority` is not written.

Parent is Output tip `93fd0cd531ffe9c3e6ec1cd80c868d0ae09e44f7` (`feat/bot-mode-hosted-bot-file-handoff-20260831`, NousResearch #99159). That branch was not pushed.

Wrong-owner candidates `73fb56d8` and `64d01d9a` are not in this commit.

## Overlay

Throwaway trees only. Not committed. The same overlay set was applied to a parent worktree (RED) and to this contract (GREEN).

Checked out by OID:

| Path | Source | `git hash-object` |
|---|---|---|
| `gateway/hosted_room_safety.py` | Retention `004015d6087fe031231c4d7d9e0032cc59b679eb` | `7bfb1bf04b59c52e27603370ef278ae85419ab22` |
| `gateway/hosted_room_output_completion.py` | Retention `004015d6087fe031231c4d7d9e0032cc59b679eb` | `10134f074e170f6010b7ba22d80abb93e1d34f4b` |
| `gateway/hosted_room_task_scan.py` | Retention `004015d6087fe031231c4d7d9e0032cc59b679eb` | `42f2ff4f5e93014b83cb5afd6390d0c61d5d98d9` |
| `gateway/hosted_room_input_custody.py` | Input `d12f6041191233e7fa4f05bd7c02a02b84a7b85c` | `4690a2970612e1e84878bebcffb2aeeeecf09fb1` |
| `gateway/session_group_state.py` | Route `46c3ce81784406af7a742f9d79e121c3917e584e` | `8634628ee86af069bfe089d96df737dbb0f2295f` |
| `gateway/session_group_disband.py` | Route `46c3ce81784406af7a742f9d79e121c3917e584e` | `8b1f4373697970b433a97576d622182e79ca9ae8` |
| `gateway/session_group_retirement.py` | Route `46c3ce81784406af7a742f9d79e121c3917e584e` | `c9a0b092c2a1a91a0f5e33480a2a49da089603f7` |
| `gateway/hosted_room_route_schema.py` | Route `46c3ce81784406af7a742f9d79e121c3917e584e` | `04dd05e850e36b8710f2c186ce21b11ec1152dfd` |

Splices, not whole-module checkouts:

- `SessionDB.live_read_connection` is the method from `0be79d80d0aab81dae999eb48b9a7b94be2938c1` (`hermes_state.py`), inserted before `_read_ctx`.
- `SessionDB.live_write_connection` is the method from `55a032ed1a` (`fix: add non-reopening held SessionDB writer fence`), inserted before `_read_ctx`.
- `HostedRoomPolicyCheckpoint.snapshot` is the method from `e535c040f582fec2a3acbbc787d73aeeb5e5365b`, including `held_output_threads` and `read_connection`. `ContextManager` is imported. The rest of the checkpoint file stays the Output tip.
- `route_security_digest` is the function from `46c3ce81784406af7a742f9d79e121c3917e584e:gateway/hosted_room_links.py`. `hashlib` is imported beside it. The links module is not replaced. `hosted_room_link_records` is not introduced.
- `HostedRoomService.__init__` gains `self.attachments = HostedRoomAttachmentStore(self.db_path)` so the Output publisher's `output_attachments` property resolves. The store class is the Output tip's `gateway/hosted_room_attachments.py`. The Files tip copy of that module was not kept: it does not export `read_viewer_from_store`, which Output retirement imports.

The test calls Retention `_raise_if_quarantined` through `rooms.room_safety` and Route `initialize_route_schema` so the fence's disband-fence read has its table. `RoomQuarantinedError` is installed on `hosted_rooms` only when that class is absent. The quarantine table is created empty.

## RED

Parent `93fd0cd531ffe9c3e6ec1cd80c868d0ae09e44f7` plus the test file plus the overlay above. The secondary module and the retry delegates are absent.

```text
HOME=/tmp/output-secondary-red/home TMPDIR=/tmp/output-secondary-red/tmp \
  scripts/run_tests.sh tests/gateway/test_secondary_retained_publication.py -q --tb=short
```

1 file, 0 passed, 5 failed, runner wall 12.0s (pytest 5.79s).

The settled turn completes. Each test then raises `AttributeError` because `CanonicalHostedRoomService` has no `publish_secondary_publication` or `register_secondary_publication`. No secondary row is published.

The committed tree without the overlay does not collect the test: `ModuleNotFoundError: No module named 'gateway.hosted_room_output_completion'`.

```text
HOME=/tmp/output-secondary-bare/home TMPDIR=/tmp/output-secondary-bare/tmp \
  scripts/run_tests.sh tests/gateway/test_secondary_retained_publication.py -q --tb=line
```

1 file, 0 tests, collection error, runner wall 1.4s.

## GREEN

Same overlay, plus this contract.

```text
HOME=/tmp/output-secondary-green/home TMPDIR=/tmp/output-secondary-green/tmp \
  scripts/run_tests.sh tests/gateway/test_secondary_retained_publication.py -q --tb=short
```

1 file, 5 passed, 0 failed, runner wall 6.3s (pytest inside the file subprocess).

| Test | Result |
|---|---|
| `test_unregistered_secondary_publication_fails_closed` | passed |
| `test_secondary_publish_retry_and_completion_keep_provenance` | passed |
| `test_secondary_publication_rejects_unauthorized_and_stale_routes` | passed |
| `test_secondary_publication_expires_without_extending_the_grant` | passed |
| `test_secondary_publication_lifetime_and_owner_fail_closed` | passed |

Unregistered publish, retry, and completion raise `not registered`. Send-consent raises `send consent is not publication authority` and does not change `total_changes`. A registered publication publishes once, retries only when due, completes at the same attempt, and keeps `valid_until`, work, route, member, and event digest. A later publish or retry returns that completion and does not insert another registration. A forged route raises `route is unauthorized` with no row. A result that does not match the caller snapshot raises `snapshot changed` and leaves the registration unblocked. The same result, once the caller snapshot matches, blocks `stale_binding` and refuses completion. After `valid_until`, publish and retry block `expired_grant` and do not adopt `clock + 86400`. Re-register raises `lifetime expired`. An authorization failure stays blocked when a later `ConnectionError` is recorded for the same attempt. Swapping `session_authority`, bumping the epoch, draining, setting `_db_replaced`, or clearing `hosted_room_service` fails closed. Primary event, retry, and completion rows stay equal to the pre-call fingerprint.

## Adversarial review

Re-review count: **2**.

First pass: a second `record_secondary_publication_failure` with a retryable error could clear `authorization_or_verification` and set `blocked=0` for the same attempt. That is fixed. A blocked row returns the blocked view and is not rewritten. The lifetime test records `ConnectionError` after `RoomArtifactError` and still sees `authorization_or_verification`.

Confirmatory pass: `_output_owner` and `_output_policy_read` are not in the product diff. Consent does not take a store connection. Secondary completion deletes only the secondary registration. The publication id does not include `valid_until`, and publish does not rewrite `metadata_json`. An `owner changed` error from live metadata is re-raised, not committed as a route block. No private owner is installed. `73fb56d8` and `64d01d9a` are not in the tree. NousResearch #99159 was not updated. Fork `main` was not updated. Open findings: none.

## Still later owners

A3 consumer path that calls this contract. Publication of NousResearch #99159 / #98072 / #100016 / #106742 / #99107. F1, native/device, history rewrite, and A7 legacy atomic Stop.
