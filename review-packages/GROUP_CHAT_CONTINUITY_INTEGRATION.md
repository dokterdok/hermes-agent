# Group Chat continuity integration

This commit contains the provider modules. A fresh worktree does not apply an overlay.

Fencing stays. `promote_replica` returns `executable: false`. An unfenced promotion does not admit append, lease, or driver work. With `gateway.hosted_room_safety` installed, `groups.send` is code 4111 and the quarantine reason is `unsafe_replica_promotion`. This is not host-loss recovery. Retention replicas blob `e59558eb` is not the file on this tree. `promote_replica` and `demote_room` stay.

## Pins

| Piece | OID |
|---|---|
| Runtime product | `879b3a2146eaf0f97443a7d03491756d4af11db7` |
| Runtime series tip (docs) | `ff18610351f4cd0fc9a69bd3a037bd8516f086f6` |
| Post-fencing #10 tip | `7b3b6bdbc817da3d7102a20eb5c392e121e31190` |
| Files import #11 | `a36e562f02a69c3651fcee0d2a2bb70d1724c56d` |
| Route digest #12 | `327b81637fdc35dd2864c18476122f8bc6a45825` |
| Catalog receipt #13 | `9a1540b0ccb3180c2de25871260784cabb0383e7` |
| Retention tip | `004015d6087fe031231c4d7d9e0032cc59b679eb` |
| Safety blob | `7bfb1bf04b59c52e27603370ef278ae85419ab22` |
| Splice commit | `7bca11a8869a4664ee19a2ce048e70dae10e3bea` |
| Splice SHA-256 | `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90` |
| Files tip | `b353ab32c527bd02e2f606d567da4ca324c3aaff` |
| Attachment store blob | `99586695410fe478a4fdf3c1b811eddf13a9b900` |
| Attachment store test blob | `f35bed3bc07e9223de899fc8b028d331d6e4d9ac` |
| Catalog blob | `8aab5f2c68d026262c3793428806f4f54f35fb38` |
| Catalog test blob | `e641354ff06686e8c6b5400735fab1c12cb1c5bb` |
| Route tip | `46c3ce81784406af7a742f9d79e121c3917e584e` |
| Route patch SHA-256 | `9f3d71736a9b1b998a2809b6c45c0223f7ccadf2ad9204ab0b3c7da21e22713e` |

The route patch is the digest, `target_profile`, and `revoke_grant_exact` slice. The whole links module is not checked out.

## Build and test

```text
scripts/run_tests.sh \
  tests/gateway/test_group_chat_upgrade_journey.py \
  tests/gateway/test_shipped_group_history_import.py \
  tests/gateway/test_shipped_history_attachment_import.py \
  tests/gateway/test_imported_member_retirement.py \
  tests/gateway/test_hosted_room_attachments.py \
  tests/gateway/test_hosted_room_attachment_catalog.py \
  tests/gateway/test_hosted_room_replicas.py \
  tests/tui_gateway/test_groups_replication_methods.py \
  tests/tui_gateway/test_group_history_import.py \
  tests/gateway/test_hosted_room_viewer_state.py \
  tests/gateway/test_session_hosted_rpc.py \
  tests/gateway/test_session_hosted_service.py \
  -q --tb=line
```

Use a private `HOME` and `TMPDIR`. The runner clears `HERMES_HOME`. Desktop is not part of this recipe.
