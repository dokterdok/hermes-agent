"""Round-2 review: an alias named like its model must still select its endpoint."""
from tests.hermes_cli.test_cli_gateway_model_scope import _chat_cli, _submits
from tests.hermes_cli.test_cli_gateway_model_route import RecordingConnection


def test_same_named_alias_still_routes_to_its_endpoint(monkeypatch):
    from hermes_cli import model_switch
    from gateway.session_cli_turn import one_turn_runtime
    resolve = model_switch.switch_model
    conn = RecordingConnection({"model": "shared-model", "provider": "custom"})
    cli_mod, stub, printed = _chat_cli(monkeypatch, conn)
    monkeypatch.setattr(model_switch, "switch_model", resolve)
    cfg = {"model": {"default": "shared-model", "provider": "custom", "base_url": "http://127.0.0.1:7001/v1"},
           "model_aliases": {"shared-model": {"model": "shared-model", "provider": "custom",
                              "base_url": "http://127.0.0.1:7003/v1", "api_key": "sk-alias"}}}
    monkeypatch.setattr("hermes_cli.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr("hermes_cli.runtime_provider.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(model_switch, "DIRECT_ALIASES", {})
    monkeypatch.setattr("hermes_cli.models_validate.validate_requested_model", lambda *_a, **_k: {
        "accepted": True, "persist": True, "recognized": True, "message": ""})
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", dict)
    session = {"provider": "custom", "api_key": "sk-session", "base_url": "http://127.0.0.1:7001/v1",
               "api_mode": "chat_completions"}
    stub.model, stub.provider, stub.requested_provider = "shared-model", "custom", "custom"
    stub.base_url = stub._explicit_base_url = session["base_url"]
    stub.api_key = stub._explicit_api_key = session["api_key"]
    stub.agent.model, stub.agent.provider = "shared-model", "custom"
    cli_mod.HermesCLI._handle_model_switch(stub, "/model shared-model --once")
    assert not [line for line in printed if "fail" in line.lower()], printed
    assert stub.base_url == "http://127.0.0.1:7003/v1"
    assert stub.chat("use the selected alias endpoint") == "ok"
    submit = _submits(conn)[0]
    override = submit.get("model_override")
    runtime = one_turn_runtime(override, "shared-model", session, {})[1] if override else session
    assert (runtime["base_url"], runtime["api_key"]) == ("http://127.0.0.1:7003/v1", "sk-alias"), submit

