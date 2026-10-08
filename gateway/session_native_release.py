"""Immediate native release with the same durable seals as startup collection."""
from pathlib import Path
import time
import uuid

from gateway import session_ingress_media as media
from gateway.hosted_room_input_reclamation import copy_path, verified_identity, _external_holds, _remove_selected_copies
from hermes_state_input_custody import copy_is_held, create_schema, seal_copy


def _indexed_candidate(conn, db, reference, held, identities, now):
    path = Path(reference['path'])
    if str(path) in held or path.parent.parent != media._media_root():
        return None
    record = conn.execute('''SELECT * FROM input_custody_copies
        WHERE namespace='native' AND digest=? AND name=?''', (reference['sha256'], path.name)).fetchone()
    copy = dict(record) if record is not None else dict(copy_id=uuid.uuid4().hex, namespace='native',
        digest=reference['sha256'], name=path.name, size=reference['size'], generation=1,
        state='preparing', device=None, inode=None)
    if (copy_path(db, copy) != path or copy['size'] != reference['size'] or copy_is_held(conn, copy, now)
            or _external_holds(conn, db, copy, path, now)):
        return None
    try:
        if media._file_identity(path) in identities:
            return None
    except FileNotFoundError:
        return copy if copy['state'] == 'sealed' else None
    if copy['state'] == 'sealed':
        return copy  # The committed identity also owns any interrupted quarantine slot.
    identity = verified_identity(path, copy['digest'], copy['size'])
    if copy['state'] != 'removed' and copy['device'] is not None and identity != (copy['device'], copy['inode']):
        return None
    if record is None:
        conn.execute('INSERT INTO input_custody_copies VALUES(?,?,?,?,?,?,?,?,?)',
            (copy['copy_id'], 'native', copy['name'], copy['digest'], copy['size'], 1, 'ready', *identity))
    else:
        conn.execute("""UPDATE input_custody_copies SET generation=generation+?,state='ready',device=?,inode=?
            WHERE copy_id=?""", (int(copy['state'] == 'removed'), *identity, copy['copy_id']))
    return dict(conn.execute('SELECT * FROM input_custody_copies WHERE copy_id=?', (copy['copy_id'],)).fetchone())


def release_native_media(db, admission_id):
    from hermes_state_runtime import get_session_admission

    row = get_session_admission(db, admission_id=admission_id)
    if row is None or row['status'] != 'terminal':
        return 0
    references = media.admission_media_references(row['payload'])
    if not references:
        return 0

    def seal(conn):
        create_schema(conn)
        epoch = conn.execute('SELECT epoch FROM runtime_epoch WHERE singleton=1').fetchone()[0]
        held = media._held_media_paths(conn)
        identities = media._held_file_identities(held, media._media_root())
        if identities is None:
            return epoch, []
        selected = set()
        for reference in references:
            try:
                copy = _indexed_candidate(conn, db, reference, held, identities, time.time())
                if copy is not None and (copy['state'] == 'sealed' or seal_copy(conn, copy, time.time())):
                    selected.add((copy['copy_id'], copy['generation']))
            except (OSError, ValueError):
                continue
        return epoch, sorted(selected)

    epoch, selected = db._execute_write(seal)
    return _remove_selected_copies(db, epoch=epoch, selected=selected)
