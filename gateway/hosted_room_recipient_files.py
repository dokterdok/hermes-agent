"""Files-owned profile-local custody, bound to the served target's live DB owner."""
import hashlib
import json
import time

from gateway.hosted_room_attachments import (
    HostedRoomAttachmentStore, AttachmentIntegrityError, MAX_ATTACHMENT_BYTES,
    _safe_manifest_entry,
)
from hermes_state_runtime import RuntimeStoreError, _epoch


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _held(authority, conn):
    db = authority.db
    registry = getattr(authority.runner, 'session_authorities', None)
    if (registry is None or registry.for_home(authority.profile_id) is not authority
            or conn is None or db._read_conns_closed or conn is not db._conn
            or db._db_replaced or db._db_wal_generation_lost or db._wal_generation_was_lost()):
        raise RuntimeStoreError('runtime_draining')
    authority._require_admission_open()
    db._raise_if_db_corrupt()
    db._raise_if_db_replaced()
    _epoch(conn, authority.epoch)


def _store(authority):
    # No constructor initialization, independent SQLite connection or room maintenance.
    return HostedRoomAttachmentStore(authority.db.db_path, _defer_initialization=True)


def _fields(identity, manifest, data=None):
    item = _safe_manifest_entry({k: manifest[k] for k in ('attachment_id', 'kind', 'name', 'size', 'mime')})
    if (type(manifest.get('sha256')) is not str or len(manifest['sha256']) != 64
            or type(identity.get('valid_until')) not in (int, float)
            or type(identity.get('index')) is not int):
        raise AttachmentIntegrityError('recipient binding invalid')
    encoded = _encoded(identity)
    key = hashlib.sha256(encoded.encode()).hexdigest()
    if data is not None:
        if (type(data) is not bytes or len(data) != item['size'] or not data
                or len(data) > MAX_ATTACHMENT_BYTES or hashlib.sha256(data).hexdigest() != manifest['sha256']):
            raise AttachmentIntegrityError('recipient bytes incomplete or changed')
    return key, encoded, _encoded(manifest), float(identity['valid_until'])


def retain_recipient_bytes(authority, *, identity, manifest, data, attempt):
    """Admit a write only inside the current served Files writer's generation."""
    key, encoded, manifest_json, expiry = _fields(identity, manifest, data)
    if type(attempt) is not int or attempt < 1 or time.time() >= expiry:
        raise AttachmentIntegrityError('recipient attempt or lifetime invalid')
    store = _store(authority)
    db = authority.db
    with db.live_write_connection() as conn:
        _held(authority, conn)
        # Only after the held owner admits this transaction may Files create its private root.
        store._prepare_private_root()
        removed = store.retain_recipient(conn, key=key, encoded=encoded,
            manifest_json=manifest_json, digest=manifest['sha256'], data=data,
            valid_until=expiry, attempt=attempt, now=time.time())
        _held(authority, conn)
    for blob_id in removed:
        store._blob_path(blob_id).unlink(missing_ok=True)
    return read_recipient_bytes(authority, identity=identity, manifest=manifest, attempt=attempt)


def read_recipient_bytes(authority, *, identity, manifest, attempt):
    """Read current held owner and verified bytes without DDL or maintenance."""
    key, encoded, manifest_json, expiry = _fields(identity, manifest)
    store = _store(authority)
    with authority.db.live_read_connection() as conn:
        _held(authority, conn)
        result = store.read_recipient(conn, key=key, encoded=encoded,
            manifest_json=manifest_json, digest=manifest['sha256'], size=manifest['size'],
            valid_until=expiry, attempt=attempt, now=time.time())
        _held(authority, conn)
    return {'identity': identity, 'manifest': manifest, **result}
