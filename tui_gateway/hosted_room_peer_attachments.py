"""Selected #98072 bounded uploads; the existing peer client owns routing."""
import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence

from gateway.hosted_room_peer import (
    HostedMemberDispatch, attachment_manifest_digest, canonical_attachment_manifest,
)
from tui_gateway.hosted_room_peer_http import (
    MAX_PEER_RESPONSE_BYTES, MAX_PEER_ERROR_RESPONSE_BYTES, PeerRunsHTTPError,
    _PeerResponseTooLarge, _PeerResponseDeadlineExceeded, _read_bounded_response,
    _open_roomlink_url, _response_error_code, _is_proven_pre_admission_failure,
)

logger = logging.getLogger(__name__)


def bound_attachment_payloads(store, room_id, member_id, attachments):
    """Resolve immutable task references through the member's committed ACL."""
    if not attachments:
        return []
    from gateway.hosted_room_driver import validate_bound_task_manifest
    manifest = validate_bound_task_manifest(attachments)
    if store is None:
        raise ValueError('Group Chat attachment storage is unavailable')
    result = []
    for item in manifest:
        saved = store.read(room_id=room_id, event_id=item['event_id'],
                           attachment_id=item['attachment_id'], recipient_member_id=member_id)
        if any(saved.attachment[key] != item[key] for key in ('kind', 'name', 'mime', 'size')):
            raise ValueError('Group Chat attachment identity changed')
        result.append({**{key: item[key] for key in ('attachment_id', 'kind', 'name', 'mime', 'size')},
                       'sha256': hashlib.sha256(saved.data).hexdigest(), 'data': saved.data})
    canonical_attachment_manifest([{key: value for key, value in item.items() if key != 'data'} for item in result])
    return result


