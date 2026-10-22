# Gateway-owned groups in Desktop

A Group Chat is either a **gateway room** (the gateway owns its log and runs its turns; `canonical-group-*` in `src/plugins/hermes-bots/`) or a **classic room** (Desktop runs its rounds; `group-*`). Opening a gateway room captures its exact authority (connection, profile, room id); changing the foreground profile does not retarget its controls.

## Which rooms are gateway rooms

One classifier, `groupExecutionMode`, reads `groups.capabilities`. Two surfaces answer it: the gateway's own (`gateway/session_group_controls.py`) and the standalone server's older one, reached by SSH, URL and Cloud connections (`hermes serve`, `hermes dashboard`; `tui_gateway/methods_groups.py`).

- **Gateway rooms:** an answer with `driver: true`, from either surface. Both run their own room driver, so these rooms keep working while Desktop is closed. With the driver stopped they are unavailable until it returns.
- **Classic rooms:** a server without the group surface at all (`-32601`), or one that reports no room driver and owns no rooms.
- Anything else (a transport error, a malformed answer) is unavailable, with **Retry**.

A gateway room offers a control only when the answer's `methods` lists its method: Discard (`groups.discard`), Retry (`groups.retry`), Allow and Deny (`groups.approve`), file upload and download (`groups.attachment.upload`, `groups.attachment.download`), Rename (`groups.rename`) and End group chat (`groups.disband`). The standalone surface lists no `groups.discard` and no attachments, so those controls do not appear there. Until the answer arrives none of these controls is shown; an answer without a method list keeps them all.

New Group Chat on a gateway connection offers only the Bots the gateway says it will seat (`seatable_profiles`) and creates a gateway room. When the gateway refuses, Desktop shows "The gateway couldn't create this group chat." followed by the gateway's own detail, which names the member it will not seat.

A classic room opened on a gateway connection is first looked up by name in `groups.list`; a match opens that gateway room. Otherwise:

- a classic room whose Bots all live on other connections keeps its classic workspace (it never reaches this gateway's owner);
- any other classic room explains that Desktop ran it and offers **Start new group**, a fresh gateway room with the same Bots. Earlier messages are not copied. When those Bots cannot form a gateway group (another connection, two to six Bots, distinct profiles and handles, `@all` and `@everyone` reserved), it says why and offers nothing.

## The room workspace

The gateway owns the log and the work. Desktop polls `groups.state` and `groups.log` every two seconds while the room is visible. It reads the log after the last event it showed, and from the start again when the room's `authority_epoch` changes.

- **Status:** the header lists each member as working or queued (`driver_status.member_activity`) and, beside that, what the room waits on: a stopped driver, a blocked room, approvals, and other pending actions. A failed member never hides the others' live work.
- **Send** uses the native prepared-submission journal: one unresolved intent per room, shared by every Desktop window and written with compare-and-set. The entry records `attempted: false` until a request first carries it; the mark is written before that request leaves. A refusal of the first request (`invalid_params`, `permission_denied`, `unknown_execution` or `stale_generation`) proves nothing was posted, so the words come back for editing. A refusal of any later request, a timeout or an unconfirmed reply keeps the intent for **Retry**, which resends the same `event_id`; the gateway recognizes it, so it is never posted twice. A Send is delivered only by a receipt whose `client_event_id` is this intent's (and `accepted: true` when present). When another window holds the room's intent, Send sends that one first and puts this window's words back afterwards; when another window has already finished with it, Send does not resend it. If the journal cannot be read, Send stays locked and **Try loading again** reads it again without writing.
- **Stop** (`groups.stop`) has its own pending state, so it works while a Send is in flight. It keeps one `cancel_id` until the gateway confirms how many replies it stopped.
- **Retry, Discard and approvals** come only from `driver_status.pending_actions` and send the exact member, task, generation and request identity. Each row names its Bot. An approval shows its description and its command or proposed file change; only the choices the gateway offered appear, and **Allow once** needs this exact request with a real command or edit. Discard asks first: side effects are not undone.
- **Attachments** upload with `groups.attachment.upload` and download with `groups.attachment.download`.
- **Rename** (`groups.rename`) keeps one `event_id` per intended name across retries. **End group chat** (`groups.disband`) asks first, keeps one `cancel_id`, and removes the room from Desktop and closes its tab only when the gateway returns its tombstone.

History shows messages and the failure, stop and ownership notices; bookkeeping events (`turn.started`, `turn.settled`, `room.activity` below its work limit) stay out. Your own messages read as **You**. Room text may come from another machine: its `MEDIA:` paths, Markdown images, file links and directive cards stay inert, and an explicit web link opens only on a click.

Errors stay visible, and no failed gateway-room action falls back to Desktop-run execution. Membership editing is not offered.

## Contract tests

`canonical-groups-contract.test.ts` reads `GROUP_METHODS` and `_FIELDS` from `gateway/session_group_controls.py`, and `canonical-group-contract-test-utils.ts` wraps each canonical test's request double, so a method or field the gateway does not declare fails a test.
