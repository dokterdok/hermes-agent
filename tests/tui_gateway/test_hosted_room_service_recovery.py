"""Retained peer generation and profile discovery remain valid after service changes."""
from pathlib import Path

from gateway import hosted_room_driver as driver, hosted_rooms
from gateway.hosted_room_peer import GatewayRoomCatalog, catalog_mapping
from tui_gateway.hosted_room_peer_transport import PeerMemberRoute
from tui_gateway.hosted_room_service import HostedRoomService
from tests.tui_gateway.test_hosted_room_service import _FakePeerClient, _server


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


def test_peer_recovery_replays_the_same_execution_generation(tmp_path: Path):
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

    service._resolve_member_transport(
        service.bindings()[0],
        {
            "identity": identity,
            "status": "indeterminate",
            "execution_generation": 1,
            "payload": {
                "target_member_id": "member-peer",
                "target_profile": "reviewer",
                "source_event_seq": 9,
                "prompt": "Recover the accepted review.",
            },
        },
    )

    assert len(peer.recoveries) == 1
    recovered = peer.recoveries[0]["dispatch"]
    assert recovered["task_id"] == "task-1"
    assert recovered["execution_generation"] == 1
    assert recovered["prompt"] == "Recover the accepted review."


def test_local_profiles_skips_delete_tombstones_and_dot_dirs(tmp_path: Path):
    """`hermes profile delete` leaves ``profiles/.deleted/<name>``; neither the tombstone dir, a
    tombstoned profile, nor a marker-less cron shell is a roster member (#106847: ``.deleted``
    failed validate_roster every cycle; #99392: side-effect dirs listed as bots)."""
    from hermes_constants import mark_named_profile_deleted

    profiles = tmp_path / "profiles"
    (profiles / "ops").mkdir(parents=True)
    (profiles / "ops" / "config.yaml").write_text("{}\n", encoding="utf-8")
    (profiles / "gone").mkdir()
    (profiles / "gone" / "config.yaml").write_text("{}\n", encoding="utf-8")
    mark_named_profile_deleted(profiles / "gone")
    assert (profiles / ".deleted").is_dir()
    (profiles / "shell" / "cron").mkdir(parents=True)

    service = HostedRoomService(_server(), db_path=tmp_path / "shared-state.db")

    assert service.local_profiles() == ("default", "ops")
