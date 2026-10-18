"""G1 probe on MSG-22 (a Bot Chat follows its profile).

G1: a busy first open leaves the adoption pending; a ``reload.mcp`` on the same session in between
must not forget it, or the next warm open on that connection never adopts the frozen chat.

Real: SessionAuthority / AuthorityConnection over SQLite, ``session.resume``, ``reload.mcp``,
``session.mutate`` reasoning, the policy's one effective read. MCP discovery is stubbed (no servers).
"""
import json
from types import SimpleNamespace

import pytest

from tests.gateway.test_bot_chat_follows_profile import _follows, _frozen_bot_chat, home  # noqa: F401
from tests.gateway.test_follow_profile_followups import _profile
from tests.gateway.test_session_follow_profile_config import _owner, _route


def _no_mcp(monkeypatch, runner):
    from tools import mcp_tool_discovery as discovery
    runner.config = SimpleNamespace(multiplex_profiles=False)
    runner._mcp_reload_refresh_cached_agents = lambda *a, **k: None
    monkeypatch.setattr(discovery, 'reconcile_mcp_servers_with_config', lambda: {'added': [], 'removed': []})
    monkeypatch.setattr(discovery, 'mcp_servers_not_connected', lambda: [])
    monkeypatch.setattr(discovery, 'mcp_servers_ready', lambda: [])


@pytest.mark.asyncio
async def test_g1_reload_mcp_between_busy_open_and_warm_reopen_keeps_adoption(home, monkeypatch):
    import hermes_state_mutation_guards as guards
    from gateway.session_controls import AuthorityConnection
    from hermes_state_runtime import RuntimeStoreError
    _profile(home, 'old-model', 1, 'old-key')
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    _no_mcp(monkeypatch, runner)
    real, refused = guards.require_idle, []

    def busy_once(db, conn, ids):
        if not refused:
            refused.append(ids)
            raise RuntimeStoreError('session_busy')
        return real(db, conn, ids)
    monkeypatch.setattr(guards, 'require_idle', busy_once)
    desktop = AuthorityConnection(authority, object(), {'user_id': 'human'})
    try:
        ref, _params = _frozen_bot_chat(authority, owner.actor, home)
        live = authority.sessions[ref.session_id]
        _profile(home, 'new-model', 2, 'new-key')
        sid = {'session_id': ref.session_id}
        response = await desktop.dispatch({'id': 'busy', 'method': 'session.resume', 'params': dict(sid)})
        assert 'result' in response and refused and not _follows(runner, live), response
        # The turn finished; Desktop toggles an MCP server for this chat (reload.mcp on this session).
        response = await desktop.dispatch({'id': 'mcp', 'method': 'reload.mcp',
                                           'params': dict(sid, confirm=True)})
        assert 'result' in response, response
        # Desktop's warm reopen on the same connection.
        response = await desktop.dispatch({'id': 'warm', 'method': 'session.resume', 'params': dict(sid)})
        assert 'result' in response, response
        assert _follows(runner, live), 'the pending adoption was forgotten by reload.mcp'
        assert _route(runner, live) == ('new-model', 'http://127.0.0.1:2/v1', 'new-key')
    finally:
        await desktop.close()
        await owner.close()
        store.close_all_db_handles()
