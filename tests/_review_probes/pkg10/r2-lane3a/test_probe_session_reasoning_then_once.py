"""Review probe (claude, lane3a r2): a session ``/reasoning high`` followed by ``/model B --once``.
0632abae13a turns ``push_reasoning`` off for the one-turn build, and the restore does not rebuild the
agent, so the session pick is never committed: neither the --once turn nor the turn after runs high."""
from tests.hermes_cli.test_cli_gateway_model_route import RecordingConnection
from tests.hermes_cli.test_cli_gateway_model_scope import _chat_cli, _submits


def _reasoning_mutates(conn):
    return [p["payload"] for m, p in conn.calls if m == "session.mutate" and p.get("operation") == "reasoning"]


def test_session_reasoning_pick_survives_a_following_once(monkeypatch):
    from hermes_cli.cli_agent_setup_mixin import _retire_agent
    conn = RecordingConnection({"model": "old/model", "provider": "openrouter"})
    cli_mod, stub, printed = _chat_cli(monkeypatch, conn)
    stub._confirm_and_apply_cli_model_switch = cli_mod.HermesCLI._confirm_and_apply_cli_model_switch.__get__(stub)
    # What ``/reasoning high`` (session scope) does: hermes_cli/cli_commands_mixin.py:2280-2282.
    stub.reasoning_config = {"enabled": True, "effort": "high"}
    stub._reasoning_user_set = True
    _retire_agent(stub)
    cli_mod.HermesCLI._handle_model_switch(stub, "/model claude-sonnet-4.6 --provider anthropic --once")
    assert not [line for line in printed if "fail" in line.lower()], printed
    assert stub.chat("the once turn") == "ok"
    assert stub.chat("the turn after") == "ok"
    once, after = _submits(conn)
    committed = [p for p in _reasoning_mutates(conn) if p.get("effort") == "high"]
    assert committed or (once.get("model_override") or {}).get("reasoning") == "high", (
        "the session /reasoning high never reached the gateway", _reasoning_mutates(conn), once, after,
        conn.state("s").info.get("reasoning_effort"))
    assert conn.state("s").info.get("reasoning_effort") == "high", "the session does not run high afterwards"
