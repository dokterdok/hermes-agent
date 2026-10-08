"""RoomLink room-member grants and capability HTTP handlers."""

from contextlib import nullcontext

import sqlite3
import sys
import time
import uuid
from typing import Any, Optional

try:
    from aiohttp import web
except ImportError:
    web = None  # type: ignore[assignment]


class RoomGrantReauthorizationRequired(ValueError):
    """A validly signed room grant was revoked or superseded."""


def _json_error(_openai_error, message: str, *, status: int, **error_kwargs) -> "web.Response":
    """JSON error response built with the injected ``_openai_error`` envelope builder."""
    return web.json_response(_openai_error(message, **error_kwargs), status=status)


def adapter_handler(name: str, api_server):
    """Bind the adapter while resolving grant handlers and facade policy at call time."""
    async def handler(self, request: "web.Request") -> "web.Response":
        return await getattr(sys.modules[__name__], name)(
            self, request, _openai_error=api_server._openai_error,
            _api_request_profile=api_server._api_request_profile)
    handler.__name__ = name
    return handler


def _require_unchanged_execution_policy(claims: dict[str, Any], execution_policy: dict[str, Any]) -> None:
    """Keep renewal from silently granting a changed execution policy."""
    if str(execution_policy.get("policy_digest") or "") != str(claims.get("execution_policy_digest") or ""):
        raise RoomGrantReauthorizationRequired("room execution policy changed")


def _room_grant_error_response(exc: Optional[Exception] = None, *, _openai_error) -> "web.Response":
    """401 invalid grant, 403 reauthorization-required for a revoked/superseded grant, or 409 for an
    authority epoch fenced here for succession."""
    from gateway.hosted_room_fence import RoomAuthorityFenced
    if isinstance(exc, RoomAuthorityFenced):
        return _json_error(_openai_error, exc.message, err_type="gateway_auth_error", code=exc.code, status=exc.status)
    if isinstance(exc, RoomGrantReauthorizationRequired):
        message, code, status = "Room authorization needs to be renewed.", "room_reauthorization_required", 403
    else:
        message, code, status = "Room authorization is invalid or expired.", "invalid_room_grant", 401
    return _json_error(_openai_error, message, err_type="gateway_auth_error", code=code, status=status)


def _hard_expiry(claims: dict[str, Any]) -> float:
    return float(claims.get("status_expires_at", claims["expires_at"]))


_ROOM_IDENTITY_FIELDS = ("room_id", "home_install_id", "authority_gateway_id", "authority_epoch", "member_id")


def _room_identity(source: dict[str, Any], *, coerce: bool = False) -> dict[str, Any]:
    """Room-authority kwargs for ``issue_room_grant``; *coerce* applies ``str``/``int`` to raw body values."""
    text = str if coerce else (lambda v: v)
    return {k: int(source[k]) if k == "authority_epoch" else text(source[k]) for k in _ROOM_IDENTITY_FIELDS}


def _local_target(claims: dict[str, Any] | None, _api_request_profile) -> tuple[str, str]:
    """Return ``(profile, installation_id)`` for this gateway; *claims* must target it."""
    from gateway import hosted_rooms
    profile = _api_request_profile.get() or "default"
    installation_id = hosted_rooms.local_authority_gateway_id()
    if claims is not None and (claims["target_profile"], claims["target_install_id"]) != (profile, installation_id):
        raise ValueError("room grant target does not match this profile")
    return profile, installation_id


def _local_room_catalog(self, profile: str, installation_id: str) -> tuple[dict, dict]:
    """Return ``(execution_policy, catalog)`` for this gateway's *profile*."""
    from gateway.hosted_room_peer import PROTOCOL_VERSION, catalog_mapping
    from gateway.hosted_room_execution_policy import execution_policy_mapping
    with self._profile_scope(profile):
        execution_policy = execution_policy_mapping(target_profile=profile)
    catalog = catalog_mapping(
        installation_id=installation_id, protocol_versions=(PROTOCOL_VERSION,), link_modes=("direct",),
        persistent_process=True, text=True, attachments=False, target_profile=profile,
        execution_policy=execution_policy)
    return execution_policy, catalog


