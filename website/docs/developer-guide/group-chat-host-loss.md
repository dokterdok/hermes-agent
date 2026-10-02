# Group Chat host-loss recovery proposal

:::note Design direction, not an enabled recovery feature
This document records the Layer 7 proposal in [#104601](https://github.com/NousResearch/hermes-agent/pull/104601), part of [#97681](https://github.com/NousResearch/hermes-agent/issues/97681). It does not implement schema changes, enrollment, election or deployment. Automatic promotion remains disabled, and passive asynchronous replication continues to report `source_loss_safe: false`.
:::

## Goal and current boundary

The goal is for a Group Chat to survive loss of its authority host: the same conversation and accepted work remain identifiable, healthy Bots continue, and no second authority starts conflicting work. An unknown accepted attempt must never be retransmitted merely because its coordinator disappeared.

The maintainer's requirement is continuous replication of the **full ordered group log to every participant gateway**. This includes idle and unaddressed participants. Several Bots on one installation need one coherent gateway-level copy. A surviving participant should have, or safely recover, the complete history before becoming a successor; possession of a copy alone grants no authority.

Main's existing manual promotion/demotion primitives are documented in the [operator recovery procedure](../user-guide/bot-mode.md#transferring-hosted-room-authority). That procedure requires fencing the old writer and is not an atomic handover. In the Layer 7 stack with [#99107](https://github.com/NousResearch/hermes-agent/pull/99107), the takeover gate stays closed and unproved authority changes remain quarantined. This proposal does not weaken that gate or the manual-fencing warning.

Current preservation provides authenticated, bounded page transfer and work evidence for opted-in routes. Page limits are transport bounds, not an intended recent-history window. Asynchronous acknowledgments do not yet prove that every participant holds the latest history, or that a successor can safely execute work.

## Full-history custody and catch-up

Future reviewed work should make each participant installation a required replication destination, derived from the room's roster rather than only from available transport routes. Missing enrollment, missing permissions, offline lag, gaps and capacity refusal must remain visible. They must not disappear from coverage when a route is removed or a grant expires.

Joining or upgrading must establish the participant owner's explicit consent for full shared history and the necessary work evidence. Existing grants must not be silently widened. A new or returning gateway catches up from its missing contiguous prefix through bounded pages, preserving original event IDs, order and payloads. An authenticated surviving custodian must eventually be able to supply catch-up data before the recipient becomes leader.

An offline gateway cannot impose a unanimous-acknowledgment barrier on healthy participants. It retains a catch-up obligation and remains incomplete until verified. The eventual protected-commit rule must require sufficient independent durable copies before promising survival of acknowledged data; it must not require every participant to be online. A last-page acknowledgment proves only its particular watermark, not that no newer events exist.

Capacity handling must preserve required active history or explicitly refuse further writes. It must never discard valid payload and advertise the remaining marker as a complete copy. Lossless archival may be considered separately. Explicit End, copy retirement and retention expiry also remain separate: a permanently retired room ID is not retained conversation content.

## Stable origins and authority lineage

The schema and replay contract should be reviewed for succession before promotion is enabled. Existing sequence and authority-history mechanisms are the starting point; a wholesale storage rewrite is not implied.

- Keep room identity and origin stable while the current authority changes. Qualify owner and participant identities by their installation and profile.
- Keep a local Bot bound to its original installation. Moving room authority must not reinterpret its profile name as a different Bot on the successor.
- Extend one global ordered history across verified `N → N+1` authority spans. Preserve earlier actors, epochs and events instead of resetting sequence or rewriting history.
- Distinguish the event's author, the current authority and a custodian transporting historical pages. Validate gaps, conflicting overlap and transition provenance.
- Keep room authority epoch, participant runtime epoch and execution generation distinct. Preserve original Run ownership, admission and artifact identities.

Any changed wire representation needs explicit compatibility handling. Syntactically valid lineage, a larger epoch or a caller-supplied verification flag is not exclusive-authority proof. Unverifiable history must remain inspectable where safe, but cannot become a basis for execution.

## Coordination, consent and unavailable resources

Proposed coordination uses three independent, self-hosted machines. This does **not** mean only three log replicas, exactly three participants, or succession restricted to privileged nonparticipant backups. Every participant remains a full-history destination; any authorized, sufficiently caught-up surviving participant is a potential authority.

Actual hosts, data locations, access grants and the enforcement mechanism remain unselected. Multiple containers on one physical host count as one failure domain. An established consensus implementation such as etcd is only a candidate, not an adopted dependency. This proposal adds no service or deployment.

Later active recovery must prove one exclusive successor and enforce that decision at accepting writers, admission and publication boundaries. A timeout, cached lease observation or local epoch increment is insufficient. Partitions, competing successors and a returning old process must not create a second writer. Without sufficient proof, new authoritative actions pause.

Participant owners may preauthorize named successors through narrowly scoped, revocable and auditable permissions. That delegation does not transfer installation signing keys, private Bot credentials or unrelated sessions. Whole-Bot replication is separate scope: an unavailable Bot or private tool may remain blocked while the group and healthy Bots continue.

## Accepted work and controls

A successor must reconcile original accepted identities rather than create replacement Runs or generations. Bounded work records are evidence, not complete execution checkpoints; missing records do not prove nonadmission. Live approvals require their exact pending identity and generation, and copied approval text must never become a new approval.

Future successor controls need valid permission for the unchanged original scope. They must preserve revocation, permanent participant freezes and Disband. [#105079](https://github.com/NousResearch/hermes-agent/pull/105079) provides bounded participant-owner containment, not election authority or proof that all group work stopped.

Shared files retain their exact manifest and custody requirements. Execution resolution or authority loss alone must not authorize publication, disposal or source reclamation. Independently retained bytes and valid disposition evidence remain necessary; private workspace copying is not implied by full group-log replication.

## Acceptance before enabling succession

Future implementation should demonstrate:

1. Every participant retains the exact full prefix beyond page limits, including an unaddressed gateway and multiple Bots sharing one installation.
2. Offline catch-up, restart, lost replies and capacity pressure preserve history without blocking healthy participants on unanimous acknowledgments.
3. Multi-epoch replay preserves original identities and Bot placement; absent or forged succession proof refuses.
4. Partitions, competing candidates and the old writer's return yield at most one accepting authority.
5. Crashes around admission, approval, publication and cleanup cause no unknown-work retransmission, duplicated visible result or loss of held files.
6. Normally installed gateways on independent physical hosts recover with Desktop closed, while unavailable private resources remain explicit.

Schema, replay and enrollment changes require focused review in their owning PRs. Active succession requires its own enforcement and fault evidence. Until then, the existing gates and operator-fencing requirements remain in force.
