"""Physical output seals survive cleanup faults without adopting replacement bytes."""
import json
import os
import sqlite3
import time

import pytest

from gateway import hosted_room_input_cleanup as cleanup
from gateway import hosted_room_output_cleanup as physical
from gateway.hosted_room_artifacts import RoomArtifactError, RoomArtifactOutbox
from tests.gateway.test_hosted_room_artifacts import _scope, _blob


def stored(tmp_path):
    outbox, scope = RoomArtifactOutbox(tmp_path / 'state.db'), _scope()
    item = outbox.put_bytes(scope=scope, data=b'Owned output bytes', source_name='output.txt')
    return outbox, scope, item, _blob(outbox, item['artifact_id'])


def records(outbox):
    with outbox._connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM hosted_room_output_artifacts')]


def finish(outbox, scope, item, operation):
    if operation == 'ack':
        return outbox.acknowledge(scope, [item['artifact_id']], message_event_id='dmessage:abc')
    if operation == 'discard':
        return outbox.discard_durably(scope)
    with outbox._connect() as conn:
        conn.execute('UPDATE hosted_room_output_artifacts SET created_at=0')
        if operation == 'receipt_expiry':
            conn.execute("UPDATE hosted_room_output_artifacts SET acknowledged_at=1, receipt_expires_at=1, ack_message_event_id='dmessage:abc'")
    if operation == 'expiry':
        return outbox.prune_unacknowledged_artifacts()
    return outbox.prune_acknowledged_receipts()


@pytest.mark.parametrize('operation', ['ack', 'discard', 'expiry', 'receipt_expiry'])
@pytest.mark.parametrize('replacement', [b'Unrelated replacement', b'Owned output bytes'])
def test_replacement_leaf_keeps_cleanup_pending_even_when_bytes_match(tmp_path, operation, replacement):
    outbox, scope, item, path = stored(tmp_path)
    original = path.with_name('original-kept')
    path.rename(original)
    path.write_bytes(replacement)
    try:
        finish(outbox, scope, item, operation)
    except (OSError, RoomArtifactError):
        pass
    assert original.read_bytes() == b'Owned output bytes' and path.read_bytes() == replacement
    row, = records(outbox)
    assert row['blob_reclaimed_at'] is None
    assert row['acknowledged_at'] is not None or row['cleanup_required_at'] is not None
    assert outbox.prune_acknowledged_receipts(now=time.time() + 100_000_000) == 0
    replacement_kept = path.with_name('replacement-kept')
    path.rename(replacement_kept)
    original.rename(path)
    recovered = RoomArtifactOutbox(outbox.db_path)
    assert not path.exists() and replacement_kept.read_bytes() == replacement
    assert not records(recovered) or records(recovered)[0]['blob_reclaimed_at'] is not None


@pytest.mark.parametrize('operation', ['ack', 'discard'])
def test_replaced_ordinary_parent_does_not_prove_absence(tmp_path, operation):
    outbox, scope, item, path = stored(tmp_path)
    original = tmp_path / 'original-directory'
    outbox.blob_root.rename(original)
    outbox.blob_root.mkdir()
    with pytest.raises((OSError, RoomArtifactError)):
        finish(outbox, scope, item, operation)
    assert (original / path.name).read_bytes() == b'Owned output bytes'
    assert records(outbox)[0]['blob_reclaimed_at'] is None


@pytest.mark.parametrize('missing', [False, True])
def test_legacy_receipt_upgrades_only_verified_bytes(tmp_path, missing):
    outbox, scope, item, path = stored(tmp_path)
    with outbox._connect() as conn:
        conn.execute('UPDATE hosted_room_output_artifacts SET blob_identity=NULL')
    if missing:
        path.unlink()
    assert finish(outbox, scope, item, 'ack') == 1
    assert records(outbox)[0]['blob_reclaimed_at'] is not None and not path.exists()


