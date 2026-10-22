"""Webapp-only routes: the host terminal (loopback or authenticated binds), the
gateway ticket each browser socket dial mints (``/api/webapp/gateway-ticket``, which
ensures the profile's gateway), and the one-use handoffs (child window, launch page and
its typed code) for the private, loopback Webapp session."""
from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

from hermes_cli.dashboard_auth.request_utils import _http_origin
from hermes_cli.web_server_surface import private_launch

_log = logging.getLogger("hermes_cli.web_server")
router = APIRouter()
WINDOW_TICKET_TTL_SECONDS = 30
#: How long the launch code ``hermes webapp`` prints stays redeemable, and how many wrong entries burn it.
LAUNCH_CODE_TTL_SECONDS = 120
LAUNCH_CODE_ATTEMPTS = 5
#: 32 symbols without look-alikes (0/O, 1/I): ten of them are 50 bits.
_LAUNCH_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_MAX_WINDOW_TICKETS = 128
_ticket_lock = threading.Lock()


def _require_private_webapp(request: Request) -> None:
    if not private_launch(request.app.state):
        raise HTTPException(404, "Private Webapp window handoff is unavailable")


@router.websocket("/api/host-terminal")
async def host_terminal_ws(ws: WebSocket) -> None:
    from hermes_cli import web_host_terminal
    from hermes_cli.web_host_terminal_sessions import host_terminal
    from hermes_cli.web_server_chat import _PTY_BRIDGE_AVAILABLE, PtyUnavailableError, _pty_fail, _ws_gate

    gate = await _ws_gate(ws, "host-terminal")
    if gate is None:
        return
    peer, mode, cred = gate
    if not web_host_terminal.request_allowed():
        await ws.close(code=4403, reason="host terminal requires authenticated Webapp")
        return
    await ws.accept()
    _log.info("host terminal accepted peer=%s mode=%s cred=%s", peer, mode, cred)
    if not _PTY_BRIDGE_AVAILABLE:
        await _pty_fail(
            ws, PtyUnavailableError("Pseudo-terminal support is not installed on this host."), surface="Terminal")
        return
    await host_terminal(ws)


def _mint_window_ticket(state) -> str:
    """One-use ticket that ``/webapp/window-session`` exchanges for this start's session token."""
    from hermes_cli.web_server import _SESSION_TOKEN

    now = time.monotonic()
    with _ticket_lock:
        tickets = getattr(state, "webapp_window_tickets", {})
        tickets = {key: value for key, value in tickets.items()
                   if value[0] > now and value[1] == _SESSION_TOKEN}
        state.webapp_window_tickets = tickets
        if len(tickets) >= _MAX_WINDOW_TICKETS:
            raise HTTPException(429, "Too many pending Webapp windows; try again shortly")
        ticket = secrets.token_urlsafe(32)
        tickets[ticket] = (now + WINDOW_TICKET_TTL_SECONDS, _SESSION_TOKEN)
    return ticket


def mint_launch_code(state) -> str:
    """The one-use code ``hermes webapp`` prints beside its launch page, formatted ``XXXXX-XXXXX``.

    The operator types it into that page, so it never enters a URL: a URL reaches a browser in
    argv (world-readable on Linux) whether the server opens it or a terminal click does, and a
    peer-owner check cannot see past a same-user port forwarder. Minting replaces any earlier code."""
    from hermes_cli.web_server import _SESSION_TOKEN

    code = "".join(secrets.choice(_LAUNCH_CODE_ALPHABET) for _ in range(10))
    with _ticket_lock:
        state.webapp_launch_code = {"code": code, "expires": time.monotonic() + LAUNCH_CODE_TTL_SECONDS,
                                    "token": _SESSION_TOKEN, "misses": 0}
    return f"{code[:5]}-{code[5:]}"


def _spend_launch_code(state, typed: str) -> bool:
    """Whether *typed* is this start's live launch code, spending it. Each wrong entry counts, and
    :data:`LAUNCH_CODE_ATTEMPTS` of them burn the code: guessing it is out of reach, burning it only
    sends the operator back to ``hermes webapp`` for a fresh one."""
    from hermes_cli.web_server import _SESSION_TOKEN

    typed = re.sub(r"[^A-Z0-9]", "", typed.upper())
    with _ticket_lock:
        pending = getattr(state, "webapp_launch_code", None)
        if not pending:
            return False
        if pending["expires"] <= time.monotonic() or pending["token"] != _SESSION_TOKEN:
            state.webapp_launch_code = None
            return False
        if not hmac.compare_digest(typed.encode(), pending["code"].encode()):
            pending["misses"] += 1
            if pending["misses"] >= LAUNCH_CODE_ATTEMPTS:
                state.webapp_launch_code = None
            return False
        state.webapp_launch_code = None
        return True