def _http_routes(self) -> list[tuple[str, str, Any]]:
    from gateway.platforms.api_server_room_proof import wrap, cleanup_issuance
    from gateway.platforms import api_server_replica_retirement, api_server_room_replicas, api_server_room_work_records
    from gateway.hosted_room_work_records import MAX_BYTES
    limits = {'/v1/room-members/replica': api_server_room_replicas.MAX_REPLICA_HTTP_BYTES,
              api_server_room_replicas.custody_path(): api_server_room_replicas.MAX_CUSTODY_PAGES_REQUEST_BYTES,
              '/v1/room-members/work-records': MAX_BYTES + 1024,
              '/v1/group-replicas/retire': api_server_replica_retirement.MAX_RETIREMENT_REQUEST_BYTES}
    return [(method, path, wrap(self, handler, max_bytes=limits.get(path))) for method, path, handler in [
        ("POST", "/v1/room-members/invitations", self._handle_room_member_invitation),
        ("GET", "/v1/room-members/capabilities", self._handle_room_member_capabilities),
        ("POST", "/v1/room-members/grants/refresh", self._handle_room_member_grant_refresh),
        ("POST", "/v1/room-members/grants/revoke", self._handle_room_member_grant_revoke),
        ("POST", "/v1/room-members/grants/revoke-exact", self._handle_room_member_grant_revoke_exact),
        ("POST", "/v1/room-members/grants/cleanup-issuance", lambda request: cleanup_issuance(self, request)),
        *api_server_room_replicas.http_routes(self), *api_server_room_work_records.http_routes(self),
        *api_server_replica_retirement.http_routes(self)]] + _succession_routes(self)


def _succession_routes(self):
    from gateway.platforms import api_server_room_succession
    return api_server_room_succession.http_routes(self)


def _room_grant_token(request: "web.Request") -> str:
    if hasattr(request, "get") and request.get("verified_room_grant"):
        return request["verified_room_grant"]
    scheme, separator, token = str(request.headers.get("Authorization") or "").partition(" ")
    return token.strip() if separator and scheme.lower() == "hermesroom" else ""


def _room_grant_secret(self) -> bytes:
    from gateway.hosted_room_peer import gateway_room_grant_secret
    return gateway_room_grant_secret()


def _decode_request_grant(self, request: "web.Request", *, permission: str) -> dict[str, Any]:
    """Signature/scope/horizon check only (no revocation lookup)."""
    from gateway.hosted_room_peer import decode_room_grant
    token = self._room_grant_token(request)
    if not token:
        raise ValueError("room grant is missing")
    return decode_room_grant(self._room_grant_secret(), token, permission=permission)


def _grant_db(adapter):
    """Canonical grants share the accepting session writer; legacy uses its room store."""
    from gateway.session_authorities import active_authority
    from gateway import hosted_rooms
    authority = active_authority(getattr(adapter, 'gateway_runner', None))
    return authority.db.db_path if authority is not None else hosted_rooms.default_db_path()


def _room_grant_claims(self, request: "web.Request", *, permission: str, conn=None) -> dict[str, Any]:
    claims = _decode_request_grant(self, request, permission=permission)
    from gateway import hosted_rooms
    from gateway.hosted_room_peer import room_grant_token_digest
    db_path = _grant_db(self)
    if hosted_rooms.room_grant_is_revoked(
            db_path, claims=claims, token_sha256=room_grant_token_digest(self._room_grant_token(request)), conn=conn):
        raise RoomGrantReauthorizationRequired("room grant is revoked")
    if _retirement_only(claims):
        from gateway.platforms.api_server_run_authority import room_authority
        if not self._run_idempotency_store.permits_room_retirement(room_authority(claims)):
            raise RoomGrantReauthorizationRequired("room retirement authority is not retained")
    elif not hosted_rooms.peer_room_grant_is_current(db_path, claims=claims, conn=conn):
        raise RoomGrantReauthorizationRequired("room grant is no longer current")
    return claims


