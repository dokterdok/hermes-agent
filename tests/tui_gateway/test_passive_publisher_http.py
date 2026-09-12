"""Real loopback publisher/receiver integration without runtime registration."""

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_room_replicas as replicas
from gateway import hosted_rooms as rooms
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.platforms import api_server_room_replicas as history_api
from tui_gateway.hosted_room_replication import HostedRoomReplicationPublisher
from tui_gateway.hosted_room_replication_http import PassiveReplicationHTTPClient
from tests.gateway.passive_ingress_fixtures import pair, OWNER_KEY, signed_grant  # noqa: F401
from tests.tui_gateway.passive_publisher_fixtures import KEY, enroll, save_link


def app_for(pair):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": OWNER_KEY}))
    app = web.Application()
    for method, path, handler in adapter._http_route_table():
        if path in {'/v1/room-members/replica', '/v1/room-members/work-records',
                    '/v1/room-members/capabilities'}:
            app.router.add_route(method, path, handler)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_publisher_reaches_real_authenticated_history_and_work_endpoints(pair, version):
    if version == 2:
        pair.successor_fixture()
    pair.token = signed_grant(pair.secret, gateway=pair.gateway, epoch=pair.epoch,
                             permissions=("status", "replicate", "work_records"))
    pair.reserve()
    async with TestClient(TestServer(app_for(pair))) as http:
        url = str(http.make_url("/"))
        if version == 2:
            enroll(pair, url)
        save_link(pair, url=url)
        pub = HostedRoomReplicationPublisher(pair.source, local_gateway_id=pair.gateway)
        for _ in range(3):
            await asyncio.to_thread(pub._publish_one, KEY)
        state = replicas.replica_state(pair.target, room_id="room")
        assert state["last_seq"] == pair.page()["latest_seq"]
        assert state["work_records"]["availability"] == "available"
        assert state["work_records"]["source_loss_safe"] is False
        assert pub.status()["work_records"][0]["status"] == "acked"
        assert not pub.status()["running"]  # Direct bounded steps, no automatic lifecycle.
    assert not rooms.list_rooms(pair.target)


@pytest.mark.asyncio
async def test_utf8_page_fits_real_receiver_without_ascii_expansion(pair):
    for seq in range(8):
        rooms.append_event(pair.source, room_id="room", event_id=f"unicode-{seq}", kind="message.user",
            actor={"kind": "user", "id": "alice"}, payload={"text": "\u00e9" * 130000},
            authority_gateway_id=pair.gateway, authority_epoch=pair.epoch)
    body = dict(room_id="room", room_name="Workshop", members=pair.members, page=pair.page())
    assert len(json.dumps(body, ensure_ascii=True).encode()) > history_api.MAX_REPLICA_HTTP_BYTES
    assert len(json.dumps(body, ensure_ascii=False).encode()) < history_api.MAX_REPLICA_HTTP_BYTES
    async with TestClient(TestServer(app_for(pair))) as http:
        client = PassiveReplicationHTTPClient(base_url=str(http.make_url("/")))
        ack = await asyncio.to_thread(client.replicate_page, grant=pair.token, target_profile="default", **body)
        assert ack["stored_seq"] == body["page"]["cursor"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix,profile", [("", "reviewer"), ("/p/reviewer", "reviewer"), ("", "default")])
async def test_passive_transport_addresses_profile_once(suffix, profile):
    path = ("" if profile == "default" else "/p/reviewer") + "/v1/room-members/work-records"
    app = web.Application()
    async def accept(request):
        assert request.headers["Authorization"] == "HermesRoom disposable-scoped-fixture"
        return web.json_response({"path": request.path})
    app.router.add_post(path, accept)
    async with TestClient(TestServer(app)) as http:
        client = PassiveReplicationHTTPClient(base_url=str(http.make_url(suffix or "/")))
        result = await asyncio.to_thread(client.replicate_work_records,
            grant="disposable-scoped-fixture", target_profile=profile, record={})
        assert result["path"] == path
