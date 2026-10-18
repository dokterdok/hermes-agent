"""Disk discovery must isolate one unreadable profile's optional metadata."""
import asyncio
from pathlib import Path
from types import SimpleNamespace


def test_invalid_sibling_metadata_does_not_fail_the_owning_roster(tmp_path, monkeypatch):
    from gateway.session_controls import AuthorityConnection
    from hermes_state import SessionDB

    home = tmp_path / "state"
    other = home / "profiles" / "broken"
    other.mkdir(parents=True)
    (home / "config.yaml").write_text("model:\n  default: fixture\n", encoding="utf-8")
    (other / "config.yaml").write_text("model:\n  default: fixture\n", encoding="utf-8")
    # A Windows editor saved the optional display-name metadata as ANSI.
    (other / "profile.yaml").write_bytes(b"display_name: Andr\xe9\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    with SessionDB(home / "state.db") as db:
        authority = SimpleNamespace(profile_id=str(home), instance_id="owner", db=db, events={}, sessions={})

        async def probe():
            connection = AuthorityConnection(authority, object(), {"user_id": "reader"})
            return await connection.dispatch({"id": "r", "method": "profiles.list",
                                              "params": {"include_sessions": False}})

        result = asyncio.run(probe())
    assert "result" in result, result
    assert any(row["name"] == "default" for row in result["result"]["profiles"])


def test_discovery_from_named_home_preserves_install_identity_and_omits_ghosts(tmp_path, monkeypatch):
    from gateway.session_controls import AuthorityConnection
    from hermes_cli.install_identity import get_install_id
    from hermes_state import SessionDB

    root = tmp_path / "state"
    owner = root / "profiles" / "worker"
    sibling = root / "profiles" / "friend"
    for home in (root, owner, sibling):
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("model:\n  default: fixture\n", encoding="utf-8")
    (root / "profiles" / "ghost").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(owner))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    with SessionDB(owner / "state.db") as db:
        authority = SimpleNamespace(profile_id=str(owner), instance_id="owner", db=db, events={}, sessions={})

        async def probe():
            connection = AuthorityConnection(authority, object(), {"user_id": "reader"})
            return await connection.dispatch({"id": "r", "method": "profiles.list", "params": {}})

        result = asyncio.run(probe())["result"]
    assert result["install_id"] == get_install_id()
    rows = {row["name"]: row for row in result["profiles"]}
    assert set(rows) == {"default", "worker", "friend"}
    assert "canonical_session" not in rows["friend"]
    assert "canonical_session" not in rows["default"]
