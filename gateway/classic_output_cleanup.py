"""Classic export cleanup on its live writer, without outbox construction or pruning."""
import os
from pathlib import Path
import re

from gateway.hosted_room_artifacts import RoomArtifactError


class ClassicCleanupUnavailable(RuntimeError):
    """Retirement is durable but physical cleanup is not yet confirmed."""


def require_live_outbox(db, conn):
    if db._read_conns_closed or db.read_only or conn is not db._conn or not conn.in_transaction:
        raise ClassicCleanupUnavailable('Classic cleanup requires the current owner transaction')
    from hermes_state_errors import StateDbReplacedError
    try:
        db._raise_if_db_replaced()
    except StateDbReplacedError as exc:
        raise ClassicCleanupUnavailable('Classic cleanup owner was replaced') from exc
    required = {
        'hosted_room_output_artifacts': {'scope_key', 'blob_name', 'cleanup_required_at'},
        'hosted_room_output_generation_fences': {
            'lineage_identity', 'lineage_json', 'max_generation', 'retired_generation', 'updated_at'},
    }
    for table, columns in required.items():
        if not columns <= {row['name'] for row in conn.execute('PRAGMA table_info(' + table + ')')}:
            raise ClassicCleanupUnavailable('Classic output inventory is unavailable')
    return Path(db.db_path).parent / 'hosted-room-artifact-outbox' / 'blobs'


def seal_classic_blobs(conn, root, rows):
    """The caller commits this phase before attempting physical cleanup."""
    from gateway.hosted_room_output_cleanup import capture_output_identity
    import json

    columns = {row['name'] for row in conn.execute('PRAGMA table_info(hosted_room_output_artifacts)')}
    if 'blob_identity' not in columns:
        conn.execute('ALTER TABLE hosted_room_output_artifacts ADD COLUMN blob_identity TEXT')
    for row in rows:
        if 'blob_identity' in row.keys() and row['blob_identity']:
            continue
        identity = capture_output_identity(root, row)
        conn.execute('UPDATE hosted_room_output_artifacts SET blob_identity=? WHERE artifact_id=?',
                     (json.dumps(identity, sort_keys=True), row['artifact_id']))


def unlink_classic_blobs(root, rows):
    from gateway.hosted_room_input_cleanup import remove_sealed_copy
    import json

    for row in rows:
        name = row['blob_name']
        if type(name) is not str or not re.fullmatch(r'blob_[0-9a-f]{32}', name):
            raise ClassicCleanupUnavailable('Classic blob identity changed')
        if 'blob_identity' not in row.keys() or not row['blob_identity']:
            raise ClassicCleanupUnavailable('Classic cleanup seal is unavailable')
        identity = json.loads(row['blob_identity'])
        if (identity['copy_id'] != name.removeprefix('blob_') or identity['namespace'] != 'output'
                or identity['size'] != row['size'] or identity['digest'] != row['sha256']):
            raise ClassicCleanupUnavailable('Classic cleanup evidence changed')
        try:
            removed = remove_sealed_copy(Path(os.path.abspath(root / name)), identity)
        except ValueError as exc:
            raise RoomArtifactError(str(exc)) from exc
        if not removed:
            raise ClassicCleanupUnavailable('Classic output cleanup could not be confirmed')
