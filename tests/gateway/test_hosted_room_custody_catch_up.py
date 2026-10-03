"""Catch-up from another custodian: signed requests and replies, page by page, never waiting for the host.

Real stores and room identity keys per installation (``test_hosted_room_custody_lineage``); between
installations only the HTTP transport is replaced by a direct call to the real handler, made as the
installation that answers. The route itself is served once by aiohttp.
"""

import json
import time

import pytest

from gateway import hosted_room_custody as custody
from gateway import hosted_room_identity as identity
from gateway import hosted_rooms as rooms
from tests.gateway.test_hosted_room_custody_lineage import (  # noqa: F401
    MEMBERS, ROOM, acting, configure, copy_to, enroll, message, net, verify, watermark)
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError


@pytest.fixture
def custodians(net, monkeypatch):
    """home hosts with second and third as custodians; second holds everything, third only the start."""
    home, second, third = net["home"], net["second"], net["third"]
    with acting(home):
        rooms.create_room(home.db, room_id=ROOM, name="Workshop", members=MEMBERS, authority_gateway_id=home.install_id)
    enroll(home, second)
    enroll(home, third, successor=False)
    configure(home)
    for index in range(7):
        message(home, f"m{index}", 1)
    copy_to(home, second)
    copy_to(home, third, limit=2)
    by_endpoint, offline, replies = {i.endpoint: i for i in net.values()}, set(), []

    def custody_pages(self, *, body):
        source = by_endpoint[self.base_url]
        if source.name in offline:
            raise PeerRunsHTTPError("peer RoomLink endpoint is unreachable", retryable=True)
        with acting(source):
            try:
                reply = custody.serve_custodian_pages(source.db, json.loads(json.dumps(body)))
            except custody.CustodyAuthorizationError as exc:
                raise PeerRunsHTTPError("refused", status_code=403, error_code="custody_not_authorized") from exc
        replies.append(reply)
        return transform[0](reply) if transform else reply

    transform: list = []
    monkeypatch.setattr(PeerRunsHTTPClient, "custody_pages", custody_pages)
    return net, offline, transform


def catch_up(target, source, *, limit=2):
    pages = 0
    with acting(target):
        while True:
            fetched = custody.fetch_custodian_pages(target.db, room_id=ROOM, source_install_id=source.install_id,
                                                    after_seq=watermark(target)["seq"], limit=limit)
            pages += 1
            if custody.ingest_custodian_page(target.db, fetched, _verify_transition=verify)["stored_seq"] >= \
                    fetched["page"]["latest_seq"]:
                return pages


def test_a_custodian_catches_up_from_another_page_by_page(custodians):
    net, offline, _ = custodians
    second, third = net["second"], net["third"]
    assert watermark(third)["seq"] == 2 < watermark(second)["seq"]
    # The host is offline: catching up never waits for it, and resumes from the copy's own watermark.
    offline.add("home")
    with acting(third), pytest.raises(PeerRunsHTTPError, match="unreachable"):
        custody.fetch_custodian_pages(third.db, room_id=ROOM, source_install_id=net["home"].install_id,
                                      after_seq=2, limit=2)
    assert catch_up(third, second) >= 3
    assert watermark(third) == watermark(second)  # the exact same prefix, hash-chained


def test_catch_up_requests_and_replies_are_signed_and_scoped(custodians):
    net, _, transform = custodians
    home, second, third, fresh = (net[name] for name in ("home", "second", "third", "fresh"))
    with acting(third):
        request = {"room_id": ROOM, "requester_install_id": third.install_id, "source_install_id": second.install_id,
                   "after_seq": 0, "limit": 2, "issued_at": time.time(), "nonce": "a" * 32}
        signed = {**request, "signature": identity.sign(custody.PAGES_DOMAIN, request)}
    with acting(fresh):  # an installation the room's configuration does not list
        unlisted = {**request, "requester_install_id": fresh.install_id}
        stranger = {**unlisted, "signature": identity.sign(custody.PAGES_DOMAIN, unlisted)}
    with acting(second):
        assert custody.serve_custodian_pages(second.db, signed)["page"]["cursor"] == 2
        for body in ({**signed, "after_seq": 1},  # changed after signing
                     stranger,
                     {**signed, "source_install_id": home.install_id},  # addressed to another custodian
                     {**signed, "nonce": "not-a-nonce"}):
            with pytest.raises(custody.CustodyAuthorizationError):
                custody.serve_custodian_pages(second.db, body)
        with pytest.raises(custody.CustodyAuthorizationError, match="not current"):
            custody.serve_custodian_pages(second.db, signed, now=request["issued_at"] + custody.REQUEST_SKEW_SECONDS + 1)
        with pytest.raises(custody.CustodyError, match="fields"):
            custody.serve_custodian_pages(second.db, {**signed, "extra": True})
    # A reply changed in transit, or answering another request, is refused before anything is stored.
    for change in (lambda reply: {**reply, "room_name": "Elsewhere"}, lambda reply: {**reply, "nonce": "b" * 32}):
        transform[:] = [change]
        with acting(third), pytest.raises(custody.CustodyError):
            custody.fetch_custodian_pages(third.db, room_id=ROOM, source_install_id=second.install_id, after_seq=2,
                                          limit=2)
    assert watermark(third)["seq"] == 2


@pytest.mark.asyncio
async def test_the_catch_up_route_answers_only_signed_custodians(custodians):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gateway.platforms import api_server_room_replicas as routes
    from types import SimpleNamespace
    net, _, _ = custodians
    second, third = net["second"], net["third"]

    async def read_json_body(request):
        return await request.json(), None

    adapter = SimpleNamespace(_read_json_body=read_json_body, gateway_runner=None)
    app = web.Application()
    for method, path, handler in routes.http_routes(adapter):
        app.router.add_route(method, path, handler)
    with acting(third):
        request = {"room_id": ROOM, "requester_install_id": third.install_id, "source_install_id": second.install_id,
                   "after_seq": 0, "limit": 2, "issued_at": time.time(), "nonce": "c" * 32}
        body = {**request, "signature": identity.sign(custody.PAGES_DOMAIN, request)}
    async with TestClient(TestServer(app)) as client:
        with acting(second):
            import gateway.platforms.api_server_room_replicas as module
            original = module._grant_db
            module._grant_db = lambda adapter: second.db
            try:
                answered = await client.post(custody.PAGES_PATH, json=body)
                refused = await client.post(custody.PAGES_PATH, json={**body, "limit": 3})
            finally:
                module._grant_db = original
        assert answered.status == 200 and (await answered.json())["page"]["cursor"] == 2
        assert refused.status == 403 and (await refused.json())["error"]["code"] == "custody_not_authorized"
