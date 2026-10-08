"""Replay one operator's setup invitation after a lost reply, without reminting.

The reservation and receipt commit together in the target's private grant store.
Old absent requests cannot mint after the initial five-minute admission window;
expired receipts can therefore be pruned without resurrecting their authority.
"""
import json
import time

from hermes_state_runtime import RuntimeStoreError


def issue(authority, subject, params, mint, publish):
    """Return an immutable receipt; only unfinished publication may resume.

    The mint callback inserts this receipt before committing the reservation.
    A pending flag survives a later RunStore failure without reminting on retry.
    """
    from gateway import hosted_rooms as rooms
    from gateway.platforms.api_server_room_grants import _grant_db
    from gateway.session_group_peers import _api_server
    request_id, requested_at = params.get('request_id'), params.get('requested_at')
    if (not subject or not isinstance(request_id, str) or not 16 <= len(request_id) <= 128
            or type(requested_at) not in (int, float)):
        raise RuntimeStoreError('invalid_params')
    frozen = json.dumps(params, sort_keys=True, separators=(',', ':'), allow_nan=False)
    db_path = _grant_db(_api_server(authority))
    with rooms._transaction(db_path, immediate=True) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS hosted_room_setup_invitations (
            request_id TEXT PRIMARY KEY, subject TEXT NOT NULL, request_json TEXT NOT NULL,
            response_json TEXT NOT NULL, expires_at REAL NOT NULL,
            publication_pending INTEGER NOT NULL DEFAULT 0)''')
        from hermes_cli.sqlite_util import add_column_if_missing
        add_column_if_missing(conn, 'hosted_room_setup_invitations', 'publication_pending',
                              'publication_pending INTEGER NOT NULL DEFAULT 0')
        row = conn.execute('SELECT * FROM hosted_room_setup_invitations WHERE request_id=?', (request_id,)).fetchone()
        if row is not None:
            if row['subject'] != subject or row['request_json'] != frozen:
                raise RuntimeStoreError('idempotency_conflict')
            response = json.loads(row['response_json'])
            if row['publication_pending'] and publish(conn, response):
                _published(conn, request_id, subject, frozen, row['response_json'])
            return response, False
        now = time.time()
        if not now - 300 <= requested_at <= now + 30:
            raise RuntimeStoreError('invitation_request_expired')
        conn.execute('DELETE FROM hosted_room_setup_invitations WHERE expires_at<?', (now - 300,))
        if conn.execute('SELECT COUNT(*) FROM hosted_room_setup_invitations').fetchone()[0] >= 4096:
            raise RuntimeStoreError('invitation_capacity')
        def save_receipt(response):
            conn.execute("""INSERT INTO hosted_room_setup_invitations(
                request_id,subject,request_json,response_json,expires_at,publication_pending)
                VALUES (?,?,?,?,?,1)""", (request_id, subject, frozen,
                    json.dumps(response, separators=(',', ':')), response['status_expires_at']))
        response = mint(conn, save_receipt)
        _published(conn, request_id, subject, frozen, json.dumps(response, separators=(',', ':')))
        return response, True


def _published(conn, request_id, subject, request_json, response_json):
    """Never mark a replacement receipt if its id was pruned/reused after commit."""
    conn.execute("""UPDATE hosted_room_setup_invitations SET publication_pending=0
        WHERE request_id=? AND subject=? AND request_json=? AND response_json=?""",
        (request_id, subject, request_json, response_json))