def _put_attachment(
    self,
    path: str,
    *,
    data: bytes,
    grant: str,
) -> dict[str, Any]:
    streamed = len(data) > 10_000_000

    def chunks():
        view = memoryview(data)
        for offset in range(0, len(view), 64 * 1024):
            yield view[offset : offset + 64 * 1024].tobytes()

    request = urllib.request.Request(
        self.base_url + self._profile_prefix + path,
        data=chunks() if streamed else data,
        method="PUT",
        headers={
            "Authorization": f"HermesRoom {self._require_room_grant(grant)}",
            "Content-Type": "application/octet-stream",
            **({} if streamed else {"Content-Length": str(len(data))}),
            "User-Agent": "Hermes-RoomLink/1.0",
        },
    )
    deadline = time.monotonic() + self.timeout_seconds
    try:
        with _open_roomlink_url(
            request,
            timeout=self.timeout_seconds,
            reject_redirects=True,
        ) as response:
            raw = _read_bounded_response(
                response,
                max_bytes=MAX_PEER_RESPONSE_BYTES,
                deadline=deadline,
            ).decode("utf-8", "replace")
    except _PeerResponseTooLarge as exc:
        raise PeerRunsHTTPError(
            "peer attachment response exceeded the RoomLink size limit",
            ambiguous=True,
        ) from exc
    except _PeerResponseDeadlineExceeded as exc:
        raise PeerRunsHTTPError(
            "peer attachment response exceeded the RoomLink time budget",
            retryable=True,
            ambiguous=True,
        ) from exc
    except urllib.error.HTTPError as exc:
        try:
            detail = _read_bounded_response(
                exc,
                max_bytes=MAX_PEER_ERROR_RESPONSE_BYTES,
                deadline=deadline,
            ).decode("utf-8", "replace")[:500]
        except Exception:
            detail = ""
        error_code = _response_error_code(detail)
        logger.debug(
            "Peer RoomLink attachment upload returned HTTP %s (%s)",
            exc.code,
            error_code or "no-code",
        )
        if exc.code in {301, 302, 303, 307, 308}:
            raise PeerRunsHTTPError(
                "peer attachment upload refused an HTTP redirect",
                status_code=exc.code,
            ) from exc
        raise PeerRunsHTTPError(
            f"peer rejected attachment upload with HTTP {exc.code}",
            retryable=exc.code in {408, 425, 429} or exc.code >= 500,
            ambiguous=exc.code >= 500,
            status_code=exc.code,
            error_code=error_code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PeerRunsHTTPError(
            "peer attachment upload is unreachable",
            retryable=True,
            ambiguous=not _is_proven_pre_admission_failure(exc),
            not_admitted=_is_proven_pre_admission_failure(exc),
        ) from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise PeerRunsHTTPError(
            "peer returned non-JSON attachment data"
        ) from exc
    if not isinstance(payload, dict):
        raise PeerRunsHTTPError("peer returned a non-object attachment response")
    return payload


def discard_attachments(
    self,
    *,
    task_id: str,
    execution_generation: int,
    grant: str,
) -> Mapping[str, Any]:
    """Retire one exact terminal batch; repeated calls are harmless."""

    path = (
        "/v1/room-members/attachments/"
        f"{urllib.parse.quote(str(task_id), safe='')}/"
        f"{int(execution_generation)}"
    )
    return self._request(
        path,
        method="DELETE",
        room_grant=self._require_room_grant(grant),
        reject_redirects=True,
    )


def stage_attachments(
    self,
    *,
    dispatch: Mapping[str, Any],
    attachments: Sequence[Mapping[str, Any]],
    grant: str,
) -> Mapping[str, Any]:
    """Push one complete, digest-bound attachment set before admission."""
    checked = HostedMemberDispatch.from_mapping(dispatch)
    self._require_room_grant(grant)
    payloads: list[tuple[dict[str, Any], bytes]] = []
    manifest_input: list[dict[str, Any]] = []
    for raw in attachments:
        if not isinstance(raw, Mapping):
            raise PeerRunsHTTPError("attachment payload must be an object")
        unknown = set(raw) - {
            "attachment_id",
            "kind",
            "name",
            "size",
            "mime",
            "sha256",
            "data",
        }
        if unknown or "data" not in raw:
            raise PeerRunsHTTPError("attachment payload fields are invalid")
        data = raw["data"]
        if not isinstance(data, (bytes, bytearray)):
            raise PeerRunsHTTPError("attachment data must be bytes")
        metadata = {key: value for key, value in raw.items() if key != "data"}
        manifest_input.append(metadata)
        payloads.append((metadata, bytes(data)))
    try:
        manifest = canonical_attachment_manifest(manifest_input)
    except ValueError as exc:
        raise PeerRunsHTTPError(str(exc)) from exc
    digest = attachment_manifest_digest(manifest)
    if checked.attachment_manifest_digest != digest:
        raise PeerRunsHTTPError(
            "attachment manifest does not match the peer dispatch"
        )
    for metadata, data in payloads:
        if (
            len(data) != int(metadata["size"])
            or hashlib.sha256(data).hexdigest() != metadata["sha256"]
        ):
            raise PeerRunsHTTPError(
                "attachment bytes do not match their manifest"
            )
    registered = self._request(
        "/v1/room-members/attachments",
        method="POST",
        body={
            "hosted_room_dispatch": checked.as_mapping(),
            "attachments": manifest,
        },
        room_grant=grant,
        reject_redirects=True,
    )
    result: Mapping[str, Any] = registered
    for metadata, data in payloads:
        path = (
            "/v1/room-members/attachments/"
            f"{urllib.parse.quote(checked.task_id, safe='')}/"
            f"{checked.execution_generation}/"
            f"{urllib.parse.quote(str(metadata['attachment_id']), safe='')}"
        )
        try:
            result = _put_attachment(self, path, data=data, grant=grant)
        except PeerRunsHTTPError as exc:
            if not exc.ambiguous:
                raise
            result = _put_attachment(self, path, data=data, grant=grant)
    if not result.get("complete"):
        raise PeerRunsHTTPError("peer attachment batch is incomplete")
    return {
        "complete": True,
        "manifest_digest": digest,
        "count": len(manifest),
    }
