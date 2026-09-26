# A6 adversarial review — dokterdok/hermes-agent draft #7

Owner delta versus supplier `03ab73504dae640e8cfef0b19841c7053aa31cbe`.
Claimed Tower intent: `5c90081c171c2c7a3d04fb7d1fb244dbfd4f9b18` then
`ced83ae886f61b44636dbcf72d047afa2d9b6a05`. Prior closeout PASS was a hypothesis.

Public #109338 (`fix/canonical-legacy-group-gates-20260912` @
`acf21665bcecf0edf27e117a35b9338a34f567c3`) was not rewritten. The archive ref
`archive/pr109338-before-replacement-20260924` is the same OID. This review
does not authorize that publication.

## Review 1 — OPEN

Tip reviewed: `ced83ae886f61b44636dbcf72d047afa2d9b6a05`.

### In scope, confirmed

1. **Never-active prune dropped routes, then raised.**
   `prune_never_active_keyed_sessions` deleted `gateway_routing` for every
   candidate, then called `delete_session`. A terminal ledger row raises
   `SessionLedgerProtectedError`. Eligible rows were already gone, and the
   retained row lost its route. Reproduced: candidates `owned` + `junk`,
   exception raised, sessions left `['owned']`, routes `[]`.
   `hermes sessions prune --never-active` listed those rows
   (`list_never_active_keyed_sessions` had no `exclude_ledger_owned`) and
   `cmd_sessions` then mapped the raise to exit 1 after the partial apply.
   Ordinary prune/archive was already correct: prune preview passes
   `exclude_ledger_owned`, commit uses `protected_session_ids`, archive does
   not exclude ledger rows.

2. **Explicit delete of a terminal ledger row was unmapped on four callers.**
   Live rows already failed closed. Terminal rows are the A6 refusal
   (`runtime_coordination_required`), and these callers did not surface it:
   - `gateway/platforms/api_server.py` `_handle_delete_session` — uncaught, HTTP 500.
   - `tui_gateway/methods_session.py` `session.delete` — blanket `Exception` → 5036.
   - `hermes_cli/sessions_cmd_browse.py` — every exception became "Delete failed."
   - `hermes_cli/cli_tui_runtime_mixin.py` `/exit --delete` — debug log only.
   CLI `cmd_sessions` and dashboard `_with_db` already map the refusal
   (exit 1 / HTTP 409). Explicit bulk `delete_sessions` still refuses the
   whole batch. That contract stays.

### Checked, not findings

- **False-green tests.** The 30-case gate uses real SQLite stores.
  `FakeDB` / lease stubs that set `skipped_protected=0` only accept the new
  keyword so older caller tests still run. They do not claim to prove
  protection. `test_http_prune_commit_reports_transaction_protection` reported
  the HTTP counters without re-reading the store; sibling tests already
  re-read. The commit test now re-reads too.
- **Lower-owner implementations.** `hermes_state_raw_delete.py` is the A6
  guard. `retire_sessions`, `check_native_route`, and the canonical
  `mutate_session` path are consumed, not reimplemented. This fix calls
  `delete_session` / `protected_session_ids` / `retire_routes` inside that
  delete. It does not copy them.
- **Path toward rewriting #109338.** The draft branch is
  `03ab73504d..ced83ae886` plus this fix commit. No push of
  `fix/canonical-legacy-group-gates-20260912`. Both the public branch and the
  archive ref were re-read at `acf21665bcec` before the fix commit.
- **Attribution.** `5c90081` and `ced83ae` stay authored by David Dudok de Wit
  and still cite `acf21665`, `1403ca51`, and `29b11c1`. `fangliquanflq` history
  stays on the held public interval. It is not in this delta. Existing commit
  messages were not rewritten.

## Review 2 — CLEAN

The fix commit on this branch addresses both open items. Re-read of that diff:

- Never-active preview passes `exclude_ledger_owned` and prints
  `skipped_protected` with the same wording as ordinary prune.
- The sweep classifies ledger owners first, skips them, and does not raise.
  Their routes stay. A route is counted only after `delete_session` commits
  (`retire_routes` drops it after the guard). A ledger row that appears in
  the race is skipped and keeps its route. A row that is already gone still
  drops a stale route.
- `protected_delete_refusal` is the shared `(message, reason)` pair.
  API DELETE maps it to HTTP 409. TUI `session.delete` maps it to 4033
  (`data.code = runtime_coordination_required`); 4023, 4028, 4029, and 4091
  stay their existing meanings. Unrelated exceptions stay 5036 / propagated.
  Browse flashes the refusal text. `/exit --delete` prints `Refused:`.
  Other browse failures stay "Delete failed." Other exit failures stay a
  debug log.
- `delete_sessions` still refuses the whole explicit batch. Onboarding clear
  still uses that path. Archive still includes ledger rows. Canonical
  `mutate_session` / `retire_sessions` were not edited.
- No new core tool, no new env var, no prompt-cache change, no public-history
  rewrite.

Focused gate after the fix (isolated `HOME` / `HERMES_HOME` / `TMPDIR` /
`--basetemp`, `scripts/run_tests.sh`, `HERMES_TEST_WORKERS=1`,
`HERMES_TEST_FILE_RETRIES=0`):

`tests/hermes_state/test_raw_maintenance_readonly.py` 20 passed,
`tests/hermes_cli/test_raw_maintenance_reporting.py` 16 passed.
**36 passed, 0 failed**, 2 files, 8.0s.

`tests/hermes_state/test_never_active_keyed_prune.py` (sibling contract for
the same sweep, not part of the focused gate): 17 passed, 0 failed.

`#109338` history representation remains **HELD**.

Re-review count: 2.
