# Group Chat Continuity: Runtime Preparation

Let a team of Bots keep working after Desktop closes, then check progress,
send instructions or retrieve a file from another client. This branch adapts
the existing [continuity stack](https://github.com/NousResearch/hermes-agent/issues/97681)
to the [unified gateway runtime](https://github.com/NousResearch/hermes-agent/pull/106742).
It keeps the gateway responsible for execution instead of adding another
session runner in Desktop or the messaging adapters.

**This is a development checkpoint, not a release build.** It is based on
runtime `f9a5ccbd83f4` and its merged main `de2d6a1b9350`. The original source PRs remain
separate review units; this branch does not change their readiness or the
maintainer's review schedule. Use disposable profiles and matching client and
gateway revisions when evaluating it.

## What Is Available

| Journey | Current preparation |
| --- | --- |
| Create a group across gateways | Select existing Bots from their saved connections. Setup preserves one group identity and the selected installations across interrupted responses. The coordinating gateway must use its default profile; peers need direct reachability. |
| Close and reopen Desktop | Accepted work belongs to the gateway. Desktop reads the same ordered history when it reconnects. This is not automatic takeover if the gateway itself fails. |
| Share and retrieve files | Shared inputs, version-aware catalogs, search and scoped downloads are carried over. Output publication supports local default/named producers and default-profile peers. Named peer outputs remain a parity gap. |
| Check in from messaging | Explicitly authorized chats can view groups, send input, retrieve outputs and handle scoped approvals. Telegram has a reply-based compose flow; native choices depend on adapter support. This supplements Desktop rather than replacing its full group experience. |
| Reopen retained classic history | Old history and local file copies stay readable. Already-published file references can also be fetched from their authorized original producer; missing or unbound sources remain unavailable. No replacement session is created for a download. |
| Preserve evidence on another host | Opted-in participant gateways can retain authenticated history and work records. Recovery preview and custody checks remain non-executing; promotion, takeover and automatic continuation are not enabled. |

Each Bot retains its own working context, tools and credentials. Files enter the
shared space explicitly: mentioning one Bot does not make a group attachment
private, and sharing does not copy every file onto every participating gateway.

## Access And Recovery

Group ownership remains tied to the authenticated creator. The runtime's new
operator access to ordinary local sessions does not automatically share Groups
between different login identities. The room owner explicitly grants messaging
access; command-admin status alone does not grant all rooms or their files.
Shared messaging chats also require an audience confirmation.

Document lifetime across cleanup is undergoing a separate upgrade-migration
review. The intended boundary protects existing shared inputs without preventing
cleanup of unrelated new uploads. This checkpoint is not yet a document-retention
release candidate.

An unavailable gateway is not evidence that accepted work never ran. Setup and
publication preserve their original identifiers so an interrupted acknowledgement
can be reconciled instead of creating another group or repeating a task.

Named coordinators, named peer file outputs, managed-worker file publication,
messaging Stop/Retry/discard and active host-loss recovery are not claimed as
finished by this checkpoint. Existing authored implementations and evidence are
retained while their canonical-runtime contracts are adapted; an inactive port
is not presented as a working feature.

Older producer-held files require an already-authorized original session and
storage owned by that same default-profile gateway. Deleted or unbound sessions,
ambiguous history and named-profile files held by another owner remain unavailable.

## Source Ownership

- [#99107](https://github.com/NousResearch/hermes-agent/pull/99107), [#99960](https://github.com/NousResearch/hermes-agent/pull/99960) and [#100016](https://github.com/NousResearch/hermes-agent/pull/100016): authority, admission and connection reliability.
- [#97846](https://github.com/NousResearch/hermes-agent/pull/97846): Desktop continuity and setup.
- [#98072](https://github.com/NousResearch/hermes-agent/pull/98072), [#99159](https://github.com/NousResearch/hermes-agent/pull/99159), [#104198](https://github.com/NousResearch/hermes-agent/pull/104198) and [#104199](https://github.com/NousResearch/hermes-agent/pull/104199): shared files, outputs and retrieval.
- [#98073](https://github.com/NousResearch/hermes-agent/pull/98073): messaging control; [#98307](https://github.com/NousResearch/hermes-agent/pull/98307) retains field integration and client evidence.
- [#104601](https://github.com/NousResearch/hermes-agent/pull/104601), [#105079](https://github.com/NousResearch/hermes-agent/pull/105079) and [#105197](https://github.com/NousResearch/hermes-agent/pull/105197): passive preservation and recovery sources.

Original authorship and source-commit references are preserved. Generic runtime
repairs belong in [#108594](https://github.com/NousResearch/hermes-agent/pull/108594)
(already merged into the runtime branch) and its focused follow-up
[#109338](https://github.com/NousResearch/hermes-agent/pull/109338), rather than
being hidden inside a Files or recovery layer. These are semantic ports where
the runtime changed, not claims that entire source PRs have been absorbed.

The runtime now incorporates unsupportedpastels' authored
[#109403](https://github.com/NousResearch/hermes-agent/pull/109403), which refreshes
Desktop conversations and mounted tiles after missed events, and the viewer-detach
work in [#109404](https://github.com/NousResearch/hermes-agent/pull/109404), including a
[small ordering repair](https://github.com/dokterdok/hermes-agent/commit/e30c00250bb64ecdf34dbae6de5a312103d770f0)
so a stale reply cannot disconnect the winning terminal view. This preparation
keeps the maintainer's reconciled versions rather than applying competing copies.

## Verification Boundary

The F9 composition passed 258 Desktop journal/Group-creation checks across 12
files, 165 Files UI checks across 9 files, and the renderer typecheck. Selected
Python checks passed for Files, messaging access/views and API contracts
(104 checks), plus media/API/peer retention (28 checks), without automatic retries.
Independent reviews cover the consequential repairs; these counts overlap other
recorded checks and are not presented as unique programme totals.

Earlier classic-file verification also fed the actual serialized backend response
through the Files consumer to download initiation after explicit native adoption.
No hidden adoption is part of that read path. The earlier 1,110-check Python and
383-check Desktop checkpoint predates F9 and is not a full-suite result for this
revision. Document migration remains a separate review gate.

These are focused integration and component results, not a green full repository
suite or new signed-installation, physical-host failure or live-client acceptance.
Recorded field testing on the original PRs remains revision-specific. The
remaining unsupported paths above need their own implementation and acceptance
before this branch can replace the field build.
