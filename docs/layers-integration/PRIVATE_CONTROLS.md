# Private controls — client wiring only

v1 `CONTROLLER_INPUTS` stays the immutable baseline. v2 replaces the control
slice pins only. This file records what the Desktop consumer calls. It does
not apply those commits onto cb8, and it is not cb8 acceptance.

## Pins consumed

| Slice | Pin | What the client uses |
|---|---|---|
| Permission #111939 | `3b0d88e044f4a689def4ae1fee45186aafb51e11` | Replaces v1 `34332b47` for this slice. Native operator methods `groups.messaging.room.stop.grant`, `groups.messaging.room.stop.revoke`, `groups.messaging.room.approval.grant`, `groups.messaging.room.approval.revoke`. |
| Messaging #98073 head | `eea4a0c96d321bfd9a03705627f7f0d6a6280f40` | Public recipe `docs/messaging-private-journey/compose_private_controls.py`. |
| Messaging product | `8f5338e6a7699c615112e2e2ef50136f31f8e466` | Private grammar `/group room-ref stop` and `/group room-ref approve pa-selector once\|deny`. |
| Route #100016 | `8a29b6d13bc558221130b4216a93f373afa68caf` | Unchanged. Not applied here. |

The recipe still starts from Messaging `d4d9f905e8123eea38ad81c4cdd6ac44257315d8`
plus the Route six paths plus permission and consumer overlays. Full source
pins only. There is no local-owner replay mode in this client.

Supplier notes under `layers-private-controls-20260928/` and any gate log are
supplier evidence. They are not this combined client, and hosted CI is not
claimed passing.

## What the client sends

Shared contract: `apps/shared/src/private-controls.ts`. The bots plugin keeps
a behavior-matched copy at `apps/desktop/src/plugins/hermes-bots/private-controls.ts`
because plugin modules cannot import `@hermes/shared`. Desktop SDK re-exports
the shared module for callers outside the plugin.

- Displayed approval identity is `pa-` plus 64 hex digits. The client does
  not recompute that hash and does not send the selector on `groups.approve`.
  A list position such as `1` is rejected before any RPC.
- `groups.approve` body is `room_id`, `member_id`, `task_id`,
  `execution_generation`, `request_id`, and `choice` of `once` or `deny`.
  The row is chosen only when exactly one displayed action has that selector.
  An approval row with no `pa-` selector renders no Allow once and no Deny.
- Native Stop is `groups.stop` with `room_id` and a UUID `cancel_id`. That
  cancel id is not a `pa-` selector and not a digit list position. Stop does
  not grant or revoke consent.
- Grant and revoke are the four `groups.messaging.room.*` methods. They carry
  the persisted recipient (seven keys, `runtime_profile` `default`), the
  `mrr-` room-read binding, and generations. Revoke also sends the `mrc-`
  control binding id. A stop grant does not approve, and an approval grant
  does not stop.

`CanonicalGroupWorkspace` shows the consent buttons only when the caller
passes `operatorControl` (recipient plus room-read binding). The workspace
does not invent a messaging recipient. cb8 `groups.state` does not emit
selectors, so Allow once and Deny stay closed until a joined backend returns
a real `pa-` token. Calls to the four consent methods fail closed on this
tree because cb8 does not register them. That is not an A3 pass.

## Still with Barry

No file in `gateway/`, `tui_gateway/`, `hermes_state*.py`, or `agent/` was
edited for this wiring.

- Join Permission `3b0d88e044` and Messaging product `8f5338e6` (recipe head
  `eea4a0c96d`) onto cb8, including `gateway/session_policy.py` if that join
  touches it. Preserve cb8 `present_sections` and the annotation/absent-clearing
  behavior. Barry's bounded join lives in
  `/opt/data/workspace/layers-runtime-target-join-20260928/`.
- `hermes_state_runtime.py` input custody / `_terminal_write` versus cb8
  None-absent annotations. See `BACKEND_CONFLICTS.md`.
- Route `8a29b6d` six paths. Still open. Not pasted here.
- Retention F1 `c9f0029475f085e3b5e66b77df74cd5470925aef` versus recipe
  `004015d`. Still open.
- R3 retry keywords. `BACKEND_RETURNS_R3.md`. Desktop still sends `member_id`
  and `execution_generation` on `groups.retry`.

## What this is not

The two-gateway cb8 journey was not run. The 6f2 sibling-gateway recovery is
not a cb8 result. A3 is not closed: the client can form the public calls, and
this gateway does not implement them. A1–A7 are not closed. #98307 was not
published.
