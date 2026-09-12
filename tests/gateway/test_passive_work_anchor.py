"""Publisher hints consume real retained work without replacing its capture/ACK."""

import sqlite3
from contextlib import closing

import pytest

from gateway import hosted_room_work_records as work
from gateway import hosted_rooms as rooms


@pytest.mark.parametrize("outcome", ["acked", "old-authority", "invalid", "unavailable"])
def test_anchor_hint_tracks_exact_pending_scope_and_preserves_evidence(tmp_path, outcome):
    path = tmp_path / "work.db"
    rooms.create_room(path, room_id="room", name="Room", members=[{"profile": "ops", "handle": "ops"}],
                      authority_gateway_id="owner", now=10)
    rooms.append_event(path, room_id="room", event_id="input", kind="message.user",
        actor={"kind": "user", "id": "alice"}, payload={"text": "hello"},
        authority_gateway_id="owner", authority_epoch=1)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        args = dict(room_id="room", target_install_id="target", through_seq=1)
        assert not work.pending_delivery_is_anchored_locked(conn, **args)
        record = work.prepare_delivery_locked(conn, local_gateway_id="owner", route_generation="route", **args)
        assert work.pending_delivery_is_anchored_locked(conn, **args)
        assert not work.pending_delivery_is_anchored_locked(conn, **{**args, "through_seq": 0})
        if outcome == "acked":
            assert work.acknowledge_locked(conn, room_id="room", target_install_id="target",
                route_generation="route", record=record, ack=work.acknowledgement(record))
        elif outcome == "old-authority":
            conn.execute("UPDATE hosted_rooms SET authority_gateway_id='new-owner',authority_epoch=2 WHERE room_id='room'")
        elif outcome == "invalid":
            conn.execute(f"UPDATE {work.PENDING_TABLE} SET record_json='invalid'")
        else:
            work.delivery_status_locked(conn, room_id="room", target_install_id="target",
                route_generation="route", record=record, status="unavailable")
        before = conn.execute(f"SELECT record_json FROM {work.PENDING_TABLE}").fetchone()[0]
        assert work.pending_delivery_is_anchored_locked(conn, **args) is (outcome == "unavailable")
        assert conn.execute(f"SELECT record_json FROM {work.PENDING_TABLE}").fetchone()[0] == before
