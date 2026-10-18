"""Round-2 review: a same-model --once still has an independent reasoning choice."""
from tests.hermes_cli.test_cli_gateway_model_scope import _chat_cli, _pick
from tests.hermes_cli.test_cli_gateway_model_route import RecordingConnection


class ReasoningConnection(RecordingConnection):
    def __init__(self):
        super().__init__({"model": "old/model", "provider": "openrouter", "reasoning_effort": "low"})
        self.turn_efforts = []

    def call(self, method, *, timeout=None, **params):
        if method == "session.mutate" and params.get("operation") == "reasoning":
            self._state.info["reasoning_effort"] = params["payload"]["effort"]
        if method == "prompt.submit":
            override = params.get("model_override") or {}
            self.turn_efforts.append(override.get("reasoning", self._state.info["reasoning_effort"]))
        return super().call(method, timeout=timeout, **params)


def test_once_reasoning_reaches_turn_when_model_is_unchanged(monkeypatch):
    conn = ReasoningConnection()
    cli_mod, stub, printed = _chat_cli(monkeypatch, conn)
    stub.reasoning_config = {"enabled": True, "effort": "low"}
    stub._confirm_and_apply_cli_model_switch = cli_mod.HermesCLI._confirm_and_apply_cli_model_switch.__get__(stub)
    _pick(monkeypatch, "old/model", "openrouter", "https://openrouter.ai/api/v1", "chat_completions")
    cli_mod.HermesCLI._handle_model_switch(stub, "/model old/model --provider openrouter --once --reasoning high")
    assert not [line for line in printed if "fail" in line.lower()], printed
    assert stub.chat("the high reasoning turn") == "ok"
    assert conn.turn_efforts == ["high"], conn.calls
