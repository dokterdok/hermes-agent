"""Document admission recovery: a reserved Run is not an accepted canonical input."""
import hashlib
import json

from hermes_state_runtime import RuntimeStoreError


def _canonical_evidence(conn, *, run_id, session_id, scope=None, dispatch=None):
    from hermes_state_terminal import identity_key
    rows = conn.execute("SELECT * FROM session_admissions WHERE principal_id='api' AND request_id=?", (run_id,)).fetchall()
    retired = conn.execute('SELECT value FROM state_meta WHERE key=?',
                           (identity_key('api', session_id, run_id),)).fetchone()
    projected = conn.execute("SELECT 1 FROM logical_attempts WHERE principal_id='api' AND request_id=?", (run_id,)).fetchone()
    logical = None
    if dispatch is not None and not rows:
        logical = conn.execute("""SELECT 1 FROM logical_attempts WHERE principal_id='api' AND session_id=?
            AND owner_scope=? AND task_id=? AND execution_generation=?""",
            (session_id, scope, dispatch['task_id'], dispatch['execution_generation'])).fetchone()
    return rows, retired, projected, logical


def require_canonical_absence(adapter, *, run_id, scope, session_id, task_id, execution_generation):
    """Absence must cover live, retired and certified logical evidence, without preparing input."""
    from gateway.session_authorities import active_authority
    authority = active_authority(adapter.gateway_runner)
    if authority is None:
        raise RuntimeStoreError('storage_unavailable')
    with authority.db._read_ctx() as conn:
        if any(_canonical_evidence(conn, run_id=run_id, session_id=session_id)):
            raise RuntimeStoreError('room_document_outcome_unknown')
    from hermes_state_logical_attempts import lookup_logical_attempt
    if lookup_logical_attempt(authority.db, principal_id='api', session_id=session_id,
            owner_scope=scope, task_id=task_id, execution_generation=execution_generation) is not None:
        raise RuntimeStoreError('room_document_outcome_unknown')


def pending_cancellation(dispatch, scope, *, session_id=None, key_absent=False):
    """Bounded identity for id-Stop retries; never persist prompt, files or credentials."""
    from gateway.hosted_room_peer_output import dispatch_digest
    from gateway.platforms.api_server_room_dispatch import _member_session_id
    return dict(scope=scope, session_id=session_id or _member_session_id(dispatch), task_id=dispatch.task_id,
                execution_generation=dispatch.execution_generation, dispatch_digest=dispatch_digest(dispatch),
                output_enabled=dispatch.document_output is not None, key_absent=key_absent)


def settle_cancelled_admission(adapter, run_id, status, *, scope):
    """Both Stop routes certify a durable cancellation after its accepting writer is fenced."""
    pending = status.get('canonical_admission_absence_pending')
    if pending is None:
        return status
    fields = {'scope', 'session_id', 'task_id', 'execution_generation', 'dispatch_digest', 'output_enabled', 'key_absent'}
    if (not isinstance(pending, dict) or set(pending) != fields or pending['scope'] != scope
            or type(pending['output_enabled']) is not bool or type(pending['key_absent']) is not bool
            or not isinstance(pending['session_id'], str) or not pending['session_id']
            or not isinstance(pending['task_id'], str) or not pending['task_id']
            or type(pending['execution_generation']) is not int or pending['execution_generation'] < 1
            or not isinstance(pending['dispatch_digest'], str) or len(pending['dispatch_digest']) != 64
            or any(char not in '0123456789abcdef' for char in pending['dispatch_digest'])):
        raise RuntimeStoreError('storage_unavailable')
    key = f"room:{pending['task_id']}:{pending['execution_generation']}"
    expected_id = 'run_' + hashlib.sha256((scope + '\0' + key).encode()).hexdigest()[:32]
    if run_id != expected_id:
        raise RuntimeStoreError('storage_unavailable')
    require_canonical_absence(adapter, run_id=run_id, scope=scope, session_id=pending['session_id'],
        task_id=pending['task_id'], execution_generation=pending['execution_generation'])
    settled = {**status, 'status': 'cancelled', 'last_event': 'run.cancelled'}
    settled.pop('canonical_admission_absence_pending')
    if pending['key_absent']:
        settled['admission_cancelled'] = True
    if pending['output_enabled']:
        settled['canonical_admission_absent'] = {'dispatch_digest': pending['dispatch_digest']}
    adapter._run_idempotency_store.update_status(run_id, settled)
    adapter._run_statuses[run_id] = settled
    return settled


def recover_unaccepted(adapter, record, *, scope, key, fingerprint, session_id):
    """True only after retiring a dead owner's demonstrably unaccepted reservation.

    A live/unknown owner or incomplete canonical evidence is uncertainty, never
    permission to ask for new bytes or run again. Accepted/retired input wins.
    """
    from gateway.session_authorities import active_authority
    authority = active_authority(adapter.gateway_runner)
    if authority is None:
        raise RuntimeStoreError('storage_unavailable')
    authority._require_admission_open()
    run_id = record['run_id']
    with authority.db._read_ctx() as conn:
        rows, retired, projected, _ = _canonical_evidence(conn, run_id=run_id, session_id=session_id)
        if projected and not rows and not retired:
            raise RuntimeStoreError('room_document_outcome_unknown')
        if rows or retired:
            if rows and (len(rows) != 1 or rows[0]['target_session_id'] != session_id):
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
    authority = active_authority(adapter.gateway_runner)
    if authority is None:
        raise RuntimeStoreError('storage_unavailable')
    authority._require_admission_open()
    with authority.db._read_ctx() as conn:
        rows, retired, projected, logical = _canonical_evidence(
            conn, run_id=run_id, session_id=session_id, scope=scope, dispatch=dispatch)
        if not rows:
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
    cancelled = record is not None and record['status'].get('status') == 'cancelled'
    if outcome == "reused" and record is not None and peer_files and not cancelled:
        try:
            if recover_unaccepted(adapter, record, scope=scope, key=key,
                                  fingerprint=fingerprint, session_id=session_id):
                outcome, record = "missing", None
        except RuntimeStoreError as exc:
            return _json_error(_openai_error, exc.reason, code=exc.reason, status=503)
    if outcome == "conflict" or (outcome == "reused" and record is not None):
        return _replay_or_conflict(adapter, request, outcome, record, gateway_session_key, _openai_error)
    return None


async def prepare_run_input(adapter, request, *, scope, key, fingerprint, session_id,
                            gateway_session_key, room_dispatch, peer_files, _openai_error):
    """Replay accepted input before preparing new bytes or spending a concurrency slot."""
    replay = lookup_run_response(adapter, request, scope=scope, key=key, fingerprint=fingerprint,
        session_id=session_id, gateway_session_key=gateway_session_key,
        peer_files=peer_files, _openai_error=_openai_error)
    if replay is not None or not peer_files:
        return None, replay
    return await prepare_peer_files(adapter, request, room_dispatch, idempotency_scope=scope,
        idempotency_key=key, session_id=session_id, gateway_session_key=gateway_session_key,
        _openai_error=_openai_error)
