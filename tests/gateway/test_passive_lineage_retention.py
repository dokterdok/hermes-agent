"""Already-retained lineage fixtures, not authority-changing operations."""

import json
import sqlite3
from contextlib import closing

import pytest

from gateway import hosted_room_passive_lineage as lineage
from gateway import hosted_room_work_lineage as provenance
from gateway import hosted_room_work_records as work


def descriptor(start):
    history = [{"gateway_id": "owner-a", "epoch": 1, "from_seq": 0},
               {"gateway_id": "owner-b", "epoch": 2, "from_seq": start}]
    return lineage.descriptor(history, gateway_id="owner-b", epoch=2)


@pytest.mark.parametrize("start", [2, 3, 4, 5])
def test_enrollment_extension_cannot_discard_known_source_tail(tmp_path, start):
    spans, _, _ = descriptor(start)
    with closing(sqlite3.connect(tmp_path / "retained.db")) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE hosted_room_replica_events (room_id TEXT, seq INTEGER, authority_epoch INTEGER, kind TEXT, actor_json TEXT, payload_json TEXT)")
        for seq in (1, 2):
            conn.execute("INSERT INTO hosted_room_replica_events VALUES ('room',?,1,'message.user',?,?)",
                         (seq, json.dumps({"kind": "user", "id": "alice"}), json.dumps({"text": "hello"})))
        previous = dict(authority_gateway_id="owner-a", authority_epoch=1, version=None)
        replica = dict(latest_seq=4, replica_version=None, authority_gateway_id="owner-a", authority_epoch=1)
        before = [tuple(row) for row in conn.execute("SELECT * FROM hosted_room_replica_events")]
        if start <= replica["latest_seq"]:
            with pytest.raises(lineage.PassiveLineageError):
                lineage.compatible_extension(conn, "room", spans, previous=previous, replica=replica)
        else:
            lineage.compatible_extension(conn, "room", spans, previous=previous, replica=replica)
            assert lineage.status(spans, 2) == "pending"
        assert [tuple(row) for row in conn.execute("SELECT * FROM hosted_room_replica_events")] == before


@pytest.mark.parametrize("change", ["none", "prefix", "digest", "header", "receipt"])
def test_work_target_prefix_keeps_original_producer_and_requires_verified_lineage(tmp_path, change):
    _, encoded, digest = descriptor(3)
    with closing(sqlite3.connect(tmp_path / "target.db")) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(f"CREATE TABLE {lineage.ENROLLMENTS} (room_id TEXT, is_current INTEGER, version INTEGER, authority_gateway_id TEXT, authority_epoch INTEGER, lineage_sha256 TEXT, authority_history_json TEXT)")
        conn.execute(f"INSERT INTO {lineage.ENROLLMENTS} VALUES ('room',1,2,'owner-b',2,?,?)", (digest, encoded))
        replica = dict(room_id="room", replica_version=2, lineage_sha256=digest,
                       authority_gateway_id="owner-b", authority_epoch=2, last_seq=3)
        # Projection of an already-validated record used only by provenance.
        record = dict(lineage_sha256=digest, history={"seq": 3},
            tasks=[{"task_id": "old-task", "source_event_seq": 1}],
            receipts=[{"home_install_id": "owner-a", "authority_gateway_id": "owner-a", "authority_epoch": 1}])
        if change == "prefix":
            record["history"]["seq"] = 2
        elif change == "digest":
            record["lineage_sha256"] = "0" * 64
        elif change == "header":
            replica["authority_gateway_id"] = "owner-a"
        elif change == "receipt":
            record["receipts"][0]["authority_gateway_id"] = "owner-b"
        if change != "none":
            with pytest.raises(work.WorkRecordError):
                provenance.target_prefix_locked(conn, replica, record)
        else:
            spans = provenance.target_prefix_locked(conn, replica, record)
            assert provenance.task_origins(record, spans) == {"old-task": {"gateway_id": "owner-a", "epoch": 1}}
            assert record["receipts"][0]["home_install_id"] == "owner-a"
