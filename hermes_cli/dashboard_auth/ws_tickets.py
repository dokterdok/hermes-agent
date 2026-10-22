"""WS-upgrade auth credentials for gated mode, and for the browser's sockets on any bind.

Browsers cannot set ``Authorization`` on a WebSocket upgrade, and gated mode has no token
injected into the SPA, so two credential shapes exist: (1) single-use browser tickets
(``mint_ticket`` / ``consume_ticket``) fetched via authenticated ``POST /api/auth/ws-ticket`` and
passed as ``?ticket=`` on the upgrade — 30 s TTL, a leak is uninteresting; (2) a process-lifetime
internal credential (``internal_ws_credential`` / ``consume_internal_credential``) for
*server-spawned* WS clients (the embedded-TUI PTY child on ``/api/ws`` + ``/api/pub``), which
reuse their attach URL on every reconnect, possibly >30 s after boot — minted once, never expires,
multi-use, never injected into any HTML/SPA (leaves the process only via the child's environment,
so browser XSS cannot read it; grants no more than a ticket). In-memory; ``time.time`` patchable.

A ticket minted for a ``route`` (``mint_socket_ticket``) opens only that socket path, for only the
profile it names, and only while the credential that minted it is still the live one
(``socket_ticket_refusal``): the private loopback Webapp's session token mints these from its
request header, so no browser socket needs that token in its URL.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import Any, Dict, Optional, Sequence, Tuple

#: Long enough for ``getWsTicket()`` -> open WS, short enough that a leaked ticket is uninteresting.
TTL_SECONDS = 30

_lock = threading.Lock()
_tickets: dict[str, tuple[int, dict[str, Any]]] = {}  # ticket -> (expires_at, info)
_internal_credential: Optional[str] = None  # lazily minted; guarded by ``_lock``

#: Identity recorded for internal-credential connections (audit logs distinguish them from tickets).
INTERNAL_USER_ID = "server-internal"
INTERNAL_PROVIDER = "server-internal"


class TicketInvalid(Exception):
    """Ticket missing, expired, or already consumed."""


def mint_ticket(*, user_id: str, provider: str, issuer: str = "",
                extra: Optional[dict[str, Any]] = None) -> str:
    """One-shot base64url ticket (32 random bytes) bound to this identity; ``consume_ticket``
    hands the ``info`` dict back to the WS handler. ``extra`` rides along for routes that need
    server-chosen context (the Bot Desktop bridge pins the RFB socket's profile home here so a
    client can never pick another profile's screen)."""
    ticket = secrets.token_urlsafe(32)
    info = {"user_id": user_id, "provider": provider, "minted_at": int(time.time()), **(extra or {})}
    if issuer:
        info["issuer"] = issuer
    with _lock:
        _tickets[ticket] = (int(time.time()) + TTL_SECONDS, info)
        _gc_expired_locked()
    return ticket


def consume_ticket(ticket: str) -> dict[str, Any]:
    """Validate and consume (single-use). Raises :class:`TicketInvalid` on missing/expired/used."""
    now = int(time.time())
    with _lock:
        entry = _tickets.pop(ticket, None)
        if entry is None:
            # Truncated so misuse never logs the secret in full.
            truncated = (ticket[:8] + "…") if ticket else "<empty>"
            raise TicketInvalid(f"unknown ticket: {truncated}")
        expires_at, info = entry
        if expires_at < now:
            raise TicketInvalid("expired")
        return info


#: A plugin's own socket namespace (``/api/plugins/<name>/<path>``), as the router mounts it: any
#: plugin name the loader accepts is one path segment.
_PLUGIN_SOCKET_ROUTE = re.compile(r"/api/plugins/[^/?#\\]+/[^?#\\]*")


def socket_route_allowed(route: str) -> bool:
    """Whether a browser ticket may name ``route``: the gateway socket, the host terminal, or a
    plugin's socket. Never ``/api/pty``, ``/api/console`` or a server-internal channel."""
    if route in ("/api/ws", "/api/host-terminal"):
        return True
    return bool(_PLUGIN_SOCKET_ROUTE.fullmatch(route)) and not {".", ".."} & set(route.split("/"))


def _credential_fingerprint(credential: str) -> str:
    return hashlib.sha256(credential.encode()).hexdigest()


def mint_socket_ticket(*, route: str, profile: str, user_id: str, provider: str, issuer: str = "",
                       credential: Optional[str] = None, session_token: Optional[str] = None) -> str:
    """A ticket for one dial of the socket at ``route``, for ``profile`` ("" = the launch profile).
    It dies with what minted it: ``credential`` is the loopback session token (kept as a
    fingerprint), ``session_token`` the gated session's access token, kept for the ticket's 30 s
    only so the upgrade can verify that session again."""
    extra: dict[str, Any] = {"route": route, "profile": profile}
    if credential is not None:
        extra["credential"] = _credential_fingerprint(credential)
    if session_token is not None:
        extra["session_token"] = session_token
    return mint_ticket(user_id=user_id, provider=provider, issuer=issuer, extra=extra)


def socket_ticket_refusal(info: dict[str, Any], *, route: str, profiles: Sequence[str],
                          live_credential: Optional[str]) -> Optional[str]:
    """Why a consumed ticket may not open the socket at ``route`` with these ``?profile=`` values,
    or None. A ticket without a route is the unbound kind (the gated dashboard SPA, Electron OAuth,
    the Webapp's gateway ticket) and is left to its caller. ``live_credential`` is the secret a
    credential-bound ticket must still match (None: no such secret is valid here)."""
    if "route" not in info:
        return None
    if info["route"] != route:
        return "ticket bound to another route"
    if len(profiles) > 1 or (profiles[0].strip() if profiles else "") != info.get("profile", ""):
        return "ticket bound to another profile"
    bound = info.get("credential")
    if bound is not None and not (live_credential and hmac.compare_digest(
            bound, _credential_fingerprint(live_credential))):
        return "ticket outlived the credential that minted it"
    return None


def _gc_expired_locked() -> None:
    """Drop expired tickets. Caller must hold ``_lock``."""
    now = int(time.time())
    for t in [t for t, (exp, _) in _tickets.items() if exp < now]:
        _tickets.pop(t, None)


def internal_ws_credential() -> str:
    """Process-lifetime internal WS credential, minted once. Never injected into the SPA or
    returned over REST — only passed to a spawned child via its environment."""
    global _internal_credential
    with _lock:
        if _internal_credential is None:
            _internal_credential = secrets.token_urlsafe(32)
        return _internal_credential


def consume_internal_credential(value: str) -> dict[str, Any]:
    """Validate an internal credential (NOT single-use); returns the fixed server-internal
    ``{user_id, provider}`` info dict, mirroring ``consume_ticket``. Constant-time compare; any
    value is rejected until a credential has been minted."""
    with _lock:
        expected = _internal_credential
    if not value or expected is None:
        raise TicketInvalid("no internal credential")
    if not secrets.compare_digest(value.encode(), expected.encode()):
        raise TicketInvalid("internal credential mismatch")
    return {"user_id": INTERNAL_USER_ID, "provider": INTERNAL_PROVIDER}


def _reset_for_tests() -> None:
    """Test-only: drop all tickets and the internal credential."""
    global _internal_credential
    with _lock:
        _tickets.clear()
        _internal_credential = None
