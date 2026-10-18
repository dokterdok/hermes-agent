"""Probe G6: a one-turn model_override that names a direct alias on the session's own provider
must run on the alias's endpoint and key, not the session's."""
import pytest


SESSION_URL = "http://127.0.0.1:7001/v1"
ALIAS_URL = "http://127.0.0.1:7002/v1"


@pytest.fixture
def alias_config(monkeypatch):
    cfg = {
        "model": {"default": "session-model", "provider": "custom", "base_url": SESSION_URL},
        "model_aliases": {"fast": {"model": "fast-model", "provider": "custom", "base_url": ALIAS_URL,
                                   "api_key": "sk-alias"}},
    }
    monkeypatch.setattr("hermes_cli.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr("hermes_cli.runtime_provider.load_config", lambda *a, **k: cfg)
    probed = {}

    def _fake_validate(model_name, provider, *, api_key=None, base_url=None, api_mode=None, **_kw):
        probed.update(model=model_name, api_key=api_key, base_url=base_url)
        return {"accepted": True, "persist": True, "recognized": True, "message": ""}

    monkeypatch.setattr("hermes_cli.models_validate.validate_requested_model", _fake_validate)
    import hermes_cli.model_switch as ms
    monkeypatch.setattr(ms, "DIRECT_ALIASES", {})
    return cfg, probed


def test_alias_override_runs_on_the_alias_endpoint(alias_config):
    from gateway.session_cli_turn import one_turn_runtime
    cfg, probed = alias_config
    model, runtime = one_turn_runtime(
        {"model": "fast"}, "session-model",
        {"provider": "custom", "api_key": "sk-session", "base_url": SESSION_URL, "api_mode": "chat_completions"},
        cfg)
    assert model == "fast-model"
    # switch_model resolved the alias endpoint (the probe saw it) ...
    assert probed.get("base_url") == ALIAS_URL, probed
    # ... and the one-turn runtime must keep it.
    assert (runtime["base_url"], runtime["api_key"]) == (ALIAS_URL, "sk-alias"), runtime


def test_cli_form_override_of_the_alias_model_runs_on_the_alias_endpoint(alias_config):
    """The classic CLI sends the RESOLVED model and provider (``_turn_route``), not the alias name."""
    from gateway.session_cli_turn import one_turn_runtime
    cfg, probed = alias_config
    _model, runtime = one_turn_runtime(
        {"model": "fast-model", "provider": "custom"}, "session-model",
        {"provider": "custom", "api_key": "sk-session", "base_url": SESSION_URL, "api_mode": "chat_completions"},
        cfg)
    assert (runtime["base_url"], runtime["api_key"]) == (ALIAS_URL, "sk-alias"), (runtime, probed)