def authorize_room_admission(adapter, request):
    """Recheck a peer bearer in the canonical admission's accepting write transaction.

    The callback is private request state, never persisted with execution input. Its
    reads share the writer lock with participant revocation and scope replacement.
    """
    token = _room_grant_token(request)
    if not token:
        return None
    secret = adapter._room_grant_secret()
    from gateway import hosted_rooms
    from gateway.hosted_room_peer import decode_room_grant, room_grant_token_digest
    from hermes_state_runtime import RuntimeStoreError
    digest = room_grant_token_digest(token)
    def authorize(conn):
        try:
            claims = decode_room_grant(secret, token, permission="dispatch")
            if (hosted_rooms.room_grant_is_revoked(None, claims=claims, token_sha256=digest, conn=conn)
                    or not hosted_rooms.peer_room_grant_is_current(None, claims=claims, conn=conn)):
                raise ValueError("room grant was revoked or replaced")
        except ValueError as exc:
            raise RuntimeStoreError("room_reauthorization_required") from exc
    return authorize


async def _handle_room_member_invitation(
    self, request: "web.Request", *, _openai_error, _api_request_profile) -> "web.Response":
    """Mint a short-lived room/profile grant for a trusted home gateway."""
    auth_err = self._check_auth(request)
    if auth_err:
        return auth_err
    body, error = await self._read_json_body(request)
    if error:
        return error
    required = set(_ROOM_IDENTITY_FIELDS)
    allowed = required | {"grant_id", "ttl_seconds", "status_ttl_seconds", "replication", "work_records",
                          "passive_only", "successor", "continuation", "previous_authority", "retirement_only"}
    if set(body) - allowed or not required <= set(body):
        return _json_error(
            _openai_error, "Invitation is missing required room authority fields.",
            code="invalid_room_invitation", status=400)
    try:
        profile, _ = _local_target(None, _api_request_profile)
        invitation = _issue_invitation(self, body, profile)
    except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as exc:
        return _json_error(_openai_error, str(exc), code="invalid_room_invitation", status=400)
    return web.json_response({"object": "hermes.room_member.invitation", **invitation}, status=201)


def _issue_invitation(self, body: dict[str, Any], profile: str, *, conn=None, commit_receipt=None, _verified_origin=None) -> dict[str, Any]:
    """Mint and reserve one room grant for *profile*: the API-key route and ``groups.peer.invite``."""
    from gateway import hosted_rooms
    from gateway.hosted_room_peer import decode_room_grant, invitation_permissions, issue_room_grant
    from gateway.hosted_room_passive_protocol import passive_capabilities
    if (type(body.get("retirement_only", False)) is not bool
            or (body.get("retirement_only") and "previous_authority" in body)):
        raise ValueError("retirement_only must be a boolean without previous_authority")
    permissions = invitation_permissions(
        body.get("replication", True), body.get("work_records", False),
        passive_only=body.get("passive_only", False), successor=body.get("successor", False))
    if body.get("retirement_only"):
        permissions = ("status", "retire")
    continuation = body.get("continuation", True)
    if type(continuation) is not bool:
        raise ValueError("continuation must be a boolean")
    target_install_id = hosted_rooms.local_authority_gateway_id()
    ttl = float(body.get("ttl_seconds", 3600))
    if not 60 <= ttl <= 24 * 60 * 60:
        raise ValueError("ttl_seconds must be between 60 and 86400")
    status_ttl = float(body.get("status_ttl_seconds", ttl))
    if not ttl <= status_ttl <= 30 * 24 * 60 * 60:
        raise ValueError("status_ttl_seconds must be at least ttl_seconds and no more than 2592000")
    execution_policy, catalog = _local_room_catalog(self, profile, target_install_id)
    token = issue_room_grant(
        self._room_grant_secret(),
        grant_id=str(body.get("grant_id") or f"grant-{uuid.uuid4().hex}"),
        **_room_identity(body, coerce=True),
        target_install_id=target_install_id, target_profile=profile, permissions=permissions,
        execution_policy_digest=execution_policy["policy_digest"], issued_at=time.time(),
        ttl_seconds=ttl, status_ttl_seconds=status_ttl)
    claims = decode_room_grant(self._room_grant_secret(), token, permission="status")
    invitation = {"grant": token, "target_profile": profile, "catalog": catalog,
                  "expires_at": float(claims["expires_at"]), "status_expires_at": float(claims["status_expires_at"]),
                  "passive_replication": passive_capabilities()}
    receipt_writer = (lambda writer: commit_receipt(invitation)) if commit_receipt is not None else None
    write_extra = _invitation_consent_writer(self, claims, body, continuation, ttl, status_ttl, _verified_origin)
    _record_invitation(self, claims, body, _verified_origin, conn=conn,
                       write_extra=write_extra, commit_receipt=receipt_writer)
    return invitation




