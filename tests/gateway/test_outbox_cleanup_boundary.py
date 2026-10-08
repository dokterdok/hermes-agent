"""Observe cleanup intent without allowing any deletion of replacement fixture bytes."""
from contextlib import suppress
from pathlib import Path
import subprocess

import pytest

from gateway.hosted_room_artifacts import RoomArtifactError, RoomArtifactOutbox
from tests.gateway.test_hosted_room_artifacts import _scope, _blob


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('operation', ['ack', 'discard'])
@pytest.mark.parametrize('legacy', [False, True])
def test_cleanup_does_not_follow_a_substituted_blob_directory(tmp_path, monkeypatch, operation, legacy):
    outbox, scope = RoomArtifactOutbox(tmp_path / 'state.db'), _scope()
    item = outbox.put_bytes(scope=scope, data=b'Original output bytes', source_name='output.txt')
    if legacy:
        with outbox._connect() as conn:
            conn.execute('UPDATE hosted_room_output_artifacts SET blob_identity=NULL')
    blob = _blob(outbox, item['artifact_id'])
    displaced = tmp_path / 'retained-original-blobs'
    unrelated = tmp_path / 'unrelated-fixture-data'
    unrelated.mkdir()
    replacement = unrelated / blob.name
    replacement.write_bytes(b'Unrelated fixture bytes remain intact')
    assert all(p.is_relative_to(tmp_path) for p in (outbox.blob_root, displaced, unrelated, replacement))
    outbox.blob_root.rename(displaced)
    made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(outbox.blob_root), str(unrelated)],
                          capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    attempts = []
    original_unlink = Path.unlink

    def observe_unlink(path, *args, **kwargs):
        if path.name == blob.name:
            attempts.append(str(path.resolve()))
            raise OSError('Review fixture intercepts cleanup before deletion')
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', observe_unlink)
        with suppress(OSError, RoomArtifactError):
            if operation == 'ack':
                outbox.acknowledge(scope, [item['artifact_id']], message_event_id='dmessage:abc')
            else:
                outbox.discard_durably(scope)
    assert replacement.read_bytes() == b'Unrelated fixture bytes remain intact'
    assert (displaced / blob.name).read_bytes() == b'Original output bytes'
    assert attempts == [], 'Cleanup reached a replacement-directory pathname: ' + repr(attempts)
