"""Native replacement windows and durable output quarantine recovery."""
import json
import threading

import pytest

from gateway import hosted_room_input_cleanup as cleanup
from gateway import hosted_room_output_cleanup as physical
from gateway.hosted_room_artifacts import RoomArtifactError, RoomArtifactOutbox
from tests.gateway.test_outbox_cleanup_recovery import finish, records, stored


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('mutation', ['leaf', 'parent', 'write'])
def test_windows_checked_output_handles_exclude_late_replacement(tmp_path, monkeypatch, mutation):
    outbox, scope, item, path = stored(tmp_path)
    verify = cleanup._verified_source
    results = []
    def change():
        try:
            if mutation == 'write':
                path.write_bytes(b'Unrelated later data')
            else:
                target = path if mutation == 'leaf' else path.parent
                target.rename(target.with_name(target.name + '-moved'))
        except PermissionError:
            results.append('held')
    def checked(source, identity):
        verify(source, identity)
        worker = threading.Thread(target=change)
        worker.start()
        worker.join(10)
        assert not worker.is_alive() and results == ['held']
    monkeypatch.setattr(cleanup, '_verified_source', checked)
    assert finish(outbox, scope, item, 'ack') == 1
    assert not path.exists() and records(outbox)[0]['blob_reclaimed_at'] is not None


@pytest.mark.platforms('posix')
@pytest.mark.parametrize('newer_name', [False, True])
def test_posix_output_quarantine_preserves_replacement_without_overwrite(tmp_path, monkeypatch, newer_name):
    outbox, scope, item, path = stored(tmp_path)
    original = path.with_name('original-kept')
    rename = cleanup._rename_noreplace
    injected = False
    def race(source_dir, source, target_dir, target):
        nonlocal injected
        if not injected and source == path.name:
            injected = True
            path.rename(original)
            path.write_bytes(b'Replacement preserved')
            rename(source_dir, source, target_dir, target)
            if newer_name:
                path.write_bytes(b'Newer replacement preserved')
            return
        return rename(source_dir, source, target_dir, target)
    monkeypatch.setattr(cleanup, '_rename_noreplace', race)
    with pytest.raises((OSError, RoomArtifactError)):
        finish(outbox, scope, item, 'ack')
    assert original.read_bytes() == b'Owned output bytes'
    row, = records(outbox)
    assert row['blob_reclaimed_at'] is None
    slot = path.parent / cleanup._quarantine_name(json.loads(row['blob_identity'])) / 'copy'
    if newer_name:
        assert path.read_bytes() == b'Newer replacement preserved'
        assert slot.read_bytes() == b'Replacement preserved'
    else:
        assert path.read_bytes() == b'Replacement preserved'


@pytest.mark.platforms('posix')
def test_posix_output_parent_is_bound_before_later_symlink(tmp_path, monkeypatch):
    outbox, scope, item, path = stored(tmp_path)
    displaced, unrelated = tmp_path / 'original', tmp_path / 'unrelated'
    unrelated.mkdir()
    foreign = unrelated / path.name
    foreign.write_bytes(b'Keep unrelated bytes')
    rename = cleanup._rename_noreplace
    replaced = False
    def race(*args):
        nonlocal replaced
        if not replaced:
            replaced = True
            path.parent.rename(displaced)
            path.parent.symlink_to(unrelated, target_is_directory=True)
        return rename(*args)
    monkeypatch.setattr(cleanup, '_rename_noreplace', race)
    assert finish(outbox, scope, item, 'ack') == 1
    assert foreign.read_bytes() == b'Keep unrelated bytes'
    assert not (displaced / path.name).exists()


@pytest.mark.platforms('posix')
def test_posix_interrupted_output_quarantine_replays_its_seal(tmp_path, monkeypatch):
    outbox, scope, item, path = stored(tmp_path)
    rename = cleanup._rename_noreplace
    def interrupt(*args):
        rename(*args)
        raise OSError('fixture ends after quarantine')
    with monkeypatch.context() as patch:
        patch.setattr(cleanup, '_rename_noreplace', interrupt)
        with pytest.raises(OSError, match='after quarantine'):
            finish(outbox, scope, item, 'discard')
    row, = records(outbox)
    slot = path.parent / cleanup._quarantine_name(json.loads(row['blob_identity'])) / 'copy'
    assert not path.exists() and slot.read_bytes() == b'Owned output bytes'
    recovered = RoomArtifactOutbox(outbox.db_path)
    assert records(recovered) == [] and not slot.exists()


def test_live_staging_writer_is_not_an_orphan(tmp_path):
    outbox = RoomArtifactOutbox(tmp_path / 'state.db')
    name = 'blob_' + 'b' * 32
    with physical.staged_blob(outbox, name) as (source, _):
        source.write(b'Live uncommitted writer')
        source.flush()
        physical.reclaim_pending(outbox)
        assert (outbox.blob_root / name).exists()
        source.seek(0)
        assert source.read() == b'Live uncommitted writer'
        with outbox._connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM hosted_room_output_blob_cleanup').fetchone()[0] == 1
    assert not (outbox.blob_root / name).exists()


@pytest.mark.platforms('posix')
def test_reopening_preserves_private_directory_permissions(tmp_path):
    import os
    import stat

    outbox = RoomArtifactOutbox(tmp_path / 'state.db')
    os.chmod(outbox.root, 0o777)
    os.chmod(outbox.blob_root, 0o777)
    RoomArtifactOutbox(outbox.db_path)
    assert stat.S_IMODE(outbox.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(outbox.blob_root.stat().st_mode) == 0o700