async def _handle_room_member_capabilities(
    self, request: "web.Request", *, _openai_error, _api_request_profile) -> "web.Response":
    """Verify a scoped grant and return this target's live room catalog."""
    try:
        claims = self._room_grant_claims(request, permission="status")
        profile, installation_id = _local_target(claims, _api_request_profile)
        _, catalog = _local_room_catalog(self, profile, installation_id)
    except Exception as exc:
        return _room_grant_error_response(exc, _openai_error=_openai_error)
    if _retirement_only(claims):
        return web.json_response({"object": "hermes.room_member.capabilities",
            **{key: claims[key] for key in _ROOM_IDENTITY_FIELDS}, "target_profile": profile,
            "catalog": catalog, "retirement_only": True})
    from gateway.hosted_room_passive_protocol import passive_capabilities
    enrollment = None
    if "replicate" in claims.get("permissions", ()):
        # The home confirms its copy-retirement setup from this, before it copies under it.
        from gateway import hosted_rooms
        from gateway.hosted_room_replica_retirement import current_target_enrollment
        enrollment = current_target_enrollment(
            _grant_db(self), room_id=claims["room_id"],
            authority_gateway_id=claims["authority_gateway_id"], authority_epoch=claims["authority_epoch"])
    from gateway.hosted_room_custody import local_always_on, local_consent, local_names
    from gateway.hosted_room_identity import local_public_key
    # The home pins this key at custody enrollment; the reply is authenticated by the pinned grant.
    name, operator_name = local_names()
    room_identity = {"install_id": installation_id, "public_key": local_public_key(), "name": name,
                     "operator_name": operator_name, "allowed": local_consent(_grant_db(self), claims["room_id"]),
                     "always_on": local_always_on()}
    from gateway.hosted_room_documents import advertised_capability
    documents = advertised_capability(self) if request.headers.get("Hermes-Room-Features") == "document-input-v1" else None
    from gateway.session_peer_output import output_available
    output = output_available(self) if (request.headers.get("Hermes-Room-Features") == "document-output-v1"
                                      and {"status", "stop", "dispatch"} <= set(claims["permissions"])) else None
    return web.json_response({
        **({"document_output": output} if output is not None else {}),
        **({"document_inputs": documents} if documents is not None else {}),
        "object": "hermes.room_member.capabilities", **{k: claims[k] for k in _ROOM_IDENTITY_FIELDS},
        "target_profile": profile, "catalog": catalog, "passive_replication": passive_capabilities(),
        "room_identity": room_identity, "permissions": list(claims.get("permissions", ())),
        **({"retirement_enrollment": enrollment} if enrollment is not None else {})})


def _consented_permissions(self, claims: dict[str, Any]) -> list[str]:
    """A renewal carries the operator's current consent to continue the group, and nothing more."""
    from gateway.hosted_room_custody import local_consent
    permissions = [permission for permission in claims["permissions"] if permission != "successor"]
    if "replicate" in permissions and local_consent(_grant_db(self), claims["room_id"]):
        permissions.append("successor")
    return permissions


