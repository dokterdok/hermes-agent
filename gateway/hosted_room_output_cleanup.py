"""Physical outbox ownership and replay, separate from logical ACK/Discard.

Creation seals the actual file before writing. Promotion transfers that seal to
the artifact row in the same commit; a failed or interrupted producer leaves a
cleanup journal. Unknown legacy orphans have no ownership evidence and are kept.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sqlite3

from gateway import hosted_room_input_cleanup as cleanup


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS hosted_room_output_blob_cleanup (
        blob_name TEXT PRIMARY KEY, identity_json TEXT NOT NULL)''')


def _identity(source, parent, name, *, complete):
    saved = os.fstat(source.fileno())
    if not stat.S_ISREG(saved.st_mode) or saved.st_nlink != 1:
        raise ValueError('output blob is not a private regular file')
    source.seek(0)
    return {'copy_id': name.removeprefix('blob_'), 'generation': 1, 'namespace': 'output',
            'device': str(saved.st_dev), 'inode': str(saved.st_ino),
            'parent_device': str(parent.st_dev), 'parent_inode': str(parent.st_ino),
            'size': saved.st_size if complete else None,
            'digest': hashlib.file_digest(source, 'sha256').hexdigest() if complete else None}


@contextmanager
def _windows_parent(root, *, create=False):
    import ntsecuritycon
    import pywintypes
    import win32con
    import win32file

    flags = win32file.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT
    try:
        with ExitStack() as held:
            for part in [*reversed(root.parents), root]:
                access = (win32con.GENERIC_READ | win32con.GENERIC_WRITE
                          if part == root else ntsecuritycon.FILE_READ_ATTRIBUTES)
                try:
                    handle = win32file.CreateFile(str(part), access, win32con.FILE_SHARE_READ,
                                                  None, win32con.OPEN_EXISTING, flags, None)
                except pywintypes.error as exc:
                    if not create or exc.winerror not in {2, 3}:
                        raise
                    os.mkdir(part, mode=0o700)
                    handle = win32file.CreateFile(str(part), access, win32con.FILE_SHARE_READ,
                                                  None, win32con.OPEN_EXISTING, flags, None)
                held.callback(handle.Close)
                attributes = win32file.GetFileInformationByHandle(handle)[0]
                if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT or not attributes & win32con.FILE_ATTRIBUTE_DIRECTORY:
                    raise ValueError('output blob directory changed')
            yield handle
    except pywintypes.error as exc:
        raise OSError(exc.winerror, exc.strerror) from exc


def ensure_directory(root):
    """Create beneath held ancestors; never chmod or create through a replacement."""
    root = Path(os.path.abspath(root))
    if os.name == 'nt':
        with _windows_parent(root, create=True):
            return
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root.anchor, flags)
    try:
        for part in root.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=directory)
            except FileNotFoundError:
                os.mkdir(part, mode=0o700, dir_fd=directory)
                os.fsync(directory)
                child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
    finally:
        os.close(directory)


@contextmanager
def _windows_file(root, name, *, create):
    import msvcrt
    import ntsecuritycon
    import win32api
    import win32con
    import win32file

    with _windows_parent(root) as parent, ExitStack() as held:
        access = win32con.GENERIC_READ | ntsecuritycon.DELETE
        if create:
            access |= win32con.GENERIC_WRITE
        leaf = win32file.CreateFile(str(root / name), access, win32con.FILE_SHARE_READ, None,
            win32con.CREATE_NEW if create else win32con.OPEN_EXISTING,
            win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)
        held.callback(leaf.Close)
        attributes = win32file.GetFileInformationByHandle(leaf)[0]
        if attributes & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
            raise ValueError('output blob entry changed')
        process = win32api.GetCurrentProcess()
        duplicate = win32api.DuplicateHandle(process, leaf, process, 0, False, win32con.DUPLICATE_SAME_ACCESS)
        descriptor = msvcrt.open_osfhandle(duplicate.Detach(), os.O_BINARY | (os.O_RDWR if create else os.O_RDONLY))
        with os.fdopen(descriptor, 'r+b' if create else 'rb') as source:
            if create:
                win32file.FlushFileBuffers(parent)
            yield source, cleanup._windows_handle_stat(parent)
        win32file.FlushFileBuffers(parent)


@contextmanager
def _held_file(root, name, *, create=False):
    if not re.fullmatch(r'blob_[0-9a-f]{32}', name):
        raise ValueError('output blob identity changed')
    root = Path(os.path.abspath(root))
    if os.name == 'nt':
        with _windows_file(root, name, create=create) as opened:
            yield opened
        return
    import fcntl
    with cleanup._parent_fd(root) as parent:
        flags = os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY)
        descriptor = os.open(name, flags, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'r+b' if create else 'rb') as source:
            fcntl.flock(source.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if create:
                os.fsync(parent)
            yield source, os.fstat(parent)
        os.fsync(parent)


def _save(conn, name, identity):
    conn.execute('''INSERT INTO hosted_room_output_blob_cleanup VALUES (?, ?)
        ON CONFLICT(blob_name) DO UPDATE SET identity_json=excluded.identity_json''',
                 (name, json.dumps(identity, sort_keys=True)))


@contextmanager
def staged_blob(outbox, name):
    """The open producer excludes cleanup until promotion or writer termination."""
    try:
        with _held_file(outbox.blob_root, name, create=True) as (source, parent):
            identity = _identity(source, parent, name, complete=False)
            with outbox._connect() as conn:
                _save(conn, name, identity)
                conn.commit()
            try:
                yield source, parent
            finally:
                # A handled write failure can tighten its original creation seal
                # to the exact partial bytes before releasing the producer lock.
                with outbox._connect() as conn:
                    if conn.execute('SELECT 1 FROM hosted_room_output_blob_cleanup WHERE blob_name=?', (name,)).fetchone():
                        source.flush()
                        _save(conn, name, _identity(source, parent, name, complete=True))
                        conn.commit()
    finally:
        # The row may already have transferred ownership to the artifact. A fault
        # here leaves the physical journal for a later constructor; never erase it.
        try:
            reclaim_pending(outbox, names=[name])
        except (OSError, ValueError, sqlite3.Error):
            pass


def promote(conn, source, parent, name):
    identity = _identity(source, parent, name, complete=True)
    conn.execute('DELETE FROM hosted_room_output_blob_cleanup WHERE blob_name=?', (name,))
    return json.dumps(identity, sort_keys=True)


def _legacy_identity(outbox, row):
    name = str(row['blob_name'])
    with _held_file(outbox.blob_root, name) as (source, parent):
        identity = _identity(source, parent, name, complete=True)
        if identity['size'] != row['size'] or identity['digest'] != row['sha256']:
            raise ValueError('legacy output bytes changed')
        with outbox._connect() as conn:
            _save(conn, name, identity)
            conn.commit()
    return identity


def remove_artifact(outbox, row):
    """Keep the logical row until physical removal and directory sync succeed."""
    name = str(row['blob_name'])
    with outbox._connect() as conn:
        pending = conn.execute('SELECT identity_json FROM hosted_room_output_blob_cleanup WHERE blob_name=?',
                               (name,)).fetchone()
    encoded = row['blob_identity'] or (pending['identity_json'] if pending else None)
    try:
        identity = json.loads(encoded) if encoded else _legacy_identity(outbox, row)
    except FileNotFoundError:
        if os.name == 'nt':
            with _windows_parent(Path(os.path.abspath(outbox.blob_root))) as parent:
                saved = cleanup._windows_handle_stat(parent)
        else:
            with cleanup._parent_fd(Path(os.path.abspath(outbox.blob_root))) as parent:
                saved = os.fstat(parent)
        identity = {'copy_id': name.removeprefix('blob_'), 'generation': 1, 'namespace': 'output',
                    'device': 'absent', 'inode': 'absent', 'size': row['size'], 'digest': row['sha256'],
                    'parent_device': str(saved.st_dev), 'parent_inode': str(saved.st_ino)}
    if not cleanup.remove_sealed_copy(Path(os.path.abspath(outbox.blob_root / name)), identity):
        raise ValueError('output cleanup could not verify ownership')
    with outbox._connect() as conn:
        conn.execute('DELETE FROM hosted_room_output_blob_cleanup WHERE blob_name=?', (name,))
        conn.commit()


def reclaim_pending(outbox, *, names=None):
    with outbox._connect() as conn:
        rows = conn.execute('''SELECT pending.* FROM hosted_room_output_blob_cleanup AS pending
            WHERE NOT EXISTS (SELECT 1 FROM hosted_room_output_artifacts AS artifact
                              WHERE artifact.blob_name=pending.blob_name)''').fetchall()
    for row in rows:
        name = row['blob_name']
        if names is not None and name not in names:
            continue
        try:
            if not cleanup.remove_sealed_copy(Path(os.path.abspath(outbox.blob_root / name)),
                                              json.loads(row['identity_json'])):
                continue
        except (OSError, ValueError):
            continue
        with outbox._connect() as conn:
            conn.execute('DELETE FROM hosted_room_output_blob_cleanup WHERE blob_name=?', (name,))
            conn.commit()
