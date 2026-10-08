"""Classic retirement keeps committed physical evidence across owner writer phases."""
import json

import pytest

from tests.gateway.test_classic_current_export import (
    classic_runtime as classic_runtime, rpc, _publish_classic_file,
)


def output_row(f):
    with f.db._read_ctx() as conn:
        return dict(conn.execute('SELECT * FROM hosted_room_output_artifacts').fetchone())


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_classic_cleanup_seal_commits_before_removal_and_recovers(classic_runtime, monkeypatch, legacy):
    from gateway import session_classic_output as classic
    f = classic_runtime
    installation, request, _, terminal, _ = await _publish_classic_file(f, 'sealed-cleanup')
    if legacy:
        f.db._execute_write(lambda conn: conn.execute('UPDATE hosted_room_output_artifacts SET blob_identity=NULL'))
    before = output_row(f)
    path = f.home / 'hosted-room-artifact-outbox' / 'blobs' / before['blob_name']
    data = path.read_bytes()
    selectors = dict(session_id=f.session_id, installation=installation, group_id=request['group_id'],
                     export_id=terminal['classic_export']['export_id'])
    def stop(root, rows):
        assert all(row['blob_identity'] for row in rows)
        raise OSError('fixture stops before physical removal')
    with monkeypatch.context() as patch:
        patch.setattr(classic, 'unlink_classic_blobs', stop)
        reply = await rpc(f, 'session.export.discard', **selectors)
        assert reply['error']['message'] == 'classic_export_unavailable'
    sealed = output_row(f)
    assert sealed['blob_identity'] and sealed['cleanup_required_at'] is not None
    assert path.read_bytes() == data
    reply = await rpc(f, 'session.export.discard', **selectors)
    assert reply['result'] == {'retired': True} and not path.exists()


@pytest.mark.asyncio
async def test_classic_cleanup_preserves_replacement_with_identical_bytes(classic_runtime):
    f = classic_runtime
    installation, request, _, terminal, _ = await _publish_classic_file(f, 'replaced-cleanup')
    before = output_row(f)
    path = f.home / 'hosted-room-artifact-outbox' / 'blobs' / before['blob_name']
    data = path.read_bytes()
    displaced = path.with_name('original-kept')
    path.rename(displaced)
    path.write_bytes(data)
    selectors = dict(session_id=f.session_id, installation=installation, group_id=request['group_id'],
                     export_id=terminal['classic_export']['export_id'])
    reply = await rpc(f, 'session.export.discard', **selectors)
    assert reply['error']['message'] == 'classic_export_unavailable'
    after = output_row(f)
    assert after['cleanup_required_at'] is not None and after['blob_identity'] == before['blob_identity']
    assert path.read_bytes() == displaced.read_bytes() == data


@pytest.mark.asyncio
async def test_classic_rechecks_epoch_after_legacy_seal_commits(classic_runtime, monkeypatch):
    from gateway import session_classic_output as classic
    from hermes_state_runtime import begin_runtime_epoch
    f = classic_runtime
    installation, request, _, terminal, _ = await _publish_classic_file(f, 'seal-epoch')
    f.db._execute_write(lambda conn: conn.execute('UPDATE hosted_room_output_artifacts SET blob_identity=NULL'))
    before = output_row(f)
    path = f.home / 'hosted-room-artifact-outbox' / 'blobs' / before['blob_name']
    write = f.db._execute_write
    sealed = False
    seal = classic.seal_classic_blobs
    def observe_seal(*args):
        nonlocal sealed
        seal(*args)
        sealed = True
    monkeypatch.setattr(classic, 'seal_classic_blobs', observe_seal)
    def replace_after_seal(operation, *args, **kwargs):
        result = write(operation, *args, **kwargs)
        if sealed:
            # The explicit retire and seal transactions have both committed.
            monkeypatch.setattr(f.db, '_execute_write', write)
            begin_runtime_epoch(f.db, instance_id='replacement-after-seal')
        return result
    monkeypatch.setattr(f.db, '_execute_write', replace_after_seal)
    reply = await rpc(f, 'session.export.discard', session_id=f.session_id,
                      installation=installation, group_id=request['group_id'],
                      export_id=terminal['classic_export']['export_id'])
    assert 'error' in reply
    after = output_row(f)
    assert json.loads(after['blob_identity'])['inode'] and after['cleanup_required_at'] is not None
    assert path.exists()