@router.post("/api/webapp/window-ticket")
async def issue_window_ticket(request: Request):
    from hermes_cli.web_server import _require_token

    _require_private_webapp(request)
    _require_token(request)
    ticket = _mint_window_ticket(request.app.state)
    return JSONResponse({"ticket": ticket}, headers={"Cache-Control": "no-store"})


@router.post("/api/webapp/launch-code")
async def issue_launch_code(request: Request):
    """A fresh one-use launch code, for ``hermes webapp`` run again by the same OS user: it reads
    this server's owner-only token file, asks here, and shows the code on its own terminal."""
    from hermes_cli.web_server import _require_token

    _require_private_webapp(request)
    _require_token(request)
    return JSONResponse({"code": mint_launch_code(request.app.state), "ttl_seconds": LAUNCH_CODE_TTL_SECONDS},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/webapp/gateway-ticket")
async def issue_gateway_ticket(request: Request, profile: str | None = None):
    """The browser's one-use ticket for its live socket to *profile*'s gateway.

    Minted only once that owner is reachable (started if absent), so a lasting cause reaches the
    browser as this 503's verdict rather than as a socket that keeps closing; the ticket pins the
    home so ``/api/ws`` dials exactly that owner, for this caller's login."""
    from hermes_cli.dashboard_auth.ws_tickets import TTL_SECONDS, mint_ticket
    from hermes_cli.web_server_cron import _cron_profile_home
    from hermes_cli.web_server_surface import policy
    from hermes_cli.webapp_gateway_owner import GatewayAttachError, owner_endpoint
    from hermes_cli.webapp_gateway_relay import principal_identity

    if not policy(request.app.state).gateway_attach:
        raise HTTPException(404, "Gateway attach is unavailable on this surface")
    identity = principal_identity(request)
    home = _cron_profile_home(profile)[1]
    no_store = {"Cache-Control": "no-store"}
    try:
        await owner_endpoint(home)
    except GatewayAttachError as exc:
        return JSONResponse(exc.verdict(), status_code=503, headers=no_store)
    ticket = mint_ticket(user_id=identity["user_id"], provider=identity["provider"], issuer=identity["issuer"],
                         extra={"gateway_home": str(home)})
    return JSONResponse({"ticket": ticket, "ttl_seconds": TTL_SECONDS}, headers=no_store)


@router.post("/webapp/window-session")
async def redeem_window_ticket(request: Request):
    from hermes_cli.web_server import _SESSION_TOKEN

    _require_private_webapp(request)
    # This endpoint deliberately sits outside /api: the child window or launch
    # tab has no session yet. Only a purpose-specific credential can exchange for
    # the existing one: a child window's ticket, or the launch code the operator typed.
    # Private links use the actual bind origin, which may differ from a
    # configured loopback public_url. Host validation remains in middleware;
    # proxy-normalized ASGI scheme/Host (not raw forwarded headers) are trusted.
    origins = request.headers.getlist("origin")
    origin = _http_origin(origins[0]) if len(origins) == 1 else None
    target_origin = _http_origin(f"{request.url.scheme}://{request.url.netloc}")
    if origin is None or origin != target_origin:
        raise HTTPException(403, "Webapp window handoff requires a same-origin request")
    # Keep the unauthenticated exchange body-free; never parse an arbitrary
    # JSON/multipart payload just to extract a fixed-size capability.
    code = request.headers.get("X-Hermes-Launch-Code")
    if code is not None:
        if not _spend_launch_code(request.app.state, code):
            raise HTTPException(403, "Launch code wrong, used or expired; run hermes webapp again for a new one")
        return JSONResponse({"token": _SESSION_TOKEN}, headers={"Cache-Control": "no-store"})
    ticket = request.headers.get("X-Hermes-Window-Ticket", "")
    with _ticket_lock:
        entry = getattr(request.app.state, "webapp_window_tickets", {}).pop(ticket, None)
    if entry is None or entry[0] <= time.monotonic() or entry[1] != _SESSION_TOKEN:
        raise HTTPException(403, "Webapp window link expired or was already used; open a new window")
    return JSONResponse({"token": _SESSION_TOKEN}, headers={"Cache-Control": "no-store"})


_LAUNCH_SCRIPT = r"""
const basePath = __BASE_PATH__;
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', location.pathname);
const status = document.getElementById('status');
const form = document.getElementById('launch');
const input = document.getElementById('code');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const code = input.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
  input.value = '';
  try {
    const response = await fetch(`${basePath}/webapp/window-session`, {
      method: 'POST', credentials: 'same-origin', headers: {'X-Hermes-Launch-Code': code}
    });
    if (!response.ok) throw new Error();
    const {token} = await response.json();
    if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error();
    const target = new URL(`${basePath}/`, location.origin);
    target.search = params.get('query') || '';
    const key = `hermes.webapp.session.v1:${JSON.stringify([location.origin, basePath])}`;
    try {
      // The tab's grant, kept as the child-window handoff keeps it: a URL carrying the token would
      // stay in browser (and synced) history for the server's lifetime.
      sessionStorage.setItem(key, token);
    } catch {
      // Storage is blocked: the token crosses this same-tab navigation in memory, never in a URL;
      // the SPA takes it from window.name once and clears it.
      window.name = `${key}=${token}`;
    }
    location.replace(target.href);
  } catch {
    status.textContent = 'That code is wrong, used or expired. Type it again; after a few wrong tries, '
      + 'or once it expires, run hermes webapp again for a new one.';
    input.focus();
  }
});
"""


def _handoff_page(request: Request, title: str, status: str, script: str, body: str = "") -> HTMLResponse:
    """Credential-free, uncached page whose only script is *script* (``__BASE_PATH__`` bound)."""
    from hermes_cli.web_server_dashboard import _normalise_prefix

    prefix = _normalise_prefix(request.headers.get("x-forwarded-prefix"))
    nonce = secrets.token_urlsafe(16)
    script = script.replace("__BASE_PATH__", json.dumps(prefix).replace("</", "<\\/"))
    return HTMLResponse(
        '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
        f'<title>{title}</title><p id="status">{status}</p>{body}'
        f'<script nonce="{nonce}">{script}</script>',
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        },
    )


