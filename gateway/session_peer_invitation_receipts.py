"""Opt-in native reply recovery for the #100016 target-issued invitation.

#97846-style setup journals need durable replies; grant_id stays the source label.
A distinct request_id opts the canonical native caller into recovery. No execution lives here.
"""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from time import time

from gateway import hosted_room_peer as peer, hosted_rooms as rooms
from gateway.hosted_room_grant_state import grant_state_db_paths
from gateway.session_authorities import authority_for_profile_id
from hermes_state_runtime import RuntimeStoreError, _epoch, _text


PREFIX = 'gateway.peer.invitation.v1:'
MAX_RECEIPTS = 1024
MAX_RECEIPT_BYTES = 32 * 1024


def _json(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise RuntimeStoreError('room_invitation_receipt_limit')
    return encoded


def _seal(secret, key, value):
    payload = _json(value)
    tag = hmac.new(secret, (key + '\n' + payload).encode('ascii'), hashlib.sha256).hexdigest()
    return _json({'payload': payload, 'tag': tag})


def _open(secret, key, raw):
    try:
        if len(raw) > MAX_RECEIPT_BYTES:
            raise ValueError('oversized receipt')
        sealed = json.loads(raw)
        tag = hmac.new(secret, (key + '\n' + sealed['payload']).encode('ascii'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(tag, sealed['tag']):
            raise ValueError('receipt authentication failed')
        value = json.loads(sealed['payload'])
        if value['state'] not in {'pending', 'committed'}:
            raise ValueError('invalid receipt state')
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeStoreError('room_invitation_invalidated') from exc


def _guard(authority, actor, conn):
    if (actor.profile_id != authority.profile_id or 'session:control' not in actor.capabilities
            or Path(authority.db.db_path).resolve().parent != Path(authority.profile_id).resolve()
            or authority_for_profile_id(authority.runner, authority.profile_id) is not authority):
        raise RuntimeStoreError('permission_denied')
    _epoch(conn, authority.epoch)


def _bearer(secret, value):
    token = peer.issue_room_grant(secret, **{**value['intent']['grant'],
        'grant_id': value['grant_id'], 'issued_at': value['issued_at']})
    if not hmac.compare_digest(hashlib.sha256(token.encode('ascii')).hexdigest(), value['token_sha256']):
        raise RuntimeStoreError('room_invitation_invalidated')
    permission = 'dispatch' if 'dispatch' in value['intent']['grant']['permissions'] else 'status'
    try:
        claims = peer.decode_room_grant(secret, token, permission=permission, now=time())
    except peer.HostedRoomGrantError as exc:
        raise RuntimeStoreError('room_invitation_expired') from exc
    return token, claims


def _verify_conn(conn, path, claims, *, pending):
    """Use one caller-owned snapshot, including inside the canonical writer."""
    now = time()
    if rooms.room_grant_is_revoked(path, claims=claims, now=now, _conn=conn):
        raise RuntimeStoreError('room_invitation_invalidated')
    if not rooms.peer_room_grant_is_current(path, claims=claims, now=now, _conn=conn):
        raise RuntimeStoreError('room_invitation_pending' if pending else 'room_invitation_invalidated')
    row = conn.execute('SELECT expires_at FROM hosted_room_peer_reservations '
        'WHERE room_id=? AND member_id=? AND target_profile=?',
        (claims['room_id'], claims['member_id'], claims['target_profile'])).fetchone()
    if row is None or float(row[0]) < claims['status_expires_at']:
        raise RuntimeStoreError('room_invitation_pending' if pending else 'room_invitation_invalidated')


def _verify_stores(paths, claims, *, pending):
    # Never keep a SQLite transaction open while opening another store. Reads
    # neither create/migrate stores nor run the reservation upsert on a retry.
    for path in paths:
        try:
            conn = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=10)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
                _verify_conn(conn, path, claims, pending=pending)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise RuntimeStoreError('room_invitation_pending' if pending else 'room_invitation_unavailable') from exc


def issue_with_receipt(authority, actor, request_id, *, secret, grant, catalog):
    """Return the original bearer, or refuse without reauthorizing its scope.

Pending recovery requires positive existing reservations in every enforcing DB.
An incomplete/compensated attempt remains pending; a fresh request is a separate
explicit authorization. Bounded receipts are never evicted to reuse an old ID.
"""
    checked_id = peer._identifier(request_id, field='request_id')
    if checked_id != request_id:
        raise RuntimeStoreError('invalid_params')
    key = PREFIX + hashlib.sha256(checked_id.encode('ascii')).hexdigest()
    paths = tuple(dict.fromkeys(str(Path(path).resolve()) for path in grant_state_db_paths(authority.profile_id)))
    intent = json.loads(_json({'subject': _text(actor.subject), 'profile_id': authority.profile_id,
        'request_id': checked_id, 'stores': paths, 'grant': grant, 'catalog': catalog}))

    def prepare(conn):
        _guard(authority, actor, conn)
        row = conn.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()
        if row is not None:
            value = _open(secret, key, row[0])
            if value['intent'] != intent:
                raise RuntimeStoreError('room_invitation_conflict')
            _bearer(secret, value)
            return value, False
        if conn.execute('SELECT COUNT(*) FROM state_meta WHERE key GLOB ?', (PREFIX + '*',)).fetchone()[0] >= MAX_RECEIPTS:
            raise RuntimeStoreError('room_invitation_receipt_limit')
        value = {'intent': intent, 'issued_at': time(), 'state': 'pending',
                 'grant_id': grant['grant_id'] or 'grant-' + secrets.token_hex(16)}
        token = peer.issue_room_grant(secret, **{**grant, 'grant_id': value['grant_id'], 'issued_at': value['issued_at']})
        value['token_sha256'] = hashlib.sha256(token.encode('ascii')).hexdigest()
        conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?)', (key, _seal(secret, key, value)))
        return value, True

    value, created = authority.db._execute_write(prepare)
    token, claims = _bearer(secret, value)
    if created:
        from gateway.hosted_room_grant_state import reserve_grant_state
        # The durable pending intent has committed. The existing source helper
        # opens each enforcing store and compensates partial writes by itself.
        try:
            reserve_grant_state(paths, claims=claims, expires_at=claims['status_expires_at'])
        except Exception as exc:
            raise RuntimeStoreError('room_invitation_pending') from exc
    _verify_stores(paths, claims, pending=value['state'] == 'pending')

    def commit(conn):
        _guard(authority, actor, conn)
        row = conn.execute('SELECT value FROM state_meta WHERE key=?', (key,)).fetchone()
        if row is None:
            raise RuntimeStoreError('room_invitation_invalidated')
        current = _open(secret, key, row[0])
        if current != value and not (value['state'] == 'pending' and current == {**value, 'state': 'committed'}):
            raise RuntimeStoreError('room_invitation_conflict')
        _bearer(secret, current)
        _verify_conn(conn, authority.db.db_path, claims, pending=current['state'] == 'pending')
        if current['state'] == 'pending':
            current['state'] = 'committed'
            conn.execute('UPDATE state_meta SET value=? WHERE key=?', (_seal(secret, key, current), key))
        return current

    committed = authority.db._execute_write(commit)
    _verify_stores(paths, claims, pending=False)
    # Expiry can pass during store reads; reconstruction never extends it.
    token, claims = _bearer(secret, committed)
    return {'grant': token, 'target_profile': grant['target_profile'], 'catalog': catalog,
            'endpoint': catalog['endpoint'], 'expires_at': claims['expires_at'],
            'status_expires_at': claims['status_expires_at']}