async def _handle_room_member_grant_refresh(
    self, request: "web.Request", *, _openai_error, _api_request_profile) -> "web.Response":
    """Refresh dispatch access without a Desktop or broad gateway key."""
    body, error = await self._read_json_body(request)
    if error:
        return error
    if set(body) - {"ttl_seconds"}:
        return _json_error(
            _openai_error, "Grant refresh accepts only ttl_seconds.",
            code="invalid_room_grant_refresh", status=400)
    try:
        from gateway.hosted_room_peer import MAX_DISPATCH_GRANT_TTL_SECONDS, issue_room_grant
        from gateway.hosted_room_execution_policy import execution_policy_mapping
        # A status-only bearer must never mint dispatch authority: renewal needs live "dispatch".
        claims = self._room_grant_claims(request, permission="dispatch")
        profile, installation_id = _local_target(claims, _api_request_profile)
        from gateway.platforms.api_server_room_succession import fence_check
        fence_check(self)(claims["room_id"], int(claims["authority_epoch"]))
        now = request.get("room_proof_issued_at", time.time())
        hard_expiry = _hard_expiry(claims)
        remaining = hard_expiry - now
        requested = float(body.get("ttl_seconds", MAX_DISPATCH_GRANT_TTL_SECONDS))
        if remaining <= 0 or requested <= 0:
            raise ValueError("room grant renewal horizon expired")
        dispatch_ttl = min(requested, MAX_DISPATCH_GRANT_TTL_SECONDS, remaining)
        with self._profile_scope(profile):
            execution_policy = execution_policy_mapping(target_profile=profile)
        _require_unchanged_execution_policy(claims, execution_policy)
        token = issue_room_grant(
            self._room_grant_secret(), grant_id="grant-refresh-" + request.get("room_proof_request_id", uuid.uuid4().hex),
            **_room_identity(claims), target_install_id=installation_id, target_profile=profile,
            execution_policy_digest=execution_policy["policy_digest"],
            permissions=_consented_permissions(self, claims), issued_at=now, ttl_seconds=dispatch_ttl,
            status_expires_at=hard_expiry)
        # A revocation that landed after the first check must not let this renewal out; one that
        # lands after this check covers it, since it was issued before.
        self._room_grant_claims(request, permission="dispatch")
    except Exception as exc:
        return _room_grant_error_response(exc, _openai_error=_openai_error)
    return web.json_response({
        "object": "hermes.room_member.grant", "grant": token, "expires_at": now + dispatch_ttl,
        "status_expires_at": hard_expiry, "execution_policy": execution_policy})


async def _handle_room_member_grant_revoke(
    self, request: "web.Request", *, _openai_error, _api_request_profile) -> "web.Response":
    """Revoke exactly the scoped grant authenticating this request."""
    body, error = await self._read_json_body(request)
    if error:
        return error
    if set(body) - {"retire_authority"} or ("retire_authority" in body and type(body["retire_authority"]) is not bool):
        return _json_error(
            _openai_error, "Grant revoke accepts only the boolean retire_authority option.",
            code="invalid_room_grant_revoke", status=400)
    try:
        from gateway import hosted_rooms
        # Idempotent: a response-lost retry authenticates with the grant just denylisted, so
        # verify signature/scope/horizon directly (not _room_grant_claims) and upsert the id.
        claims = _decode_request_grant(self, request, permission="status")
        _local_target(claims, _api_request_profile)
        if body.get("retire_authority"):
            if "retire" not in claims["permissions"]:
                return _json_error(_openai_error, "This grant does not authorize room retirement.",
                                   code="room_retirement_not_granted", status=403)
            from gateway.platforms.api_server_run_authority import room_authority, room_run_scope
            authority = room_authority(claims)
            if not self._run_idempotency_store.room_authority_retired(authority):
                self._room_grant_claims(request, permission="retire")
            self._run_idempotency_store.retire_room_authority(room_run_scope(claims), authority)
        hosted_rooms.revoke_room_grant_scope(
            _grant_db(self), claims=claims, expires_at=_hard_expiry(claims))
    except Exception:
        return _room_grant_error_response(_openai_error=_openai_error)
    return web.json_response({"object": "hermes.room_member.grant.revocation", "revoked": True,
                              "authority_retired": bool(body.get("retire_authority"))})


