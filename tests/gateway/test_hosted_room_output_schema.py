"""Opening a pre-receipt outbox upgrades metadata without replacing its bytes or fences."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from gateway.hosted_room_artifacts import RoomArtifactOutbox
from tests.gateway.test_hosted_room_artifacts import _scope


def _legacy_outbox(tmp_path, *, reclaimed_column=False):
    path, scope, data = tmp_path / "state.db", _scope(), b"retained output\n"
    blob = tmp_path / "hosted-room-artifact-outbox" / "blobs" / ("blob_" + "a" * 32)
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    record = ("rart_original", scope.key, json.dumps(scope.as_mapping(), sort_keys=True),
              "report.txt", "file", "text/plain", len(data), hashlib.sha256(data).hexdigest(),
              blob.name, time.time(), None)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript((Path(__file__).parent / "fixtures" / "hosted_output_legacy.sql").read_text())
        conn.execute("INSERT INTO hosted_room_output_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?)", record)
        conn.execute("INSERT INTO hosted_room_output_generation_fences VALUES (?,?,?,?,?)",
                     (scope.lineage_json, scope.lineage_json, 1, 0, time.time()))
        if reclaimed_column:
            conn.execute("ALTER TABLE hosted_room_output_artifacts ADD COLUMN blob_reclaimed_at REAL")
        columns = [row[1] for row in conn.execute("PRAGMA table_info(hosted_room_output_artifacts)")]
        fence = conn.execute("SELECT * FROM hosted_room_output_generation_fences").fetchone()
    return path, scope, blob, data, record, columns, fence


@pytest.mark.parametrize("reclaimed_column", [False, True])
def test_old_outbox_keeps_pending_bytes_and_supports_exact_ack(tmp_path, reclaimed_column):
    path, scope, blob, data, record, _columns, fence = _legacy_outbox(
        tmp_path, reclaimed_column=reclaimed_column)
    outbox = RoomArtifactOutbox(path)
    with closing(sqlite3.connect(path)) as conn:
        saved = conn.execute("SELECT * FROM hosted_room_output_artifacts").fetchone()
        assert saved[:len(record)] == record
        assert all(value is None for value in saved[len(record):])
        assert conn.execute("SELECT * FROM hosted_room_output_generation_fences").fetchone() == fence
    metadata, saved_bytes = outbox.read(scope, record[0])
    assert saved_bytes == data == blob.read_bytes()
    assert outbox.list(scope) == [metadata]
    message_id = "dmessage:" + scope.task_id.removeprefix("dtask:")
    assert outbox.acknowledge(scope, [record[0]], message_event_id=message_id) == 1
    assert not blob.exists()
    reopened = RoomArtifactOutbox(path)
    assert reopened.acknowledge(scope, [record[0]], message_event_id=message_id) == 0
    assert reopened.scope_manifest(scope) == [metadata]


def test_failed_outbox_upgrade_rolls_back_and_retries(tmp_path):
    path, scope, blob, data, record, columns, fence = _legacy_outbox(tmp_path)
    with closing(sqlite3.connect(path)) as conn:
        conn.set_authorizer(lambda op, *_: sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_CREATE_INDEX else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            with conn:
                RoomArtifactOutbox._initialize(conn)
        conn.set_authorizer(None)
        assert [row[1] for row in conn.execute("PRAGMA table_info(hosted_room_output_artifacts)")] == columns
        assert conn.execute("SELECT * FROM hosted_room_output_artifacts").fetchone() == record
        assert conn.execute("SELECT * FROM hosted_room_output_generation_fences").fetchone() == fence
    assert RoomArtifactOutbox(path).read(scope, record[0])[1] == data == blob.read_bytes()
