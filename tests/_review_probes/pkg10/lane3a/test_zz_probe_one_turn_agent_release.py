"""Probe G2: a one-turn (model_override) agent must not leave the cache unreleased.

``_evict_cached_agent`` pops the entry and skips ``release_clients`` while the agent is mid-turn.
``publish_settled_info`` runs inside the turn (``run_turn_runner`` finally), so the one-turn agent is
still running when it is evicted. After the turn ends it must either still be in the cache (the next
turn's signature check / idle sweep releases it) or have been released.
"""
import threading
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.waiting import wait_until


def test_one_turn_agent_is_released_or_still_cached_after_its_turn(monkeypatch):
    from gateway.run import GatewayRunner
    from gateway.session_cli_turn import cli_turn_scope
    from gateway.session_local_inventory import publish_settled_info
    from gateway import session_local, session_policy

    route = "agent:main:local:dm:s"
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._agent_cache = OrderedDict()
    runner._agent_cache_lock = threading.Lock()
    agent = MagicMock()
    agent.model, agent.provider, agent.tools, agent._cached_system_prompt = "once-model", "anthropic", None, ""
    agent.context_compressor = None
    runner._agent_cache[route] = (agent, "sig-once", 0, "s")
    runner._running_agents[route] = agent  # the turn that built it is still running

    live = SimpleNamespace(route=route, source=SimpleNamespace())
    authority = SimpleNamespace(profile_id="p", sessions={"s": live}, runner=runner)
    published = []
    turn = SimpleNamespace(
        _ctx=SimpleNamespace(inbound_message_id="adm", session_key=route),
        _approval_owner=(authority, "s", 1), _runner=runner,
        _publish_execution=lambda kind, payload: published.append((kind, payload)))
    policy = SimpleNamespace(current_model=lambda _a: "session-model", provider="openrouter", source="cli")
    monkeypatch.setattr(session_local, "local_session_info", lambda *_a, **_k: {"model": "once-model"})
    monkeypatch.setattr(session_policy, "policy_for_source", lambda *_a, **_k: policy)

    with cli_turn_scope("adm", {"model_override": {"model": "once-model", "provider": "anthropic"}}):
        publish_settled_info(turn, agent)
    assert published, "session.info was not published"

    # The turn ends: its running slot clears (production: _release_running_agent_state).
    del runner._running_agents[route]

    still_cached = route in runner._agent_cache
    if not still_cached:
        wait_until(lambda: agent.release_clients.called, timeout=3, what="the evicted one-turn agent was released")
    assert still_cached or agent.release_clients.called
