# A2 Runtime #106742 closeout — consolidated review package

Review only. This document does not change product behavior. It reconciles the overnight “Runtime COMPLETE” claim with the still-open primary PR, classifies the already-identified remaining candidates, and records the focused checks named by that contribution.

Local test PASS is not public acceptance, not a merge of #106742 or #120652, and not a green `All required checks pass` gate. Document findings from adversarial review of `af9b4fa` are corrected in this revision. The milestone stays **HELD** on the maintainer actions below. See `review-packages/FINDINGS.md`.

## COMPLETE vs OPEN

The overnight note (`OVERNIGHT-HERMES-CONTINUATION-20260923.md`) says Runtime is COMPLETE because PR **#120652** and a linking comment on **#106742** were published, with the four-fix head at `38da16a41cb83bf809d3531fde197c5e77a8c475`.

Live GitHub re-checked 2026-09-26 after the first draft of this package:

| Item | Live state |
| --- | --- |
| [#106742](https://github.com/NousResearch/hermes-agent/pull/106742) | **OPEN**, not draft. Head `d7f5c13d73784b4e536bf6fe0d20a48089523ec0` on `feat/unified-gateway-runtime` (unchanged from the kickoff pin). Base ref `main`. API `base.sha` `d5785bb525e4d82848e2051f5e8bc19ab50e9934`. `mergeable=false`, `mergeable_state=dirty`. Author `teknium1`. `updated_at` `2026-09-24T17:27:30Z`. |
| Live `main` | `d0288be5b3330d2442e3907185b8e9d0958297bb` (`2026-09-26T03:51:55Z`, `fmt(js): npm run fix on merge (#123423)`). |
| [#120652](https://github.com/NousResearch/hermes-agent/pull/120652) | **OPEN**, not merged, not draft. Head `38da16a41cb83bf809d3531fde197c5e77a8c475` on `dokterdok/hermes-agent:fix/runtime-reviewed-followups-20260923`. Base ref `feat/unified-gateway-runtime`. API `base.sha` `26b02651ad6f30b5409a0da90f1ed9663f03ffd2`. `mergeable=true`, `mergeable_state=unstable`. `updated_at` `2026-09-24T04:45:58Z` (earlier than the runtime tip commit). Commit status on `38da16a` is `pending` with an empty status list. `gh pr checks` reports no checks. |
| Linking comment | [dokterdok, 2026-09-23T21:02:57Z](https://github.com/NousResearch/hermes-agent/pull/106742#issuecomment-5802886339). It says #120652 was opened against runtime base `26b0265` with four fixes and **274 passed, 0 failed, 4 Windows-only skips**, and that nothing was merged or deployed. That sentence is a publication notice. It does not close #106742. |
| [#121813](https://github.com/NousResearch/hermes-agent/pull/121813) | **OPEN**, not merged. Later same-thread note ([Lokee86, 2026-09-24T17:14:52Z](https://github.com/NousResearch/hermes-agent/pull/106742#issuecomment-5818736260)). Head `6ae81b2f4ccdaf98d84da5bd9edd419a690548bf`. API base sha `eb9d6887dec958c9042bb79fd65ecbf868263229`. `mergeable=true`, `unstable`. The PR text says it does not duplicate #120652. |

“Published” in the overnight note means the follow-up PR and the linking comment exist. `38da16a` is not an ancestor of `d7f5c13`. #106742 remains the open runtime integration PR and GitHub reports it dirty against `main`.

Compare of `38da16a` to runtime tip `d7f5c13`: `diverged`, ahead 4, behind 714. A content merge of those two commits is clean: `git merge-tree --write-tree d7f5c13d73784b4e536bf6fe0d20a48089523ec0 38da16a41cb83bf809d3531fde197c5e77a8c475` prints tree `d9dfbbb105196fca621bb8fb648ef9fcd7430fa2` and exits 0. Today’s pulls API also returns `mergeable=true`. That API field is not a substitute for the merge-tree above: #120652 `updated_at` predates the tip move, and `base.sha` is still `26b02651`. `mergeable_state=unstable` is GitHub’s label while the combined status is `pending`; it is not, by itself, proof that a check suite failed.

## Pins

- Primary: NousResearch/hermes-agent#106742 @ `d7f5c13d73784b4e536bf6fe0d20a48089523ec0` (`feat/unified-gateway-runtime`). Tip did not move on this re-check.
- Live main: `d0288be5b3330d2442e3907185b8e9d0958297bb`.
- Four-fix contribution: #120652 @ `38da16a41cb83bf809d3531fde197c5e77a8c475`. Its parent is `26b02651ad6f30b5409a0da90f1ed9663f03ffd2`, which is an ancestor of the runtime tip.
- Commits on #120652, oldest first: `5dbea4fc55` hosted admission, `4dbffd08c9` config v48 MCP replay, `cd663be1cd` Kanban exit policy, `38da16a41c` diagnostic automation category.
- Separate already-identified follow-up: #121813 @ `6ae81b2f4ccdaf98d84da5bd9edd419a690548bf`. `eb9d6887dec958c9042bb79fd65ecbf868263229` is an ancestor of the runtime tip.
- Related, named by #120652 as untested composition, left untouched: #111216 OPEN, head `03ab73504dae640e8cfef0b19841c7053aa31cbe`, base `feat/unified-gateway-runtime`.
- This closeout: https://cursor.com/agents/bc-950540df-5f77-5c85-9123-399bfc2a2c9f
- Draft handback PR: https://github.com/dokterdok/hermes-agent/pull/6 (draft). Its base is **fork** `main` at `057dcdf236f8a6a26721c10fcc6ccb72726e272a`, not Nous `main`.
- Verification tree (not pushed): `d9dfbbb105196fca621bb8fb648ef9fcd7430fa2`. Local commit `5ba3844bd8372908894ff0f90432035519f4e612` has that tree (`git rev-parse 5ba3844^{tree}`).

## Diff boundary

The intended remaining contribution, per the runtime-controller delivery note, is the already-identified config / Kanban / automation (and same-home hosted admission) fix set in #120652. It targets `feat/unified-gateway-runtime`, not `main`.

The applicable patch against the pinned tip is:

```text
git diff d7f5c13d73784b4e536bf6fe0d20a48089523ec0 d9dfbbb105196fca621bb8fb648ef9fcd7430fa2
```

That command’s bytes are included below with no trimming. SHA-256 `09f944383f31934ec8086e4afec154321489fd9bf57a4e10c7f1a69792beb4ef` (48524 bytes). `git apply --check` of those bytes onto a detached checkout of `d7f5c13` succeeds.

The owner three-dot `git diff d7f5c13...38da16a` is `26b02651..38da16a`. It has the same 18 paths and the same `+501/−52`, and the same added lines, but it is not the same patch text. On the tip, `hermes_cli/config_migrations.py` has a comment immediately after the migration tuple that is not in the three-dot context. `git apply --check` of the three-dot patch onto `d7f5c13` fails at `hermes_cli/config_migrations.py:765`. Use the tip diff below, not the three-dot patch, to reproduce the tested tree.

This patch is not the 1,075-file #106742-vs-main diff, not the #121813 diff, and not the git diff of draft #6. Draft #6 adds only this review document onto stale fork `main`.

Symbols absent on pinned tip `d7f5c13` and introduced by this diff: `automation_notification_metadata`, `turn_exit_code` / `hermes_cli/turn_exit.py`, `_config_version: 48`, and migration step `(48, _migrate_to_46)`. The tip’s `DEFAULT_CONFIG["_config_version"]` is 47, and its `MIGRATIONS` list ends at step 47. An existing `_authorize_write` parameter in `hermes_state_runtime.py` is reused by the hosted-admission commit; the same-home recheck inside the admission transaction is part of this diff. On the tested tree, `hermes_cli/kanban_db.py` defines `KANBAN_RATE_LIMIT_EXIT_CODE = 75` and `KANBAN_TERMINAL_PROVIDER_EXIT_CODE = 78`. Those constants are what `turn_exit_code` returns for transient and terminal provider reasons. They were already on the tip; this diff does not invent the numbers.

## Candidate classification

Already-identified only. No new inventory.

| Candidate | Class | Why |
| --- | --- | --- |
| P2 same-home hosted admission recheck (`5dbea4fc55`) | **keep** | The tip diff still adds the writer-transaction recheck. Merge-tree onto `d7f5c13` is clean. Same-home only, as #120652 states. |
| P2 runtime v48 replay of MCP enabled/disabled normalization (`4dbffd08c9`) | **keep** | Tip config version is 47 and has no `(48, _migrate_to_46)` step. Merge-tree is clean. |
| P2 shared Kanban/CLI terminal exit policy (`cd663be1cd`, new `hermes_cli/turn_exit.py`) | **keep** | `turn_exit_code` is absent on the tip. Merge-tree is clean. Incomplete results return 1; interrupt returns 130; transient provider reasons return 75; terminal provider reasons return 78. |
| P3 diagnostic automation category through admission/storage/replay (`38da16a41c`) | **keep** | `automation_notification_metadata` is absent on the tip. Merge-tree is clean. |
| #121813 ten review-gap fixes (list below) | **keep**, **outside this diff** | `git merge-tree --write-tree d7f5c13 6ae81b2f` exits 0 with tree `1fb2e90cb1cd1e55213c5a7923e8acd83d852cd0`. Diff stat of that tree against `d7f5c13` is still 24 files, `+600/−52`, matching the published PR stat. Stacking #121813 onto the #120652 merge is also clean (tree `11653ac38df89a3c59f987581bb0098d03c9e1ae`). Shared paths with #120652: `gateway/session_authority.py`, `gateway/session_automation.py`, `gateway/session_bot.py`. The matching stat means those edits are not already present as the same patch. This closeout did not re-run #121813’s tests and did not show each of the ten behaviors failing on the tip. |
| Rebase of #106742 onto live `main` | **HELD**, upstream | `git merge-tree --write-tree d0288be5b3330d2442e3907185b8e9d0958297bb d7f5c13d73784b4e536bf6fe0d20a48089523ec0` exits 1 with 51 conflicts. Upstream owns the rebase. This closeout did not rebase, merge, or comment on NousResearch. |
| #111216 composition | **outside scope / untested** | Named by #120652 as an untested related admission PR. Not merged into the test tree and not classified file-by-file. |
| Draft #6 merged to fork `main` | **do not merge** | See Publication. |

#121813 items, kept as a separate open follow-up. Presence below means the published commit message, not a fresh failure reproduction on `d7f5c13`:

1. Route local ticket/bootstrap through the multiplexer control owner (`705e040139`).
2. Terminate CLI/ACP projections on replay gaps (`5a94a1be91`).
3. Retain ACP cancellations that arrive before admission identity is returned (`5a94a1be91`).
4. Recover mixed native/viewer queues (`d06dbcd34b`).
5. Retire scheduled authority work when a late hot-serve attempt fails (`d06dbcd34b`).
6. Isolate corrupt Bot receipt files (`827649f47e`).
7. Keep headless cron receipt recovery from connecting to or spawning a gateway (`827649f47e`).
8. Scope one-shot/stream-json output to the submitted admission (`6ae81b2f4c`).
9. Include explicit `conversation_history` in Responses idempotency identity (`6ae81b2f4c`).
10. Unsubscribe failed SSE subscribers when `StreamResponse.prepare()` raises (`6ae81b2f4c`).

#121813’s own “38 passed” figure was not re-run here. Its PR body does not name the 15-file command this closeout was told to execute.

## Tested

Command is the one in the #120652 body. It was run on local commit `5ba3844`, whose tree is `d9dfbbb` (the clean composition of the four commits onto pinned tip `d7f5c13`). It was not run only on the older base `26b02651`.

Environment: Linux, CPython 3.11.16 from `uv sync --extra dev --frozen` in that worktree. Invoking `HOME=/tmp/a2-runtime-home` (mode 0700), `umask 077`. Canonical `scripts/run_tests.sh` uses `env -i` and forwards `HOME`; it does not forward `TMPDIR` or `HERMES_HOME`. On tip `d7f5c13`, `tests/conftest.py` fixture `_isolate_hermes_home` is `autouse=True` and points `HERMES_HOME` at a per-test temp directory. `TZ=UTC`, `LANG=C.UTF-8`, `PYTHONHASHSEED=0`, `-j 1`, `--file-retries 0`, `--file-timeout 300`.

```bash
umask 077
bash scripts/run_tests.sh -j 1 -q --file-timeout 300 --file-retries 0 \
  tests/gateway/test_session_hosted_rpc.py \
  tests/gateway/test_session_hosted_service.py \
  tests/gateway/test_session_hosted_controls.py \
  tests/hermes_cli/test_config.py \
  tests/hermes_cli/test_runtime_config_migration_join.py \
  tests/hermes_cli/test_single_query_exit_contract.py \
  tests/hermes_cli/test_kanban_managed_exit_reconciliation.py \
  tests/gateway/test_kanban_result_exit.py \
  tests/gateway/test_automation_notification_category.py \
  tests/gateway/test_local_automation.py \
  tests/gateway/test_authority_automation.py \
  tests/gateway/test_automation_retry.py \
  tests/gateway/test_bot_result_recovery.py \
  tests/tools/test_bot_live_owner_delivery.py \
  tests/cron/test_cron_live_bot_delivery.py
```

Result: **15 files, 274 passed, 0 failed, 4 skipped, 209.4s**. The 4 skips are the parametrized `windows_only` cases of `test_windows_writer_rejects_mixed_case_protected_name` in `tests/hermes_cli/test_config.py` (`Hermes_Yolo_Mode`, `Hermes_Optional_Mcps`, `Hermes_Copilot_Acp_Command`, `Hermes_Copilot_Acp_Args`). The runner reports them as windows_only skips on Linux. Passed counts by file, in runner order, sum to 274: cron live bot delivery 2, authority automation 2, automation notification category 9, automation retry 2, bot result recovery 3, kanban result exit 17, local automation 5, hosted controls 1, hosted rpc 22, hosted service 4, config 169, kanban managed exit reconciliation 6, runtime config migration join 4, single-query exit contract 19, bot live owner delivery 9.

Also named by the #120652 verification section and run on the same tree:

- `ruff check` on the 18 changed paths: all checks passed.
- `git diff --check d7f5c13 HEAD` in that worktree: clean.

What this run does not show: it does not execute #106742’s required CI gate, and the worktree commit was not pushed.

## Untested

- #106742’s full branch, its CI gate, and a merge onto Nous `main`.
- #121813’s suite (historical “38 passed” only) and a behavioral replay of each of its ten items on `d7f5c13`.
- #111216 composed with this diff.
- Native macOS/Windows (including the 4 windows_only cases), real providers, WAL-mode campaign, sustained/soak use.
- Cross-home hosted admission (explicitly out of the four-fix scope).
- Goal-loop input parity between CLI and managed workers (explicitly out of the shared exit policy).
- Whether #91305’s terminal provider exit 76 can be combined with this policy’s 78. The #120652 text already says that needs an explicit compatibility decision.
- Failed dependency jobs behind `All required checks pass` on `d7f5c13`. Not triaged.

## Outside scope

- Any new feature, migration, verifier, or candidate beyond the lists above.
- Performing the rebase or merge of `feat/unified-gateway-runtime` onto Nous `main`.
- Comments or pushes on upstream #106742, #120652, #121813, #111216, #100016, #97846.
- Merge of dokterdok/hermes-agent#5.
- Merge of draft #6.

## Publication

Draft #6 (https://github.com/dokterdok/hermes-agent/pull/6) is a handback document on `dokterdok/hermes-agent`. Its base branch is fork `main` at `057dcdf236f8a6a26721c10fcc6ccb72726e272a`. That commit is not Nous `main` `d0288be`. The pull request diff is this markdown (and `FINDINGS.md`), not the runtime product patch.

Do not merge #6. Merging it does not update NousResearch/hermes-agent, does not land #120652, and does not make #106742 mergeable.

## Holds

These stay held. This package does not perform them.

1. **Upstream rebase of #106742.** Action: NousResearch maintainer merges or rebases `main` @ `d0288be5b3330d2442e3907185b8e9d0958297bb` into `feat/unified-gateway-runtime` @ `d7f5c13d73784b4e536bf6fe0d20a48089523ec0` and pushes that branch. Reproduce conflicts with `git merge-tree --write-tree d0288be5b3330d2442e3907185b8e9d0958297bb d7f5c13d73784b4e536bf6fe0d20a48089523ec0` (exit 1, 51 conflicts). Consequence until that push exists: GitHub keeps #106742 `mergeable=false` / `dirty`, so it cannot merge to `main`.

Conflict paths from that command:

- apps/desktop/electron/gateway-file-download.ts
- apps/desktop/electron/main.ts
- apps/desktop/electron/pool-spawn-coordinator.ts (deleted on runtime tip, modified on main)
- apps/desktop/electron/pool-stop.ts (deleted on runtime tip, modified on main)
- apps/desktop/src/api/client.ts
- apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.test.ts
- apps/desktop/src/app/session/hooks/use-prompt-actions/index.test.tsx
- apps/desktop/src/app/session/hooks/use-prompt-actions/submit-view-binding.test.tsx (deleted on main, modified on runtime tip)
- apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts
- apps/desktop/src/global.d.ts
- apps/desktop/src/store/composer-queue.ts
- apps/desktop/src/store/notifications.ts
- apps/desktop/src/store/profile.test.ts
- apps/desktop/src/store/updates.ts
- cron/bot_chat_delivery.py (deleted on runtime tip, modified on main)
- gateway/platforms/api_server_runs.py
- gateway/run.py
- gateway/run_notifications.py
- gateway/run_turn.py
- gateway/status.py
- hermes_cli/backup.py
- hermes_cli/update_cmd.py
- hermes_cli/update_cmd_maint.py
- hermes_cli/web_server.py
- tests/conftest.py
- tests/cron/test_codex_execution_paths.py
- tests/cron/test_cron_bot_chat_delivery.py
- tests/cron/test_cron_empty_payload.py
- tests/cron/test_cron_mcp_toolset_empty_block.py
- tests/cron/test_cron_workdir.py
- tests/cron/test_scheduler.py
- tests/cron/test_sessiondb_init_hang.py
- tests/fixtures/resolution_allowlist.json
- tests/gateway/test_api_server_sse_keepalive.py
- tests/gateway/test_peer_dm_into_open_bot_chat.py
- tests/hermes_cli/test_cli_preloaded_skills.py (deleted on runtime tip, modified on main)
- tests/hermes_cli/test_safe_mode.py
- tests/hermes_cli/test_sessions_pin.py
- tests/plugins/test_a2a_plugin.py
- tests/tools/test_bot_live_owner_delivery.py
- tests/tools/test_bot_mode_dm.py
- tests/tools/test_completed_process_results.py
- tests/tui_gateway/test_bot_relay_methods.py
- tools/bot_mode_dm.py
- tools/bot_relay.py
- tools/registry.py
- tui_gateway/server.py
- tui_gateway/transport.py
- ui-tui/src/app/useMainApp.ts
- ui-tui/src/gatewayClient.ts
- website/docs/user-guide/desktop.md

2. **CI gate on the runtime head, separate from the conflict list.** On `d7f5c13`, check-run `All required checks pass` concluded `failure`. `.github/workflows/ci.yaml` on that commit says branch protection should require only that rollup. The rollup `needs` list failed closed because these jobs concluded `failure`: `Desktop core E2E / Desktop core E2E (Linux)`, `JS & TS checks / JS & TS checks`, `OS-specific tests / Windows E2E (real processes)`, `Python tests / Run tests`, `Python tests / e2e`, `Python tests / e2e-upgrade`. `merge` and `publish` were `skipped`. This review did not read branch-protection settings and did not triage those jobs. A rebase onto `d0288be` does not inherit a green gate from today’s 274-pass. Action: after the rebase push, the maintainer needs that rollup green on the new head before #106742 can merge. That triage is not authorized as part of this closeout.

3. **#120652 merge into the runtime branch.** Action: maintainer of `feat/unified-gateway-runtime` decides whether to merge #120652 (`38da16a`) into that branch. Content merge onto `d7f5c13` is clean (tree `d9dfbbb`) and the named 15-file run passed on that tree. This agent did not merge it. Merging it does not merge #106742 to `main` and does not turn the red rollup green. #120652 has no check runs of its own.

## RESULT

```text
Scope: A2 runtime #106742 closeout review package, corrected after adversarial review of draft #6. No new product slice.
Pins (repo/PR/OID): NousResearch/hermes-agent#106742 head d7f5c13d73784b4e536bf6fe0d20a48089523ec0 (unchanged) on feat/unified-gateway-runtime; live main d0288be5b3330d2442e3907185b8e9d0958297bb; #120652 OPEN 38da16a41cb83bf809d3531fde197c5e77a8c475 (not an ancestor of the tip); #121813 OPEN 6ae81b2f4ccdaf98d84da5bd9edd419a690548bf; #111216 OPEN 03ab73504dae640e8cfef0b19841c7053aa31cbe (untouched). Verification tree d9dfbbb105196fca621bb8fb648ef9fcd7430fa2. Draft handback dokterdok/hermes-agent#6, base fork main 057dcdf236f8a6a26721c10fcc6ccb72726e272a.
Changes (paths): review-packages/A2-RUNTIME-106742-CLOSEOUT.md, review-packages/FINDINGS.md. No product source change. No NousResearch push or comment.
Tests run (commands + counts): On tree d9dfbbb, scripts/run_tests.sh -j 1 -q --file-timeout 300 --file-retries 0 of the 15 files named in #120652. 15 files, 274 passed, 0 failed, 4 skipped (windows_only parametrize in tests/hermes_cli/test_config.py), 209.4s. ruff check on the 18 changed paths: passed. git diff --check: clean. Re-review: git apply --check of the embedded tip diff onto d7f5c13. HOME=/tmp/a2-runtime-home; canonical runner env -i; autouse conftest HERMES_HOME. CPython 3.11.16, uv sync --extra dev --frozen.
Outcome: BLOCKED
Adversarial review: HELD
Exact blocker (if any): (1) Upstream rebase/merge of feat/unified-gateway-runtime @ d7f5c13 onto main @ d0288be — 51 merge-tree conflicts; GitHub mergeable=false/dirty. (2) All required checks pass is failure on d7f5c13; 274-pass does not satisfy that gate. (3) #120652 stays OPEN until a maintainer merges it into the runtime branch; content merge onto d7f5c13 is clean and was not performed here.
Suggested next kickoff: None under this authority. Do not start #121813 from this closeout.
Risks / holds: Do not merge draft #6. Do not FF/merge #100016, #97846, or dokterdok/hermes-agent#5. #121813 patch still applies and was not executed. #111216 composition untested. Cross-home admission, exit-code 76 vs 78, macOS/Windows, providers, and soak are outside the tested boundary.
```

## Exact diff against pinned tip

`git diff d7f5c13d73784b4e536bf6fe0d20a48089523ec0 d9dfbbb105196fca621bb8fb648ef9fcd7430fa2`

SHA-256 `09f944383f31934ec8086e4afec154321489fd9bf57a4e10c7f1a69792beb4ef`

```diff
diff --git a/gateway/session_authority.py b/gateway/session_authority.py
index 1c3068e21d..0b8cb0597d 100644
--- a/gateway/session_authority.py
+++ b/gateway/session_authority.py
@@ -282,7 +282,7 @@ class SessionAuthority:
                 results[sid] = exc.reason
         return results
 
-    async def submit(self, actor: Principal, request: Submission):
+    async def submit(self, actor: Principal, request: Submission, *, _authorize_write=None):
         self.authorize(actor, request.ref, 'session:submit')
         self._require_admission_open()
         if (request.intent != 'queue' or not {'text'} <= set(request.payload) <= {
@@ -305,7 +305,8 @@ class SessionAuthority:
                 'principal_id': actor.subject}
         row = admit_session_input(self.db, epoch=self.epoch, principal_id=actor.subject,
                                   session_id=request.ref.session_id, request_id=request.request_id,
-                                  payload=payload, intent=request.intent)
+                                  payload=payload, intent=request.intent,
+                                  _authorize_write=_authorize_write)
         self._publish_pending(request.ref)
         self._schedule(request.ref)
         return self._receipt(row)
diff --git a/gateway/session_automation.py b/gateway/session_automation.py
index 088ca28729..308d361bb5 100644
--- a/gateway/session_automation.py
+++ b/gateway/session_automation.py
@@ -56,13 +56,22 @@ def _owner(runner, event):
     return entry
 
 
+def automation_notification_metadata(metadata):
+    """Validate trusted producer category; omit the default from old fingerprints."""
+    category = metadata.get('notification_category', 'result')
+    if category not in ('result', 'diagnostic'):
+        raise RuntimeStoreError('invalid_params')
+    return {'notification_category': category} if category == 'diagnostic' else {}
+
+
 def snapshot_automation(authority, adapter, event, identity):
     runner = authority.runner
     if (not event.internal or event.message_type != MessageType.TEXT or event.is_command()
             or not isinstance(event.text, str) or not identity
             or event.media_urls or event.prompt_response or event.source.platform == Platform.API_SERVER
-            or set(event.metadata) - {'gateway_session_key', 'gateway_session_id', 'automation_identities', 'turn_author'}):
+            or set(event.metadata) - {'gateway_session_key', 'gateway_session_id', 'automation_identities', 'turn_author', 'notification_category'}):
         raise RuntimeStoreError('invalid_params')
+    notification = automation_notification_metadata(event.metadata)
     entry = _owner(runner, event)
     if event.source.platform == Platform.LOCAL:
         return snapshot_local_automation(authority, adapter, event, identity, entry)
@@ -87,6 +96,7 @@ def snapshot_automation(authority, adapter, event, identity):
         'timestamp': datetime.fromtimestamp(0, timezone.utc).isoformat(),
         'event': {'message_id': identity}, 'provenance': provenance,
         'automation': {'identity': identity, 'owner': entry.session_id}}
+    envelope['automation'].update(notification)
     if getattr(event, '_heartbeat_session_id', None):
         envelope['automation']['heartbeat'] = event._heartbeat_session_id
     identities = event.metadata.get('automation_identities')
@@ -116,6 +126,7 @@ def snapshot_local_automation(authority, adapter, event, identity, entry):
         raise RuntimeStoreError('permission_denied')
     descriptor = {'identity': identity, 'owner': ref.session_id,
                   'route': entry.session_key, 'target': entry.session_id}
+    descriptor.update(automation_notification_metadata(event.metadata))
     if event.metadata.get('turn_author') is not None:
         from agent.turn_author import parse_turn_author
         descriptor['turn_author'] = parse_turn_author(event.metadata['turn_author'])
@@ -151,6 +162,7 @@ def restore_local_automation(authority, ref, row):
     event = MessageEvent(text=row['payload']['text'], source=live.source, internal=True,
         message_id=descriptor['identity'], metadata={'gateway_session_key': live.route,
             'gateway_session_id': entry.session_id})
+    event.metadata.update(automation_notification_metadata(descriptor))
     if descriptor.get('turn_author') is not None:
         event.metadata['turn_author'] = deepcopy(descriptor['turn_author'])
     if descriptor.get('heartbeat'):
diff --git a/gateway/session_bot.py b/gateway/session_bot.py
index 436d3a4842..529a453c5c 100644
--- a/gateway/session_bot.py
+++ b/gateway/session_bot.py
@@ -146,7 +146,8 @@ async def _migrate(authority, actor, home, root):
             _write(path, record)
             continue
         await _admit(authority, actor, home, root, _delivery_id(record['delivery_id']),
-                     record['message'], ref, live, entry, author=parse_turn_author(record.get('author')), legacy=record)
+                     record['message'], ref, live, entry, author=parse_turn_author(record.get('author')), legacy=record,
+                     notification_category=record.get('notification_category', 'result'))
 
 
 async def recover_bot_deliveries(authority):
@@ -191,8 +192,11 @@ def _watch_reply(authority, home, key, admission_id):
 async def deliver(connection, params):
     authority, actor = connection.authority, connection.actor
     home = _home(authority, actor, params.get('profile'))
-    if set(params) - {'id', 'profile', 'message', 'session_id', 'author'}:
+    if set(params) - {'id', 'profile', 'message', 'session_id', 'author', 'notification_category'}:
         raise RuntimeStoreError('invalid_params')
+    from gateway.session_automation import automation_notification_metadata
+    notification = automation_notification_metadata(params)
+    category = notification.get('notification_category', 'result')
     try:
         key = _delivery_id(params.get('id'))
     except ValueError as exc:
@@ -209,7 +213,8 @@ async def deliver(connection, params):
         record = _read(path)
         if record is not None and record.get('admission_id'):
             if (record['message'] != message or record['principal_id'] != actor.subject
-                    or record.get('author') != author):
+                    or record.get('author') != author
+                    or record.get('notification_category', 'result') != category):
                 raise RuntimeStoreError('admission_conflict')
             authority.authorize(actor, SessionRef(authority.profile_id, record['session_id']), 'session:submit')
             return _result(authority, record)
@@ -217,7 +222,8 @@ async def deliver(connection, params):
         record = _read(path)
         if record is not None and record.get('admission_id'):
             if (record['message'] != message or record['principal_id'] != actor.subject
-                    or record.get('author') != author):
+                    or record.get('author') != author
+                    or record.get('notification_category', 'result') != category):
                 raise RuntimeStoreError('admission_conflict')
             authority.authorize(actor, SessionRef(authority.profile_id, record['session_id']), 'session:submit')
             return _result(authority, record)
@@ -226,20 +232,26 @@ async def deliver(connection, params):
         ref, live, entry = _target(authority, actor)
         if params.get('session_id', entry.session_id) != entry.session_id:
             raise RuntimeStoreError('admission_conflict')
-        return await _admit(authority, actor, home, root, key, message, ref, live, entry, author=author)
+        return await _admit(authority, actor, home, root, key, message, ref, live, entry, author=author,
+                            notification_category=category)
 
 
-async def _admit(authority, actor, home, root, key, message, ref, live, entry, author=None, legacy=None):
+async def _admit(authority, actor, home, root, key, message, ref, live, entry, author=None, legacy=None,
+                 notification_category='result'):
+    from gateway.session_automation import automation_notification_metadata
+    notification = automation_notification_metadata({'notification_category': notification_category})
     path = root / f'{key}.json'
     event = MessageEvent(text=message, source=live.source, internal=True,
         message_id='bot:' + key, metadata={'gateway_session_key': live.route,
                                          'gateway_session_id': entry.session_id})
+    event.metadata.update(notification)
     if author is not None:
         event.metadata['turn_author'] = dict(author)
     # Pin the physical target before committing. A process death in this
     # two-store window leaves an explicit unknown record, never a new target.
     record = dict(legacy or {}, delivery_id=key, profile_home=str(home), session_id=ref.session_id,
         principal_id=actor.subject, message=message, status='ambiguous')
+    record.update(notification)
     if author is not None:
         record['author'] = dict(author)
     _write(path, record)
diff --git a/gateway/session_envelope.py b/gateway/session_envelope.py
index 58d6d93755..826520edd3 100644
--- a/gateway/session_envelope.py
+++ b/gateway/session_envelope.py
@@ -151,6 +151,8 @@ def restore_native(payload, runner=None):
             event._heartbeat_session_id = envelope['automation']['heartbeat']
         event.metadata = {'gateway_session_key': envelope['route'],
                           'gateway_session_id': envelope['automation']['owner']}
+        from gateway.session_automation import automation_notification_metadata
+        event.metadata.update(automation_notification_metadata(envelope['automation']))
     return event
 
 
diff --git a/gateway/session_hosted_rpc.py b/gateway/session_hosted_rpc.py
index 057785c573..d21d68988c 100644
--- a/gateway/session_hosted_rpc.py
+++ b/gateway/session_hosted_rpc.py
@@ -19,10 +19,11 @@ _RESULTLESS_OUTCOMES = frozenset({'interrupted', 'cancelled'})
 
 class HostedRoomAuthorityRPC:
     def __init__(self, authority, loop, *, room_id, member_id, profile, principal,
-                 authorize, timeout=30):
+                 authorize, authorize_write=None, timeout=30):
         self.authority, self.loop = authority, loop
         self.room_id, self.member_id, self.profile = room_id, member_id, profile
         self.principal, self.authorizer, self.timeout = principal, authorize, timeout
+        self.authorize_write = authorize_write
         self.callbacks = {}
         binding = json.dumps([room_id, member_id, profile], separators=(',', ':'))
         self.creation_id = 'hosted:' + hashlib.sha256(binding.encode()).hexdigest()
@@ -137,8 +138,11 @@ class HostedRoomAuthorityRPC:
         from gateway.session_hosted_attachments import submission_payload
         payload = await asyncio.to_thread(
             submission_payload, self, params['prompt'], params.get('attachments'))
+        authorize_write = self.authorize_write
         receipt = await self.authority.submit(self.principal, Submission(
-            request_id, self.ref, payload, 'queue'))
+            request_id, self.ref, payload, 'queue'),
+            _authorize_write=(lambda conn: authorize_write(conn, task, generation))
+            if authorize_write is not None else None)
         self.callbacks[receipt.admission_id] = params['on_terminal']
         if receipt.status in {'queued', 'started'}:
             waiter = self.authority.waiters.get(receipt.admission_id)
diff --git a/gateway/session_hosted_service.py b/gateway/session_hosted_service.py
index 47a2e7376a..9772df7ea6 100644
--- a/gateway/session_hosted_service.py
+++ b/gateway/session_hosted_service.py
@@ -149,28 +149,53 @@ class CanonicalHostedRoomService(HostedControls, HostedRoomService):
                     source_home=self.authority.profile_id, room_id=binding.room_id,
                     member_id=member, profile=profile)
                 return self.member_rpcs[key]
-            def authorize(operation, identity, generation):
-                self.authorize_room(owner, binding.room_id)
-                room = self._room(binding.room_id)
+            def authorized(conn, operation, identity, generation):
+                # Admission checks must share the FIFO writer's snapshot. Opening
+                # another transaction here would reintroduce the revocation race.
+                from gateway.hosted_rooms import _room_from_row
+                from gateway.hosted_room_driver import _task_from_row
+                _epoch(conn, self.authority.epoch)
+                owned = conn.execute('SELECT value FROM state_meta WHERE key=?',
+                                     (_OWNER + binding.room_id,)).fetchone()
+                if owned is None or owned[0] != owner:
+                    return False
+                stored = conn.execute('SELECT * FROM hosted_rooms WHERE room_id=?',
+                                      (binding.room_id,)).fetchone()
+                if stored is None or stored['disbanded_at'] is not None:
+                    return False
+                room = _room_from_row(stored)
                 if (room['authority_gateway_id'], room['authority_epoch']) != (binding.gateway_id, binding.authority_epoch):
                     return False
                 members = room['members']
-                if not any(m.get('member_id') == member and m.get('profile') == profile for m in members):
+                if not any(m.get('member_id') == member and m.get('profile') == profile
+                           and (m.get('target') is None or (
+                               isinstance(m.get('target'), dict)
+                               and m['target'].get('kind', 'local') == 'local'))
+                           for m in members):
                     return False
                 if self.profile_homes().get(profile) != home:
                     return False
                 if identity is not None:
-                    from gateway.hosted_room_driver import list_tasks
-                    return any(t['identity'] == identity and t['execution_generation'] == generation
-                               and t['payload'].get('target_profile') == profile
-                               and t['status'] in {'running', 'stopping'}
-                               for t in list_tasks(self.db_path, room_id=binding.room_id))
+                    stored = conn.execute('SELECT * FROM hosted_room_driver_tasks WHERE room_id=? AND task_id=?',
+                                          (binding.room_id, identity.task_id)).fetchone()
+                    if stored is None:
+                        return False
+                    current = _task_from_row(stored)
+                    return (current['identity'] == identity and current['execution_generation'] == generation
+                            and current['payload'].get('target_profile') == profile
+                            and current['payload'].get('target_member_id', profile) == member
+                            and current['status'] in ({'running'} if operation in {'submit', 'execute'}
+                                                       else {'running', 'stopping'}))
                 return True
+            def authorize(operation, identity, generation):
+                with self.authority.db._read_ctx() as conn:
+                    return authorized(conn, operation, identity, generation)
             principal = Principal(owner, self.authority.profile_id,
                 frozenset({'session:create', 'session:read', 'session:submit', 'session:control', 'session:approve'}),
                 'hosted:' + binding.room_id + ':' + member)
             self.member_rpcs[key] = HostedRoomAuthorityRPC(self.authority, self.loop,
-                room_id=binding.room_id, member_id=member, profile=profile, principal=principal, authorize=authorize)
+                room_id=binding.room_id, member_id=member, profile=profile, principal=principal, authorize=authorize,
+                authorize_write=lambda conn, identity, generation: authorized(conn, 'submit', identity, generation))
         return self.member_rpcs[key]
 
     def check_admission(self, ref, row):
diff --git a/gateway/session_kanban.py b/gateway/session_kanban.py
index 28e2225fb3..e94cbff771 100644
--- a/gateway/session_kanban.py
+++ b/gateway/session_kanban.py
@@ -172,11 +172,11 @@ def run_worker_turns(agent, frame, history):
         author = frame.get('turn_author')
         return agent.run_conversation(frame['text'], conversation_history=history,
                                       **({'turn_author': author} if author is not None else {}))
-    from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
+    from hermes_cli.turn_exit import turn_exit_code
     code = 1
     try:
         result = _run_task_turns(agent, frame, history, context)
-        code = KANBAN_RATE_LIMIT_EXIT_CODE if result.get('failed') and result.get('failure_reason') in {'rate_limit', 'billing'} else int(bool(result.get('failed') or result.get('interrupted')))
+        code = turn_exit_code(result, kanban_worker=True)
         return result
     finally:
         import os
diff --git a/hermes_cli/cli_single_query.py b/hermes_cli/cli_single_query.py
index 4f3df2d825..bb71fbefc2 100644
--- a/hermes_cli/cli_single_query.py
+++ b/hermes_cli/cli_single_query.py
@@ -125,9 +125,7 @@ def _sync_cli_session_id_from_agent(cli) -> None:
 # ``failure_reason`` values that say nothing about the task itself: the provider is walled,
 # down or unreachable, or the account is out of credit, so a Kanban worker signals "try
 # later" instead of "I failed" and the dispatcher does not spend the task's retry budget on it.
-_TRANSIENT_PROVIDER_REASONS = frozenset({
-    "rate_limit", "upstream_rate_limit", "billing", "overloaded", "server_error", "timeout",
-})
+from hermes_cli.turn_exit import TRANSIENT_PROVIDER_REASONS as _TRANSIENT_PROVIDER_REASONS
 
 
 # ``failure_reason`` values a retry can never heal: the credential was rejected, the model does
@@ -137,9 +135,7 @@ _TRANSIENT_PROVIDER_REASONS = frozenset({
 # ``kanban.failure_limit`` is spent. ``billing`` stays transient: credit comes back.
 # ``upstream_blocked`` (a WAF/CDN refusing the SDK's User-Agent) is terminal too: only a
 # header change heals it, never a retry.
-_TERMINAL_PROVIDER_REASONS = frozenset({
-    "auth", "auth_permanent", "model_not_found", "ssl_cert_verification", "upstream_blocked",
-})
+from hermes_cli.turn_exit import TERMINAL_PROVIDER_REASONS as _TERMINAL_PROVIDER_REASONS
 
 
 def _single_query_exit_code(result, *, credentials_rate_limited: bool = False) -> int:
@@ -157,24 +153,13 @@ def _single_query_exit_code(result, *, credentials_rate_limited: bool = False) -
     (EX_CONFIG): the dispatcher blocks the card at once.
     """
     from cli import _TERMINAL_PROVIDER_REASONS, _TRANSIENT_PROVIDER_REASONS
-    if not isinstance(result, dict):
-        if credentials_rate_limited and os.environ.get("HERMES_KANBAN_TASK"):
-            from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
-            return KANBAN_RATE_LIMIT_EXIT_CODE
-        return 1
-    if result.get("interrupted"):
-        return 130
-    if not (result.get("failed") or result.get("partial") or result.get("completed") is False):
-        return 0
-    if os.environ.get("HERMES_KANBAN_TASK"):
-        reason = result.get("failure_reason")
-        if reason in _TRANSIENT_PROVIDER_REASONS:
-            from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
-            return KANBAN_RATE_LIMIT_EXIT_CODE
-        if reason in _TERMINAL_PROVIDER_REASONS:
-            from hermes_cli.kanban_db import KANBAN_TERMINAL_PROVIDER_EXIT_CODE
-            return KANBAN_TERMINAL_PROVIDER_EXIT_CODE
-    return 1
+    from hermes_cli.turn_exit import turn_exit_code
+    return turn_exit_code(
+        result, kanban_worker=bool(os.environ.get("HERMES_KANBAN_TASK")),
+        credentials_rate_limited=credentials_rate_limited,
+        transient_reasons=_TRANSIENT_PROVIDER_REASONS,
+        terminal_reasons=_TERMINAL_PROVIDER_REASONS,
+    )
 
 
 def _run_quiet_single_query(cli, effective_query, emitter=None):
diff --git a/hermes_cli/config_defaults.py b/hermes_cli/config_defaults.py
index b9456d8857..2d9533c168 100644
--- a/hermes_cli/config_defaults.py
+++ b/hermes_cli/config_defaults.py
@@ -2650,7 +2650,7 @@ DEFAULT_CONFIG = {
         # Extra ports detection probes for an external llama-server (besides 8080).
         "detect_ports": [],
     },
-    "_config_version": 47,  # Config schema version - bump this when adding new required fields
+    "_config_version": 48,  # Config schema version - bump this when adding new required fields
 }
 
 
diff --git a/hermes_cli/config_migrations.py b/hermes_cli/config_migrations.py
index fae59ce578..ac1ae0f4db 100644
--- a/hermes_cli/config_migrations.py
+++ b/hermes_cli/config_migrations.py
@@ -766,6 +766,10 @@ MIGRATIONS: Tuple[Tuple[int, Callable[[Dict[str, Any], bool], None]], ...] = (
             "admitted to the target profile's running gateway and tracked by receipt, so cron no "
             "longer runs (or times out) a Bot Chat turn of its own."),
         extra_guard=lambda raw: "bot_chat_delivery_timeout_seconds" in raw)),
+    # Runtime and main both used v46 for different migrations. Even a config
+    # already advanced to v47 can still carry the old disabled-server spelling.
+    # Reusing the idempotent conversion leaves already-migrated choices intact.
+    (48, _migrate_to_46),
 )
 
 #: Steps triggered by a legacy key or identifier (a renamed or retired key, a removed plugin or
diff --git a/hermes_cli/turn_exit.py b/hermes_cli/turn_exit.py
new file mode 100644
index 0000000000..56fdb40efe
--- /dev/null
+++ b/hermes_cli/turn_exit.py
@@ -0,0 +1,38 @@
+"""Turn-result exit policy shared by CLI views and authority-owned workers.
+
+Worker identity is supplied by the caller, not inferred from a daemon's environment.
+"""
+
+# Provider walls do not spend a Kanban task's retry budget.
+TRANSIENT_PROVIDER_REASONS = frozenset({
+    "rate_limit", "upstream_rate_limit", "billing", "overloaded", "server_error", "timeout",
+})
+# These failures need an operator change; the dispatcher parks the task immediately.
+TERMINAL_PROVIDER_REASONS = frozenset({
+    "auth", "auth_permanent", "model_not_found", "ssl_cert_verification", "upstream_blocked",
+})
+
+
+def turn_exit_code(
+    result, *, kanban_worker: bool, credentials_rate_limited: bool = False,
+    transient_reasons=TRANSIENT_PROVIDER_REASONS, terminal_reasons=TERMINAL_PROVIDER_REASONS,
+) -> int:
+    """Preserve completion, interruption and retryable/terminal provider outcomes."""
+    if not isinstance(result, dict):
+        if credentials_rate_limited and kanban_worker:
+            from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
+            return KANBAN_RATE_LIMIT_EXIT_CODE
+        return 1
+    if result.get("interrupted"):
+        return 130
+    if not (result.get("failed") or result.get("partial") or result.get("completed") is False):
+        return 0
+    if kanban_worker:
+        reason = result.get("failure_reason")
+        if reason in transient_reasons:
+            from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
+            return KANBAN_RATE_LIMIT_EXIT_CODE
+        if reason in terminal_reasons:
+            from hermes_cli.kanban_db import KANBAN_TERMINAL_PROVIDER_EXIT_CODE
+            return KANBAN_TERMINAL_PROVIDER_EXIT_CODE
+    return 1
diff --git a/hermes_state_runtime.py b/hermes_state_runtime.py
index d6c62ce800..3e9a883727 100644
--- a/hermes_state_runtime.py
+++ b/hermes_state_runtime.py
@@ -84,7 +84,8 @@ def begin_runtime_epoch(db, *, instance_id: str) -> int:
 
 
 def admit_session_input(db, *, epoch: int, principal_id: str, session_id: str,
-                        request_id: str, payload: dict, intent: str = 'queue') -> dict:
+                        request_id: str, payload: dict, intent: str = 'queue',
+                        _authorize_write=None) -> dict:
     for value in (principal_id, session_id, request_id):
         _text(value)
     if intent not in ('queue', 'steer', 'redirect'):
@@ -94,11 +95,19 @@ def admit_session_input(db, *, epoch: int, principal_id: str, session_id: str,
     retired = retry_terminal_admission(db, epoch=epoch, principal_id=principal_id, session_id=session_id,
         request_id=request_id, payload=payload, intent=intent)
     if retired is not None:
+        if _authorize_write is not None:
+            def authorize_retired(conn):
+                _epoch(conn, epoch)
+                if _authorize_write(conn) is not True:
+                    raise RuntimeStoreError('permission_denied')
+            db._execute_write(authorize_retired)
         return retired
     digest = admission_fingerprint(canonical_target=session_id, payload={'input': json.loads(encoded), 'intent': intent})
     def write(conn):
         _epoch(conn, epoch)
         _session(conn, session_id)
+        if _authorize_write is not None and _authorize_write(conn) is not True:
+            raise RuntimeStoreError('permission_denied')
         old = conn.execute('''SELECT * FROM session_admissions
             WHERE principal_id=? AND target_session_id=? AND request_id=?''', (principal_id, session_id, request_id)).fetchone()
         if old is not None:
diff --git a/tests/gateway/test_automation_notification_category.py b/tests/gateway/test_automation_notification_category.py
new file mode 100644
index 0000000000..6222ab6d26
--- /dev/null
+++ b/tests/gateway/test_automation_notification_category.py
@@ -0,0 +1,131 @@
+"""Diagnostic-only metadata survives the canonical FIFO, without inference."""
+from types import SimpleNamespace
+
+import pytest
+
+from gateway.config import GatewayConfig, Platform, PlatformConfig
+from gateway.platforms.event import MessageEvent
+from gateway.run import GatewayRunner
+from gateway.session_authority import SessionAuthority, initialize_session_authority
+from gateway.session_contract import Principal
+from hermes_state_runtime import RuntimeStoreError, get_session_admission, list_session_admissions
+
+
+async def _authority(tmp_path, monkeypatch):
+    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
+    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
+    monkeypatch.setattr('gateway.session_bot._watch_reply', lambda *args: None)
+    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
+    from hermes_state import SessionDB
+    db = SessionDB(tmp_path / 'state.db')
+    runner.session_store._db = db
+    runner._session_db = db
+    authority = await initialize_session_authority(runner, profile_id='default', instance_id='category-fixture',
+                                                  db=db)
+    return runner, authority
+
+
+def test_live_delivery_forwards_diagnostic_category(tmp_path, monkeypatch):
+    from tools import bot_live_delivery as live
+    calls = []
+    monkeypatch.setattr(live, 'authority_delivery', lambda home, params: calls.append(params) or {})
+    owner = dict(profile_home=str(tmp_path), session_id='bot', canonical=True,
+                 lease_id='fixture', live_session_id='bot')
+    live.deliver_to_live_owner(tmp_path, owner, 'warning', notification_category='diagnostic')
+    assert calls[0]['notification_category'] == 'diagnostic'
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize('category', ['result', 'diagnostic'])
+async def test_bot_category_is_committed_restored_and_part_of_retry_identity(tmp_path, monkeypatch, category):
+    from gateway.session_bot import deliver
+    from gateway.session_local import create_local_session
+    from gateway.session_local_title import title_new_session
+    from gateway.session_automation import restore_local_automation
+    runner, authority = await _authority(tmp_path, monkeypatch)
+    actor = Principal('test-owner', 'default', frozenset({'session:create', 'session:submit', 'session:read'}), 'fixture')
+    try:
+        ref = create_local_session(authority, actor, dict(request_id='bot', source='gui', model='fixture', toolsets=[]))
+        title_new_session(authority, ref, 'Bot Chat')
+        connection = SimpleNamespace(authority=authority, actor=actor)
+        params = dict(id='a' * 32, profile='default', message='notice', notification_category=category)
+        first = await deliver(connection, params)
+        retry = await deliver(connection, params)
+        assert first['admission_id'] == retry['admission_id']
+        # Re-open the actual on-disk receipt through the production readback
+        # helper; substitute only transport, then exercise the real owner gate.
+        from tools import bot_live_delivery as live
+        monkeypatch.setattr(live, 'authority_delivery', lambda home, request: request)
+        replay = live.read_delivery_result(tmp_path, params['id'])
+        assert replay is not None
+        assert replay.get('notification_category', 'result') == category
+        assert (await deliver(connection, replay))['admission_id'] == first['admission_id']
+        row = get_session_admission(authority.db, admission_id=first['admission_id'])
+        assert row is not None
+        descriptor = row['payload']['local_automation_v1']
+        restored = restore_local_automation(authority, ref, row)
+        if category == 'diagnostic':
+            assert descriptor['notification_category'] == 'diagnostic'
+            assert restored.metadata['notification_category'] == 'diagnostic'
+        else:
+            assert 'notification_category' not in descriptor
+            assert 'notification_category' not in restored.metadata
+            # Omitted default retains the old request fingerprint.
+            assert (await deliver(connection, {k: v for k, v in params.items() if k != 'notification_category'}))['admission_id'] == first['admission_id']
+        with pytest.raises(RuntimeStoreError, match='admission_conflict'):
+            await deliver(connection, {**params, 'notification_category': 'result' if category == 'diagnostic' else 'diagnostic'})
+        assert len(list_session_admissions(authority.db, session_id=ref.session_id, pending_only=False)) == 1
+    finally:
+        authority.db.close()
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize('category', [None, [], {}, 'other'])
+async def test_bot_invalid_category_refuses_before_admission(tmp_path, monkeypatch, category):
+    from gateway.session_bot import deliver
+    runner, authority = await _authority(tmp_path, monkeypatch)
+    actor = Principal('test-owner', 'default', frozenset({'session:submit'}), 'fixture')
+    try:
+        with pytest.raises(RuntimeStoreError, match='invalid_params'):
+            await deliver(SimpleNamespace(authority=authority, actor=actor),
+                          dict(id='a' * 32, profile='default', message='notice', notification_category=category))
+        assert authority.db.get_session_by_title('Bot Chat') is None
+    finally:
+        authority.db.close()
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize('category', ['result', 'diagnostic'])
+async def test_native_automation_category_roundtrip(tmp_path, monkeypatch, category):
+    from gateway.session_ingress_context import native_callback, register_transport_home
+    from gateway.session_envelope import restore_native
+    from plugins.platforms.discord.adapter import DiscordAdapter
+    runner, authority = await _authority(tmp_path, monkeypatch)
+    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
+    runner.adapters = {Platform.DISCORD: adapter}
+    runner._wire_adapter_handlers(adapter)
+    register_transport_home(runner, None, tmp_path)
+    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
+    source = adapter.build_source(chat_id='42', chat_type='dm', user_id='42')
+    human = MessageEvent(text='human', source=source, message_id='human')
+    try:
+        with native_callback(runner, human, tmp_path):
+            await authority.admit_native(human)
+        route = runner.session_store._generate_session_key(source)
+        sid = runner.session_store.peek_session_id(route)
+        event = MessageEvent(text='notice', source=source, internal=True,
+            metadata={'gateway_session_key': route, 'gateway_session_id': sid, 'notification_category': category})
+        receipt = await authority.admit_automation(adapter, event, 'notice')
+        row = get_session_admission(authority.db, admission_id=receipt.admission_id)
+        assert row is not None
+        descriptor = row['payload']['native_text_v1']['automation']
+        restored = restore_native(row['payload'], runner)
+        assert restored.internal
+        assert descriptor.get('notification_category', 'result') == category
+        assert restored.metadata.get('notification_category', 'result') == category
+        if category == 'result':
+            assert 'notification_category' not in descriptor
+            event.metadata.pop('notification_category')
+            assert (await authority.admit_automation(adapter, event, 'notice')).admission_id == receipt.admission_id
+    finally:
+        authority.db.close()
diff --git a/tests/gateway/test_kanban_result_exit.py b/tests/gateway/test_kanban_result_exit.py
new file mode 100644
index 0000000000..bb5f8df59d
--- /dev/null
+++ b/tests/gateway/test_kanban_result_exit.py
@@ -0,0 +1,48 @@
+"""Canonical Kanban receipts retain the shared one-shot outcome semantics."""
+import json
+import os
+from types import SimpleNamespace
+
+import pytest
+
+from gateway import session_kanban
+from hermes_cli import kanban_db as kb
+from hermes_cli.kanban_db_connect import connect_closing
+
+
+@pytest.mark.parametrize(
+    "result, expected",
+    [
+        ({"completed": True}, 0),
+        ({"failed": True}, 1),
+        ({"partial": True}, 1),
+        ({"completed": False}, 1),
+        ({"interrupted": True}, 130),
+        (None, 1),
+        *[({"failed": True, "failure_reason": reason}, kb.KANBAN_RATE_LIMIT_EXIT_CODE)
+          for reason in ("rate_limit", "upstream_rate_limit", "billing", "overloaded", "server_error", "timeout")],
+        *[({"failed": True, "failure_reason": reason}, kb.KANBAN_TERMINAL_PROVIDER_EXIT_CODE)
+          for reason in ("auth", "auth_permanent", "model_not_found", "ssl_cert_verification", "upstream_blocked")],
+    ],
+)
+def test_managed_result_persists_exact_worker_exit(tmp_path, monkeypatch, result, expected):
+    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
+    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
+    kb.init_db()
+    path = kb.kanban_db_path()
+    with connect_closing(path) as conn:
+        task_id = kb.create_task(conn, title="private fixture", assignee="default")
+        task = kb.claim_task(conn, task_id, claimer="private-host:owner")
+        assert task is not None
+        context = {"db": str(path), "task_id": task_id,
+                   "run_id": task.current_run_id, "claim_lock": task.claim_lock}
+        with kb.write_txn(conn):
+            kb._append_event(conn, task_id, "worker_bound",
+                             {"pid": os.getpid(), "claim_lock": task.claim_lock},
+                             run_id=task.current_run_id)
+    monkeypatch.setattr(session_kanban, "_run_task_turns", lambda *_args: result)
+    frame = {"policy": {"kanban_json": json.dumps(context)}}
+
+    assert session_kanban.run_worker_turns(SimpleNamespace(), frame, []) is result
+    assert session_kanban.worker_exit_code(path, context) == expected
+    assert session_kanban.worker_exit_code(path, context | {"claim_lock": "foreign"}) == 1
diff --git a/tests/gateway/test_session_hosted_rpc.py b/tests/gateway/test_session_hosted_rpc.py
index 726b1b7d96..9ff6a645a2 100644
--- a/tests/gateway/test_session_hosted_rpc.py
+++ b/tests/gateway/test_session_hosted_rpc.py
@@ -6,6 +6,90 @@ from types import SimpleNamespace
 import pytest
 
 
+@pytest.mark.parametrize('member_target', [{}, {'target': None}], ids=['missing-target', 'null-target'])
+@pytest.mark.parametrize('revocation', [None, 'member', 'room_epoch', 'owner', 'task_generation', 'stopping', 'disbanded', 'malformed_target'])
+def test_local_hosted_member_revoked_during_preparation_is_not_admitted(owner, monkeypatch, revocation, member_target):
+    """Revocation must fence admission, not merely pause execution afterward."""
+    import concurrent.futures
+    import json
+    from pathlib import Path
+    import time
+
+    from gateway import hosted_room_driver as tasks, session_hosted_attachments
+    from gateway.hosted_rooms import create_room, local_authority_gateway_id
+    from gateway.session_hosted_service import CanonicalHostedRoomService
+    from hermes_state_runtime import RuntimeStoreError, list_session_admissions
+    from tui_gateway.hosted_room_driver import HostedRoomBinding
+
+    authority, loop, _, _ = owner
+    service = CanonicalHostedRoomService(authority, loop)
+    # The component fixture uses a synthetic profile identifier, not a daemon home.
+    monkeypatch.setattr(service, 'profile_homes', lambda: {'default': Path(authority.profile_id)})
+    service.authorize_room('alice', 'room', create=True)
+    gateway = local_authority_gateway_id()
+    members = [
+        {'member_id': 'one', 'profile': 'default', 'handle': 'one', **member_target},
+        {'member_id': 'two', 'profile': 'other', 'handle': 'two'},
+    ]
+    create_room(authority.db.db_path, room_id='room', name='Room',
+                authority_gateway_id=gateway, members=members)
+    identity = tasks.TaskIdentity('room', 'task', 'thread', 'turn')
+    payload = {'target_profile': 'default', 'target_member_id': 'one',
+               'source_event_seq': 1, 'prompt': 'frozen'}
+    tasks.admit_task(authority.db.db_path, identity, payload=payload, clock=time.time)
+    lease = tasks.acquire_lease(authority.db.db_path, room_id='room', gateway_id=gateway,
+        authority_epoch=1, process_generation='test', ttl_seconds=120, clock=time.time)
+    tasks.start_task(authority.db.db_path, identity, lease,
+                     expected_cancel_generation=0, clock=time.time)
+    task, = tasks.list_tasks(authority.db.db_path, room_id='room')
+    rpc = service._resolve_member_transport(HostedRoomBinding('room', gateway, 1), task)
+    coords = {'profile': 'default', 'source': 'bot_room'}
+    sid = rpc.create(**coords, title='Group: room')['session_id']
+    preparing, release = threading.Event(), threading.Event()
+    original = session_hosted_attachments.submission_payload
+
+    def paused_preparation(*args, **kwargs):
+        preparing.set()
+        assert release.wait(10), 'test did not release preparation'
+        return original(*args, **kwargs)
+
+    monkeypatch.setattr(session_hosted_attachments, 'submission_payload', paused_preparation)
+    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
+        submitted = executor.submit(rpc.submit, **coords, session_id=sid, prompt='frozen',
+            task=identity, execution_generation=task['execution_generation'],
+            on_terminal=lambda receipt: None)
+        try:
+            assert preparing.wait(10), 'producer never reached preparation'
+            if revocation is not None:
+                if revocation == 'malformed_target':
+                    members[0]['target'] = []
+                else:
+                    members[0] = {'member_id': 'replacement', 'profile': 'default', 'handle': 'replacement'}
+                mutations = {
+                    'member': ('UPDATE hosted_rooms SET members_json=? WHERE room_id=?', (json.dumps(members), 'room')),
+                    'room_epoch': ('UPDATE hosted_rooms SET authority_epoch=2 WHERE room_id=?', ('room',)),
+                    'owner': ('UPDATE state_meta SET value=? WHERE key=?', ('bob', 'gateway.hosted.owner.v1:room')),
+                    'task_generation': ('UPDATE hosted_room_driver_tasks SET execution_generation=2 WHERE room_id=?', ('room',)),
+                    'stopping': ("UPDATE hosted_room_driver_tasks SET status='stopping' WHERE room_id=?", ('room',)),
+                    'disbanded': ('UPDATE hosted_rooms SET disbanded_at=? WHERE room_id=?', (time.time(), 'room')),
+                    'malformed_target': ('UPDATE hosted_rooms SET members_json=? WHERE room_id=?', (json.dumps(members), 'room')),
+                }
+                sql, args = mutations[revocation]
+                authority.db._execute_write(lambda conn: conn.execute(sql, args))
+        finally:
+            release.set()
+        if revocation is None:
+            receipt = submitted.result(timeout=10)
+            retry = rpc.submit(**coords, session_id=sid, prompt='frozen', task=identity,
+                execution_generation=task['execution_generation'], on_terminal=lambda receipt: None)
+            assert retry['admission_id'] == receipt['admission_id']
+        else:
+            with pytest.raises(RuntimeStoreError, match='permission_denied'):
+                submitted.result(timeout=10)
+    rows = list_session_admissions(authority.db, session_id=sid, pending_only=False)
+    assert len(rows) == (1 if revocation is None else 0)
+
+
 @pytest.fixture
 def owner(tmp_path, monkeypatch):
     from gateway.config import GatewayConfig
diff --git a/tests/hermes_cli/test_kanban_managed_exit_reconciliation.py b/tests/hermes_cli/test_kanban_managed_exit_reconciliation.py
new file mode 100644
index 0000000000..d1d18d05f7
--- /dev/null
+++ b/tests/hermes_cli/test_kanban_managed_exit_reconciliation.py
@@ -0,0 +1,41 @@
+"""Join authority-owned worker receipts with main's exit classification."""
+import pytest
+
+from hermes_cli import kanban_db as kb
+from hermes_cli import kanban_db_dispatch as dispatch
+
+
+@pytest.mark.parametrize(
+    "receipt, reaped, logged, event, rate_limited, terminal_provider",
+    [
+        (0, ("unknown", None), None, "protocol_violation", False, False),
+        (kb.KANBAN_RATE_LIMIT_EXIT_CODE, ("nonzero_exit", 1), None,
+         "rate_limited", True, False),
+        (kb.KANBAN_TERMINAL_PROVIDER_EXIT_CODE, ("unknown", None), None,
+         "crashed", False, True),
+        (3, ("unknown", None), None, "crashed", False, False),
+        (None, ("nonzero_exit", 3), None, "crashed", False, False),
+        (None, ("unknown", None), kb.KANBAN_RATE_LIMIT_EXIT_CODE,
+         "rate_limited", True, False),
+    ],
+)
+def test_dead_worker_classification_accepts_managed_and_legacy_results(
+    monkeypatch, receipt, reaped, logged, event, rate_limited, terminal_provider,
+):
+    monkeypatch.setattr(dispatch, "_classify_worker_exit", lambda _pid: reaped)
+    monkeypatch.setattr(dispatch, "_worker_log_exit_code", lambda *_a, **_k: logged)
+    monkeypatch.setattr(dispatch, "_worker_final_output", lambda *_a, **_k: "worker detail")
+
+    result = dispatch._classify_dead_worker(
+        900001, "private-host:owner", receipt, task_id="private-task", board="private-board",
+    )
+
+    assert result.event_kind == event
+    assert result.rate_limited is rate_limited
+    assert result.terminal_provider is terminal_provider
+    expected_code = receipt if receipt is not None else reaped[1] if reaped[1] is not None else logged
+    assert result.event_payload["exit_code"] == expected_code
+    if rate_limited:
+        assert "worker_output" not in result.event_payload
+    else:
+        assert result.event_payload["worker_output"] == "worker detail"
diff --git a/tests/hermes_cli/test_runtime_config_migration_join.py b/tests/hermes_cli/test_runtime_config_migration_join.py
new file mode 100644
index 0000000000..bea440505e
--- /dev/null
+++ b/tests/hermes_cli/test_runtime_config_migration_join.py
@@ -0,0 +1,48 @@
+"""Both parent branches used config v46 for different transformations."""
+import pytest
+import yaml
+
+from hermes_cli.config import DEFAULT_CONFIG, check_config_version, migrate_config
+
+
+@pytest.mark.parametrize("parent", ["before_either", "main_v46", "runtime_v46", "runtime_v47"])
+def test_join_migrates_both_parent_histories(tmp_path, monkeypatch, parent):
+    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
+    cron = {"max_parallel_jobs": 2}
+    server = {"command": "fixture-only-not-executed", "args": ["--example"]}
+    if parent not in ("runtime_v46", "runtime_v47"):
+        cron["bot_chat_delivery_timeout_seconds"] = 900
+    if parent == "main_v46":
+        server["enabled"] = False
+    else:
+        server.update(enabled=True, disabled=True)
+    raw = {
+        "_config_version": {"before_either": 45, "runtime_v47": 47}.get(parent, 46),
+        "cron": cron,
+        "mcp_servers": {"legacy": server, "active": {"command": "also-inert", "enabled": True}},
+    }
+    path = tmp_path / "config.yaml"
+    path.write_text("# preserve user comment\n" + yaml.safe_dump(raw), encoding="utf-8")
+
+    results = migrate_config(interactive=False, quiet=True)
+
+    migrated = yaml.safe_load(path.read_text(encoding="utf-8"))
+    assert "bot_chat_delivery_timeout_seconds" not in migrated["cron"]
+    assert migrated["cron"]["max_parallel_jobs"] == 2
+    assert migrated["mcp_servers"]["legacy"] == {
+        "command": "fixture-only-not-executed", "args": ["--example"], "enabled": False,
+    }
+    assert migrated["mcp_servers"]["active"] == {"command": "also-inert", "enabled": True}
+    assert "# preserve user comment" in path.read_text(encoding="utf-8")
+    assert migrated["_config_version"] == DEFAULT_CONFIG["_config_version"] > 46
+    assert check_config_version() == (DEFAULT_CONFIG["_config_version"],) * 2
+    assert not results["warnings"]
+    assert any("bot_chat_delivery_timeout_seconds" in note for note in results["config_added"]) == (parent not in ("runtime_v46", "runtime_v47"))
+    assert any("disabled → enabled: false" in note for note in results["config_added"]) == (parent != "main_v46")
+
+    before = path.read_bytes()
+    repeated = migrate_config(interactive=False, quiet=True)
+    assert path.read_bytes() == before
+    assert not any("bot_chat_delivery_timeout_seconds" in note or "disabled → enabled: false" in note
+                   for note in repeated["config_added"])
+    assert repeated["warnings"] == []
diff --git a/tools/bot_live_delivery.py b/tools/bot_live_delivery.py
index 8da2846f82..f6d108b5a8 100644
--- a/tools/bot_live_delivery.py
+++ b/tools/bot_live_delivery.py
@@ -235,11 +235,14 @@ def deliver_to_live_owner(
     pinned = _owner(profile_home, owner)
     if not isinstance(message, str):
         raise ValueError("message must be a string")
+    if notification_category not in ('result', 'diagnostic'):
+        raise ValueError("invalid notification category")
     home = Path(profile_home).resolve()
     return authority_delivery(home, dict(id=_delivery_id(delivery_id if delivery_id is not None else uuid.uuid4().hex),
         profile=home.name if home.parent.name == "profiles" else "default",
         message=message, **({"session_id": pinned["session_id"]} if pinned["session_id"] else {}),
-        **({"author": dict(author)} if author else {})))
+        **({"author": dict(author)} if author else {}),
+        **({"notification_category": "diagnostic"} if notification_category == "diagnostic" else {})))
 
 
 def claim_pending_delivery(profile_home, owner):
@@ -281,10 +284,12 @@ def read_delivery_result(profile_home: Path | str, delivery_id: str) -> dict[str
     record = _read(_root(profile_home) / f"{_delivery_id(delivery_id)}.json")
     if record is not None and record.get('admission_id'):
         home = Path(profile_home).resolve()
-        # The authority compares the stored author to the retry payload; omitting it is a conflict.
+        # Readback replays the immutable envelope; dropping category or author conflicts.
         return authority_delivery(home, dict(id=delivery_id,
             profile=home.name if home.parent.name == 'profiles' else 'default', message=record['message'],
-            **({'author': dict(record['author'])} if record.get('author') else {})))
+            **({'author': dict(record['author'])} if record.get('author') else {}),
+            **({'notification_category': record['notification_category']}
+               if 'notification_category' in record else {})))
     return record
 
 
```
