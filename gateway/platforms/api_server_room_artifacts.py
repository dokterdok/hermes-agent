"""#99159 read/ACK port, restricted to exact canonical root-target Runs.

Source: 5ee4a941a6b64a084221d7d521ce42480bedc15c. Route registration is
separate from permission issuance and producer exposure. No discard route.
"""

from __future__ import annotations

from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore[assignment]


def _http_routes(self) -> list[tuple[str, str, Any]]:
    async def read_artifact(request):
        from gateway.platforms import api_server

        return await _handle_room_run_artifact(
            self,
            request,
            _openai_error=api_server._openai_error,
        )

    async def acknowledge(request):
        from gateway.platforms import api_server

        return await _handle_room_run_artifact_ack(
            self,
            request,
            _openai_error=api_server._openai_error,
        )

    return [
        (
            "GET",
            "/v1/runs/{run_id}/artifacts/{artifact_id}",
            read_artifact,
        ),
        (
            "POST",
            "/v1/runs/{run_id}/artifacts/ack",
            acknowledge,
        ),
    ]


def _load_scope_and_status(
    self,
    request,
    *,
    permission: str,
    validate_manifest: bool = True,
):
    from gateway.hosted_room_artifacts import (
        RoomArtifactScope,
        validate_terminal_artifact_manifest,
    )

    claims = self._room_grant_claims(request, permission=permission)
    run_id = str(request.match_info["run_id"])
    if not self._request_owns_run(request, run_id):
        raise ValueError("run not found")
    from gateway.platforms.api_server_authority_runs import run_admission, run_projection
    from gateway.session_peer_output import root_output_authority
    owned = run_admission(self, run_id)
    if owned is None or not root_output_authority(owned[0]):
        raise ValueError("run not found")
    status = run_projection(self, run_id)
    if not isinstance(status, dict):
        raise ValueError("run not found")
    if status.get("status") != "completed":
        raise ValueError("run not completed")
    scope = RoomArtifactScope.from_mapping(status.get("room_artifact_scope") or {})
    if any(
        claims.get(key) != value
        for key, value in (
            ("room_id", scope.room_id),
            ("home_install_id", scope.home_install_id),
            ("authority_gateway_id", scope.authority_gateway_id),
            ("authority_epoch", scope.authority_epoch),
            ("member_id", scope.member_id),
            ("target_install_id", scope.target_install_id),
            ("target_profile", scope.target_profile),
        )
    ):
        raise ValueError("run not found")
    manifest = (
        validate_terminal_artifact_manifest(status.get("artifacts"))
        if validate_manifest
        else []
    )
    return run_id, scope, status, manifest


async def _handle_room_run_artifact(
    self,
    request: "web.Request",
    *,
    _openai_error,
) -> "web.Response":
    try:
        _run_id, scope, _status, manifest = _load_scope_and_status(
            self,
            request,
            permission="artifact.read",
        )
        artifact_id = str(request.match_info["artifact_id"])
        expected = next(
            (item for item in manifest if item["artifact_id"] == artifact_id),
            None,
        )
        if expected is None:
            raise ValueError("artifact not found")
        from gateway.hosted_room_artifacts import RoomArtifactOutbox
        from gateway.platforms.api_server_authority_runs import run_admission
        authority, _ = run_admission(self, _run_id)
        metadata, data = RoomArtifactOutbox(authority.db.db_path).read(scope, artifact_id)
        if metadata != expected:
            raise ValueError("artifact not found")
        return web.Response(
            body=data,
            content_type=metadata["mime"],
            headers={
                "X-Hermes-Artifact-SHA256": metadata["sha256"],
                "Cache-Control": "no-store",
            },
        )
    except Exception:
        return web.json_response(
            _openai_error("Artifact not found.", code="artifact_not_found"),
            status=404,
        )


async def _handle_room_run_artifact_ack(
    self,
    request: "web.Request",
    *,
    _openai_error,
) -> "web.Response":
    try:
        _run_id, scope, status, manifest = _load_scope_and_status(
            self,
            request,
            permission="artifact.ack",
        )
        body = await request.json()
        # Body I/O yields: none of the pre-read authorization is a write grant.
        _run_id, scope, status, manifest = _load_scope_and_status(
            self, request, permission="artifact.ack")
        if not isinstance(body, dict) or set(body) != {
            "artifact_ids",
            "manifest_digest",
            "message_event_id",
        }:
            raise ValueError("invalid acknowledgement")
        artifact_ids = body["artifact_ids"]
        expected_message_event_id = (
            f"dmessage:{scope.task_id.removeprefix('dtask:')}"
        )
        if (
            not isinstance(artifact_ids, list)
            or tuple(artifact_ids) != tuple(item["artifact_id"] for item in manifest)
            or body["manifest_digest"] != status["artifacts"]["manifest_digest"]
            or body["message_event_id"] != expected_message_event_id
        ):
            raise ValueError("invalid acknowledgement")
        from gateway.hosted_room_artifacts import RoomArtifactOutbox
        from gateway.platforms.api_server_authority_runs import run_admission
        authority, _ = run_admission(self, _run_id)
        expected = (_run_id, scope, status["artifacts"], manifest)

        def authorize_ack(conn, checked_scope):
            from gateway import hosted_rooms
            from hermes_state_runtime import _epoch
            _epoch(conn, authority.epoch)
            current_id, current_scope, current_status, current_manifest = _load_scope_and_status(
                self, request, permission="artifact.ack")
            if (checked_scope != scope or run_admission(self, current_id)[0] is not authority
                    or (current_id, current_scope, current_status["artifacts"], current_manifest) != expected):
                raise ValueError("artifact acknowledgement binding changed")
            # Refresh signature/permission/expiry and shared grant state, then
            # check the profile's enforcing copy under the actual ACK write lock.
            claims = self._room_grant_claims(request, permission="artifact.ack")
            if (hosted_rooms.room_grant_is_revoked(authority.db.db_path, claims=claims, _conn=conn)
                    or not hosted_rooms.peer_room_grant_is_current(authority.db.db_path, claims=claims, _conn=conn)):
                raise ValueError("artifact acknowledgement grant changed")

        outbox = RoomArtifactOutbox(authority.db.db_path, authorize_write=authorize_ack)
        # Exact Run/grant/manifest/event checks above remain mandatory after the
        # short ACK receipt expires. Durable completed retirement is positive evidence.
        changed = 0 if outbox.retirement_complete(scope) else outbox.acknowledge(
            scope,
            artifact_ids,
            message_event_id=expected_message_event_id,
        )
        return web.json_response({"acknowledged": True, "changed": changed})
    except Exception:
        return web.json_response(
            _openai_error(
                "Artifact acknowledgement was rejected.",
                code="invalid_artifact_ack",
            ),
            status=409,
        )
