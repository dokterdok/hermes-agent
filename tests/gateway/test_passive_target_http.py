"""Ordinary loopback endpoint tests. No API runtime, inference, or deployment."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.platforms import api_server_room_replicas as history_api
from gateway.platforms import api_server_room_work_records as work_api
from gateway.platforms import api_server_replica_retirement as retirement_api
from gateway import hosted_room_replicas as replicas
from gateway import hosted_room_work_records as work
from gateway import hosted_rooms as rooms
from tests.gateway.passive_ingress_fixtures import pair, OWNER_KEY  # noqa: F401


def application():
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": OWNER_KEY}))
    app = web.Application()
    for module in (history_api, work_api, retirement_api):
        for method, path, handler in module.http_routes(adapter):
            app.router.add_route(method, path, handler)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_owner_enrollment_scoped_history_work_and_retirement_round_trip(pair, version):
    if version == 2:
        pair.successor_fixture()
    public, history, closing_value = pair.enrollment()
    headers = {"Authorization": f"HermesRoom {pair.token}"}
    async with TestClient(TestServer(application())) as http:
        body = {"enrollment": public}
        if history is not None:
            body["authority_history"] = history
        response = await http.post("/v1/group-replicas/enroll", json=body,
                                   headers={"Authorization": f"Bearer {OWNER_KEY}"})
        assert response.status == 200, await response.text()
        record = pair.record()
        response = await http.post("/v1/room-members/work-records", json={"record": record}, headers=headers)
        assert response.status == 409
        page_body = dict(room_id="room", room_name="Workshop", members=pair.members, page=pair.page())
        response = await http.post("/v1/room-members/replica", json=page_body, headers=headers)
        assert response.status == 200, await response.text()
        copied = await response.json()
        assert copied["stored_seq"] == record["history"]["seq"]
        for _ in range(2):
            response = await http.post("/v1/room-members/work-records", json={"record": record}, headers=headers)
            assert response.status == 200, await response.text()
            ack = await response.json()
            assert ack == {"object": "hermes.room_member.work_records", **work.acknowledgement(record)}
        notice = {k: v for k, v in public.items() if k not in {"roster_sha256", "commitment"}}
        for _ in range(2):
            response = await http.post("/v1/group-replicas/retire", json=notice,
                headers={"Authorization": f"HermesReplicaRetirement {closing_value}"})
            assert response.status == 200, await response.text()
            assert (await response.json())["retired"] is True
        response = await http.post("/v1/room-members/work-records", json={"record": record}, headers=headers)
        assert response.status == 409
        response = await http.post("/v1/room-members/replica", json=page_body, headers=headers)
        assert response.status == 409
    assert not rooms.list_rooms(pair.target)
    assert replicas.replica_state(pair.target, room_id="room")["safety_status"] == "retired"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["replica", "work-records"])
async def test_endpoint_uses_scoped_grant_and_bounded_request_body(pair, monkeypatch, route):
    async with TestClient(TestServer(application())) as http:
        response = await http.post(f"/v1/room-members/{route}", json={},
                                   headers={"Authorization": f"Bearer {OWNER_KEY}"})
        assert response.status == 401
        if route == "replica":
            monkeypatch.setattr(history_api, "MAX_REPLICA_HTTP_BYTES", 64)
        else:
            monkeypatch.setattr(work, "MAX_BYTES", 64)
        response = await http.post(f"/v1/room-members/{route}", json={"extra": "x" * 4096},
                                   headers={"Authorization": f"HermesRoom {pair.token}"})
        assert response.status in {400, 413}
    assert not rooms.list_rooms(pair.target)
