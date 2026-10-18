"""A real Windows sharing violation must not poison sibling metadata forever."""
import asyncio
import ctypes
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB


@pytest.mark.platforms("windows")
def test_sibling_metadata_recovers_after_file_unlock(tmp_path, monkeypatch):
    home = tmp_path / "state"
    helper = home / "profiles" / "helper"
    helper.mkdir(parents=True)
    (home / "config.yaml").write_text("model:\n  default: fixture-model\n", encoding="utf-8")
    (helper / "config.yaml").write_text("model:\n  default: fixture-model\n", encoding="utf-8")
    metadata = helper / "profile.yaml"
    metadata.write_text("display_name: Friendly Helper\nui_meta:\n  hermes-bots:\n    color: blue\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(metadata), 0x80000000, 0, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value, ctypes.get_last_error()

    with SessionDB(home / "state.db") as db:
        authority = SimpleNamespace(profile_id=str(home), instance_id="owner", db=db, events={}, sessions={})

        async def roster():
            connection = AuthorityConnection(authority, object(), {"user_id": "reader"})
            response = await connection.dispatch({"id": "r", "method": "profiles.list", "params": {"include_sessions": False}})
            assert "result" in response, response
            return {row["name"]: row for row in response["result"]["profiles"]}

        try:
            before = metadata.stat()
            with pytest.raises(PermissionError):
                metadata.read_text(encoding="utf-8")
            first = asyncio.run(roster())
            assert first["helper"]["display_name"] == "helper"
        finally:
            assert kernel.CloseHandle(handle)

        assert metadata.read_text(encoding="utf-8").startswith("display_name: Friendly Helper")
        after = metadata.stat()
        assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
        recovered = asyncio.run(roster())["helper"]
        assert recovered["display_name"] == "Friendly Helper", recovered
        assert recovered["ui_meta"]["hermes-bots"]["color"] == "blue"
