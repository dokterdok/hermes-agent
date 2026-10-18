"""Review probe (claude, lane3a r2): a same-provider ``--once`` pick typed as a BUILT-IN alias
(``MODEL_ALIASES``: opus / sonnet / gpt5 ...) carries no endpoint or key of its own, yet
``resolved_via_alias`` is set, so ``one_turn_runtime`` takes the "alias runs whole" branch and the
turn leaves the session's credential pool, endpoint, key and wire."""
import pytest

from gateway.session_cli_turn import one_turn_runtime

PROXY = "https://llm-proxy.corp.example/anthropic"
DEFAULT = "https://api.anthropic.com"


QUICK = {"quick": {"model": "claude-haiku-4-5", "provider": "anthropic"}}  # endpointless shortcut


def _catalog(monkeypatch, aliases):
    cfg = {"model": {"default": "claude-sonnet-4-6", "provider": "anthropic"}, "model_aliases": dict(aliases)}
    monkeypatch.setattr("hermes_cli.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr("hermes_cli.runtime_provider.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr("hermes_cli.models_validate.validate_requested_model", lambda *a, **k: {
        "accepted": True, "persist": True, "recognized": True, "message": ""})
    monkeypatch.setattr("hermes_cli.model_switch.DIRECT_ALIASES", {})
    monkeypatch.setattr("hermes_cli.model_switch.list_provider_models",
                        lambda provider: ["claude-haiku-4-5", "claude-sonnet-4-6"] if provider == "anthropic" else [])
    monkeypatch.setattr("hermes_cli.models._PROVIDER_MODELS", {})
    # The provider's DEFAULT route, which the explicit-provider re-resolution hands back.
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", lambda **_k: {
        "provider": "anthropic", "api_key": "sk-env-default", "base_url": DEFAULT,
        "api_mode": "anthropic_messages"})
    return cfg


def _session(pool):
    return {"provider": "anthropic", "api_key": "sk-session", "base_url": PROXY,
            "api_mode": "anthropic_messages", "credential_pool": pool,
            "request_overrides": {"metadata": {"team": "radiology"}}}


@pytest.mark.parametrize("typed,aliases", [("claude-haiku-4-5", {}), ("haiku", {}), ("quick", QUICK),
                                           ("claude-haiku-4-5", QUICK)],
                         ids=["model-id", "builtin-alias", "endpointless-model_aliases-entry",
                              "model-id-reverse-matching-an-endpointless-alias"])
def test_builtin_alias_once_keeps_the_session_route(monkeypatch, typed, aliases):
    anthropic_catalog = _catalog(monkeypatch, aliases)
    """What ``/model haiku --once`` sends since c0fc6d45dcf (``alias or model``) vs the resolved id."""
    pool = object()
    model, runtime = one_turn_runtime({"model": typed, "provider": "anthropic"}, "claude-sonnet-4-6",
                                      _session(pool), anthropic_catalog)
    assert model == "claude-haiku-4-5"
    assert runtime["credential_pool"] is pool, "session credential pool dropped"
    assert (runtime["base_url"], runtime["api_key"]) == (PROXY, "sk-session")
    assert runtime["request_overrides"] == {"metadata": {"team": "radiology"}}


def test_cli_sends_the_builtin_alias_name(monkeypatch):
    """c0fc6d45dcf: ``_route_off_session`` sends ``alias or model``; a built-in alias is an alias."""
    from hermes_cli.model_switch import resolve_alias
    monkeypatch.setattr("hermes_cli.model_switch.DIRECT_ALIASES", {})
    monkeypatch.setattr("hermes_cli.model_switch._ensure_direct_aliases", lambda: None)
    monkeypatch.setattr("hermes_cli.model_switch.list_provider_models",
                        lambda provider: ["claude-haiku-4-5", "claude-sonnet-4-6"])
    monkeypatch.setattr("hermes_cli.models._PROVIDER_MODELS", {})
    assert resolve_alias("haiku", "anthropic") == ("anthropic", "claude-haiku-4-5", "haiku")
