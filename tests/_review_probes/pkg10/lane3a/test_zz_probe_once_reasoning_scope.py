"""Probe G4: ``/model B --once --reasoning high`` is one turn. The reasoning level must not become a
session-scope commit that outlives that turn (with MP-16 every session-scope ``reasoning`` mutate is a
durable ``session_pins.reasoning`` that profile edits and resume refreshes no longer override)."""
from tests._review_probes.pkg10.lane3a._cli_harness import RecordingConnection, _chat_cli


def _reasoning_mutates(conn):
    return [p["payload"] for m, p in conn.calls if m == "session.mutate" and p.get("operation") == "reasoning"]


def test_once_reasoning_leaves_no_session_reasoning_commit(monkeypatch):
    conn = RecordingConnection({"model": "old/model", "provider": "openrouter"})
    cli_mod, stub, printed = _chat_cli(monkeypatch, conn)
    stub._confirm_and_apply_cli_model_switch = cli_mod.HermesCLI._confirm_and_apply_cli_model_switch.__get__(stub)
    cli_mod.HermesCLI._handle_model_switch(
        stub, "/model claude-sonnet-4.6 --provider anthropic --once --reasoning high")
    assert not [line for line in printed if "fail" in line.lower()], printed
    stub.chat("the once turn")
    stub.chat("the turn after")
    # Net effect on the shared session after the one turn and its restore: nothing committed.
    assert _reasoning_mutates(conn) == [], _reasoning_mutates(conn)
