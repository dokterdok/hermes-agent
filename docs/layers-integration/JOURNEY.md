# First vertical journey

Required shape: a hosted group whose members are two independently identified
gateways; a real client admits file work; the disposable client closes; gateway
work continues; a fresh client recovers the same ordered conversation, the
same members, and the exact file bytes for each version.

## Kept from the 6f2 candidate

`cursor/barryx-layers-integrate-52cc` at `5202c7d1f214b10d62a08e2e9c09c36c8bc4f2ce`
ran `tests/gateway/test_sibling_gateway_file_recovery.py` once from that
checkout and once from a bundle import. Both passed.

That run is Linux, two processes, separate homes and install ids, loopback
`RoomModel` (deterministic, not a provider), authenticated gateway sockets,
and `groups.attachment` on one room. It is not installed Electron, not native
Windows, not two physical machines, and not RoomLink attachments. It is not
a cb8 result. The gateway edits in that history (`hosted_rooms.py` connection
borrow, replica callback) are not on this branch.

## Continued here

Desktop `CanonicalGroupWorkspace` reads `room.members` from the existing
`groups.state` payload, in supplier order, and a new connection id remounts
the room. Same-name files stay distinct because download uses `attachment_id`
and `event_id` from `groups.log`. The vitest
`canonical-group-fresh-client.test.tsx` covers that client contract.

It does not start two gateways, does not prove bytes on disk, and does not
close A1–A7. A3 Stop/approve/deny consent stays open while Permission
#111939 and Messaging controls have no accepted successor. The workspace
still calls the existing `groups.stop` / `groups.approve` methods only;
nothing in this change substitutes for a missing consent RPC.

Explicit Retry stays a client send of `groups.retry` with `member_id` and
`execution_generation`. The gateway rejection of that call is
`docs/layers-integration/BACKEND_RETURNS_R3.md`. The button does not drop
those fields.

A fresh connection follows `groups.log` pages. The next request uses the last
event `seq` as `since_seq`. A page that does not advance that cursor shows
the invalid-cursor alert and does not publish the partial page or the member
list. A later binding on another room downloads only that room's
`attachment_id`. These checks are mocked `requestProfile` calls.

Native Windows Save/Cancel stays held. Host-loss successor (A7) stays with
Barry.
