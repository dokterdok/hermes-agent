# Adversarial review — A3 owner-union / revocation guard

Parent: `46c3ce81784406af7a742f9d79e121c3917e584e`  
Reviewed tip: `75a8cc67866ab93117a4ccfbcf637fb0c350f037`  
Re-review count: 2 (round 1 open, round 2 after the fix)

Prior local PASS claims were not treated as evidence.

## Round 1 — open

### F1 — P1 — ownerless refusal relabeled, HTTP test false-green

- Path: `gateway/platforms/api_server_room_replay.py` `_require_unaccepted`; `tests/gateway/test_route_canonical_http.py`
- Mechanism: when `active_authority` is `None`, the parent raises `storage_unavailable` (HTTP 503). Tip `75a8cc` raised `canonical_room_peer_unsupported` instead. `_room_dispatch_error` then returns HTTP 403 `invalid_room_dispatch`. That reason is the one `root_target` uses for “not a canonical peer”. Route’s own comment on `_canonical_room_peer` says a missing owner is unavailable, not permission to leave this owner. The focused test nulled the registry slot and asserted only status 403, so it passed because of the relabel.
- RED: after restoring `storage_unavailable` and before changing the test, `test_route_canonical_http.py` failed: body `code=storage_unavailable`, `assert 503 == 403`.

### Checked, not open

- Post-prepare authorizer is present in `gateway/session_hosted_rpc.py` after `await prepare_hosted_input` and before Output `new_admission_authorizer` and `authority.submit`. The dispatch-time check in `_dispatch_owned` remains. `is not True` rejects any non-literal True.
- RED: with that post-prepare check deleted, `test_hosted_task_revocation.py` failed at `isinstance(None, RuntimeStoreError)` (the revoked submit admitted). Restoring the check is required for green.
- `admit_session_input` runs `_authorize_write` only on the NEW path, before `INSERT`. Existing-row return does not call it.
- `hosted_session_id` and `prospective_room_session` use the same session id, so the logical-index absence check is on the session that admission binds.
- Text-only `prepare_hosted_input` does not create an `AcceptedInputHandle`. A denied submit therefore has no accepted handle to replay.
- Custody/index bootstrap in the HTTP test calls `prepare_logical_attempt_index` because an unprepared index is `storage_unavailable`. That matches `initialize_session_authority`. It does not skip the check.
- The 58-path owner composition is the lane. Donor internals were not re-audited as new product.

## Round 2 — re-review of the fix

F1 closed:

- `storage_unavailable` is restored when the launch authority is missing. No NEW admission is certified from that branch.
- The HTTP test now separates the two refusals. Clearing only `runner.session_authority` (registry slot kept) is `root_target` → HTTP 403 message `canonical_room_peer_unsupported`, and `scheduled` stays 1. Nulling the registry slot afterwards is HTTP 503 `storage_unavailable`, and `scheduled` stays 1. A relabel back to peer-unsupported fails the 503 assertion.

Focused diagnostic pytest after the fix: `2 passed in 3.70s` (`test_hosted_task_revocation.py`, `test_route_canonical_http.py`). Not `scripts/run_tests.sh`.

### H1 — HELD — do not move the only task recheck into the write hook

- Path: `gateway/session_hosted_rpc.py` `_submit`; `gateway/session_authority.py` `submit`; `hermes_state_runtime.py` `admit_session_input`
- Mechanism: `submit` has no `await` before `admit_session_input`, but a thread can still change authorizer state during the synchronous normalize/SQLite section after the post-prepare check returns True. Putting the task authorizer only inside `_authorize_write` would miss that race’s important sibling: existing-row replay returns before the hook, and `submit` still calls `_schedule`. Revoked retries would schedule.
- Protected action: keep the post-prepare `authorizer('submit', task, generation) is True` check in `_submit`, before `authority.submit`. Do not delete it in favor of the NEW-only write hook.

## Open findings

None.

Adversarial review: HELD (one protected residual, zero open findings).
