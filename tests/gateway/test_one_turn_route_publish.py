"""A one-turn model override must not become the route other clients read, and a same-provider
override must keep the session endpoint and key unless it names an alias with its own."""
from types import SimpleNamespace

import pytest

from gateway.session_cli_turn import cli_turn_scope, one_turn_runtime
from hermes_cli.model_switch import DirectAlias, ModelSwitchResult


def test_settled_info_publishes_the_policy_route_and_drops_the_cached_agent(monkeypatch):
    """The one turn ran another model at another level; the published route and level are the session's."""
    from gateway.session_local_inventory import publish_settled_info
    published, evicted = [], []
    policy = SimpleNamespace(
        current_model=lambda _authority: "session-model", provider="openrouter", source="cli",
        effective=lambda _authority, secrets=True: (
            {"model": {"provider": "openrouter"}, "agent": {"reasoning_effort": "low"}}, "session-model"))
    live = SimpleNamespace(route="route-1", source=SimpleNamespace())
    authority = SimpleNamespace(profile_id="p", sessions={"s": live})
    turn = SimpleNamespace(
        _ctx=SimpleNamespace(inbound_message_id="adm", session_key="route-1"),
        _approval_owner=(authority, "s", 1),
        _runner=SimpleNamespace(_evict_turn_agent=lambda key, _agent: evicted.append(key)),
        _publish_execution=lambda kind, payload: published.append((kind, payload)),
    )
    agent = SimpleNamespace(model="once-model", provider="anthropic", tools=None, _cached_system_prompt="",
                            context_compressor=None)

    from gateway import session_local, session_policy
    monkeypatch.setattr(session_local, "local_session_info", lambda *_a, **_k: {
        "model": "once-model", "provider": "anthropic", "reasoning_effort": "high"})
    monkeypatch.setattr(session_policy, "policy_for_source", lambda *_a, **_k: policy)

    with cli_turn_scope("adm", {"model_override": {"model": "once-model", "provider": "anthropic",
                                                   "reasoning": "high"}}):
        publish_settled_info(turn, agent)

    assert evicted == ["route-1"]
    assert published and published[0][0] == "session.info"
    assert published[0][1]["model"] == "session-model"
    assert published[0][1]["provider"] == "openrouter"
    assert published[0][1]["reasoning_effort"] == "low"


def test_a_one_turn_agent_is_released_once_its_turn_ends(monkeypatch):
    """The settle drops the one-turn agent from the cache while its turn still runs it, and nothing else
    holds it. It is released when that turn's slot clears (a Codex app-server child would otherwise
    outlive the turn), not while the turn runs it."""
    import threading
    from collections import OrderedDict
    from unittest.mock import MagicMock

    from gateway import session_local, session_policy
    from gateway.run import GatewayRunner
    from gateway.session_local_inventory import publish_settled_info
    from tests.waiting import wait_until

    route = "agent:main:local:dm:s"
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._agent_cache, runner._agent_cache_lock = OrderedDict(), threading.Lock()
    runner._persist_active_agents = lambda: None
    agent = MagicMock(model="once-model", provider="anthropic", tools=None, _cached_system_prompt="",
                      context_compressor=None)
    runner._agent_cache[route] = (agent, "sig-once", 0, "s")
    runner._running_agents[route] = agent  # the turn that built it is still running
    authority = SimpleNamespace(profile_id="p", sessions={"s": SimpleNamespace(route=route, source=SimpleNamespace())},
                                runner=runner)
    turn = SimpleNamespace(_ctx=SimpleNamespace(inbound_message_id="adm", session_key=route),
                           _approval_owner=(authority, "s", 1), _runner=runner,
                           _publish_execution=lambda *_a: None)
    monkeypatch.setattr(session_local, "local_session_info", lambda *_a, **_k: {"model": "once-model"})
    monkeypatch.setattr(session_policy, "policy_for_source", lambda *_a, **_k: None)

    with cli_turn_scope("adm", {"model_override": {"model": "once-model", "provider": "anthropic"}}):
        publish_settled_info(turn, agent)
    assert route not in runner._agent_cache
    assert not agent.release_clients.called

    runner._release_running_agent_state(route)
    wait_until(lambda: agent.release_clients.called, timeout=5, what="the one-turn agent's release")


