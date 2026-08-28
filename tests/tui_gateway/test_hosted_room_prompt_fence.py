"""Older Desktop clients cannot start a second driver for hosted rooms."""

from __future__ import annotations

from gateway import hosted_rooms
from gateway import desktop_room_mailbox
import tui_gateway.server as server


def _stub_session(monkeypatch, *, title):
    monkeypatch.setattr(
        server,
        "_sess_nowait",
        lambda _params, _rid: (
            {"id": "session-1", "title": title, "source": "bot_room"},
            None,
        ),
    )


def test_direct_prompt_to_hosted_group_session_is_rejected(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    hosted_rooms.create_room(
        hosted_rooms.default_db_path(),
        room_id="room-hosted",
        name="Hosted room",
        members=[
            {"member_id": "one", "profile": "one", "handle": "one"},
            {"member_id": "two", "profile": "two", "handle": "two"},
        ],
        authority_gateway_id=hosted_rooms.local_authority_gateway_id(),
    )
    _stub_session(monkeypatch, title="Group: room-hosted")

    result = server._methods["prompt.submit"](
        "request-1", {"session_id": "session-1", "text": "continue"}
    )

    assert result["error"]["code"] == 4122
    assert "managed by its gateway" in result["error"]["message"]


def test_direct_prompt_to_non_hosted_group_reaches_normal_admission(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_session(monkeypatch, title="Group: local-only")
    monkeypatch.setattr(
        server,
        "_ensure_active_session_slot",
        lambda _sid, _session: "normal admission reached",
    )

    result = server._methods["prompt.submit"](
        "request-2", {"session_id": "session-1", "text": "continue"}
    )

    assert result["error"] == {"code": 4090, "message": "normal admission reached"}


def test_direct_prompt_is_refused_when_room_authority_cannot_be_verified(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    _stub_session(monkeypatch, title="Group: room-unknown")
    monkeypatch.setattr(
        hosted_rooms,
        "room_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk busy")),
    )

    result = server._methods["prompt.submit"](
        "request-3", {"session_id": "session-1", "text": "continue"}
    )

    assert result["error"]["code"] == 5122
    assert result["error"]["message"] == (
        "Could not verify this group. Try again after the gateway recovers."
    )


def test_classic_room_turn_token_is_verified_before_normal_admission(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    session = {
        "id": "session-1",
        "session_key": "stored-1",
        "title": "Group: local-only",
        "source": "desktop_bot_room",
    }
    monkeypatch.setattr(server, "_sess_nowait", lambda _params, _rid: (session, None))
    observed = {}

    def verify(db_path, **kwargs):
        observed.update(kwargs)
        assert db_path == desktop_room_mailbox.default_db_path()
        return {
            "scope": {
                "room_id": "room-1",
                "task_id": "dturn-1",
                "execution_generation": 1,
                "member_id": "member-1",
                "target_profile": "default",
                "home_install_id": "gateway-1",
                "target_install_id": "gateway-1",
                "authority_gateway_id": "gateway-1",
                "authority_epoch": 1,
            }
        }

    monkeypatch.setattr(desktop_room_mailbox, "verify_turn_submission", verify)
    monkeypatch.setattr(
        server,
        "_ensure_active_session_slot",
        lambda _sid, _current: "normal admission reached",
    )

    result = server._methods["prompt.submit"](
        "request-classic",
        {
            "session_id": "session-1",
            "text": "continue",
            "desktop_room_turn_token": "drt_secret",
        },
    )

    assert result["error"] == {"code": 4090, "message": "normal admission reached"}
    assert observed == {
        "token": "drt_secret",
        "session_key": "stored-1",
        "profile": "default",
    }


def test_classic_room_turn_token_fails_closed_for_wrong_source(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        server,
        "_sess_nowait",
        lambda _params, _rid: (
            {
                "id": "session-1",
                "session_key": "stored-1",
                "title": "Group: local-only",
                "source": "desktop",
            },
            None,
        ),
    )

    result = server._methods["prompt.submit"](
        "request-forged",
        {
            "session_id": "session-1",
            "text": "continue",
            "desktop_room_turn_token": "drt_secret",
        },
    )

    assert result["error"]["code"] == 4123
    assert "desktop_bot_room" in result["error"]["message"]


def test_launch_profile_artifacts_use_the_active_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "named-profile-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(server, "_current_profile_name", lambda: "launch-profile")
    monkeypatch.setattr(server, "_profile_home", lambda _profile: None)

    assert server._profile_state_db_path("launch-profile") == home / "state.db"
