"""Classic export cleanup on its live writer, without outbox construction or pruning."""
from contextlib import ExitStack
import os
from pathlib import Path
import re

from gateway.hosted_room_artifacts import RoomArtifactError
from hermes_platform.host.facts import os_family


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


def unlink_classic_blobs(root, names):
    if any(type(name) is not str or not re.fullmatch(r'blob_[0-9a-f]{32}', name) for name in names):
        raise ClassicCleanupUnavailable('Classic blob identity changed')
    if os_family() == 'win32':
        import pywintypes
        try:
            _unlink_windows(root, names)
        except pywintypes.error as exc:
            raise ClassicCleanupUnavailable('Classic output cleanup could not be confirmed') from exc
    else:
        _unlink_posix(root, names)


def _unlink_posix(root, names):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root.anchor, flags)
    try:
        for component in root.parts[1:]:
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        for name in names:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass  # A prior interrupted cleanup is confirmed by the directory sync.
        os.fsync(directory)
    finally:
        os.close(directory)


def _unlink_windows(root, names):
    import ntsecuritycon
    import pywintypes
    import win32con
    import win32file

    flags = win32file.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT
    sharing = win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE
    with ExitStack() as held:
        # Deny new write/delete handles so directories cannot be renamed or made reparse points.
        for component in [*reversed(root.parents), root]:
            access = (win32con.GENERIC_READ | win32con.GENERIC_WRITE
                      if component == root else ntsecuritycon.FILE_READ_ATTRIBUTES)
            handle = win32file.CreateFile(str(component), access, win32con.FILE_SHARE_READ, None,
                                          win32con.OPEN_EXISTING, flags, None)
            held.callback(handle.Close)
            attributes = win32file.GetFileInformationByHandle(handle)[0]
            if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT or not attributes & win32con.FILE_ATTRIBUTE_DIRECTORY:
                raise RoomArtifactError('Classic output directory changed')
        for name in names:
            try:
                blob = win32file.CreateFile(str(root / name), ntsecuritycon.DELETE | ntsecuritycon.FILE_READ_ATTRIBUTES,
                    sharing | win32con.FILE_SHARE_DELETE, None, win32con.OPEN_EXISTING,
                    win32file.FILE_FLAG_OPEN_REPARSE_POINT | win32file.FILE_FLAG_WRITE_THROUGH, None)
            except pywintypes.error as exc:
                if exc.winerror == 2:
                    continue
                raise ClassicCleanupUnavailable('Classic output file cannot be removed') from exc
            try:
                attributes = win32file.GetFileInformationByHandle(blob)[0]
                if attributes & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
                    raise RoomArtifactError('Classic output file changed')
                win32file.SetFileInformationByHandle(blob, win32file.FileDispositionInfo, True)
            finally:
                blob.Close()
        win32file.FlushFileBuffers(handle)
