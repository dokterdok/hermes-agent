"""``hermes group``: the product's words over the local gateway's succession and custody calls."""
import argparse

import pytest

from hermes_cli.subcommands import group

HOST, HERE, OTHER = "install:" + "a" * 32, "install:" + "b" * 32, "install:" + "c" * 32


def status(state="host_unreachable", targets=(HERE,), **extra):
    return {"state": state, "host": {"install_id": HOST, "name": "Mac mini", "reachable": False, "since": 0},
            "this_install": {"install_id": HERE, "name": "Home VPS", "role": "backup"}, "owner": {"name": "Dana"},
            "backups": [{"install_id": HERE, "name": "Home VPS", "successor": True, "readiness": "caught_up",
                         "behind_by": 0, "last_seen": None, "allowed": True, "designated": True, "kind": "backup",
                         "operator_name": "Dana"},
                        {"install_id": OTHER, "name": "Laptop", "successor": True, "readiness": "behind",
                         "behind_by": 3, "last_seen": None, "allowed": True, "designated": True, "kind": "member",
                         "operator_name": "Dana"}],
            "at_risk": {"count": 0}, "moving": None, "conflict": None, "moved": None, "work": None,
            "actions": [{"action": "continue", "targets": list(targets)}] if targets else [],
            "unavailable_reason": None, "previous_host": None, "unavailable_bots": [], "last_attempt": None, **extra}


@pytest.fixture
def gateway(monkeypatch):
    calls, replies = [], {}

    def call(method, **params):
        calls.append((method, params))
        reply = replies[method]
        return reply(**params) if callable(reply) else reply

    monkeypatch.setattr(group, "_call", call)
    replies["groups.list"] = {"rooms": [{"room_id": "room", "name": "Weekend plans"}]}
    return calls, replies


def test_status_prints_the_offline_host_and_where_the_group_can_continue(gateway, capsys):
    calls, replies = gateway
    replies["groups.succession.status"] = status()
    assert group.cmd_status(argparse.Namespace(group="weekend plans")) == 0
    out = capsys.readouterr().out
    assert "Mac mini is offline" in out and "The group is paused." in out
    assert "Home VPS: full copy, up to date (can continue this group)" in out
    assert "Laptop: full copy, 3 messages behind" in out and "Can continue on: Home VPS" in out
    assert calls[-1] == ("groups.succession.status", {"room_id": "room"})


def test_continue_shows_the_summary_and_continues_here(gateway, capsys):
    calls, replies = gateway
    replies["groups.succession.status"] = status()
    replies["groups.succession.prepare"] = {
        "preview_id": "preview_1", "target": {"install_id": HERE, "name": "Home VPS", "operator_name": "Sam"},
        "owner": {"name": "Dana"}, "behind_by": 0, "at_risk": {"count": 0},
        "work": {"completed": 1, "elsewhere": 0, "unknown": 1, "waiting_for_host": 0},
        "unavailable_bots": [{"member_id": "writer", "name": "Writer"}], "cautions": [{"code": "host_may_be_running"}]}
    replies["groups.succession.promote"] = status(
        "ok", targets=(), work={"completed": 1, "elsewhere": 0, "unknown": 1, "waiting_for_host": 0},
        previous_host={"install_id": HOST, "name": "Mac mini", "offline_since": 0})
    assert group.cmd_continue(argparse.Namespace(group="room", on=None, yes=True)) == 0
    out = capsys.readouterr().out
    assert "Continue “room” on Home VPS?" in out and "Sam will manage this group from Home VPS." in out
    assert "1 Bots run on Mac mini and stay unavailable until it's back: Writer." in out
    assert "Done. “room” now continues on Home VPS. 1 task(s) unknown, 0 waiting for Mac mini." in out
    assert ("groups.succession.promote", {"room_id": "room", "target_install_id": HERE, "preview_id": "preview_1",
                                          "confirm": True}) in calls


def test_continue_says_when_another_computer_is_better_placed(gateway, monkeypatch, capsys):
    calls, replies = gateway
    replies["groups.succession.status"] = status(targets=(OTHER, HERE))
    monkeypatch.setattr("builtins.input", lambda prompt: (print(prompt), "n")[1])
    assert group.cmd_continue(argparse.Namespace(group="room", on=None, yes=False)) == 1
    assert "Laptop is better placed to continue this group. Continue here anyway?" in capsys.readouterr().out
    assert not any(method == "groups.succession.prepare" for method, _ in calls)
    assert group.cmd_continue(argparse.Namespace(group="room", on="Laptop", yes=True)) == 1
    assert "Run this on Laptop" in capsys.readouterr().err


def test_keep_and_allow_call_the_gateway(gateway, capsys):
    calls, replies = gateway
    replies["groups.succession.status"] = status("continued_on_two", targets=(), conflict={"hosts": [
        {"install_id": HERE, "name": "Home VPS", "since": 1}, {"install_id": OTHER, "name": "Laptop", "since": 2}]})
    replies["groups.succession.keep"] = status("ok", targets=())
    assert group.cmd_keep(argparse.Namespace(group="room", computer="laptop")) == 0
    assert ("groups.succession.keep", {"room_id": "room", "install_id": OTHER}) in calls
    replies["groups.custody.allow"] = {"room_id": "room", "install_id": HERE, "allowed": True, "confirmed": False}
    assert group.cmd_backups(argparse.Namespace(group="room", backups_action="allow", computer="here")) == 0
    assert ("groups.custody.allow", {"room_id": "room", "successor": True}) in calls


def test_errors_use_the_products_words(gateway, capsys):
    from hermes_cli.gateway_client import GatewayClientError

    def refuse(**params):
        raise group.GroupCommandError(group._MESSAGES["not_owner"])

    calls, replies = gateway
    replies["groups.succession.status"] = refuse
    assert group.cmd_status(argparse.Namespace(group="room")) == 1
    assert capsys.readouterr().err.strip() == "Only the group's owner can do that."
    assert GatewayClientError  # the real transport maps its reason codes through _MESSAGES
