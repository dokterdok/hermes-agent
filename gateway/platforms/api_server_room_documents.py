"""Document admission recovery: a reserved Run is not an accepted canonical input."""
import hashlib
import json

from hermes_state_runtime import RuntimeStoreError


def recover_unaccepted(adapter, record, *, scope, key, fingerprint, session_id):
    """True only after retiring a dead owner's demonstrably unaccepted reservation.

    A live/unknown owner or incomplete canonical evidence is uncertainty, never
    permission to ask for new bytes or run again. Accepted/retired input wins.
    """
    from gateway.session_authorities import active_authority
    from hermes_state_terminal import identity_key
    authority = active_authority(adapter.gateway_runner)
    if authority is None:
        raise RuntimeStoreError('storage_unavailable')
    authority._require_admission_open()
    run_id = record['run_id']
    with authority.db._read_ctx() as conn:
        rows = conn.execute("SELECT target_session_id FROM session_admissions WHERE principal_id='api' AND request_id=?", (run_id,)).fetchall()
        retired = conn.execute('SELECT value FROM state_meta WHERE key=?',
                               (identity_key('api', session_id, run_id),)).fetchone()
        projected = conn.execute("SELECT 1 FROM logical_attempts WHERE principal_id='api' AND request_id=?", (run_id,)).fetchone()
        if projected and not rows and not retired:
            raise RuntimeStoreError('room_document_outcome_unknown')
        if rows or retired:
            if rows and (len(rows) != 1 or rows[0][0] != session_id):
                raise RuntimeStoreError('admission_conflict')
            return False
    if record.get('status', {}).get('status') not in {'queued', 'interrupted'}:
        raise RuntimeStoreError('room_document_outcome_unknown')
    pid, started = record.get('owner_pid'), record.get('owner_started')
    if type(pid) is not int or pid <= 0 or type(started) is not int or started <= 0:
        raise RuntimeStoreError('room_document_outcome_unknown')
    from gateway.status import _pid_exists, get_process_start_time, start_time_fingerprints_match
    try:
        exists = _pid_exists(pid)
        current = get_process_start_time(pid) if exists else None
        dead = not exists or (current and not start_time_fingerprints_match(started, current))
    except (OSError, ValueError, TypeError):
        dead = False
    if not dead:
        raise RuntimeStoreError('room_document_preparing')
    if not adapter._run_idempotency_store.forget_unaccepted(scope, key, fingerprint, record):
        raise RuntimeStoreError('room_document_outcome_unknown')
    return True


def accepted_document_run(adapter, *, run_id, session_id, dispatch, scope):
    """A missing/expired HTTP receipt cannot turn accepted or retired input into a new run."""
    from gateway.session_authorities import active_authority
    from hermes_state_terminal import identity_key
    authority = active_authority(adapter.gateway_runner)
    if authority is None:
        raise RuntimeStoreError('storage_unavailable')
    authority._require_admission_open()
    with authority.db._read_ctx() as conn:
        rows = conn.execute("SELECT * FROM session_admissions WHERE principal_id='api' AND request_id=?", (run_id,)).fetchall()
        retired = conn.execute('SELECT value FROM state_meta WHERE key=?',
                               (identity_key('api', session_id, run_id),)).fetchone()
        projected = conn.execute("SELECT 1 FROM logical_attempts WHERE principal_id='api' AND request_id=?", (run_id,)).fetchone()
        if not rows:
            logical = conn.execute("""SELECT 1 FROM logical_attempts WHERE principal_id='api' AND session_id=?
                AND owner_scope=? AND task_id=? AND execution_generation=?""",
                (session_id, scope, dispatch['task_id'], dispatch['execution_generation'])).fetchone()
            if retired or projected or logical:
                raise RuntimeStoreError('room_document_outcome_unknown')
            return None
        if len(rows) != 1 or rows[0]['target_session_id'] != session_id:
            raise RuntimeStoreError('admission_conflict')
        row = rows[0]
        try:
            data = json.loads(row['payload_json'])['api_turn_v1']
            if data['settings']['room_dispatch'] != dispatch or data['run_owner_scope'] != scope:
                raise RuntimeStoreError('admission_conflict')
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, RuntimeStoreError):
                raise
            raise RuntimeStoreError('room_document_outcome_unknown') from exc
        return ({'started': 'running', 'unknown': 'interrupted'}.get(row['status'], row['status'])
                if row['status'] != 'terminal' else row['outcome'])


