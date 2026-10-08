"""Native Windows cleanup removes pinned private files and refuses reparse paths."""
import pytest

from gateway.classic_output_cleanup import unlink_classic_blobs
from gateway.hosted_room_artifacts import RoomArtifactError


@pytest.mark.platforms('windows')
def test_classic_cleanup_flushes_deletion_and_replays_missing_names(tmp_path):
    root = tmp_path / 'blobs'
    root.mkdir()
    name = 'blob_' + 'a' * 32
    (root / name).write_bytes(b'private file')
    unlink_classic_blobs(root, [name])
    assert not (root / name).exists()
    unlink_classic_blobs(root, [name])


@pytest.mark.platforms('windows')
def test_classic_cleanup_refuses_directory_junction_without_removing_target(tmp_path):
    import _winapi
    outside = tmp_path / 'outside'
    outside.mkdir()
    name = 'blob_' + 'b' * 32
    (outside / name).write_bytes(b'must remain')
    junction = tmp_path / 'outbox'
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        with pytest.raises(RoomArtifactError, match='directory changed'):
            unlink_classic_blobs(junction, [name])
        assert (outside / name).read_bytes() == b'must remain'
    finally:
        junction.rmdir()