class _Child:
    released = False

    def release_clients(self):
        self.released = True


def test_releasing_a_one_turn_agent_leaves_its_running_review_alone(monkeypatch):
    """The one-turn agent's post-turn memory/skill review still runs on its fork when the turn's slot clears.
    The agent's own release must not cut that fork's request (the fork releases its own clients when it
    ends); any other leftover child is still released with the agent."""
    import threading
    from collections import OrderedDict

    from agent.background_review import _track_review_fork
    from gateway import session_local, session_policy
    from gateway.run import GatewayRunner
    from gateway.session_local_inventory import publish_settled_info
    from run_agent import AIAgent

    route = "agent:main:local:dm:s"
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._agent_cache, runner._agent_cache_lock = OrderedDict(), threading.Lock()
    runner._persist_active_agents = lambda: None
    runner._spawn_release_thread = lambda target, args, _name, *, inline_fallback, session_key=None: (
        runner._run_release_in_profile_scope(target, args, session_key))
    agent = AIAgent.__new__(AIAgent)  # the real release_clients
    agent.client, agent._active_children, agent._active_children_lock = None, [], threading.Lock()
    agent._background_review_agent, agent._session_messages = None, [{"role": "user", "content": "q"}]
    review, leftover = _Child(), _Child()
    _track_review_fork(agent, review, register=True)
    agent._active_children.append(leftover)
    runner._agent_cache[route] = (agent, "sig-once", 0, "s")
    runner._running_agents[route] = agent
    authority = SimpleNamespace(profile_id="p", sessions={"s": SimpleNamespace(route=route, source=SimpleNamespace())},
                                runner=runner)
    turn = SimpleNamespace(_ctx=SimpleNamespace(inbound_message_id="adm", session_key=route),
                           _approval_owner=(authority, "s", 1), _runner=runner,
                           _publish_execution=lambda *_a: None)
    monkeypatch.setattr(session_local, "local_session_info", lambda *_a, **_k: {})
    monkeypatch.setattr(session_policy, "policy_for_source", lambda *_a, **_k: None)

    with cli_turn_scope("adm", {"model_override": {"model": "once-model", "provider": "custom"}}):
        publish_settled_info(turn, agent)
    runner._release_running_agent_state(route)
    assert agent._session_messages == [] and leftover.released
    assert not review.released


def test_same_provider_override_keeps_the_session_endpoint_and_key(monkeypatch):
    def _resolve(**_k):
        return ModelSwitchResult(
            success=True, new_model="openai/gpt-4.1", target_provider="openrouter",
            api_key="", base_url="https://openrouter.ai/api/v1", api_mode="chat_completions",
            request_overrides={})

    monkeypatch.setattr("hermes_cli.model_switch.switch_model", _resolve)
    monkeypatch.setattr("hermes_cli.config.get_compatible_custom_providers", lambda _config: [])
    pool = object()
    model, runtime = one_turn_runtime(
        {"model": "openai/gpt-4.1", "provider": "openrouter"},
        "session-model",
        {"provider": "openrouter", "api_key": "sk-session", "base_url": "https://mirror.example/api/v1",
         "credential_pool": pool, "request_overrides": {"provider": {"only": ["anthropic"]}},
         "api_mode": "chat_completions"},
        {})
    assert model == "openai/gpt-4.1"
    assert runtime["base_url"] == "https://mirror.example/api/v1"
    assert runtime["api_key"] == "sk-session"
    assert runtime["credential_pool"] is pool
    assert runtime["request_overrides"] == {"provider": {"only": ["anthropic"]}}


@pytest.mark.parametrize("override", [{"model": "fast"}, {"model": "fast-model", "provider": "custom"}],
                         ids=["alias", "resolved-route"])
