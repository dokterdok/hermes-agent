"""Independent admitted producers can stage against the same private outbox."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading

import pytest

from gateway.hosted_room_artifacts import RoomArtifactOutbox
from gateway import hosted_room_output_cleanup as physical
from tests.gateway.test_hosted_room_artifacts import _scope


def test_an_idle_staged_producer_does_not_refuse_another_writer(tmp_path, monkeypatch):
    first = RoomArtifactOutbox(tmp_path / 'state.db')
    second = RoomArtifactOutbox(first.db_path)
    entered, release = threading.Event(), threading.Event()
    stage = physical.staged_blob

    @contextmanager
    def paused(outbox, name):
        with stage(outbox, name) as opened:
            if outbox is first:
                entered.set()
                assert release.wait(15)
            yield opened

    monkeypatch.setattr(physical, 'staged_blob', paused)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(first.put_bytes, scope=_scope(task_id='dtask:first'),
                                 data=b'First producer', source_name='first.txt')
        try:
            assert entered.wait(10)
            item = second.put_bytes(scope=_scope(task_id='dtask:second'),
                                    data=b'Second producer', source_name='second.txt')
            assert second.read(_scope(task_id='dtask:second'), item['artifact_id'])[1] == b'Second producer'
        finally:
            release.set()
        pending.result(timeout=15)


@pytest.mark.platforms('posix')
def test_publication_rechecks_parent_after_the_creation_seal(tmp_path):
    outbox = RoomArtifactOutbox(tmp_path / 'state.db')
    name = 'blob_' + 'd' * 32
    original = tmp_path / 'original-parent'
    with physical.staged_blob(outbox, name) as (source, parent):
        source.write(b'Owned staging bytes')
        source.flush()
        outbox.blob_root.rename(original)
        outbox.blob_root.mkdir()
        foreign = outbox.blob_root / name
        foreign.write_bytes(b'Unrelated replacement bytes')
        with outbox._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            with pytest.raises(ValueError, match='directory changed'):
                with physical.publication_blob(outbox, name, source, parent):
                    raise AssertionError('changed parent cannot publish')
    assert (original / name).read_bytes() == b'Owned staging bytes'
    assert foreign.read_bytes() == b'Unrelated replacement bytes'
