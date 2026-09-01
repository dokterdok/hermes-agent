"""Focused contract test for reconnect grant identity."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gateway.hosted_room_peer import GatewayRoomCatalog, catalog_mapping
from tui_gateway.hosted_room_peer_transport import PeerMemberRoute
from tui_gateway.hosted_room_service import HostedRoomService


def _route(grant: str) -> PeerMemberRoute:
    return PeerMemberRoute(
        home_install_id="install-home",
        member_id="member-peer",
        target_install_id="install-peer",
        target_profile="reviewer",
        capability_digest="catalog-digest",
        cancellation_scope_id="cancel-scope",
        trace_id="trace-id",
        grant=grant,
    )


def _service(db_path=None) -> HostedRoomService:
    service = object.__new__(HostedRoomService)
    if db_path is not None:
        service.db_path = db_path
    service._policy_lock = threading.RLock()
    service._peer_route_status = {("room-1", "member-peer"): "ready"}
    service._persisted_peer_route_keys = set()
    service.peer_routes = {("room-1", "member-peer"): _route("signed.room.grant")}
    service.peer_clients = {}
    service._unblock_artifact_retries = lambda _room_id, _member_id: None
    service.runtime = SimpleNamespace(wakeup=lambda: None)
    service.status = lambda _room_id: {
        "working": False,
        "peer_routes": service._route_statuses("room-1"),
    }
    return service


def test_route_status_exposes_only_the_grant_fingerprint():
    service = _service()
    status = service.status_with_grant_fingerprints("room-1")

    assert status["peer_routes"] == [
        {
            "room_id": "room-1",
            "member_id": "member-peer",
            "status": "ready",
            "grant_sha256": hashlib.sha256(b"signed.room.grant").hexdigest(),
        }
    ]
    assert "signed.room.grant" not in repr(status)


def test_route_status_and_fingerprint_are_one_locked_snapshot():
    service = _service()
    status_started = threading.Event()
    rotated = threading.Event()

    def status(_room_id):
        status_started.set()
        assert not rotated.wait(0.05)
        return {"peer_routes": service._route_statuses("room-1")}

    def rotate():
        status_started.wait()
        with service._policy_lock:
            service.peer_routes[("room-1", "member-peer")] = _route(
                "replacement.room.grant"
            )
        rotated.set()

    service.status = status
    worker = threading.Thread(target=rotate)
    worker.start()
    snapshot = service.status_with_grant_fingerprints("room-1")
    worker.join(timeout=1)

    assert rotated.is_set()
    assert snapshot["peer_routes"][0]["grant_sha256"] == hashlib.sha256(
        b"signed.room.grant"
    ).hexdigest()


def test_peer_registration_compares_and_swaps_the_observed_grant(tmp_path):
    service = _service(tmp_path / "state.db")
    catalog = GatewayRoomCatalog.from_mapping(
        catalog_mapping(installation_id="install-peer", persistent_process=True)
    )
    current = replace(
        _route("signed.room.grant"),
        capability_digest=catalog.catalog_digest,
        execution_policy_digest=catalog.execution_policy.policy_digest,
    )
    service.peer_routes[("room-1", "member-peer")] = current
    old_sha256 = hashlib.sha256(b"signed.room.grant").hexdigest()
    winner = replace(current, grant="winner.room.grant")
    persistence = {
        "target_url": "https://peer.example.test",
        "catalog": catalog,
    }

    with pytest.raises(RuntimeError, match="changed during reconnect"):
        service.register_peer_route(
            room_id="room-1",
            member_id="member-peer",
            route=winner,
            client=object(),
            expected_grant_sha256="0" * 64,
            **persistence,
        )

    assert service.peer_routes[("room-1", "member-peer")].grant == "signed.room.grant"
    service.register_peer_route(
        room_id="room-1",
        member_id="member-peer",
        route=winner,
        client=object(),
        expected_grant_sha256=old_sha256,
        **persistence,
    )
    service.register_peer_route(
        room_id="room-1",
        member_id="member-peer",
        route=winner,
        client=object(),
        expected_grant_sha256=old_sha256,
        **persistence,
    )
    assert service.peer_routes[("room-1", "member-peer")].grant == "winner.room.grant"
