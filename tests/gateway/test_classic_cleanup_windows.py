"""Native classic cleanup uses committed seals and refuses reparse paths."""
import pytest

from gateway.classic_output_cleanup import unlink_classic_blobs
from gateway.hosted_room_artifacts import RoomArtifactError
from tests.gateway.test_outbox_cleanup_recovery import records, stored


@pytest.mark.platforms('windows')
def test_classic_cleanup_flushes_deletion_and_replays_missing_names(tmp_path):
    outbox, _, _, path = stored(tmp_path)
    rows = records(outbox)
    unlink_classic_blobs(outbox.blob_root, rows)
    assert not path.exists()
    unlink_classic_blobs(outbox.blob_root, rows)


@pytest.mark.platforms('windows')
def test_classic_cleanup_refuses_directory_junction_without_removing_target(tmp_path):
    import _winapi
    outbox, _, _, path = stored(tmp_path)
    rows = records(outbox)
    junction = tmp_path / 'junction'
    _winapi.CreateJunction(str(outbox.blob_root), str(junction))
    with pytest.raises(RoomArtifactError, match='directory changed'):
        unlink_classic_blobs(junction, rows)
    assert path.read_bytes() == b'Owned output bytes'