def test_same_provider_alias_override_runs_on_the_alias_endpoint_and_key(monkeypatch, override):
    """``/model fast --once``: ``fast`` is a ``model_aliases`` entry on the session's own provider with its own
    endpoint and key. The override names the alias, or (the classic CLI) the model and provider it resolved to.
    The turn runs on the alias's endpoint and key, not the session's, and leaves the session's pool behind."""
    _resolve_aliases(monkeypatch, fast=DirectAlias(
        model="fast-model", provider="custom", base_url="http://127.0.0.1:7002/v1", api_key="sk-alias"))
    model, runtime = one_turn_runtime(override, "session-model", {
        "provider": "custom", "api_key": "sk-session", "base_url": "http://127.0.0.1:7001/v1",
        "api_mode": "chat_completions", "credential_pool": object()}, {})
    assert model == "fast-model"
    assert (runtime["base_url"], runtime["api_key"]) == ("http://127.0.0.1:7002/v1", "sk-alias")
    assert runtime["credential_pool"] is None


def test_an_alias_naming_the_sessions_endpoint_keeps_the_sessions_key_and_pool(monkeypatch):
    """An alias on the session's own endpoint, with no key of its own, is a plain pick there: the turn keeps
    the session's launch key and pool, not the profile's key."""
    endpoint = "https://openrouter.ai/api/v1"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-profile")
    _resolve_aliases(monkeypatch, fast=DirectAlias(model="vendor/fast", provider="openrouter", base_url=endpoint))
    pool = object()
    model, runtime = one_turn_runtime({"model": "fast", "provider": "openrouter"}, "vendor/old", {
        "provider": "openrouter", "api_key": "sk-launch", "base_url": endpoint, "api_mode": "chat_completions",
        "credential_pool": pool}, {})
    assert model == "vendor/fast"
    assert (runtime["api_key"], runtime["credential_pool"]) == ("sk-launch", pool)


def _resolve_aliases(monkeypatch, **aliases):
    """The real ``switch_model`` over these ``model_aliases`` entries; catalog validation accepts any model."""
    from hermes_cli import model_switch
    for name, alias in aliases.items():
        monkeypatch.setitem(model_switch.DIRECT_ALIASES, name, alias)
    monkeypatch.setattr("hermes_cli.models_validate.validate_requested_model", lambda *_a, **_k: {
        "accepted": True, "persist": True, "recognized": True, "message": ""})
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", dict)


def _same_provider_override(monkeypatch, provider, model, api_mode, base_url, session):
    def _resolve(**_k):
        return ModelSwitchResult(success=True, new_model=model, target_provider=provider, api_key="",
                                 base_url=base_url, api_mode=api_mode, request_overrides={})

    monkeypatch.setattr("hermes_cli.model_switch.switch_model", _resolve)
    monkeypatch.setattr("hermes_cli.config.get_compatible_custom_providers", lambda _config: [])
    return one_turn_runtime({"model": model, "provider": provider}, "session-model",
                            {"provider": provider, "api_key": "tok-session", **session}, {})[1]


def test_same_provider_override_runs_on_the_targets_wire(monkeypatch):
    """Copilot serves GPT-5 on the Responses API and Claude on chat completions."""
    runtime = _same_provider_override(
        monkeypatch, "copilot", "gpt-5.4", "codex_responses", "https://api.githubcopilot.com",
        {"base_url": "https://api.githubcopilot.com", "api_mode": "chat_completions"})
    assert runtime["api_mode"] == "codex_responses"
    assert (runtime["base_url"], runtime["api_key"]) == ("https://api.githubcopilot.com", "tok-session")


def test_same_provider_override_reshapes_the_opencode_endpoint_for_its_wire(monkeypatch):
    runtime = _same_provider_override(
        monkeypatch, "opencode-zen", "minimax-m2.7", "anthropic_messages", "https://opencode.ai/zen",
        {"base_url": "https://opencode.ai/zen/v1", "api_mode": "chat_completions"})
    assert (runtime["api_mode"], runtime["base_url"]) == ("anthropic_messages", "https://opencode.ai/zen")
