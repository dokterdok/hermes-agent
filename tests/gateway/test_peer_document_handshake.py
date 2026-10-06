"""Document preparation remains scoped and idempotent under ambiguous admission errors."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gateway.hosted_room_peer import decode_room_grant, gateway_room_grant_secret
from gateway.platforms.api_server_room_proof import wrap
from tests.gateway.test_session_group_peers import gateway as gateway
from tests.gateway.test_session_group_peer_routes import joined
from tests.tui_gateway.test_hosted_room_peer_http import _dispatch
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError


@asynccontextmanager
async def document_peer(gateway, monkeypatch, *, prior_ambiguity=False, outcome="accepted", challenge="signed"):
    monkeypatch.setattr(gateway.adapter, "gateway_runner", gateway.authority.runner)
    monkeypatch.setattr(gateway.authority.runner, "session_authority", gateway.authority, raising=False)
    server, _, _, catalog, grant = await joined(gateway, monkeypatch)
    claims = decode_room_grant(gateway_room_grant_secret(), grant, permission="dispatch")
    scope = ("room_id", "home_install_id", "authority_gateway_id", "authority_epoch",
             "member_id", "target_install_id", "target_profile")
    data = b"verified document"
    dispatch = _dispatch(**{key: claims[key] for key in scope}, capability_digest=catalog["catalog_digest"],
                         execution_policy_digest=catalog["execution_policy"]["policy_digest"])
    dispatch["document_inputs"] = [{"event_id": "source-event", "attachment_id": "att_" + "1" * 32,
        "recipient_member_id": claims["member_id"], "kind": "file", "name": "notes.txt", "mime": "text/plain",
        "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]
    seen = {"requests": [], "transfers": 0, "admissions": 0}

    def transfer(_db, checked):
        assert checked.as_mapping() == dispatch
        seen["transfers"] += 1
        if outcome == "source_failure":
            raise OSError("source unavailable")
        return [base64.b64encode(data).decode()]

    monkeypatch.setattr("tui_gateway.hosted_room_peer_documents.transfer_documents", transfer)

    async def runs(request):
        body = await request.json()
        seen["requests"].append((request.headers["Idempotency-Key"], body))
        assert body["hosted_room_dispatch"] == dispatch
        if prior_ambiguity and len(seen["requests"]) == 1:
            return web.json_response({"error": {"code": "outcome_unknown"}}, status=503)
        if "document_bytes" not in body:
            code = {"invalid": True} if challenge == "malformed" else "room_document_input_required"
            return web.json_response({"error": {"code": code}}, status=409)
        assert body["document_bytes"] == [base64.b64encode(data).decode()]
        if outcome == "post_failure":
            return web.json_response({"error": {"code": "outcome_unknown"}}, status=503)
        seen["admissions"] += 1
        return web.json_response({"run_id": "document-run", "status": "running"}, status=202)

    signed_runs = wrap(gateway.adapter, runs)

    async def response(request):
        reply = await signed_runs(request)
        if challenge == "unsigned":
            return web.json_response({"error": {"code": "room_document_input_required"}}, status=409)
        if challenge == "mismatched":
            reply.headers["Hermes-Room-Proof"] = "wrong-response-proof"
        return reply

    async def capabilities(request):
        reply = await gateway.adapter._handle_room_member_capabilities(request)
        if challenge == "scope_changed":
            value = json.loads(reply.body)
            value["authority_epoch"] += 1
            return web.json_response(value, status=reply.status)
        return reply

    app = web.Application()
    app.router.add_post("/v1/runs", response)
    app.router.add_get("/v1/room-members/capabilities", wrap(gateway.adapter, capabilities))
    target = TestServer(app)
    await target.start_server()
    client = PeerRunsHTTPClient(base_url=str(target.make_url("")).rstrip("/"), api_key="",
                               proof_install_id=None if challenge == "legacy" else catalog["installation_id"])
    try:
        yield client, dispatch, grant, seen
    finally:
        await target.close()
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_ambiguity,outcome", [
    (False, "accepted"), (True, "accepted"), (True, "source_failure"), (True, "post_failure"),
])
async def test_verified_document_challenge_preserves_prior_uncertainty(gateway, monkeypatch, prior_ambiguity, outcome):
    async with document_peer(gateway, monkeypatch, prior_ambiguity=prior_ambiguity, outcome=outcome) as exchange:
        client, dispatch, grant, seen = exchange
        if outcome == "accepted":
            result = await asyncio.to_thread(client.dispatch, dispatch=dispatch, grant=grant)
            assert result["run_id"] == "document-run"
            assert seen["admissions"] == 1
        else:
            with pytest.raises(PeerRunsHTTPError) as caught:
                await asyncio.to_thread(client.dispatch, dispatch=dispatch, grant=grant)
            assert caught.value.ambiguous and not caught.value.not_admitted
            assert seen["admissions"] == 0
        assert seen["transfers"] == 1
        assert {key for key, _ in seen["requests"]} == {
            f"room:{dispatch['task_id']}:{dispatch['execution_generation']}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("challenge", ["unsigned", "mismatched", "malformed", "scope_changed", "legacy"])
async def test_unverified_or_other_challenges_never_receive_document_bytes(gateway, monkeypatch, challenge):
    async with document_peer(gateway, monkeypatch, challenge=challenge) as exchange:
        client, dispatch, grant, seen = exchange
        with pytest.raises(PeerRunsHTTPError) as caught:
            await asyncio.to_thread(client.dispatch, dispatch=dispatch, grant=grant)
        assert caught.value.ambiguous and not caught.value.not_admitted
        assert seen["transfers"] == seen["admissions"] == 0
        assert all("document_bytes" not in body for _, body in seen["requests"])