_LAUNCH_FORM = (
    '<form id="launch"><input id="code" aria-label="Launch code" placeholder="XXXXX-XXXXX" '
    'autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" maxlength="32" '
    'required autofocus> <button>Open Hermes</button></form>'
)


@router.get("/webapp/launch")
async def launch_page(request: Request):
    """The page ``hermes webapp`` opens and prints, carrying no credential: the operator types the
    one-use code from that terminal, and the page enters the Webapp with the session in this tab's
    sessionStorage, never in a URL."""
    _require_private_webapp(request)
    return _handoff_page(request, "Open Hermes", "Enter the code hermes webapp printed in its terminal.",
                         _LAUNCH_SCRIPT, _LAUNCH_FORM)


# No credential appears in this public page or in its URL. The fragment
# names a transient channel; the authorized parent mints a one-use ticket.
_WINDOW_SCRIPT = r"""
const basePath = __BASE_PATH__;
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', location.pathname);
const id = params.get('channel') || '';
const status = document.getElementById('status');
if (!/^[a-f0-9-]{36}$/.test(id)) {
  status.textContent = 'Open this window from an authorized Hermes Webapp tab.';
} else {
  const channel = new BroadcastChannel(`hermes.webapp.window:${id}`);
  let receiving = false;
  let finished = false;
  const controller = new AbortController();
  const fail = message => {
    if (finished) return;
    finished = true;
    controller.abort();
    status.textContent = message;
    channel.postMessage({type: 'error'});
    channel.close();
    clearTimeout(timeout);
  };
  const timeout = setTimeout(() => fail('This window link expired. Open a new window from the original tab.'), 30000);
  channel.onmessage = async event => {
    if (receiving) return;
    if (event.data?.error) { fail(event.data.error); return; }
    const ticket = event.data?.ticket;
    if (typeof ticket !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(ticket)) return;
    receiving = true;
    try {
      const response = await fetch(`${basePath}/webapp/window-session`, {
        method: 'POST', credentials: 'same-origin',
        headers: {'X-Hermes-Window-Ticket': ticket}, signal: controller.signal
      });
      if (!response.ok) throw new Error('Window authorization expired. Open a new window from the original tab.');
      const {token} = await response.json();
      if (finished) return;
      if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(token)) throw new Error('Invalid window authorization.');
      sessionStorage.setItem(`hermes.webapp.session.v1:${JSON.stringify([location.origin, basePath])}`, token);
      const target = new URL(`${basePath}/`, location.origin);
      target.search = params.get('query') || '';
      target.hash = params.get('route') || '/';
      channel.postMessage({type: 'done'});
      finished = true;
      clearTimeout(timeout);
      channel.close();
      location.replace(target.href);
    } catch (error) { fail(error.message || 'Could not authorize this window. Open it again from the original tab.'); }
  };
  channel.postMessage({type: 'ready'});
}
"""


@router.get("/webapp/window")
async def child_window_page(request: Request):
    _require_private_webapp(request)
    return _handoff_page(request, "Opening Hermes", "Authorizing this Hermes window…", _WINDOW_SCRIPT)
