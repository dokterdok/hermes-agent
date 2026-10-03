# Group Chat host loss: copies, voters and moving a group

A Group Chat has one host: the installation that orders its log and dispatches its turns. This page
explains how a group survives losing that host, and then documents the contracts that keep every
copy of its history, decide who may continue it, and protect what the host acknowledged. The
moves themselves (fencing, the four kinds of proof, reconciling accepted work) build on these
contracts.

:::note Where each part lands
- [#99107](https://github.com/NousResearch/hermes-agent/pull/99107): the verified-transition marks
  and the four proof kinds.
- [#104601](https://github.com/NousResearch/hermes-agent/pull/104601), the contracts on this page:
  a copy on every member, custodians and successors, voters and modes, majority protection,
  configuration changes one at a time, heartbeats and the lease layer's hook points, following a
  group across moves, catch-up from any custodian, and retiring copies after a move.
- [#105079](https://github.com/NousResearch/hermes-agent/pull/105079): fences at the participants,
  one promise per epoch, host leases and the sleep-aware clock.
- [#105197](https://github.com/NousResearch/hermes-agent/pull/105197): the moves themselves
  (handover, majority certificate, careful evidence, one tap), reconciling accepted work, the status
  contract, the CLI and the notices.

Until #105079 and #105197 land, a group already keeps copies, voters and protection, but nothing
moves it: the takeover gate stays closed and an unproven change of host stays quarantined.
:::

## At a glance

- Every member computer keeps the full ordered history of the group, with an explicit opt-out.
- When the host goes away, the group continues on another computer the owner allowed. It moves
  **automatically** where that can be made safe, and **on one tap** otherwise.
- A move fences the old host, catches up from the most complete copy, and reconciles accepted
  work, so nothing runs twice. A returning host rejoins as a copy.

```text
 Host goes silent
   |
   +-- planned (stop, sleep, "Move to...") ---> old host signs a HANDOVER --> standby continues
   |
   +-- 3+ voters ("majority" mode) ------------> host's lease runs out (~20 s); a cut-off host stops itself
   |                                             standby collects promises from a majority (CERTIFIED)
   |                                             --> continues within about a minute; never two hosts
   |
   +-- exactly 2 voters ("careful" mode) ------> 180 s of silence in both directions, and the standby is online
   |                                             standby signs EVIDENCE --> continues; the owner is warned
   |
   +-- owner chose "Ask me first", or no ------> owner taps "Continue on <best computer>" (ATTESTED)
       always-on standby ("ask" mode)

 Every path: fence the old epoch at participants -> adopt the most complete copy ->
             marked authority.transition -> reconcile accepted work -> old host returns as a copy
```

*Voters* are the host plus the always-on computers that may host the group: designated by the
owner and allowed by their own operator. Laptops can continue a group when asked, but never vote.

```text
  Clients: Desktop · messaging · CLI   (attach only; status and actions go to the host)
                         |
                         v
      +---------------- HOST (epoch N) ----------------+
      |  orders the log, dispatches turns               |
      |  majority mode: runs only while it holds a lease|<-------+ lease grants
      +--------+-----------------------------+----------+        | (voters only)
               | pages + heartbeats, authenticated by room       |
               | grants: voters about every 5 s, others          |
               | each minute                                     |
               v                             v                   |
      +-- VOTER COPY ----------+    +-- VOTER COPY -----------+  |
      | always-on, may host    |    | always-on, may host     |--+
      | full ordered log       |    | full ordered log        |
      | grants lease; fences   |    | grants lease; fences    |
      +------------------------+    +-------------------------+
      +-- COPY (laptop, guest's computer, custodian-only backup) ----+
      | full ordered log; no vote; refuses old-epoch work after a move|
      +--------------------------------------------------------------+
```

## Decisions, and why

| Decision | Why | Rejected alternatives | Lands in |
|---|---|---|---|
| **Every member computer keeps the full ordered log**, with an explicit opt-out. The owner can add backup computers that hold a copy without a Bot. | Any eligible survivor can continue with the complete history. | Three fixed coordination hosts; opt-in copies. | #104601 |
| **One host per epoch.** A host change is accepted only with a verified, marked `authority.transition`. Anything unmarked stays quarantined. | Every move is single-owner, monotonic and auditable. | Leaderless or CRDT logs. | #99107 |
| **Old epochs are fenced at the participants**, with one promise per epoch. | An old host's late work is refused wherever it lands. | Trusting the old host to stop. | #105079 |
| **Successors are owner-controlled.** A computer can host only if its operator allowed it *and* the owner designated it. | A group never moves somewhere nobody chose. | Algorithmic picks among all members. | #104601 |
| **Majority mode (3+ voters):** the host keeps running only while a majority grants its lease, and a voter never backs a takeover while its grant is live. | Any two majorities share a voter, so two hosts can never both start work. Nothing hosted is needed. | An external lock service; a hosted referee by default. | voters: #104601; leases: #105079; moves: #105197 |
| **Careful mode (exactly 2 voters)** is the default for two voters; the owner can switch to "Ask me first". | With two computers a dead host and a cut link look the same. A group that stalls unnoticed is likelier, and usually worse, than a rare split that is visible and ended fast. | Always asking; moving after a plain timeout. | mode and switch: #104601; the move: #105197 |
| **Voters change one at a time**, each change stored on a majority of the old and the new voters before the next. | No configuration change can leave two majorities that disagree. | Changing the voter set freely. | #104601 |
| **Durability in majority mode:** a task is dispatched only after a majority stores its admission, and a send reports `protected: true` once a majority stores it. In the other modes the tail is shown at risk. | Nothing acknowledged is lost on an automatic move, and the successor knows every dispatched task. | Asynchronous copies with silent loss. | #104601 |
| **Room authority is not session authority.** A move changes only who orders the log and dispatches turns. Each Bot's sessions stay with its own computer. | It stays compatible with one gateway per installation. | Moving Bots or their sessions. | #104601, #105197 |

## Why it is safe

- **Never two hosts in majority mode.** A host must hold lease grants from a majority of voters,
  and a voter refuses to promise a takeover while its grant is live. Any takeover majority shares
  a voter with the host's majority, so a takeover completes only after the old lease ended.
- **Nothing confirmed as saved is lost in majority mode.** A send is confirmed (`protected: true`)
  only once a majority of voters stores it; the successor's majority includes a holder, and
  catch-up adopts the most complete copy.
- **Nothing runs twice.** In majority mode a task is dispatched only after its `task.admitted` is
  stored on a majority, so the successor knows about it. Unknown outcomes are shown and never rerun.
- **A copy can't take over by accident.** A copy follows a new host only through transitions it
  verified, signed with per-installation room identity keys pinned from the room's own records.
  The host's own pages are authenticated by room grants; only catch-up pages from another
  custodian are signed with room identity keys.
- **Changing voters can't open a gap.** A configuration that drops a voter takes effect only once
  a majority of the old voters stores it, so a cut-off standby that still holds the older
  configuration never finds a majority that follows it.
- **A paused host appends nothing.** While the host has no lease (or hands the group over), it
  writes no configuration and admits no work, so a partition can't create post-split events.

The rest of this page documents the contracts in [#104601](https://github.com/NousResearch/hermes-agent/pull/104601).
The marks are in [#99107](https://github.com/NousResearch/hermes-agent/pull/99107), the fences and
leases in [#105079](https://github.com/NousResearch/hermes-agent/pull/105079), and the moves in
[#105197](https://github.com/NousResearch/hermes-agent/pull/105197).

## Custodians and successors

Every member installation is a **custodian**: its member grant carries `replicate` unless its
operator opts out (`groups.peer.invite` with `replication: false`), and the host copies the whole
history there, one bounded page at a time. Several Bots on one installation share one copy. An
owner can also add a **custodian-only** installation, such as a backup VPS without a Bot:
`groups.peer.invite` with `custody_only: true` there mints a copy-only grant (member id
`custody:installation`, permissions `replicate` and `status`, never `dispatch`), and
`groups.custody.add {room_id, target_url, catalog, grant, successor?}` on the host enrolls it after
a live probe. `groups.custody.remove {room_id, install_id}` stops copying there.

A custodian is a **successor**, one that may continue the group, only when both are true:

- its own operator allowed it: grant permission `successor` (`groups.peer.invite(successor=true)`),
  or later `groups.custody.allow {room_id, successor}` on that computer;
- the room's owner designated it: `groups.custody.designate {room_id, install_id, successor}` on the
  host. Designations keep their order: that is the owner's order of successors.

Each installation reports whether it is **always on**: it has no battery, unless its config says
otherwise with `group_chat.always_on: true` or `false`. A computer whose battery state can't be
read is not always on. The report rides on the capabilities probe and on every acknowledgment.

| Method | Where | Who | Result |
|---|---|---|---|
| `groups.custody.status {room_id}` | host or copy | `session:read` | custodians, voters, protection (below) |
| `groups.custody.designate` | host | the room's owner (`session:control`) | `{room_id, install_id, successor, configuration_seq}` |
| `groups.custody.add` / `.remove` | host | the room's owner (`session:control`) | `{room_id, install_id, configuration_seq}` |
| `groups.custody.allow` | the custodian | its operator (`session:operator`) | `{room_id, install_id, allowed, confirmed}` |
| `groups.custody.automatic {room_id, enabled}` | host | the room's recorded owner or the operator; else `not_owner` | `{room_id, automatic, configuration_seq}` |

Errors are JSON-RPC `4001` with `error.data.reason`, such as `room_custody_invalid`,
`peer_target_mismatch`, `not_owner` or `invalid_params`.

## Room identity keys

Each installation has one Ed25519 room identity key, derived by a domain-separated HMAC from the
RoomLink secret in its installation root. Every profile, and one multiplex gateway serving them,
signs as the same installation, and no new private key is stored. Signatures are
`ed25519-v1.<base64url>` over `domain + "\0" + canonical JSON`. Keys are pinned per room and
installation: the host pins a member's key from its authenticated probe, and every custodian pins
the keys its copy's configurations name. A different key for a pinned installation is refused,
never adopted (`hosted_room_identity.verify_locked`).

## The configuration: `custody.configured`

The host records the room's custodians in the room's own log, so every copy carries them. The
event (system actor `custody-control`, id `system:custody-configured:<n>`) has exactly this payload:

```json
{
  "custodians": [{"install_id": "install:…", "public_key": "<hex>", "endpoint": "https://…",
                  "role": "authority | custodian | custodian_only", "successor": true,
                  "always_on": true, "voter": true, "name": "Mac mini", "operator_name": "Dana"}],
  "owner_name": "Dana",
  "automatic": true,
  "voters": ["install:host", "install:…"]
}
```

- Custodians are sorted by `install_id`, with exactly one `authority`: the current host, never its
  own successor. Names are display labels only, never identities.
- `voters` is the host first, then its always-on successors in the owner's order, at most seven.
  `voter` marks exactly those.
- `mode_of(configuration)` is `majority` with three or more voters, `careful` with exactly two, and
  `ask` with one or when `automatic` is off.
- **One change at a time.** A configuration changes at most one voter, or the automatic switch,
  and the next change waits until a majority of both the voters before and after it stores it.
  `voter_sets` in the status names the sets whose majorities count now: two while a change is not
  settled. Names, endpoints and non-voting custodians change at once.
- After a verified move, the new host appends its first configuration with
  `reconfigure_after_transition_locked`: it becomes the authority and the first voter, the previous
  host stays a custodian that neither continues nor votes, and every other custodian keeps its
  fields, the voters their order, and the group its automatic switch.

## Watermarks, protection and the tail at risk

Each copy, and the host's own room, has a durable watermark `(epoch, seq, event_hash)`, where
`event_hash` chains every event of that exact prefix (`sha256` over a domain-separated genesis
and each normalized event; checkpoints every 128 events keep it bounded). A custodian acknowledges
every page with its watermark, written in the same transaction as the page, and the host keeps an
acknowledgment only when it matches its own chain: a divergent copy is never counted and stops
its route, and an older Hermes without watermarks is `unsupported` and never counted.

- `at_risk_after_seq` is the highest seq that at least one successor holds. Every later event is at
  risk of being lost with the host, and clients say so.
- `protected_seq` is the highest seq that a majority of every current voter set holds, counting the
  host. `wait_protected(room_id, seq, timeout)` waits for it.
- **Majority mode waits.** A queued task runs only once a majority stores its `task.admitted`; until
  then `waiting_for_copies {task_id, seq}` names it. `groups.send` waits up to `min(10 s, the lease
  left)` and returns `protected`. An unprotected send stays in the log, inside the tail at risk,
  and clients keep offering it after a move, deduplicated by its event id.
- **Other modes never wait.** They return `protected` for the current state.
- Each dispatch decision appends `task.admitted` (system actor `room-driver`, id
  `system:task-admitted:<sha256(task_id)[:32]>:<generation>`) when the room has more than one
  custodian, so a successor can reconcile it.

`groups.custody.status` returns, on the host, every custodian with `role`, `state`, `successor`,
`voter`, `always_on`, `allowed`, `designated`, its verified `watermark`, `acknowledged_at`,
`last_seen` and `divergent`, and for the group `at_risk_after_seq`, `protected_seq`, `automatic`,
`voters`, `voter_sets`, `mode`, `waiting_for_copies` and the configuration. On a copy it reports
the configuration it holds and what the host last reported.

## Heartbeats and the lease layer's hooks

A caught-up custodian still hears from the host: a voter about every 5 seconds, any other
custodian every minute, as an empty page carrying the custody report. The lease layer plugs in
through `hosted_room_custody.register_lease_hooks()`; until it does, none of these do anything:

| Hook | Called | Contract |
|---|---|---|
| `lease_request_provider(room_id)` | on the host, building each push to a voter | returns `{epoch, duration_s, until?, sent_at}` or None; attached unchanged as `lease_request` |
| `lease_grant_hook(room_id, epoch, authority_install_id, request)` | on a voter, after it stored a push from the host it follows | returns `{granted_until_s}` or `{refused, events?}`; sent back as `lease_grant` |
| `lease_ack_hook(room_id, voter_install_id, lease_grant, sent_at)` | on the host, for each acknowledgment | `sent_at` is the request's own, taken before the send |
| `lease_remaining_provider(room_id)` | on the host, for a send's wait | seconds the majority lease still holds, or None |
| `serving_provider(room_id)` | before any append here | False while the host is paused: no `custody.configured`, no `task.admitted` (`room_host_paused`) |

## Following a group across moves

A copy follows a new host only through transitions it verified (`ingest_page(...,
_verify_transition=fn)`). Pages are verified in order, inside one writer: the events before each
`authority.transition` are stored first, with the custodians of any `custody.configured` among
them pinned, then `fn(conn, event)` checks the transition against that history and marks it, and
only then is it inserted. A page that carries a move, the new host's configuration and a later
move verifies the later one against the configuration written before it. Every other event must
belong to its span: its epoch, and for a gateway actor that span's host. A page may lag its
sender: a new host relays the old host's history before its own transition, and the copy moves
only when the transition arrives. Without a verifier, a change of host is refused.

## Catching up from any custodian

When the host is gone, the most complete copy may be anywhere.
`fetch_custodian_pages(db, room_id=, source_install_id=, after_seq=, limit=)` asks another
custodian for a page of its history at `POST /v1/room-members/custody/pages`. The request names
both installations, a nonce and its issue time, and is signed with the requester's room identity
key (`hermes.group.custody.pages.v1`). The source answers only an installation its own
configuration lists, within five minutes of issue, and signs its reply over the nonce
(`hermes.group.custody.pages-reply.v1`). `ingest_custodian_page` stores the page with every check
a host's page gets. Catch-up resumes from the copy's own watermark, from any custodian, and never
needs the host.

## Retiring copies after a move

A participant's operator can enroll the retirement of its copy with the room's first home
(`groups.replication.prepare` there, `groups.replication.enroll` here); Disband then retires the copy
with a notice signed by that enrollment's key. Once the group moves, that home's notice is refused:
the copy no longer follows it. The host the copy follows now inherits the obligation instead. The
participant's probe reports the enrollment to the copy's current verified authority (the successor
named by the latest marked transition in the copy's own log), that host's Disband closes it, and its
notice is signed with its room identity key (`hermes.group.replica.retirement.authority-notice.v1`)
and checked against the key the copy pinned for it. Every other check stays: the current active
enrollment, the destination installation, the copy's namespace and roster, a read-only signature
check before the writer and again inside it, and idempotent receipts.

## Reading a copy

`groups.list`, `groups.state` and `groups.log` show a copy as a read-only room (`copy: true`,
revision 0, no driver) to the installation's operator or to the room's recorded owner there.
Nobody else sees it, and a copy is never written to.

## One Gateway compatibility

- **Installation scope.** `install_id` and room identity keys come from the installation root, so
  per-profile gateways and one multiplex gateway are the same custodian, with one copy.
- **Room authority is not session authority.** Custody moves no sessions; a move changes only who
  orders the room's log.
- **Room logs keep their continuous prefix.** A copy is a hash-chained, contiguous prefix of the
  room's log within the room's budget. The relaxations that session replay may adopt ("a valid
  replay or an authoritative snapshot") don't apply to room logs.
- **Mixed versions.** An installation without room identity keys or watermarks is `unsupported`:
  shown, never counted, never a voter.

## Limits

- A copy started after the room moved learns its first host from the history it replays; until
  its copy reaches the first move, its own gateway-actor checks are by consistency within each span.
- Not built: a content-free referee vote, an automatic move back to the original host, and votes
  from laptops.
