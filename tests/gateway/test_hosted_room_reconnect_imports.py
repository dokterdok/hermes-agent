"""The reused narrow owners remain importable through all existing entry points."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "first",
    [
        "gateway.hosted_room_link_records",
        "gateway.hosted_room_storage",
        "gateway.hosted_rooms",
        "tui_gateway.hosted_room_peer_status",
    ],
)
def test_reconnect_owners_import_independently_and_keep_public_aliases(tmp_path, first):
    script = """
import importlib
import sys

importlib.import_module(sys.argv[1])
from gateway import hosted_room_link_records, hosted_room_storage, hosted_rooms
from tui_gateway import hosted_room_peer_status, hosted_room_service

assert hosted_rooms.room_link_record is hosted_room_link_records.room_link_record
assert hosted_room_storage.update_room_link_status is hosted_room_link_records.update_room_link_status
assert hosted_room_service._RouteStatusPeerClient is hosted_room_peer_status._RouteStatusPeerClient
assert hosted_rooms.room_link_record(sys.argv[2], room_id="missing", member_id="member") is None
"""
    result = subprocess.run(
        [sys.executable, "-c", script, first, str(tmp_path / "state.db")],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
