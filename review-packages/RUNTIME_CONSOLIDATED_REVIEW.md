# Runtime consolidated maintainer review

One package for the remaining Runtime-owned contribution against NousResearch #106742. It records the path boundary, the pins, what the compose drafts actually counted, and what stays held.

This file is a receipt. It does not publish #106742, does not merge a fork draft onto `main`, and does not copy Retention, Files, or Route bytes into the Runtime tree. This lane did not run `scripts/run_tests.sh`. Every pass/fail count below is copied from the named receipt, which was read at that commit. Blob identities were re-checked in this clone with `git rev-parse`.

## Live public head

Read this run from the GitHub API (`repos/NousResearch/hermes-agent/pulls/106742`):

| Field | Value |
|---|---|
| State | open, not draft, not merged |
| Head ref | `feat/unified-gateway-runtime` |
| Head | `d7f5c13d73784b4e536bf6fe0d20a48089523ec0` |
| Base | `d5785bb525e4d82848e2051f5e8bc19ab50e9934` (`main` at that read) |
| `updated_at` | `2026-09-24T17:27:30Z` |

The historical public Runtime base and the current #106742 head are the same commit. `git merge-base --is-ancestor` confirms it is an ancestor of draft #10 tip `0d5cf6fd8ffb51acb01ecacda5ab2e2028c18144`. Tree of that public head: `4a2bcb4e43bfbd426163c21a9715c46ecbe8d225`.

## Runtime commits

Author and committer on every commit below: David Dudok de Wit `<david@dudokdewit.net>`. No `Co-authored-by` trailer.

