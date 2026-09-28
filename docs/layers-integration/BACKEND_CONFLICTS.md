# Backend conflicts returned to Barry

No file below was hand-merged. The assembly tree keeps the cb8 blob. Causal
notes are for the backend owner. v2 accepts Permission `3b0d88e044` and
Messaging product `8f5338e6` (recipe head `eea4a0c96d`) for the control
slice. Those commits are not on this tree. The Desktop client can form the
public calls (`PRIVATE_CONTROLS.md`); cb8 does not implement them. A3 stays
open. Session policy, runtime custody, Route `8a29b6d`, Retention F1, and
R3 retry keywords stay here.

## 1. `gateway/session_policy.py`

| Tree | Blob |
|---|---|
| cb8 (kept) | `5aa15e8e8fc19d31b7156e93515d59aab94fb52c` |
| 6f2 | `89a774fe71a5e5d6ae2ad95909a5c6a88ff1d709` |
| Output recipe / runtime #111216 whole file | `1718a83bde77b81ee137f46dce7525660a3d2eb6` |

cb8 adds `present_sections()` and uses it in `LocalSessionPolicy.config` and
`build_policy`. A bare `gateway:` key parses as YAML null. Dropping that
filter makes `cfg.get('gateway', {}).get(...)` throw on a fresh install
(#58277). The recipe whole-file copy does not have `present_sections`.

The recipe file calls `runner._delivery_adapter_for`. cb8 and 6f2 call
`runner._adapter_for_source`. On cb8, `gateway/run.py` defines
`_adapter_for_source` as `return self._delivery_adapter_for(source)`. Those
calls match only while that alias stays. Replacing the file with runtime blob
`1718a83bde` drops null-section normalization. Keeping cb8's file drops a
direct call that is currently an alias, not a second resolver.

## 2. `hermes_state_runtime.py`

| Tree | Blob |
|---|---|
| cb8 (kept) | `48960a91fb2c669bd2aa311960381ca7a4415dc6` |
| 6f2 | `d6c62ce8001782ff4f1ad92142b48b1453899d5a` |
| Recipe splice on the 6f2 candidate (`8c6a1fce`) | `0697186bfe4d550e81660915ca319b16e6c7d91b` |
| Runtime pin before that splice | `238b5baeadd1429a500eee7882620ac4fa0606cb` |

cb8 records `CANONICAL_ROW` and `DB_ROW_SNAPSHOT` on `_MESSAGE_FIELDS`.
`row_annotations()` emits `_row_id`, `timestamp`, `DB_ROW_SNAPSHOT`, and
`CANONICAL_ROW`, using `None` when the key is absent. `_worker_append`
returns that list. `agent/runtime_session_store.py` on cb8
(`1392ff1056fcebbc9861d9ef7053777552cd18bb`, also kept) applies it by
deleting keys whose annotation value is `None`. A plain `dict.update` leaves
a stale stamp in place. That worker helper is not on 6f2.

The recipe splice adds `input_custody` and `_authorize_write` on
`admit_session_input`, and `_terminal_write` on `settle_session_input`,
`cancel_session_input`, and `resolve_unknown_session_input`. cb8's signatures
do not take those arguments. The splice's worker annotations are
presence-only (`_row_id`, `_canonical_content`).

Copying the recipe file onto cb8 drops removal-based annotations. Copying
cb8's file forward drops the Input custody and Output terminal guards. Both
have to remain. That join is backend-owned.

## 3. Route `8a29b6d` versus the output recipe's route spans

`8a29b6d13bc558221130b4216a93f373afa68caf` is one commit past
`1fa3c0addd0c3eec671f3019c443dd3e449db134`: delegated Stop snapshots the
attempt at fence admission (`authorize_new` / `authorize_commit` /
`existing_only` on `request_room_stop` and the existing-receipt probe).

The output plan at `23c5e66` still names route `1fa3c0`. Its spans for
`gateway/hosted_rooms.py`, `gateway/hosted_room_driver.py`, and
`tui_gateway/hosted_room_driver.py` are line ranges of the 1fa3 blobs
(`86a366c81c`, `e6ae4af92c`, `dfd2a8c766`). 8a29 replaces those blobs
(`e069158fd6`, `2b7f8faa66`, `dd33a6636d`) and adds
`gateway/hosted_room_delegated_control.py` plus
`tests/tui_gateway/test_hosted_room_delegated_control.py`.

`git diff --stat 8c6a1fce 8a29b6d --` those spliced paths is 486 insertions
and 378 deletions. Pasting 8a29's six paths over the recipe keeps the Stop
fence and drops the recipe's retention, output, and runtime spans inside the
same files. Re-cutting the spans at the new line numbers is the same class
of join. Not done here.

Related seam, same owner: the recipe's `hosted_rooms.py` splice stops the
route copy before `room_state(..., conn=)` and pastes the runtime function,
which does not take `conn`. Route `session_group_state.py` passes `conn`.
On the 6f2 candidate that raised `groups.state` `invalid_params`. Commit
`6a3a155402` on `cursor/barryx-layers-integrate-52cc` put `conn` back. That
commit is not on this branch. 8a29 does not change `room_state`; the
parameter already exists on the route file and is absent from cb8 blob
`d959f06ebc22d9c6ad5b459a81dbc4cab9192bde` (the runtime blob the recipe
cites). Restoring it without the rest of the route/recipe splice is a
private gateway repair and was not repeated.

cb8 `hosted_rooms.py` stays `d959f06ebc22d9c6ad5b459a81dbc4cab9192bde`.

## 4. Retention F1 `c9f0029` versus recipe `004015d`

Accepted supplier `c9f0029475f085e3b5e66b77df74cd5470925aef` is one commit
past recipe pin `004015d6087fe031231c4d7d9e0032cc59b679eb`. The commit changes
`gateway/hosted_room_replicas.py`: `_replica_transaction(db_path, _authorize=None)`
runs `_authorize` inside the single immediate transaction, before
`_initialize_replica_schema` and before `_audit_existing_replicas_locked`.

The output recipe still splices runtime blob `505404179ccc` (double schema
init: prelude, then again inside the immediate transaction) with retention
audit lines from `004015d` blob `e59558eb2c`. That splice has no
`_authorize` parameter.

Historical commit `0530995a6f` on the 6f2 branch added an optional callback
around the runtime double-init and did not move the audit into that writer.
That is not the F1 function. It is not on this branch. cb8
`hosted_room_replicas.py` stays `505404179ccc1ff50182b9a1707f35ef7bfb9a50`.

The F1 delta still has to be carried. The union of authorize-before-audit
and the runtime double-init is Barry's join.

## Not a supplier

Local Stop ACK `8461351e856450f33f6ed80a41b0d7e81b47fa59` and importer
`cc69090b8875f6c338fccb09d45f0cf2dfa8440b` were not published onto #106742
and were not copied here.
