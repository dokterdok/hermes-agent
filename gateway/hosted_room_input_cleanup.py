"""Remove a sealed input's verified object without resolving a mutable name again.

Windows holds non-reparse ancestors and denies leaf writes/renames until handle
disposition. POSIX first moves the entry into a private, descriptor-bound slot;
only the verified object there may be unlinked. The sealed row and deterministic
slot retain enough evidence to resume an interrupted pass without a new schema.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import os
import re
import stat


def _verified_source(source, copy):
    saved = os.fstat(source.fileno())
    partial_output = copy['namespace'] == 'output' and copy['size'] is None and copy['digest'] is None
    if not partial_output and (copy['size'] is None or copy['digest'] is None):
        raise ValueError('sealed input evidence is incomplete')
    if (not stat.S_ISREG(saved.st_mode) or saved.st_nlink != 1
            or (str(saved.st_dev), str(saved.st_ino)) != (copy['device'], copy['inode'])
            or copy['size'] is not None and saved.st_size != copy['size']
            or copy['digest'] is not None and hashlib.file_digest(source, 'sha256').hexdigest() != copy['digest']):
        raise ValueError('sealed input object changed')


def remove_sealed_copy(path, copy):
    """True only after deletion/absence is confirmed and its directory is synced."""
    copy = dict(copy)
    if (not path.is_absolute() or not re.fullmatch(r'[0-9a-f]{32}', copy['copy_id'])
            or type(copy['generation']) is not int or copy['generation'] < 1):
        raise ValueError('invalid sealed input identity')
    if os.name == 'nt':
        try:
            import pywintypes
            return _remove_windows(path, copy)
        except ImportError as exc:
            raise OSError('native cleanup is unavailable') from exc
        except pywintypes.error as exc:
            raise OSError(exc.winerror, exc.strerror) from exc
    return _remove_posix(path, copy)


def _remove_windows(path, copy):
    import ntsecuritycon
    import pywintypes
    import win32api
    import win32con
    import win32file

    flags = win32file.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT
    native = copy['namespace'] == 'native'
    container = None
    flush_parent = None
    try:
        with ExitStack() as held:
            for component in [*reversed(path.parent.parents), path.parent]:
                access = (win32con.GENERIC_READ | win32con.GENERIC_WRITE
                          if component == path.parent or native and component == path.parent.parent
                          else ntsecuritycon.FILE_READ_ATTRIBUTES)
                if native and component == path.parent:
                    access |= ntsecuritycon.DELETE
                try:
                    directory = win32file.CreateFile(str(component), access, win32con.FILE_SHARE_READ,
                        None, win32con.OPEN_EXISTING, flags, None)
                except pywintypes.error as exc:
                    if exc.winerror == 2:
                        if copy.get('parent_inode') is not None:
                            raise ValueError('sealed file directory is unavailable') from exc
                        if container is not None:
                            win32file.FlushFileBuffers(container)
                        return True
                    raise
                held.callback(directory.Close)
                attributes = win32file.GetFileInformationByHandle(directory)[0]
                if (attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT
                        or not attributes & win32con.FILE_ATTRIBUTE_DIRECTORY):
                    raise ValueError('sealed input directory changed')
                if native and component == path.parent.parent:
                    container = directory
            _verify_parent(_windows_handle_stat(directory), copy)
            _remove_windows_leaf(path, copy)
            win32file.FlushFileBuffers(directory)
            if native:
                process = win32api.GetCurrentProcess()
                flush_parent = win32api.DuplicateHandle(process, container, process, 0, False,
                                                        win32con.DUPLICATE_SAME_ACCESS)
                try:
                    win32file.SetFileInformationByHandle(directory, win32file.FileDispositionInfo, True)
                except pywintypes.error as exc:
                    if exc.winerror != 145:  # Other names in this digest directory still own it.
                        raise
        if flush_parent is not None:
            win32file.FlushFileBuffers(flush_parent)
    finally:
        if flush_parent is not None:
            flush_parent.Close()
    return True


def _remove_windows_leaf(path, copy):
    import msvcrt
    import ntsecuritycon
    import pywintypes
    import win32api
    import win32con
    import win32file

    try:
        leaf = win32file.CreateFile(str(path), win32con.GENERIC_READ | ntsecuritycon.DELETE,
            win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING,
            win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)
    except pywintypes.error as exc:
        if exc.winerror != 2:
            raise
        return
    with ExitStack() as held:
        held.callback(leaf.Close)
        attributes = win32file.GetFileInformationByHandle(leaf)[0]
        if attributes & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
            raise ValueError('sealed input entry changed')
        process = win32api.GetCurrentProcess()
        duplicate = win32api.DuplicateHandle(process, leaf, process, 0, False, win32con.DUPLICATE_SAME_ACCESS)
        fd = msvcrt.open_osfhandle(duplicate.Detach(), os.O_RDONLY | os.O_BINARY)
        with os.fdopen(fd, 'rb') as source:
            _verified_source(source, copy)
            win32file.SetFileInformationByHandle(leaf, win32file.FileDispositionInfo, True)


@contextmanager
def _parent_fd(path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory
    finally:
        os.close(directory)


def _rename_noreplace(source_dir, source, target_dir, target):
    """Never replace a newer original or an interrupted quarantine entry."""
    import ctypes
    import errno
    import sys

    libc = ctypes.CDLL(None, use_errno=True)
    name, flag = ('renameatx_np', 4) if sys.platform == 'darwin' else ('renameat2', 1)
    rename = getattr(libc, name, None)
    if rename is None:
        raise OSError(errno.ENOTSUP, 'exclusive rename is unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_dir, os.fsencode(source), target_dir, os.fsencode(target), flag):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _quarantine_name(copy):
    return '.reclaim-' + copy['copy_id'] + '-' + str(copy['generation'])


def _finish_quarantine(directory, quarantine, name, copy):
    try:
        fd = os.open('copy', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=quarantine)
    except FileNotFoundError:
        return True  # A prior pass unlinked the verified object before its SQL commit.
    try:
        with os.fdopen(fd, 'rb') as source:
            _verified_source(source, copy)
            os.unlink('copy', dir_fd=quarantine)
    except ValueError:
        # A replacement won the rename. Restore only into an empty original name.
        # If a newer entry occupies it, keep these bytes in the sealed row's slot.
        _rename_noreplace(quarantine, 'copy', directory, name)
        os.fsync(directory)
        os.fsync(quarantine)
        return False
    os.fsync(quarantine)
    return True


def _remove_posix(path, copy):
    import errno

    with ExitStack() as held:
        try:
            container = held.enter_context(_parent_fd(path.parent.parent))
        except FileNotFoundError:
            if copy.get('parent_inode') is not None:
                raise ValueError('sealed file directory is unavailable') from None
            return True
        try:
            directory = os.open(path.parent.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=container)
        except FileNotFoundError:
            if copy.get('parent_inode') is not None:
                raise ValueError('sealed file directory is unavailable') from None
            os.fsync(container)
            return True
        held.callback(os.close, directory)
        _verify_parent(os.fstat(directory), copy)
        removed = _quarantine_posix(directory, path.name, copy)
        if removed and copy['namespace'] == 'native':
            try:
                # rmdir cannot follow a substituted symlink or remove a nonempty directory.
                os.rmdir(path.parent.name, dir_fd=container)
            except OSError as exc:
                if exc.errno not in {errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR}:
                    raise
            os.fsync(container)
        return removed


def _quarantine_posix(directory, name, copy):
    with _output_removal_lock(directory, name, copy):
        return _quarantine_locked_posix(directory, name, copy)


def _quarantine_locked_posix(directory, name, copy):
    slot = _quarantine_name(copy)
    try:
        os.mkdir(slot, mode=0o700, dir_fd=directory)
    except FileExistsError:
        pass
    quarantine = os.open(slot, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        saved = os.fstat(quarantine)
        if saved.st_uid != os.geteuid() or stat.S_IMODE(saved.st_mode) != 0o700:  # windows-footgun: ok -- POSIX backend only
            raise ValueError('private cleanup directory changed')
        try:
            _rename_noreplace(directory, name, quarantine, 'copy')
        except (FileNotFoundError, FileExistsError):
            pass  # Resume only after validating whatever the private slot contains.
        os.fsync(directory)
        os.fsync(quarantine)
        removed = _finish_quarantine(directory, quarantine, name, copy)
    finally:
        os.close(quarantine)
    if removed:
        os.rmdir(slot, dir_fd=directory)
        os.fsync(directory)
    return removed


def _verify_parent(saved, copy):
    expected = copy.get('parent_device'), copy.get('parent_inode')
    if expected != (None, None) and expected != (str(saved.st_dev), str(saved.st_ino)):
        raise ValueError('sealed file directory changed')


def _windows_handle_stat(handle):
    import msvcrt
    import win32api
    import win32con

    process = win32api.GetCurrentProcess()
    duplicate = win32api.DuplicateHandle(process, handle, process, 0, False, win32con.DUPLICATE_SAME_ACCESS)
    descriptor = msvcrt.open_osfhandle(duplicate.Detach(), os.O_RDONLY | os.O_BINARY)
    try:
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _output_removal_lock(directory, name, copy):
    if copy['namespace'] != 'output':
        yield
        return
    import fcntl
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        # An interrupted cleanup may already own the object in its private slot.
        try:
            slot = os.open(_quarantine_name(copy), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                descriptor = os.open('copy', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=slot)
            finally:
                os.close(slot)
        except FileNotFoundError:
            yield
            return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)