| OID | Tree | Parent | Subject |
|---|---|---|---|
| `e79420fc8fc287805a630660d99ce7ea0a47e3a4` | `a55710f40ee79ad6b9a8f25ceb8ee250f6f99547` | `d7f5c13d` | owner-scoped inert shipped group importer |
| `00c143412d264f0001037643db7f43b19233f970` | `27b724d646bb090860957739861b5eb807a3d960` | `e79420fc` | terminal publication and imported source reservations |
| `8548f70e5c642636f33047afd94cdb5e965963db` | `ed06d20f378c0c510a861e9e04b601ce9d32a610` | `00c14341` | backfill imported source reservations after retention copy |
| `879b3a2146eaf0f97443a7d03491756d4af11db7` | `d36c5415f4c7ce55d300e0a85bd6e48297147443` | `8548f70e` | admitted adoption viewer and Stop providers |
| `c82b8148ada62f01b59a934ee9f24a64cadcac94` | `88bfc4a5a87f32fcf55be0733a6436e1065b7562` | `879b3a21` | honor room-id reservations in promote and demote |
| `df890a40941b77a8122293f80f47148a08b9ff2e` | `47181f9b9f668c793d6268a8de0df12c94d75a1e` | `c82b8148` | move replica events before the authority copy |
| `0d5cf6fd8ffb51acb01ecacda5ab2e2028c18144` | `50eb979cae51aa10f8ce300229a9f1f1167fe54c` | `df890a40` | promote/demote receipt (draft #10 tip) |

`879b3a21` is the complete-owner product tip. `c82b8148` is the reservation fix and the commit that adds the promote/demote receipt. `df890a40` is the second promote/demote product commit (shared-budget order). `0d5cf6fd` changes only `review-packages/PROMOTE_DEMOTE_RESERVATION.md`. The code delta ends at `df890a40`. That tree also contains the receipt.

Draft #8 tip `ff18610351f4cd0fc9a69bd3a037bd8516f086f6` (tree `a1a89db25dbb4bde697001002f8946befa9d4025`) is a docs commit on `879b3a21` (`review-packages/FINDINGS.md` only). It is not an ancestor of #10. `git diff --name-status ff186103..0d5cf6fd` therefore deletes `FINDINGS.md`. That file is still the full-delta receipt on draft #8. #10 did not edit it.

## Exact path set versus `d7f5c13` / #106742 head

`git diff --name-status d7f5c13..879b3a21` is these 18 paths and no others:

| Status | Path |
|---|---|
| M | `apps/shared/src/gateway-contract.generated.ts` |
| M | `apps/shared/src/gateway-contract.openrpc.json` |
| M | `gateway/hosted_room_discussion.py` |
| M | `gateway/hosted_room_driver.py` |
| A | `gateway/hosted_room_member_retirement.py` |
| A | `gateway/hosted_room_viewer_state.py` |
| M | `gateway/hosted_rooms.py` |
| M | `gateway/session_group_controls.py` |
| M | `gateway/session_hosted_rpc.py` |
| M | `gateway/session_hosted_service.py` |
| A | `tests/gateway/test_hosted_room_viewer_state.py` |
| A | `tests/gateway/test_imported_member_retirement.py` |
| M | `tests/gateway/test_session_hosted_rpc.py` |
| A | `tests/gateway/test_shipped_group_history_import.py` |
| A | `tests/tui_gateway/test_group_history_import.py` |
| M | `tui_gateway/contracts/groups_bot_relay.py` |
| M | `tui_gateway/hosted_room_service.py` |
| M | `tui_gateway/methods_groups.py` |

`git diff --name-status 879b3a21..df890a40` is these three paths:

| Status | Path |
|---|---|
| M | `gateway/hosted_room_replicas.py` |
| A | `review-packages/PROMOTE_DEMOTE_RESERVATION.md` |
| M | `tests/gateway/test_hosted_room_replicas.py` |

`gateway/hosted_room_replicas.py` and `tests/gateway/test_hosted_room_replicas.py` are the promote/demote product. The receipt is added by `c82b8148`. `df890a40` does not touch that receipt; its own diff is only those two product files. `0d5cf6fd` edits the receipt only.

`git diff --stat d7f5c13..df890a40` excluding `review-packages/`: 20 files, 2610 insertions, 28 deletions.

Supplier blobs named in draft #8 `FINDINGS.md`, re-checked at `879b3a21` and still the same at `0d5cf6fd`:

| Path | Blob |
|---|---|
| `gateway/hosted_room_viewer_state.py` | `3a4c44e9cfc0eaad42a40eec913a6f18dd0343cb` |
| `tests/gateway/test_hosted_room_viewer_state.py` | `15dd54354a588eccfb7c71896e96586eb593ed91` |
| `gateway/session_hosted_rpc.py` | `0576a7d59b602e713737624aaeed42d1c1722928` |
| `tests/gateway/test_session_hosted_rpc.py` | `d1b00a3988dbd7492495273da569668efc34b68b` |

`gateway/hosted_room_replicas.py` blob: `505404179ccc1ff50182b9a1707f35ef7bfb9a50` at `879b3a21`, `c88d5af2770d113e5c6e258397ead3f5aa99b39b` at `c82b8148`, `712ce5254dccae5c09295dad72bd658b04963605` at `df890a40` and at `0d5cf6fd`.

Not in the Runtime delta (blob identical at `d7f5c13` and at `0d5cf6fd`):

| Path | Blob on the Runtime tip | Owner blob that is not this one |
|---|---|---|
| `gateway/hosted_room_attachments.py` | `86f704fba91e6adcb6464cd8d21d7069c8f0faef` | Files `99586695410fe478a4fdf3c1b811eddf13a9b900` |
| `gateway/hosted_room_links.py` | `000986b20c0bc51cef391f8e7e94c0b4fb43c3b4` | Route `223c38ddfd92d2ffd8b96c22bd515faa549231de` |

Absent at `0d5cf6fd`: `gateway/hosted_room_safety.py`, `gateway/hosted_room_attachment_catalog.py`. `def put_import`, `def list_published`, and `def route_security_digest` are not in the Runtime tip. The import writer calls those names and raises if they are missing (`gateway/hosted_rooms.py:1452` and `:1459`). Retirement refuses a link when `route_security_digest` is not callable (`gateway/hosted_room_member_retirement.py:57`).

## Pins inside the complete-owner commit

`879b3a21` records three `Source-Commit` lines:

| Role | OID | What this clone can show |
|---|---|---|
| Importer parent | `8548f70e5c642636f33047afd94cdb5e965963db` | Present. Parent of `879b3a21`. Tree `ed06d20f378c0c510a861e9e04b601ce9d32a610`. |
| Stop ack | `8461351e856450f33f6ed80a41b0d7e81b47fa59` | Not an object here. `git fetch origin <oid>` returned `upload-pack: not our ref`. GitHub commits API returned 422 for that SHA on both `dokterdok/hermes-agent` and `NousResearch/hermes-agent`. |
| Viewer | `56a8bb9ee2383d57c22670d08b2577bea9021595` | Same: not our ref, 422 on both repositories. |

The in-tree Stop change that was consumed is `gateway/session_hosted_rpc.py` blob `0576a7d5`. A queued admission still cancels and returns `{'interrupted': True, 'status': 'interrupted'}`. A started admission calls `interrupt` and returns `{'interrupted': False, 'status': 'running'}`. The comment there says the terminal receipt is observed after the producer exits. `_STOP_ACK_STATUSES` does not occur in `gateway/hosted_room_driver.py` at this tip; that name is not restated from `FINDINGS.md`.

The in-tree viewer module is blob `3a4c44e9`. `viewer_snapshot` / `owned_viewer_room` are referenced from that module and from `tests/gateway/test_hosted_room_viewer_state.py` only. No other production caller is in this tip.

## Overlay companions, not grafted

Live heads read this run. They match the OIDs the receipts already pinned.

| Owner | PR | Live head | Tree | What stays there |
|---|---|---|---|---|
| Retention | NousResearch #99107, draft, `fix/bot-mode-passive-replicas-20260831` | `004015d6087fe031231c4d7d9e0032cc59b679eb` | `84503b7809bc650e9454d3622393f7204f377dbb` | safety blob `7bfb1bf04b59c52e27603370ef278ae85419ab22`; replicas blob `e59558eb2cc1a8ab32b0e2e2fd07a7b9ec9fc5d6` (not overlaid; it drops `promote_replica` / `demote_room`) |
| Files | NousResearch #98072, draft, `feat/bot-mode-roomlink-files-20260829` | `b353ab32c527bd02e2f606d567da4ca324c3aaff` | `78e1a657a8f5a1321c90719c48533756bb0909d1` | store `99586695410fe478a4fdf3c1b811eddf13a9b900`; store test `f35bed3bc07e9223de899fc8b028d331d6e4d9ac`; catalog `8aab5f2c68d026262c3793428806f4f54f35fb38`; catalog test `e641354ff06686e8c6b5400735fab1c12cb1c5bb` |
| Route | NousResearch #100016, open, `fix/roomlink-route-worker-sync-20260901` | `46c3ce81784406af7a742f9d79e121c3917e584e` | `c2faecc4f40201107608ddb3c4463853d8f934a1` | `route_security_digest` defined in `gateway/hosted_room_links.py` on that tip |

Fork compose drafts are docs or tests. They do not form one stack. Product modules of Retention, Files, and Route are not in the Runtime commits.

`879b3a21` is the parent of #8 and of #9, and the ancestor of #10. Neither #8 nor #9 is an ancestor of #10. A GitHub diff of #9 or #10 against the #8 branch therefore deletes `review-packages/FINDINGS.md`. That file remains on #8.

#11 through #13 are a stack on #10. Immediate parents:

| Draft | Tip | Immediate parent | What that tip's range adds |
|---|---|---|---|
| #8 | `ff18610351f4cd0fc9a69bd3a037bd8516f086f6` | `879b3a21` | `review-packages/FINDINGS.md` |
| #9 | `7bca11a8869a4664ee19a2ce048e70dae10e3bea` | `879b3a21` | `RETENTION_SAFETY_COMPOSITION.md`, `replicas-audit-splice.patch` SHA-256 `c3209fa2700a7eb62edbc512cb004a31fed4bb63089d359c9f96c49f530fbf90` |
| #10 | `0d5cf6fd8ffb51acb01ecacda5ab2e2028c18144` | `df890a40` | receipt edit only; product is the two commits under it |
| #11 | `a36e562f02a69c3651fcee0d2a2bb70d1724c56d` | `64372a2343c96045a4fcabf9ba96a18c8a440293` | `FILES_ATTACHMENT_IMPORT_COMPOSITION.md` and a further edit of `tests/gateway/test_shipped_history_attachment_import.py`. `64372a23` (parent `0d5cf6fd`) adds that test |
| #12 | `327b81637fdc35dd2864c18476122f8bc6a45825` | `31ce3ef7d8168de82053c563f82122a8346ae4c7` | `ROUTE_SECURITY_DIGEST_COMPOSITION.md`. Parent `31ce3ef7` adds `route-security-digest-overlay.patch` (SHA-256 `9f3d71736a9b1b998a2809b6c45c0223f7ccadf2ad9204ab0b3c7da21e22713e`) and edits `tests/gateway/test_imported_member_retirement.py` |
| #13 | `9a1540b0ccb3180c2de25871260784cabb0383e7` | `327b81637fdc35dd2864c18476122f8bc6a45825` | `FILES_ATTACHMENT_CATALOG_COMPOSITION.md` only |

The retirement test blob on the Runtime tip (`879b3a21` and `0d5cf6fd`) is `3378f3dc9011075247b35811843a24dd9d3ada8a`. Draft #12 replaces it with `5961e7b51456ac80d58ccd49d3f15c61fb31a49a` (disband fence and the success case). That edit is Route-draft scope. It is not in the Runtime path set.

Patch SHA-256 values above were recomputed from the blobs at #9 and #12. Safety, store, catalog, and Retention replicas blobs were recomputed with `git rev-parse <tip>:<path>`.

## Cited results

Canonical runner in those receipts: `scripts/run_tests.sh`. Not re-run here.

| Draft | Cited result | Verdict in that receipt |
|---|---|---|
| #8 bare `879b3a21` | viewer 12 passed; hosted RPC 7 passed; shipped-history import 7 failed; retirement 1 failed; TUI import 2 passed / 2 failed. Runner 21 passed, 10 failed. Failures are `HostedRoomError` at `gateway/hosted_rooms.py:1452` (safety module absent) or that error mapped to `invalid_params`. | HELD, re-review 1 |
| #9 overlay (safety `7bfb1bf0` + splice, still on `879b3a21` replicas) | five #8 files 31 passed, 0 failed, wall 3.4s. Replicas file 8 passed, 3 failed (`room_id is already reserved`). | HELD, re-review 1 |
| #10 RED, same overlay before the fix | replicas 8 passed, 3 failed, runner file 3.34s | — |
| #10 product tree, safety absent | replicas 12 passed, 0 failed, runner file 2.57s | CLEAN, re-review 2 |
| #10 overlay at `df890a40` | 6 files, 43 passed, 0 failed, wall 3.8s (replicas 12; the five #8 files 31) | same |
| #11 RED (safety + splice, Files store absent) | new attachment-import file 3 failed, 0 passed, file 1.15s, all at `hosted_rooms.py:1459` | CLEAN, re-review 2 |
| #11 GREEN (plus Files store blobs) | 8 files, 71 passed, 0 failed, wall 4.4s | same |
| #12 RED (safety + splice, Route patch absent; retirement file is the #12 version) | 2 passed, 1 failed, file 4.9s. Failure is `peer_setup_unavailable` with the member unchanged | CLEAN, re-review 2 |
| #12 GREEN (Route patch + Files store) | 8 files, 73 passed, 0 failed, wall 4.3s (retirement 3; five #8 files 33 because retirement grew by 2) | same |
| #13 RED (store present, catalog module absent) | catalog file 23 failed, 0 passed, file 4.6s. `ModuleNotFoundError` at `hosted_room_attachments.py:1427` | CLEAN, re-review 2 |
| #13 GREEN (catalog blob `8aab5f2c`) | 9 files, 96 passed, 0 failed, wall 6.0s (#12 set 73 + catalog 23) | same |

The #12/#13 greens are not results for the Runtime tip. Remote-retirement success and `list_published` were exercised only on those overlays.

## Tested, untested, outside this package

| Item | Class | Evidence |
|---|---|---|
| 18-path series at `879b3a21`, five focused files, safety absent | Tested, cited from #8 | 21 passed, 10 failed. Import does not write. |
| Promote/demote without safety, at `df890a40` | Tested, cited from #10 | replicas 12 passed. The five #8 files were not cited as re-run on that bare product tree. |
| Promote/demote with Retention triggers | Tested, cited from #10 overlay | 43 passed, triggers not deleted. |
| Importer happy path with safety, no attachments | Tested, cited from #9 and again inside #10 overlay | 31 passed on the five files. |
| Attachment-bearing import | Tested only on the #11 overlay | 3 failed without the Files store; 71 passed with it. The new test file is not on `0d5cf6fd`. |
| Remote retirement success | Tested only on the #12 overlay | Needs the Route digest patch. The Runtime retirement file is still the one-test blob `3378f3dc`. |
| `list_published` | Tested only on the #13 overlay | Needs catalog blob `8aab5f2c`. Module absent on the Runtime tip. |
| Full `pytest tests/` | Untested | No receipt claims it. |
| Suites in this lane | Untested | This document does not add a test run. |
| Production caller for `owned_viewer_room` | Untested / unwired | Grep of this tip finds the module and its test only. |
| Source commits `56a8bb9e` and `8461351e` | Not re-verified | Unreachable on both GitHub repositories. In-tree blobs above are the consumed bytes. |
| Output / secondary publication, F1 admission, native device, #109338 history, A7 | Outside this package | Not edited. Parallel owners. |
| NousResearch #120652 | Outside this package | Open, head `38da16a41cb83bf809d3531fde197c5e77a8c475` at this read. Not published, not diffed into the Runtime path set. |
| Retention safety, Files store/catalog, Route digest as product | Outside the Runtime tree | Live heads in the table above. Compose drafts cite them. They are not copied here. |

## Holds

1. Do not push, fast-forward, comment, or merge NousResearch #106742. Head stays `d7f5c13` until a maintainer publishes.
2. Do not merge this fork draft, or drafts #8–#13, onto fork `main`.
3. Do not publish #99107, #98072, #100016, or #120652 from this lane.
4. Remote retirement that succeeds needs the Route overlay (draft #12 patch, tip `46c3ce81`). The Runtime tree refuses that path with `peer_setup_unavailable`.
5. `list_published` needs the Files catalog overlay (blob `8aab5f2c` on tip `b353ab32`). The Runtime tree does not contain that module.
6. Attachment import needs the Files store overlay (blob `99586695`). The public-base attachment module on the Runtime tip is a different blob.
7. `gateway.hosted_room_safety` stays a Retention provider. The splice patch stays on draft #9. Retention replicas blob `e59558eb` is not a replacement for the Runtime replicas file.
8. Viewer wiring, native/device surfaces, and history rewrite stay later work. This package does not invent a caller or rewrite history.

## Adversarial review of this package

The first pass compared `git diff --name-status 879b3a21..df890a40` with a sentence that said that range added only the two product files. The range also adds `review-packages/PROMOTE_DEMOTE_RESERVATION.md`. The path table was corrected to three rows.

The second pass found the review notes still saying those two product paths "match" the whole range. That sentence now says the three-path list matches the diff.

The third pass treated "Parent tip #10" as `a36e562f^`. The git parent is `64372a2343c96045a4fcabf9ba96a18c8a440293`, whose parent is `0d5cf6fd`. `327b8163^` is `31ce3ef7d8168de82053c563f82122a8346ae4c7`, not `a36e562f`. #8, #9, and #10 share ancestor `879b3a21` and are not one stack. The same pass checked `64372a23..a36e562f`: that tip adds the Files receipt and also edits the attachment-import test. The table now lists immediate parents and those paths. No Runtime product file was edited.

Confirmatory pass:

- Every commit and tree in the Runtime table matches `git rev-parse <oid>^{tree}` and `git rev-parse <oid>^`.
- The 18-path list matches `git diff --name-status d7f5c13..879b3a21`. The three-path list matches `879b3a21..df890a40`. `c82b8148..df890a40` is only the replicas module and its test. The 20-file stat matches `git diff --stat` excluding `review-packages/`.
- Immediate parents in the compose table match `git rev-parse <tip>^`. `64372a23..a36e562f` is the Files receipt plus the attachment-import test. `a36e562f..31ce3ef7` is the Route patch plus the retirement test. `31ce3ef7..327b8163` and `327b8163..9a1540b0` are each one receipt file. `ff186103` and `7bca11a8` are not ancestors of `0d5cf6fd`.
- #106742 head was read from the API this run, not copied from an older note. It equals `d7f5c13`.
- Viewer `56a8bb9e` and Stop `8461351e` were fetched and queried. Both are unreachable. The package says that, and it does not describe their trees.
- `_STOP_ACK_STATUSES` is absent in `gateway/hosted_room_driver.py`. The Stop paragraph quotes the return values in `session_hosted_rpc.py` at blob `0576a7d5`.
- Overlay blobs match `git rev-parse` on #99107, #98072, and the Retention replicas file. `route_security_digest` is defined at `46c3ce81:gateway/hosted_room_links.py:25`.
- RED/GREEN integers match the receipt files at #8–#13. The #12 retirement success case is attributed to blob `5961e7b5`, not to the Runtime blob `3378f3dc`.
- `gateway/hosted_room_safety.py` and `gateway/hosted_room_attachment_catalog.py` are absent at `HEAD`. The attachment and links blobs are unchanged from `d7f5c13`.
- No product path is modified by this commit.

Open findings: none. Status: **CLEAN**.

Re-review count: **4**.
