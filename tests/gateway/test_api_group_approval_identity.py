"""Room-grant approval controls require one exact request and never widen approval policy."""
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tools import approval as approval_mod
from tools import approval_gateway_wait
from tests.gateway.test_api_server_runs import auth_adapter as auth_adapter, _create_runs_app


@pytest.mark.asyncio
async def test_room_approval_requires_and_resolves_exact_request_id(
    auth_adapter
):
    run_id = "run-room-approval"
    current = approval_gateway_wait._ApprovalEntry({
        "request_id": "approval-B",
        "command": "rm -rf build-B",
    })
    auth_adapter._run_approval_sessions[run_id] = run_id
    auth_adapter._run_statuses[run_id] = {
        "run_id": run_id,
        "status": "waiting_for_approval",
        "approval": dict(current.data),
    }
    with approval_mod._lock:
        approval_mod._gateway_queues[run_id] = [current]
    app = _create_runs_app(auth_adapter)
    try:
        with (
            patch.object(auth_adapter, "_check_run_auth", return_value=None),
            patch.object(auth_adapter, "_request_owns_run", return_value=True),
            patch.object(
                auth_adapter, "_room_grant_token", return_value="scoped-grant"
            ),
            # This isolated approval fixture uses a sentinel grant rather than
            # signing a room claim; pin its store scope without decoding it.
            patch.object(auth_adapter, "_run_idempotency_scope", return_value="0" * 64),
        ):
            async with TestClient(TestServer(app)) as cli:
                missing = await cli.post(
                    f"/v1/runs/{run_id}/approval",
                    json={"choice": "once"},
                )
                stale = await cli.post(
                    f"/v1/runs/{run_id}/approval",
                    json={"choice": "once", "request_id": "approval-A"},
                )
                exact = await cli.post(
                    f"/v1/runs/{run_id}/approval",
                    json={"choice": "once", "request_id": "approval-B"},
                )
                missing_body = await missing.json()
                stale_body = await stale.json()
                exact_body = await exact.json()
    finally:
        approval_mod.unregister_gateway_notify(run_id)

    assert missing.status == 400
    assert missing_body["error"]["code"] == "approval_request_required"
    assert stale.status == 409
    assert stale_body["error"]["code"] == "approval_not_pending"
    assert exact.status == 200
    assert exact_body["request_id"] == "approval-B"
    assert current.result == "once"
    assert "approval" not in auth_adapter._run_statuses[run_id]



@pytest.mark.asyncio
async def test_room_grant_cannot_create_session_or_permanent_approval_policy(
    auth_adapter
):
    app = _create_runs_app(auth_adapter)
    with (
        patch.object(auth_adapter, "_check_run_auth", return_value=None),
        patch.object(auth_adapter, "_request_owns_run", return_value=True),
        patch.object(
            auth_adapter,
            "_durable_run_status",
            return_value={"status": "waiting_for_approval"},
        ),
        patch.object(
            auth_adapter, "_room_grant_token", return_value="scoped-grant"
        ),
    ):
        async with TestClient(TestServer(app)) as cli:
            permanent = await cli.post(
                "/v1/runs/run-room/approval",
                json={"choice": "always"},
            )
            resolve_all = await cli.post(
                "/v1/runs/run-room/approval",
                json={"choice": "once", "resolve_all": True},
            )
            permanent_body = await permanent.json()
            resolve_all_body = await resolve_all.json()

    assert permanent.status == 400
    assert permanent_body["error"]["code"] == "invalid_approval_choice"
    assert resolve_all.status == 400
    assert resolve_all_body["error"]["code"] == "invalid_approval_scope"

