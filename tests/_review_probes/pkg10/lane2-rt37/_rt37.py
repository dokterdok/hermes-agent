"""Shared helpers for the RT-37 owner-forward review probes (claude reviewer).

Same seams as tests/tui_gateway/test_legacy_submit_forwards_to_owner.py: a real SessionDB, a real
started admission, the real legacy ``prompt.submit`` and a real ``SessionAuthority`` +
``AuthorityConnection`` as the owner. Only discovery and the socket dial are replaced."""
import asyncio
import contextlib
from types import SimpleNamespace

from hermes_state_runtime import admit_session_input, begin_runtime_epoch, claim_session_input
from tui_gateway import server

try:
    from tui_gateway import owner_forward
except ImportError:  # main: no forwarder
    owner_forward = None


class OwnerClient:
    """``GatewayClient``'s surface (``rpc`` + ``events``) over a real ``AuthorityConnection``."""

    def __init__(self, authority, *, before_rpc=None):
        from gateway.session_controls import AuthorityConnection
        self.loop, self.events = asyncio.get_running_loop(), asyncio.Queue()
        self.conn = AuthorityConnection(authority, self, {"user_id": "desktop"})
        self.before_rpc = before_rpc
        self.calls = []

    def write(self, frame):
        self.loop.call_soon_threadsafe(self.events.put_nowait, frame)
        return True

    def close(self):
        return None

    async def rpc(self, method, **params):
        self.calls.append((method, dict(params)))
        if self.before_rpc is not None:
            await self.before_rpc(method, params)
        reply = await self.conn.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in reply:
            from hermes_cli.gateway_client import rpc_error
            raise rpc_error(reply["error"])
        return reply["result"]


def legacy(monkeypatch, db):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    for name in ("_schedule_agent_build", "_schedule_session_cap_enforcement", "_register_session_cwd",
                 "_start_agent_build"):
        monkeypatch.setattr(server, name, lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_restart_completed_failed_agent_build", lambda *_a: False)
    turns, emitted = [], []
    monkeypatch.setattr(server, "_run_after_agent_ready", lambda *a: turns.append(a[1]))
    monkeypatch.setattr(server, "_emit", lambda event, sid, payload=None: emitted.append((event, sid, payload)) or True)
    created = server.handle_request({"id": "c", "method": "session.create", "params": {"source": "desktop"}})
    return created["result"]["session_id"], created["result"]["stored_session_id"], turns, emitted


def owner(monkeypatch, db, key, epoch, *, before_rpc=None, clients=None):
    from gateway.session_authority import LiveSession, SessionAuthority
    authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id="owned", instance_id="gateway",
                                 db=db, epoch=epoch)
    authority.sessions[key] = LiveSession(SimpleNamespace(platform=None, user_id="desktop"), "route")
    monkeypatch.setattr(authority, "_schedule", lambda ref: None)  # the test drives the FIFO
    if owner_forward is None:
        return authority

    @contextlib.asynccontextmanager
    async def connect(_home, endpoint):
        assert endpoint == "ready-owner"
        client = OwnerClient(authority, before_rpc=before_rpc)
        if clients is not None:
            clients.append(client)
        yield client
    monkeypatch.setattr(owner_forward, "discover_owner", lambda _home: "ready-owner")
    monkeypatch.setattr(owner_forward, "connect_owner", connect)
    return authority


def running_bot_turn(db, key, *, instance_id="gateway"):
    """The gateway is running a Bot Chat delivery on ``key``: (epoch, admission, claimed row)."""
    db.create_session(key, "test")
    epoch = begin_runtime_epoch(db, instance_id=instance_id)
    bot = admit_session_input(db, epoch=epoch, principal_id="bot", session_id=key, request_id="dm-1",
                              payload={"text": "a message_agent DM"})
    claimed = claim_session_input(db, epoch=epoch, session_id=key)
    assert claimed["admission_id"] == bot["admission_id"]
    return epoch, bot, claimed


def submit(sid, text="my own question", **extra):
    return server.handle_request({"id": "p", "method": "prompt.submit",
                                  "params": {"session_id": sid, "text": text, **extra}})