def test_legacy_receipt_refuses_changed_bytes(tmp_path):
    outbox, scope, item, path = stored(tmp_path)
    with outbox._connect() as conn:
        conn.execute('UPDATE hosted_room_output_artifacts SET blob_identity=NULL')
    path.write_bytes(b'Unrelated legacy replacement')
    with pytest.raises(RoomArtifactError, match='legacy output bytes changed'):
        finish(outbox, scope, item, 'ack')
    assert path.read_bytes() == b'Unrelated legacy replacement'
    assert records(outbox)[0]['blob_reclaimed_at'] is None


@pytest.mark.parametrize('operation', ['ack', 'discard'])
def test_interrupted_verified_removal_replays_after_restart(tmp_path, monkeypatch, operation):
    outbox, scope, item, path = stored(tmp_path)
    verify = cleanup._verified_source
    def interrupted(*args):
        raise OSError('owned fixture interrupted before deletion')
    with monkeypatch.context() as patch:
        patch.setattr(cleanup, '_verified_source', interrupted)
        with pytest.raises(OSError, match='owned fixture interrupted'):
            finish(outbox, scope, item, operation)
    row, = records(outbox)
    assert row['blob_reclaimed_at'] is None
    slot = path.parent / cleanup._quarantine_name(json.loads(row['blob_identity'])) / 'copy'
    assert (path if path.exists() else slot).read_bytes() == b'Owned output bytes'
    assert cleanup._verified_source is verify
    recovered = RoomArtifactOutbox(outbox.db_path)
    assert not path.exists() and not slot.exists()
    assert not records(recovered) or records(recovered)[0]['blob_reclaimed_at'] is not None


@pytest.mark.parametrize('replace', [False, 'leaf', 'parent'])
def test_failed_write_journal_retains_identity_until_restart(tmp_path, monkeypatch, replace):
    outbox, scope = RoomArtifactOutbox(tmp_path / 'state.db'), _scope()
    def fail(conn, source, parent, name):
        raise RuntimeError('fixture stops before artifact commit')
    with monkeypatch.context() as patch:
        patch.setattr(physical, 'promote', fail)
        patch.setattr(physical, 'reclaim_pending', lambda *a, **k: None)
        with pytest.raises(RuntimeError, match='before artifact commit'):
            outbox.put_bytes(scope=scope, data=b'Uncommitted owned bytes', source_name='failed.txt')
    assert records(outbox) == []
    with outbox._connect() as conn:
        pending, = conn.execute('SELECT * FROM hosted_room_output_blob_cleanup').fetchall()
    path = outbox.blob_root / pending['blob_name']
    assert path.read_bytes() == b'Uncommitted owned bytes'
    if replace == 'leaf':
        path.rename(path.with_name('original-kept'))
        path.write_bytes(b'Unrelated replacement')
    elif replace == 'parent':
        outbox.blob_root.rename(tmp_path / 'original-parent')
        outbox.blob_root.mkdir()
        path.write_bytes(b'Unrelated replacement')
    recovered = RoomArtifactOutbox(outbox.db_path)
    with recovered._connect() as conn:
        count = conn.execute('SELECT COUNT(*) FROM hosted_room_output_blob_cleanup').fetchone()[0]
    assert count == int(bool(replace))
    if replace:
        assert path.read_bytes() == b'Unrelated replacement'
    else:
        assert not path.exists()


def test_unknown_aged_orphan_is_not_owned_by_its_filename(tmp_path):
    outbox = RoomArtifactOutbox(tmp_path / 'state.db')
    path = outbox.blob_root / ('blob_' + 'a' * 32)
    path.write_bytes(b'Unknown bytes without an ownership journal')
    os.utime(path, (1, 1))
    RoomArtifactOutbox(outbox.db_path)
    assert path.read_bytes() == b'Unknown bytes without an ownership journal'


def test_pending_physical_journal_does_not_remove_a_live_artifact(tmp_path):
    outbox, scope, item, path = stored(tmp_path)
    row, = records(outbox)
    with sqlite3.connect(outbox.db_path) as conn:
        conn.execute('INSERT INTO hosted_room_output_blob_cleanup VALUES (?, ?)',
                     (path.name, row['blob_identity']))
    RoomArtifactOutbox(outbox.db_path)
    assert outbox.read(scope, item['artifact_id'])[1] == b'Owned output bytes'