async def prepare_peer_files(self, request, room_dispatch, *, idempotency_scope, idempotency_key,
                                 session_id, gateway_session_key, _openai_error):
    """Resolve accepted peer work first; only a new input-bearing attempt prepares transferred bytes."""
    from gateway.platforms.api_server_room_grants import _json_error
    from gateway.platforms.api_server_runs import _accepted_response
    # A missing batch is only reported after the exact accepted-run lookup above.
    # The authenticated manifest remains in the fingerprint; transfer bytes never do.
    document_run_id = "run_" + hashlib.sha256((idempotency_scope + "\0" + idempotency_key).encode()).hexdigest()[:32]
    try:
        accepted_status = accepted_document_run(self, run_id=document_run_id, session_id=session_id,
                                                dispatch=room_dispatch, scope=idempotency_scope)
    except RuntimeStoreError as exc:
        return None, _json_error(_openai_error, exc.reason, code=exc.reason,
                           status=409 if exc.reason == "admission_conflict" else 503)
    if accepted_status is not None:
        return None, _accepted_response(document_run_id, accepted_status, gateway_session_key, replayed=True)
    if not room_dispatch.get("document_inputs"):
        return None, None
    from gateway.hosted_room_documents import advertised_capability, manifest
    limits = advertised_capability(self)
    try:
        if limits is None:
            raise ValueError("document inputs unavailable")
        manifest(room_dispatch["document_inputs"], member_id=room_dispatch["member_id"], capability=limits)
    except ValueError:
        return None, _json_error(_openai_error, "This document batch is not supported.",
                           code="unsupported_room_document_input", status=409)
    if request.get("room_document_bytes") is None:
        return None, _json_error(_openai_error, "This attempt requires its document bytes.",
                           code="room_document_input_required", status=409)
    from gateway.hosted_room_documents import decode_batch
    try:
        document_bytes = decode_batch(room_dispatch["document_inputs"], request["room_document_bytes"])
    except ValueError:
        return None, _json_error(_openai_error, "Invalid document transfer.", code="invalid_room_document_input", status=400)
    from gateway.hosted_room_peer import HostedMemberDispatch
    await self._ensure_hosted_member_session(HostedMemberDispatch.from_mapping(room_dispatch))
    return document_bytes, None


def lookup_run_response(adapter, request, *, scope, key, fingerprint, session_id,
                        gateway_session_key, peer_files, _openai_error):
    """Replay an existing keyed Run, recovering only proven unaccepted document reservations."""
    if not key:
        return None
    from gateway.platforms.api_server_room_grants import _json_error
    from gateway.platforms.api_server_runs import _replay_or_conflict, _room_retention_until
    outcome, record = adapter._run_idempotency_store.lookup(
        scope, key, fingerprint, retention_until=_room_retention_until(request))
    if outcome == "reused" and record is not None and peer_files:
        try:
            if recover_unaccepted(adapter, record, scope=scope, key=key,
                                  fingerprint=fingerprint, session_id=session_id):
                outcome, record = "missing", None
        except RuntimeStoreError as exc:
            return _json_error(_openai_error, exc.reason, code=exc.reason, status=503)
    if outcome == "conflict" or (outcome == "reused" and record is not None):
        return _replay_or_conflict(adapter, request, outcome, record, gateway_session_key, _openai_error)
    return None