async def _handle_room_member_grant_revoke_exact(
    self, request: "web.Request", *, _openai_error, _api_request_profile) -> "web.Response":
    """Revoke exactly the grant authenticating this request: the rest of its scope stays usable.

    The home retires a grant it has replaced. Idempotent: a retry after a lost response, or a
    grant already past its lifetime, still gets the acknowledgement.
    """
    body, error = await self._read_json_body(request)
    if error:
        return error
    if body:
        return _json_error(
            _openai_error, "Grant revoke accepts no fields.", code="invalid_room_grant_revoke", status=400)
    try:
        from gateway import hosted_rooms
        from gateway.hosted_room_peer import decode_room_grant, room_grant_token_digest
        token = self._room_grant_token(request)
        if not token:
            raise ValueError("room grant is missing")
        claims = decode_room_grant(
            self._room_grant_secret(), token, permission="status", allow_expired_for_revocation=True)
        _local_target(claims, _api_request_profile)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _room_grant_error_response(exc, _openai_error=_openai_error)
    try:
        hosted_rooms.revoke_room_grant_token(
            _grant_db(self), claims=claims, token_sha256=room_grant_token_digest(token),
            expires_at=_hard_expiry(claims))
    except (OSError, sqlite3.Error, ValueError):
        return _json_error(
            _openai_error, "Room grant revocation could not be saved; retry it.",
            code="room_grant_revocation_unavailable", status=503)
    return web.json_response({"object": "hermes.room_member.grant.revocation", "revoked": True})


def _retirement_only(claims):
    return set(claims["permissions"]) == {"status", "retire"}


def _previous_authority(claims, body):
    """An owner explicitly names the current predecessor; room and target stay fixed."""
    from gateway.platforms.api_server_run_authority import room_authority
    previous = body.get("previous_authority")
    if previous is None:
        return None
    fields = {"home_install_id", "authority_gateway_id", "authority_epoch"}
    if not isinstance(previous, dict) or set(previous) != fields:
        raise ValueError("previous_authority requires exact home, gateway and epoch")
    if (type(previous["authority_epoch"]) is not int or previous["authority_epoch"] < 1
            or any(not isinstance(previous[key], str) or not previous[key] for key in fields - {"authority_epoch"})):
        raise ValueError("previous_authority has invalid coordinates")
    return room_authority({**claims, **previous})


