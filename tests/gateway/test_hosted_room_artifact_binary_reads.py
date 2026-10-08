"""Output reads preserve exact bytes and offsets on every host."""
from gateway.hosted_room_artifacts import RoomArtifactOutbox, RoomArtifactScope


def test_full_and_range_reads_preserve_binary_control_bytes(tmp_path):
    scope = RoomArtifactScope('room', 'task', 1, 'member', 'default', 'home', 'home', 'home', 1)
    outbox = RoomArtifactOutbox(tmp_path / 'state.db')
    data = b'first\r\n\x1alast\r\n\x00\xff'
    stored = outbox.put_bytes(scope=scope, data=data, source_name='result.bin')

    assert outbox.read(scope, stored['artifact_id'])[1] == data
    assert outbox.read_range(scope, stored['artifact_id'], offset=5, length=7)[1] == data[5:12]
