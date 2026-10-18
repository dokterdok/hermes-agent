"""Review probe (claude, lane3a r2): ``/model <the session's model> --once --reasoning high`` (a one-turn
reasoning boost on the current model). Since 0632abae13a the level only rides ``model_override``, and
``_take_turn_route`` sends no override when the pick is the session's route, so the level is dropped."""
from tests.hermes_cli.test_cli_gateway_model_route import RecordingConnection, _agent
from tests.hermes_cli.test_cli_gateway_model_scope import _chat_cli, _submits


def _reasoning_mutates(conn):
    return [p["payload"] for m, p in conn.calls if m == "session.mutate" and p.get("operation") == "reasoning"]


def test_once_reasoning_on_the_sessions_own_model_reaches_the_turn(monkeypatch):
    conn = RecordingConnection({"model": "claude-sonnet-4.6", "provider": "anthropic"})
    cli_mod, stub, printed = _chat_cli(monkeypatch, conn)
    stub.agent = _agent(conn, model="claude-sonnet-4.6", provider="anthropic")
    stub.model, stub.provider = "claude-sonnet-4.6", "anthropic"
    stub._confirm_and_apply_cli_model_switch = cli_mod.HermesCLI._confirm_and_apply_cli_model_switch.__get__(stub)
    cli_mod.HermesCLI._handle_model_switch(stub, "/model claude-sonnet-4.6 --provider anthropic --once --reasoning high")
    assert not [line for line in printed if "fail" in line.lower()], printed
    assert stub.chat("the once turn") == "ok"
    once = _submits(conn)[0]
    carried = (once.get("model_override") or {}).get("reasoning")
    pinned = [p for p in _reasoning_mutates(conn) if "high" in repr(p)]
    assert carried == "high" or pinned, (
        f"--reasoning high reached neither the admission nor the session: submit={once!r} printed={printed!r}")
