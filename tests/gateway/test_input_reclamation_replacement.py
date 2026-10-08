"""Replacement safety at the actual sealed-copy removal boundary."""
from contextlib import contextmanager
import errno
import subprocess
import threading

import pytest

from gateway import hosted_room_input_cleanup as cleanup
from gateway import hosted_room_input_reclamation as reclamation
from gateway.hosted_room_input_preparation import prepare_hosted_input
from tests.gateway.input_reclamation_fixtures import close, copy_records, expire, owned, rpc_files, v3_path


@contextmanager
def candidate(tmp_path, monkeypatch):
    db, owner = owned(tmp_path, monkeypatch)
    try:
        rpc, bound = rpc_files(tmp_path, owner)
        prepared = prepare_hosted_input(rpc, request_id='hosted:replacement', prompt='read',
                                        attachments=[bound[0][0]])
        expire(db, prepared.handle)
        yield db, owner, v3_path(prepared)
    finally:
        close(db, tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', [None, b'Unrelated replacement bytes'])
async def test_replacement_after_seal_is_preserved(tmp_path, monkeypatch, replacement):
    with candidate(tmp_path, monkeypatch) as (db, owner, path):
        original = path.read_bytes()
        displaced = path.with_name('original-kept')
        remove = reclamation.remove_sealed_copy

        def race(target, row):
            if replacement is not None:
                target.rename(displaced)
                target.write_bytes(replacement)
            return remove(target, row)

        monkeypatch.setattr(reclamation, 'remove_sealed_copy', race)
        result = reclamation.collect_working_copies(db, epoch=owner.epoch)
        if replacement is None:
            assert result['removed'] == 1 and not path.exists()
        else:
            assert result['removed'] == 0
            assert displaced.read_bytes() == original and path.read_bytes() == replacement
            assert copy_records(db)[0]['state'] == 'sealed'


@pytest.mark.asyncio
async def test_changed_bytes_on_same_object_do_not_authorize_removal(tmp_path, monkeypatch):
    with candidate(tmp_path, monkeypatch) as (db, owner, path):
        remove = reclamation.remove_sealed_copy
        replacement = b'Changed without replacing its directory entry'

        def race(target, row):
            target.write_bytes(replacement)
            return remove(target, row)

        monkeypatch.setattr(reclamation, 'remove_sealed_copy', race)
        assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 0
        assert path.read_bytes() == replacement
        assert copy_records(db)[0]['state'] == 'sealed'


@pytest.mark.platforms('windows')
@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['leaf', 'parent', 'write'])
async def test_checked_windows_handles_prevent_late_replacement(tmp_path, monkeypatch, mutation):
    with candidate(tmp_path, monkeypatch) as (db, owner, path):
        verify = cleanup._verified_source
        results = []

        def attempt():
            try:
                if mutation == 'write':
                    path.write_bytes(b'Unrelated later bytes')
                else:
                    target = path if mutation == 'leaf' else path.parent
                    target.rename(target.with_name(target.name + '-moved'))
            except PermissionError:
                results.append('held')

        def checked(source, row):
            verify(source, row)
            worker = threading.Thread(target=attempt)
            worker.start()
            worker.join(10)
            assert not worker.is_alive() and results == ['held']

        monkeypatch.setattr(cleanup, '_verified_source', checked)
        assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 1
        assert not path.exists() and results == ['held']


@pytest.mark.platforms('windows')
@pytest.mark.asyncio
async def test_parent_junction_cannot_redirect_removal(tmp_path, monkeypatch):
    with candidate(tmp_path, monkeypatch) as (db, owner, path):
        original = path.read_bytes()
        unrelated = tmp_path / 'unrelated-files'
        unrelated.mkdir()
        replacement = unrelated / path.name
        replacement.write_bytes(b'Keep these unrelated fixture bytes')
        displaced = path.parent.with_name(path.parent.name + '-held')
        remove = reclamation.remove_sealed_copy

        def race(target, row):
            assert target.is_relative_to(tmp_path) and displaced.is_relative_to(tmp_path)
            target.parent.rename(displaced)
            made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(target.parent), str(unrelated)],
                                  capture_output=True, text=True)
            assert made.returncode == 0, made.stdout + made.stderr
            return remove(target, row)

        monkeypatch.setattr(reclamation, 'remove_sealed_copy', race)
        assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 0
        assert (displaced / path.name).read_bytes() == original
        assert replacement.read_bytes() == b'Keep these unrelated fixture bytes'
        assert copy_records(db)[0]['state'] == 'sealed'


