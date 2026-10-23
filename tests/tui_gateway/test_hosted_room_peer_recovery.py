"""Focused RoomLink peer-recovery regressions."""

import time
from pathlib import Path

from gateway import hosted_room_driver as driver
from gateway import hosted_rooms
from gateway.hosted_room_peer import GatewayRoomCatalog, catalog_mapping
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPError
from tui_gateway.hosted_room_peer_transport import PeerMemberRoute
from tui_gateway.hosted_room_service import HostedRoomService

from tests.tui_gateway.test_hosted_room_driver_runtime import (
    BINDING,
    PROFILE,
    ROOM_ID,
    FakeSessionRPC,
    _admit,
    _identity,
    _runtime,
)
from tests.tui_gateway.test_hosted_room_service import (
    _FakePeerClient,
    _server,
)


class _RecoveringPeerClient(_FakePeerClient):
    def __init__(self) -> None:
        super().__init__()
        self.recoveries = []

    def recover_dispatch(self, **kwargs):
        dispatch = dict(kwargs["dispatch"])
        self.recoveries.append({**kwargs, "dispatch": dispatch})
        self.dispatches.append(dispatch)
        return {
            "status": "accepted",
            "task_id": dispatch["task_id"],
            "execution_generation": dispatch["execution_generation"],
            "run_id": "run-recovered",
        }


def test_peer_recovery_replays_only_indeterminate_generation(tmp_path: Path):
    db = tmp_path / "state.db"
    catalog = GatewayRoomCatalog.from_mapping(
        catalog_mapping(target_profile="default", installation_id="install-peer", persistent_process=True)
    )
    route = PeerMemberRoute(
        home_install_id=hosted_rooms.local_authority_gateway_id(),
        member_id="member-peer",
        target_install_id="install-peer",
        target_profile="reviewer",
        capability_digest=catalog.catalog_digest,
        cancellation_scope_id="cancel-room-1",
        trace_id="trace-room-1",
        grant="signed.room.grant",
    )
    peer = _RecoveringPeerClient()
    service = HostedRoomService(_server(), db_path=db)
    service.register_peer_route(
        room_id="room-1",
        member_id="member-peer",
        route=route,
        client=peer,
        target_url="https://peer.example.test",
        catalog=catalog,
    )
    service.create_room(
        room_id="room-1",
        name="Peer room",
        members=[
            {"member_id": "default", "profile": "default", "handle": "hermes"},
            {
                "member_id": "member-peer",
                "profile": "reviewer",
                "handle": "reviewer",
                "target": {
                    "kind": "peer",
                    "peer_id": "peer-review",
                    "installation_id": "install-peer",
                    "profile": "reviewer",
                    "capability_digest": catalog.catalog_digest,
                },
            },
        ],
    )
    identity = driver.TaskIdentity("room-1", "task-1", "thread-1", "turn-1")
    task = {
        "identity": identity,
        "execution_generation": 1,
        "payload": {
            "target_member_id": "member-peer",
            "target_profile": "reviewer",
            "source_event_seq": 9,
            "prompt": "Recover the accepted review.",
        },
    }

    service._resolve_member_transport(
        service.bindings()[0],
        {**task, "status": "running"},
    )
    assert peer.recoveries == []

    service._resolve_member_transport(
        service.bindings()[0],
        {**task, "status": "indeterminate"},
    )

    assert len(peer.recoveries) == 1
    recovered = peer.recoveries[0]["dispatch"]
    assert recovered["task_id"] == "task-1"
    assert recovered["execution_generation"] == 1
    assert recovered["prompt"] == "Recover the accepted review."


def _driver_room(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    hosted_rooms.create_room(
        db, room_id=ROOM_ID, name="Release room", members=[{"profile": PROFILE, "handle": PROFILE}],
        authority_gateway_id=BINDING.gateway_id, now=time.time())
    return db


def test_contradictory_admission_flags_keep_the_same_attempt_at_lease_expiry(tmp_path: Path):
    db = _driver_room(tmp_path)
    now = [100.0]
    identity = _identity()
    _admit(db, identity)

    class UncertainRPC(FakeSessionRPC):
        def submit(self, **kwargs):
            super().submit(**kwargs)
            raise PeerRunsHTTPError("mixed admission evidence", retryable=True, ambiguous=True, not_admitted=True)

    rpc = UncertainRPC(auto_complete=False)
    runtime = _runtime(db, rpc, clock=lambda: now[0], lease_ttl_seconds=1)
    runtime._run_cycle()
    first = driver.get_task(db, identity)
    assert first["status"] == "running" and first["execution_generation"] == 1
    runtime._run_cycle()
    now[0] += 2
    runtime._run_cycle()
    recovered = driver.get_task(db, identity)
    assert recovered["status"] == "indeterminate" and recovered["execution_generation"] == 1
    assert [params["execution_generation"] for method, params in rpc.calls if method == "submit"] == [1]
