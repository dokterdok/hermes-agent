"""Passive-only UTF-8 sends using the parent's bounded RoomLink HTTP primitives.

No dispatch, execution or broad-key fallback. Parent lifecycle/registration is
separate; capability probes use its existing scoped client unchanged.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from gateway.hosted_room_peer import HostedRoomPeerError, validate_room_link_url
from tui_gateway import hosted_room_peer_http as peer_http


def _profile_url(base_url: str, path: str, profile: str) -> str:
    """Donor profile addressing: a pre-scoped route must agree, never duplicate."""
    base_url, _ = validate_room_link_url(base_url)
    if not isinstance(profile, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", profile) is None:
        raise HostedRoomPeerError("peer target profile is invalid")
    scoped = re.search(r"/p/([^/]+)$", urllib.parse.urlsplit(base_url).path)
    if scoped:
        if urllib.parse.unquote(scoped.group(1), errors="strict") != profile:
            raise HostedRoomPeerError("peer target profile does not match the scoped endpoint")
    elif profile != "default":
        base_url += f"/p/{urllib.parse.quote(profile, safe='')}"
    return base_url + path


class PassiveReplicationHTTPClient:
    def __init__(self, *, base_url, api_key="", target_profile=None, timeout_seconds=3.0):
        if api_key:
            raise ValueError("passive delivery does not use a broad API key")
        self._peer = peer_http.PeerRunsHTTPClient(base_url=base_url, api_key="",
            target_profile=target_profile, timeout_seconds=timeout_seconds)

    def probe(self, *, grant):
        return self._peer.probe(grant=grant)

    def replicate_page(self, *, grant, target_profile, room_id, room_name, members, page):
        return self._post("/v1/room-members/replica", grant=grant, profile=target_profile,
            body=dict(room_id=room_id, room_name=room_name, members=members, page=page))

    def replicate_work_records(self, *, grant, target_profile, record):
        return self._post("/v1/room-members/work-records", grant=grant, profile=target_profile,
                          body={"record": record})

    def _post(self, path, *, grant, profile, body):
        token = self._peer._require_room_grant(grant)
        url = _profile_url(self._peer.base_url + self._peer._profile_prefix, path, profile)
        timeout = self._peer.timeout_seconds
        deadline = time.monotonic() + timeout
        request = urllib.request.Request(url, method="POST",
            data=json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers={"Authorization": f"HermesRoom {token}", "Content-Type": "application/json",
                     "User-Agent": "Hermes-RoomLink/1.0"})
        try:
            with peer_http._open_roomlink_url(request, timeout=timeout, reject_redirects=True) as response:
                raw = peer_http._read_body(response, max_bytes=peer_http.MAX_PEER_RESPONSE_BYTES,
                                          deadline=deadline, kind="", ambiguous=True)
        except urllib.error.HTTPError as exc:
            try:
                if exc.code in {301, 302, 303, 307, 308}:
                    raise peer_http.PeerRunsHTTPError("passive copy refused an HTTP redirect", status_code=exc.code) from exc
                self._peer._raise_http_error(exc, method="POST", path=path, deadline=deadline)
            finally:
                exc.close()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise peer_http.PeerRunsHTTPError("passive copy endpoint is unavailable", retryable=True, ambiguous=True) from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise peer_http.PeerRunsHTTPError("passive copy returned non-JSON data", ambiguous=True) from exc
        if not isinstance(payload, dict):
            raise peer_http.PeerRunsHTTPError("passive copy returned a non-object response", ambiguous=True)
        return payload
