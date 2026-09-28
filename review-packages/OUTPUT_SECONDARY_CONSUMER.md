# A3 consumer of Output secondary retained publication

This commit calls the Output secondary contract from draft #15 (`ece9f17e2d1a2143d563c52140d4e13417f18456`). It does not rewrite that contract. `_output_owner` and `_output_policy_read` are unchanged. Send-consent is not publication authority. Primary artifact retry and completion rows are not reused. `runner.session_authority` is not written.

Parent is the #15 tip. That branch was not merged. Fork `main` was not updated. NousResearch was not updated.

## Call path

`HostedRoomAuthorityRPC.publish_secondary_retained` is the invitation→NEW-run consumer. The same RPC object admits the hosted member session (`create` / `submit`). After the driver task has settled, that method calls `CanonicalHostedOutputPublisher.consume_secondary_retained_publication`, which calls `consume_secondary_retained_publication` in `gateway/session_hosted_output_secondary_consumer.py`.

The consumer only calls the #15 methods: `register_secondary_publication`, `publish_secondary_publication`, `retry_secondary_publication`, `record_secondary_publication_failure`, `complete_secondary_publication`, and, when a consent object is passed, `publish_secondary_from_consent`. It does not insert secondary rows itself. Primary `publish_terminal` does not call it.

A missing contract raises `Group Chat secondary publication is not registered` (or the consent refusal) before any secondary write. A later retryable failure does not clear `authorization_or_verification`; the consumer records it through the contract, which keeps the blocked view.

## Overlay

Throwaway trees only. Not committed. Same lower-owner bytes as `review-packages/OUTPUT_SECONDARY_PUBLICATION.md` on #15.

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

- `SessionDB.live_read_connection` from `0be79d80d0aab81dae999eb48b9a7b94be2938c1`, inserted before `_read_ctx`.
- `SessionDB.live_write_connection` from `55a032ed1a014986a9d2b339cab47e2ba1708012`, inserted before `_read_ctx`.
- `HostedRoomPolicyCheckpoint.snapshot` from `e535c040f582fec2a3acbbc787d73aeeb5e5365b`, including `held_output_threads` and `read_connection`. `ContextManager` is imported. The rest of the checkpoint file stays the Output tip.
- `route_security_digest` from `46c3ce81784406af7a742f9d79e121c3917e584e:gateway/hosted_room_links.py`. `hashlib` is imported beside it. The links module is not replaced.
- `HostedRoomService.__init__` gains `self.attachments = HostedRoomAttachmentStore(self.db_path)` from the Output tip's `gateway/hosted_room_attachments.py`.

## RED

Throwaway tree: parent `93fd0cd531ffe9c3e6ec1cd80c868d0ae09e44f7`, this consumer (RPC method, publisher method, consumer module, and this test), and the overlay above. The #15 secondary module and the retry delegates are absent. The runner's `PYTHONPATH` is the tree root so the editable install cannot hide the overlay. That runner edit is not committed.

```text
HOME=/tmp/output-secondary-consumer-red/home TMPDIR=/tmp/output-secondary-consumer-red/tmp \
  scripts/run_tests.sh tests/gateway/test_secondary_retained_publication_consumer.py -q --tb=line
```

Measured: 1 file, 1 passed, 4 failed. Pytest 4.90s. Runner wall 10.6s.

`test_consumer_fails_closed_when_the_contract_is_missing` passes without the overlay. A bare service raises `not registered`. A consent object is refused with `send consent is not publication authority` and is not written.

The four lifecycle tests finish the settled turn, then fail closed. Three raise `Group Chat secondary publication is not registered` at `gateway/session_hosted_output_secondary_consumer.py:24` (`_require_contract`, before `register_secondary_publication`). The consent assertion in the forged-route test passes on that same local refusal. Its route assertion then sees `not registered` rather than `route is unauthorized`, because route authorization lives in the missing contract. No secondary publication method exists on that tree, so no secondary row is written.

## GREEN

Throwaway tree: this consumer stacked on #15 `ece9f17e2d1a2143d563c52140d4e13417f18456`, same overlay, same uncommitted `PYTHONPATH` pin.

```text
HOME=/tmp/output-secondary-consumer-green/home TMPDIR=/tmp/output-secondary-consumer-green/tmp \
  scripts/run_tests.sh tests/gateway/test_secondary_retained_publication_consumer.py \
  tests/gateway/test_secondary_retained_publication.py -q --tb=line
```

Measured after the blocked-path assertions: 2 files, 10 passed, 0 failed. Runner wall 6.6s. Consumer file 5 passed in 5.57s. Contract file 5 passed in 6.64s.

The NEW-run RPC registers, publishes, records a transient failure, retries only when due, and completes at that attempt. `valid_until`, work, route, member, and event digest stay the registered commitment. A later consume returns the completion and does not insert another registration. Send-consent does not change `total_changes`. A forged route writes no row. After `valid_until`, publish/retry stay `expired_grant` and do not adopt a later horizon; completion is refused. An authorization failure stays `authorization_or_verification` when a later `ConnectionError` is recorded for the same attempt. Completion of that attempt is refused. Secondary counts stay `(1, 0)`. Primary event, retry, and completion rows stay equal to the pre-call fingerprint on the publish/retry/completion path, the forged-route path, the expiry path, and the authorization-block path. `runner.session_authority` stays the room authority.

## Adversarial review

Re-review count: 2. Verdict: CLEAN.

Review 1 of `c263fc226c05c5f64e97fcf484132661cb7ad496` found one proof gap. `test_consumer_blocked_authorization_stays_blocked` did not snapshot primary rows, and it only checked that the completion count was zero. A primary write, or deletion of the blocked registration, would still have passed.

Checked on that tip and left in place:

- Consent is refused before `_require_contract` and before any register or publish. The #15 `publish_secondary_from_consent` always raises and does not touch the store. A missing refuse method raises the same consent error locally.
- A missing contract raises at `_require_contract` before register. The RED run dies there.
- The consumer executes no SQL. Register, publish, retry, failure, and completion are the #15 methods. Each enters `_mutate`, which rechecks `_output_retry_ready`, `_output_policy_read`, `_output_owner`, and the task snapshot. Owner-changed errors are not caught here.
- `record_secondary_publication_failure` returns the existing blocked view before it can rewrite `reason_code`. A later `ConnectionError` on that attempt stays `authorization_or_verification`. `retry_secondary_publication` does not call `_mark_published` while the row is blocked. `confirm=True` then reaches `complete_secondary_publication`, which refuses and inserts no completion row.
- Expiry blocks with `expired_grant` and does not change `valid_until`. A later register raises lifetime expired.
- `publish_terminal` does not call the consumer. The #15 file still passes, so a settled turn does not by itself publish a secondary row.
- A caller-supplied publication id is never the stored id. Register computes it. A mismatched id after a valid route can observe a canonical registration the contract already committed, then refuse to continue. The forged id is not stored. That is not an authorization bypass. This slice does not add a writer that deletes the contract's own row.

Review 2 covered the blocked-path assertions and this receipt. The new checks only read `_primary` and the secondary counts. The GREEN command above includes them. No remaining in-scope finding.

`publish_secondary_retained` is the invitation→NEW-run call site. It is not invoked from `publish_terminal` or from admission. Folding it into primary publication would mix the two ledgers. A later owner calls it after the task has settled.

## Still later owners

Publication of NousResearch #99159 / #98072 / #100016 / #106742 / #99107. F1, native/device, history rewrite, and A7 legacy atomic Stop. Merging this draft onto fork `main`. A post-settlement caller of `publish_secondary_retained` that stays outside primary `publish_terminal`.
