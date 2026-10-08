"""Legacy successful native release must not remove a racing replacement."""
import subprocess

import pytest

from gateway import session_ingress_media as media
from tests.gateway.input_reclamation_fixtures import candidate, close, copy_records, owned


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', [False, True])
async def test_native_release_preserves_replacement_after_identity_check(tmp_path, monkeypatch, replacement):
    db, owner = owned(tmp_path, monkeypatch)
    try:
        row, path = candidate(tmp_path, owner, 'input.txt', b'Original native bytes')
        original = path.read_bytes()
        displaced = path.with_name('original-kept')
        identity = media._file_identity
        injected = False

        def race(target):
            nonlocal injected
            result = identity(target)
            if replacement and target == path and not injected:
                injected = True
                path.rename(displaced)
                path.write_bytes(b'Unrelated replacement bytes')
            return result

        monkeypatch.setattr(media, '_file_identity', race)
        released = media.release_admission_media(db, row['admission_id'])
        if replacement:
            assert injected and released == 0
            assert displaced.read_bytes() == original
            assert path.read_bytes() == b'Unrelated replacement bytes'
        else:
            assert released == 1 and not path.exists()
    finally:
        close(db, tmp_path)


@pytest.mark.platforms('windows')
@pytest.mark.asyncio
async def test_native_release_does_not_follow_replaced_parent(tmp_path, monkeypatch):
    db, owner = owned(tmp_path, monkeypatch)
    try:
        row, path = candidate(tmp_path, owner, 'input.txt', b'Original native bytes')
        original = path.read_bytes()
        unrelated = tmp_path / 'unrelated-files'
        unrelated.mkdir()
        replacement = unrelated / path.name
        replacement.write_bytes(b'Unrelated fixture outside custody root')
        displaced = path.parent.with_name(path.parent.name + '-held')
        identity = media._file_identity
        injected = False

        def race(target):
            nonlocal injected
            result = identity(target)
            if target == path and not injected:
                injected = True
                assert target.is_relative_to(tmp_path) and displaced.is_relative_to(tmp_path)
                target.parent.rename(displaced)
                made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(target.parent), str(unrelated)],
                                      capture_output=True, text=True)
                assert made.returncode == 0, made.stdout + made.stderr
            return result

        monkeypatch.setattr(media, '_file_identity', race)
        assert media.release_admission_media(db, row['admission_id']) == 0
        assert injected and (displaced / path.name).read_bytes() == original
        assert replacement.read_bytes() == b'Unrelated fixture outside custody root'
    finally:
        close(db, tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['before_removal', 'after_removal'])
async def test_native_release_journal_survives_restart_and_finishes(tmp_path, monkeypatch, boundary):
    from gateway import hosted_room_input_cleanup as cleanup
    from hermes_state import SessionDB
    from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch

    db, owner = owned(tmp_path, monkeypatch)
    try:
        row, path = candidate(tmp_path, owner, 'input.txt', b'Native release to recover')
        execute = db._execute_write
        calls = 0

        def interrupted(*args):
            raise OSError('fixture stops before removal')

        def writer(fn, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                def fail_after_removal(conn):
                    fn(conn)
                    assert not path.exists()
                    raise RuntimeStoreError('fixture_commit_refusal')
                return execute(fail_after_removal, *args, **kwargs)
            return execute(fn, *args, **kwargs)

        with monkeypatch.context() as patch:
            if boundary == 'before_removal':
                patch.setattr(cleanup, '_verified_source', interrupted)
            else:
                patch.setattr(db, '_execute_write', writer)
            assert media.release_admission_media(db, row['admission_id']) == 0
        record, = copy_records(db)
        assert record['state'] == 'sealed'
        if boundary == 'before_removal':
            retained = path if path.exists() else path.parent / cleanup._quarantine_name(record) / 'copy'
            assert retained.read_bytes() == b'Native release to recover'
        else:
            assert not path.exists()
        db.close()
        db = SessionDB(tmp_path / 'state.db')
        begin_runtime_epoch(db, instance_id='restarted-native-owner')
        assert media.release_admission_media(db, row['admission_id']) == 1
        assert not path.exists() and not path.parent.exists()
        assert copy_records(db) == []  # The legacy crash journal has no retained owners.
    finally:
        close(db, tmp_path)


@pytest.mark.asyncio
async def test_native_release_rechecks_a_holder_added_after_seal(tmp_path, monkeypatch):
    from gateway import session_native_release
    from hermes_state_runtime import admit_session_input, cancel_session_input

    db, owner = owned(tmp_path, monkeypatch)
    try:
        row, path = candidate(tmp_path, owner, 'input.txt', b'Another admission keeps this input')
        remove = session_native_release._remove_selected_copies
        holders = []

        def held_after_seal(*args, **kwargs):
            assert copy_records(db)[0]['state'] == 'sealed'
            holders.append(admit_session_input(db, epoch=owner.epoch, principal_id='other',
                session_id=row['target_session_id'], request_id='new-holder', payload=row['payload']))
            return remove(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(session_native_release, '_remove_selected_copies', held_after_seal)
            assert media.release_admission_media(db, row['admission_id']) == 0
        assert path.read_bytes() == b'Another admission keeps this input'
        cancel_session_input(db, epoch=owner.epoch, admission_id=holders[0]['admission_id'])
        assert media.release_admission_media(db, row['admission_id']) == 1
        assert not path.exists()
    finally:
        close(db, tmp_path)


@pytest.mark.platforms('posix')
@pytest.mark.asyncio
async def test_native_quarantine_recovers_through_startup_collector(tmp_path, monkeypatch):
    from gateway import hosted_room_input_cleanup as cleanup
    from gateway.hosted_room_input_reclamation import collect_native_inputs

    db, owner = owned(tmp_path, monkeypatch)
    try:
        row, path = candidate(tmp_path, owner, 'input.txt', b'Native quarantine to recover')
        rename = cleanup._rename_noreplace

        def interrupted(*args):
            rename(*args)
            raise OSError('fixture stops after quarantine')

        with monkeypatch.context() as patch:
            patch.setattr(cleanup, '_rename_noreplace', interrupted)
            assert media.release_admission_media(db, row['admission_id']) == 0
        assert not path.exists() and copy_records(db)[0]['state'] == 'sealed'
        assert collect_native_inputs(db, epoch=owner.epoch)['removed'] == 1
        assert not path.parent.exists() and copy_records(db) == []
    finally:
        close(db, tmp_path)
