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

Parent `93fd0cd531ffe9c3e6ec1cd80c868d0ae09e44f7` plus this consumer (RPC method, publisher method, consumer module, and this test) plus the overlay above. The #15 secondary module and the retry delegates are absent.

The settled turn completes. The consumer then raises `Group Chat secondary publication is not registered`. No secondary row is published.

The missing-contract unit test does not need the overlay. It passes on the committed tree: a bare service fails closed, and a consent object is refused without a secondary write.

## GREEN

Same overlay, plus the #15 contract at `ece9f17e2d1a2143d563c52140d4e13417f18456`, plus this consumer.

The NEW-run RPC registers, publishes, records a transient failure, retries only when due, and completes at that attempt. `valid_until`, work, route, member, and event digest stay the registered commitment. A later consume returns the completion and does not insert another registration. Send-consent does not change `total_changes`. A forged route writes no row. After `valid_until`, publish/retry stay `expired_grant` and do not adopt a later horizon; completion is refused. An authorization failure stays `authorization_or_verification` when a later `ConnectionError` is recorded for the same attempt. Primary event, retry, and completion rows stay equal to the pre-call fingerprint. `runner.session_authority` stays the room authority.

## Adversarial review

Re-review count: pending the confirmatory pass after GREEN.

## Still later owners

Publication of NousResearch #99159 / #98072 / #100016 / #106742 / #99107. F1, native/device, history rewrite, and A7 legacy atomic Stop. Merging this draft onto fork `main`.
