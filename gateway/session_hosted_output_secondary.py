"""Output-owned secondary retained publication.

Registration, lifetime, publication, retry and completion run on the live
Output owner. Send-consent is not execution or publication authority. Nothing
here installs a private owner, swaps ``runner.session_authority``, or relaxes
``_output_owner`` / ``_output_policy_read``.
"""
import json

from gateway.hosted_room_artifacts import RoomArtifactError
from gateway.session_hosted_output_retry import digest, retryable


_BINDING = ('work', 'route', 'lineage', 'member_id')


def ensure_secondary_publication_tables(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS hosted_room_secondary_publications (
        room_id TEXT NOT NULL, publication_id TEXT NOT NULL, task_id TEXT NOT NULL,
        execution_generation INTEGER NOT NULL, member_id TEXT NOT NULL,
        attempts INTEGER NOT NULL, next_attempt_at REAL NOT NULL, blocked INTEGER NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, valid_until REAL NOT NULL,
        metadata_json TEXT NOT NULL, operation TEXT NOT NULL, reason_code TEXT NOT NULL,
        PRIMARY KEY(room_id, publication_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS hosted_room_secondary_publication_completions (
        room_id TEXT NOT NULL, publication_id TEXT NOT NULL, task_id TEXT NOT NULL,
        execution_generation INTEGER NOT NULL, completed_at REAL NOT NULL,
        valid_until REAL NOT NULL, metadata_json TEXT NOT NULL, event_digest TEXT NOT NULL,
        operation TEXT NOT NULL, attempt INTEGER NOT NULL,
        PRIMARY KEY(room_id, publication_id))''')


def publish_secondary_from_consent(task, consent):
    """Refuse send-consent as publication authority. No store and no pointer."""
    del task, consent
    raise RoomArtifactError('Group Chat send consent is not publication authority')


def register_secondary_publication(service, task, *, route=None):
    def body(conn, key):
        metadata = _live_metadata(service, conn, key)
        events = _events(service, conn, key)
        now = _now(service)
        if route is not None and (not isinstance(route, str) or route != metadata['route']):
            raise RoomArtifactError('Group Chat secondary publication route is unauthorized')
        if now >= float(metadata['valid_until']):
            raise RoomArtifactError('Group Chat secondary publication lifetime expired')
        publication_id = _publication_id(key, metadata)
        done = _completion(conn, key[0], publication_id)
        row = _registration(conn, key[0], publication_id)
        if done is not None:
            if row is not None:
                raise RoomArtifactError('Group Chat secondary publication completion changed')
            return _existing_completion(service, done, metadata, events, now, key)
        if row is not None:
            stored = _stored(row)
            if not _same(stored, metadata) or stored.get('publication') != events:
                raise RoomArtifactError('Group Chat secondary publication binding changed')
            _require_owner_provenance(service, stored)
            _require_recorded_lifetime(row, stored)
            if now >= float(row['valid_until']) or now >= float(metadata['valid_until']):
                raise RoomArtifactError('Group Chat secondary publication lifetime expired')
            published = (row['operation'] == 'publish' and row['reason_code'] == 'pending'
                         and not int(row['blocked']))
            accepted = not int(row['blocked']) and row['reason_code'] in {'registered', 'pending'}
            return _view(row, accepted=accepted, published=published, completed=False)
        provenance = _provenance(service, metadata, registered_at=now, publication=events)
        encoded, valid_until = _encode(provenance)
        conn.execute('''INSERT INTO hosted_room_secondary_publications (
            room_id, publication_id, task_id, execution_generation, member_id,
            attempts, next_attempt_at, blocked, created_at, updated_at, valid_until,
            metadata_json, operation, reason_code)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                key[0], publication_id, key[1], key[2], metadata['member_id'],
                0, 0.0, 0, now, now, valid_until, encoded, 'registered', 'registered'))
        return _view(_registration(conn, key[0], publication_id), accepted=True, published=False, completed=False)
    return _mutate(service, task, body)


def publish_secondary_publication(service, task, publication_id):
    def body(conn, key):
        _require_publication_id(publication_id)
        row = _registration(conn, key[0], publication_id)
        done = _completion(conn, key[0], publication_id)
        if row is None and done is None:
            raise RoomArtifactError('Group Chat secondary publication is not registered')
        try:
            metadata = _live_metadata(service, conn, key)
            events = _events(service, conn, key)
        except RoomArtifactError as exc:
            if row is None:
                raise
            return _block(conn, row, _route_reason(exc), _now(service))
        now = _now(service)
        if done is not None:
            if row is not None:
                return _block(conn, row, 'stale_binding', now)
            return _existing_completion(service, done, metadata, events, now, key)
        stored = _stored(row)
        _require_recorded_lifetime(row, stored)
        if _expired(stored, metadata, now) or not _same(stored, metadata) or stored.get('publication') != events:
            return _block(conn, row, 'expired_grant' if _expired(stored, metadata, now) else _mismatch_reason(stored, metadata), now)
        _require_owner_provenance(service, stored)
        if int(row['blocked']) or row['operation'] != 'registered':
            published = row['operation'] == 'publish' and row['reason_code'] == 'pending' and not int(row['blocked'])
            return _view(row, accepted=published, published=published, completed=False)
        return _mark_published(conn, row, stored, now, attempt=1)
    return _mutate(service, task, body)


def retry_secondary_publication(service, task, publication_id):
    def body(conn, key):
        _require_publication_id(publication_id)
        row = _registration(conn, key[0], publication_id)
        done = _completion(conn, key[0], publication_id)
        if row is None and done is None:
            raise RoomArtifactError('Group Chat secondary publication is not registered')
        try:
            metadata = _live_metadata(service, conn, key)
            events = _events(service, conn, key)
        except RoomArtifactError as exc:
            if row is None:
                raise
            return _block(conn, row, _route_reason(exc), _now(service))
        now = _now(service)
        if done is not None:
            if row is not None:
                return _block(conn, row, 'stale_binding', now)
            return _existing_completion(service, done, metadata, events, now, key)
        if row['operation'] != 'publish':
            raise RoomArtifactError('Group Chat secondary publication is not published')
        stored = _stored(row)
        _require_recorded_lifetime(row, stored)
        if _expired(stored, metadata, now) or not _same(stored, metadata) or stored.get('publication') != events:
            return _block(conn, row, 'expired_grant' if _expired(stored, metadata, now) else _mismatch_reason(stored, metadata), now)
        _require_owner_provenance(service, stored)
        if int(row['blocked']):
            return _view(row, accepted=False, published=False, completed=False)
        if now < float(row['next_attempt_at']):
            pending = row['reason_code'] == 'pending'
            return _view(row, accepted=False, published=pending, completed=False)
        attempt = min(2147483647, int(row['attempts']) + 1)
        return _mark_published(conn, row, stored, now, attempt=attempt)
    return _mutate(service, task, body)


def record_secondary_publication_failure(service, task, publication_id, *, attempt, error):
    def body(conn, key):
        _require_publication_id(publication_id)
        row = _registration(conn, key[0], publication_id)
        if row is None or _completion(conn, key[0], publication_id) is not None:
            raise RoomArtifactError('Group Chat secondary publication is not registered' if row is None
                                    else 'Group Chat secondary publication completion changed')
        if row['operation'] != 'publish' or int(row['attempts']) != attempt:
            raise RoomArtifactError('Group Chat output attempt changed')
        if int(row['blocked']):
            return _view(row, accepted=False, published=False, completed=False)
        try:
            metadata = _live_metadata(service, conn, key)
            events = _events(service, conn, key)
        except RoomArtifactError as exc:
            return _block(conn, row, _route_reason(exc), _now(service))
        now = _now(service)
        stored = _stored(row)
        _require_recorded_lifetime(row, stored)
        if _expired(stored, metadata, now) or not _same(stored, metadata) or stored.get('publication') != events:
            return _block(conn, row, 'expired_grant' if _expired(stored, metadata, now) else _mismatch_reason(stored, metadata), now)
        _require_owner_provenance(service, stored)
        if error is None:
            raise RoomArtifactError('Group Chat secondary publication failure is missing')
        reason, blocked = ('transient', 0) if retryable(error) else ('authorization_or_verification', 1)
        delay = _delay(int(row['attempts']))
        conn.execute('''UPDATE hosted_room_secondary_publications
            SET blocked=?, reason_code=?, updated_at=?, next_attempt_at=?
            WHERE room_id=? AND publication_id=?''',
            (blocked, reason, now, now + delay, key[0], publication_id))
        return _view(_registration(conn, key[0], publication_id), accepted=False, published=False, completed=False)
    return _mutate(service, task, body)


def complete_secondary_publication(service, task, publication_id, *, attempt):
    def body(conn, key):
        _require_publication_id(publication_id)
        row = _registration(conn, key[0], publication_id)
        done = _completion(conn, key[0], publication_id)
        if row is None and done is None:
            raise RoomArtifactError('Group Chat secondary publication is not registered')
        metadata = _live_metadata(service, conn, key)
        events = _events(service, conn, key)
        now = _now(service)
        if done is not None:
            if row is not None:
                raise RoomArtifactError('Group Chat secondary publication completion changed')
            completed = _existing_completion(service, done, metadata, events, now, key)
            if int(done['attempt']) != attempt:
                raise RoomArtifactError('Group Chat output attempt changed')
            return completed
        if (row['operation'] != 'publish' or row['reason_code'] != 'pending'
                or int(row['blocked']) or int(row['attempts']) != attempt):
            raise RoomArtifactError('Group Chat secondary publication completion refused')
        stored = _stored(row)
        _require_recorded_lifetime(row, stored)
        if _expired(stored, metadata, now) or not _same(stored, metadata) or stored.get('publication') != events:
            raise RoomArtifactError('Group Chat secondary publication completion refused')
        _require_owner_provenance(service, stored)
        encoded, valid_until = _encode(stored)
        if valid_until != float(row['valid_until']):
            raise RoomArtifactError('Group Chat secondary publication lifetime expired')
        conn.execute('''INSERT INTO hosted_room_secondary_publication_completions (
            room_id, publication_id, task_id, execution_generation, completed_at, valid_until,
            metadata_json, event_digest, operation, attempt)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (
                key[0], publication_id, key[1], key[2], now, valid_until, encoded, events, 'publish', attempt))
        conn.execute('DELETE FROM hosted_room_secondary_publications WHERE room_id=? AND publication_id=?',
                     (key[0], publication_id))
        return _existing_completion(service, _completion(conn, key[0], publication_id), metadata, events, now, key)
    return _mutate(service, task, body)


def _mutate(service, task, body):
    if not service._output_retry_ready:
        raise RoomArtifactError('legacy output retries require exact authority migration')
    # Policy and physical DB lifetime are rechecked before the writer opens.
    with service._output_policy_read():
        pass
    key = service._output_key(task)

    def write(conn):
        service._output_owner(conn)
        _snapshot(conn, task, key)
        return body(conn, key)

    return service.authority.db._execute_write(write)


def _snapshot(conn, task, key):
    current = conn.execute(
        'SELECT payload_json, result_json, cancel_generation FROM hosted_room_driver_tasks '
        'WHERE room_id=? AND task_id=?', key[:2]).fetchone()
    if (current is None or json.loads(current['payload_json']) != task['payload']
            or json.loads(current['result_json']) != task['result']
            or current['cancel_generation'] != task['cancel_generation']):
        raise RoomArtifactError('Group Chat output snapshot changed')


def _live_metadata(service, conn, key):
    return service._output_metadata(conn, key)


def _events(service, conn, key):
    events = service._output_events_digest(conn, key)
    if not events:
        raise RoomArtifactError('Group Chat secondary publication evidence missing')
    return events


def _now(service):
    return float(service._artifact_clock())


def _publication_id(key, metadata):
    return digest(dict(
        room_id=key[0], task_id=key[1], execution_generation=key[2],
        member_id=metadata['member_id'], route=metadata['route'],
        lineage=metadata['lineage'], work=metadata['work']))


def _provenance(service, metadata, *, registered_at, publication):
    return {
        'work': metadata['work'],
        'route': metadata['route'],
        'lineage': metadata['lineage'],
        'member_id': metadata['member_id'],
        'valid_until': float(metadata['valid_until']),
        'owner_epoch': service._output_epoch,
        'owner_instance': service._output_instance,
        'registered_at': float(registered_at),
        'publication': publication,
    }


def _encode(provenance):
    encoded = json.dumps(provenance, sort_keys=True, separators=(',', ':'))
    stored = json.loads(encoded)
    return encoded, float(stored['valid_until'])


def _stored(row):
    return json.loads(row['metadata_json'])


def _same(stored, metadata):
    return all(stored.get(name) == metadata.get(name) for name in _BINDING)


def _expired(stored, metadata, now):
    return now >= float(stored['valid_until']) or now >= float(metadata['valid_until'])


def _mismatch_reason(stored, metadata):
    if stored.get('route') != metadata.get('route') or stored.get('lineage') != metadata.get('lineage'):
        return 'unauthorized_route'
    return 'stale_binding'


def _route_reason(exc):
    message = str(exc).lower()
    # An owner failure must roll back. It is not a route block the writer may commit.
    if 'owner changed' in message:
        raise exc
    if 'route' in message or 'grant' in message:
        return 'unauthorized_route'
    return 'stale_binding'


def _require_owner_provenance(service, stored):
    if (stored.get('owner_epoch') != service._output_epoch
            or stored.get('owner_instance') != service._output_instance):
        raise RoomArtifactError('Group Chat output owner changed')


def _require_recorded_lifetime(row, stored):
    if float(stored.get('valid_until')) != float(row['valid_until']):
        raise RoomArtifactError('Group Chat secondary publication binding changed')


def _require_publication_id(publication_id):
    if not isinstance(publication_id, str) or not publication_id:
        raise RoomArtifactError('Group Chat secondary publication is not registered')


def _registration(conn, room_id, publication_id):
    return conn.execute(
        'SELECT * FROM hosted_room_secondary_publications WHERE room_id=? AND publication_id=?',
        (room_id, publication_id)).fetchone()


def _completion(conn, room_id, publication_id):
    return conn.execute(
        'SELECT * FROM hosted_room_secondary_publication_completions WHERE room_id=? AND publication_id=?',
        (room_id, publication_id)).fetchone()


def _delay(attempts):
    return min(60.0, 2.0 ** min(max(int(attempts), 1) - 1, 16))


def _mark_published(conn, row, stored, now, *, attempt):
    delay = _delay(attempt)
    conn.execute('''UPDATE hosted_room_secondary_publications
        SET attempts=?, next_attempt_at=?, blocked=0, updated_at=?, operation=?, reason_code=?
        WHERE room_id=? AND publication_id=?''', (
            attempt, now + delay, now, 'publish', 'pending', row['room_id'], row['publication_id']))
    updated = _registration(conn, row['room_id'], row['publication_id'])
    # Binding provenance stays the registered commitment. Do not adopt a later horizon.
    if _stored(updated) != stored:
        raise RoomArtifactError('Group Chat secondary publication binding changed')
    return _view(updated, accepted=True, published=True, completed=False)


def _block(conn, row, reason, now):
    conn.execute('''UPDATE hosted_room_secondary_publications
        SET blocked=1, reason_code=?, updated_at=?
        WHERE room_id=? AND publication_id=?''', (reason, now, row['room_id'], row['publication_id']))
    return _view(_registration(conn, row['room_id'], row['publication_id']),
                 accepted=False, published=False, completed=False)


def _existing_completion(service, done, metadata, events, now, key):
    stored = _stored(done)
    _require_owner_provenance(service, stored)
    _require_recorded_lifetime(done, stored)
    if (done['task_id'] != key[1] or int(done['execution_generation']) != key[2]
            or not _same(stored, metadata) or stored.get('publication') != events
            or done['event_digest'] != events or done['event_digest'] != stored.get('publication')):
        raise RoomArtifactError('Group Chat secondary publication completion changed')
    if now >= float(done['valid_until']) or now >= float(metadata['valid_until']) or now >= float(stored['valid_until']):
        raise RoomArtifactError('Group Chat secondary publication lifetime expired')
    return {
        'accepted': True,
        'published': True,
        'completed': True,
        'publication_id': done['publication_id'],
        'operation': 'completed',
        'attempt': int(done['attempt']),
        'blocked': False,
        'reason_code': 'completed',
        'valid_until': float(done['valid_until']),
        'next_attempt_at': 0.0,
        'provenance': stored,
        'event_digest': done['event_digest'],
    }


def _view(row, *, accepted, published, completed):
    stored = _stored(row)
    return {
        'accepted': bool(accepted),
        'published': bool(published),
        'completed': bool(completed),
        'publication_id': row['publication_id'],
        'operation': row['operation'],
        'attempt': int(row['attempts']),
        'blocked': bool(row['blocked']),
        'reason_code': row['reason_code'],
        'valid_until': float(row['valid_until']),
        'next_attempt_at': float(row['next_attempt_at']),
        'provenance': stored,
        'event_digest': stored.get('publication', ''),
    }