def _record_invitation(self, claims, body, verified_origin=None, *, db_path=None, conn=None, write_extra=None, commit_receipt=None):
    from gateway import hosted_rooms
    from gateway.platforms.api_server_run_authority import room_authority
    if conn is not None and commit_receipt is None:
        raise ValueError("a borrowed invitation writer requires its durable receipt callback")
    if _retirement_only(claims):
        if not self._run_idempotency_store.permits_room_retirement(room_authority(claims)):
            raise RoomGrantReauthorizationRequired("room retirement authority is not retained")
        if commit_receipt is not None:
            commit_receipt(conn)
            conn.commit()
        return
    previous = _previous_authority(claims, body)
    previous_home = body["previous_authority"]["home_install_id"] if previous is not None else None
    db_path = db_path or _grant_db(self)
    # The reservation and optional native receipt commit together before the
    # separate RunStore publication. The store keeps admission writers fenced.
    with (nullcontext(conn) if conn is not None else hosted_rooms._transaction(db_path, immediate=True)) as writer:
        def commit_reservation(known):
            if previous is None and not known and writer.execute(
                    "SELECT 1 FROM hosted_room_peer_reservations WHERE room_id=? AND target_profile=?",
                    (claims["room_id"], claims["target_profile"])).fetchone() is not None:
                raise RoomGrantReauthorizationRequired("room origin requires an explicit predecessor")
            hosted_rooms.reserve_peer_room(db_path, claims=claims, expires_at=_hard_expiry(claims), conn=writer)
            if write_extra is not None:
                write_extra(writer)
            if commit_receipt is not None:
                commit_receipt(writer)
            writer.commit()
        if verified_origin is not None:
            target = self._run_idempotency_store.observe_verified_room_authority(
                claims, body.get("previous_authority"), verified_origin)
            replaced = target[2:4] if target is not None else previous[1:3] if previous is not None else None
            authority = room_authority(claims)
            if replaced is not None and replaced[0] == authority[1] and replaced[1] != authority[2]:
                writer.execute("""DELETE FROM hosted_room_peer_reservations WHERE room_id=?
                    AND target_profile=? AND authority_epoch=? AND authority_gateway_id=?""",
                    (claims["room_id"], claims["target_profile"], replaced[0], replaced[1]))
            # Consensus already established this authority; grant/consent failure
            # must not undo its independent, durable learned fence.
            commit_reservation(True)
        else:
            self._run_idempotency_store.commit_room_invitation(claims, previous, previous_home, commit_reservation)


def _publish_frozen_invitation(self, body, invitation, conn):
    """Finish only an explicitly pending, still-authorized committed native issuance.

    A replay never mints a token, replaces a reservation, or revives revoked grants.
    Storage failures propagate; a superseded receipt remains available for cleanup.
    """
    from gateway import hosted_rooms
    from gateway.hosted_room_peer import decode_room_grant, room_grant_token_digest
    try:
        claims = decode_room_grant(self._room_grant_secret(), invitation['grant'], permission='status')
        digest = room_grant_token_digest(invitation['grant'])
        if hosted_rooms.room_grant_is_revoked(None, claims=claims, token_sha256=digest, conn=conn):
            return False
        if _retirement_only(claims):
            return True
        if not hosted_rooms.peer_room_grant_is_current(None, claims=claims, conn=conn):
            return False
        previous = _previous_authority(claims, body)
        previous_home = body['previous_authority']['home_install_id'] if previous is not None else None
        self._run_idempotency_store.commit_room_invitation(claims, previous, previous_home, lambda known: conn.commit())
    except ValueError:
        return False
    return True


def _invitation_consent_writer(self, claims, body, continuation, ttl, status_ttl, verified_origin):
    """Capture immutable lineage before the nonreentrant RunStore callback takes its lock."""
    if _retirement_only(claims):
        return None
    store = self._run_idempotency_store
    if verified_origin is not None:
        origin = verified_origin
    elif store.knows_room_target(claims):
        origin = store.room_lineage_origin(claims)
    else:
        predecessor = {**claims, **body.get("previous_authority", {})}
        origin = store.room_origin_home(predecessor)
    permissions = claims["permissions"]

    def write(conn):
        from gateway.hosted_room_custody import set_local_consent
        from gateway.hosted_room_succession import record_consent_locked, withdraw_consent_locked
        if "replicate" in permissions:
            set_local_consent(_grant_db(self), room_id=claims["room_id"], allowed="successor" in permissions, conn=conn)
        if continuation:
            record_consent_locked(conn, room_id=claims["room_id"], member_id=claims["member_id"],
                target_profile=claims["target_profile"], options={
                    "authority": {key: claims[key] for key in ("home_install_id", "authority_gateway_id", "authority_epoch")},
                    "origin_install_id": origin, "replication": body.get("replication", True),
                    "work_records": body.get("work_records", False), "passive_only": body.get("passive_only", False),
                    "successor": body.get("successor", False), "ttl_seconds": ttl, "status_ttl_seconds": status_ttl})
        else:
            withdraw_consent_locked(conn, room_id=claims["room_id"], member_id=claims["member_id"],
                                    target_profile=claims["target_profile"])
    return write