@pytest.mark.platforms('windows')
@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['verified', 'directory_sync'])
async def test_windows_interrupted_removal_preserves_seal_and_recovers(tmp_path, monkeypatch, boundary):
    import win32file

    with candidate(tmp_path, monkeypatch) as (db, owner, path):
        original = path.read_bytes()

        def interrupted(*args):
            raise OSError(errno.EIO, 'fixture stops at removal boundary')

        with monkeypatch.context() as patch:
            if boundary == 'verified':
                patch.setattr(cleanup, '_verified_source', interrupted)
            else:
                patch.setattr(win32file, 'FlushFileBuffers', interrupted)
            assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 0
        assert copy_records(db)[0]['state'] == 'sealed'
        if boundary == 'verified':
            assert path.read_bytes() == original
        else:
            assert not path.exists()
        assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 1
        assert not path.exists()


@pytest.mark.platforms('posix')
@pytest.mark.asyncio
@pytest.mark.parametrize('newer_original', [False, True])
async def test_posix_quarantine_restores_or_retains_replacement_without_overwrite(tmp_path, monkeypatch, newer_original):
    with candidate(tmp_path, monkeypatch) as (db, owner, path):
        original = path.read_bytes()
        displaced = path.with_name('original-kept')
        rename = cleanup._rename_noreplace
        injected = False

        def race(source_dir, source, target_dir, target):
            nonlocal injected
            if not injected and source == path.name:
                injected = True
                path.rename(displaced)
                path.write_bytes(b'Replacement to retain')
                rename(source_dir, source, target_dir, target)
                if newer_original:
                    path.write_bytes(b'Newer replacement also retained')
                return
            return rename(source_dir, source, target_dir, target)

        monkeypatch.setattr(cleanup, '_rename_noreplace', race)
        assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 0
        assert displaced.read_bytes() == original
        row, = copy_records(db)
        assert row['state'] == 'sealed'
        slot = path.parent / cleanup._quarantine_name(row) / 'copy'
        if newer_original:
            assert path.read_bytes() == b'Newer replacement also retained'
            assert slot.read_bytes() == b'Replacement to retain'
            assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 0
            assert slot.read_bytes() == b'Replacement to retain'
        else:
            assert path.read_bytes() == b'Replacement to retain' and not slot.exists()


@pytest.mark.platforms('posix')
@pytest.mark.asyncio
async def test_posix_interrupted_quarantine_finishes_from_sealed_identity(tmp_path, monkeypatch):
    with candidate(tmp_path, monkeypatch) as (db, owner, path):
        original = path.read_bytes()
        rename = cleanup._rename_noreplace

        def interrupted(*args):
            rename(*args)
            raise OSError(errno.EIO, 'fixture stops after atomic quarantine')

        with monkeypatch.context() as patch:
            patch.setattr(cleanup, '_rename_noreplace', interrupted)
            assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 0
        row, = copy_records(db)
        slot = path.parent / cleanup._quarantine_name(row)
        assert row['state'] == 'sealed' and not path.exists()
        assert (slot / 'copy').read_bytes() == original
        assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 1
        assert not slot.exists() and not path.exists()


@pytest.mark.platforms('posix')
@pytest.mark.asyncio
async def test_posix_pinned_parent_never_follows_a_later_symlink(tmp_path, monkeypatch):
    with candidate(tmp_path, monkeypatch) as (db, owner, path):
        unrelated = tmp_path / 'unrelated-files'
        unrelated.mkdir()
        replacement = unrelated / path.name
        replacement.write_bytes(b'Keep unrelated fixture bytes')
        displaced = path.parent.with_name(path.parent.name + '-held')
        rename = cleanup._rename_noreplace
        injected = False

        def race(*args):
            nonlocal injected
            if not injected:
                injected = True
                path.parent.rename(displaced)
                path.parent.symlink_to(unrelated, target_is_directory=True)
            return rename(*args)

        monkeypatch.setattr(cleanup, '_rename_noreplace', race)
        assert reclamation.collect_working_copies(db, epoch=owner.epoch)['removed'] == 1
        assert replacement.read_bytes() == b'Keep unrelated fixture bytes'
        assert not (displaced / path.name).exists()
