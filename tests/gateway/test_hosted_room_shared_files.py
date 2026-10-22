"""A Bot shares files in a gateway Group Chat: staged per attempt, published only with its reply."""
import asyncio
import base64
import json
import sqlite3

import pytest

from gateway import hosted_room_driver as tasks
from gateway.hosted_room_attachments import HostedRoomAttachmentStore
from tests.gateway.fixtures.hosted_room_owner import events, owner, run_turn


def share(path, **kwargs):
    from tools import group_file_tool  # registers the tool, as discovery does
    from tools.registry import registry
    return json.loads(registry.dispatch("share_group_file", {"path": str(path), **kwargs}, task_id="default"))


def write_file(tmp_path, name="report.txt", data=b"explicit result bytes\n"):
    path = tmp_path / "work" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    return path


def sharing(paths, reply, *, shared=None, **kwargs):
    async def handle(event):
        for path in paths:
            result = await asyncio.to_thread(share, path, **kwargs)
            if shared is not None:
                shared.append(result)
        return reply
    return handle


def staged_rows(service):
    with sqlite3.connect(service.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT * FROM hosted_room_attachments WHERE upload_id LIKE 'out:%' ORDER BY created_at")]


@pytest.mark.asyncio
async def test_a_shared_file_is_published_with_the_reply_and_reaches_the_next_bot(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        data = b"explicit result bytes\n"
        shared = []
        runner._handle_message = sharing([write_file(tmp_path, data=data)], "@reviewer Here is the report.",
                                         shared=shared)
        await run_turn(authority, service)
        assert shared[0]["shared"] is True and shared[0]["name"] == "report.txt"
        message, = events(service, "message.member")
        assert message["payload"]["text"] == "@reviewer Here is the report."
        attachment, = message["payload"]["attachments"]
        assert (attachment["name"], attachment["kind"], attachment["mime"], attachment["size"]) == (
            "report.txt", "file", "text/plain", len(data))
        # Viewers download exactly those bytes, as for a user's file.
        gateway_id, epoch = service._owned_authority("room")
        saved = HostedRoomAttachmentStore(service.db_path).read_viewer(
            room_id="room", attachment_id=attachment["attachment_id"], event_id=message["event_id"],
            authority_gateway_id=gateway_id, authority_epoch=epoch)
        assert saved.data == data
        row, = staged_rows(service)
        assert (row["state"], row["event_id"], row["viewer_access"], row["expires_at"]) == (
            "committed", message["event_id"], 1, None)
        assert json.loads(row["recipient_member_ids_json"]) == ["reviewer"]
        # The mentioned Bot's next turn carries the file; the author never gets its own file back.
        service.prepare_room(service.bindings()[0])
        task, = tasks.list_tasks(service.db_path, room_id="room", status="queued")
        assert task["payload"]["target_member_id"] == "reviewer"
        assert task["payload"]["attachments"] == [{**attachment, "event_id": message["event_id"]}]
        assert "report.txt" in task["payload"]["prompt"]


@pytest.mark.asyncio
async def test_files_shared_with_a_pass_reply_still_get_their_message(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        runner._handle_message = sharing([write_file(tmp_path, "plan.md", b"# Plan\n")], "(pass)")
        await run_turn(authority, service)
        message, = events(service, "message.member")
        assert message["payload"]["text"] == "Shared plan.md."
        assert [a["name"] for a in message["payload"]["attachments"]] == ["plan.md"]


@pytest.mark.asyncio
async def test_a_turn_that_fails_publishes_no_files(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        async def handle(event):
            from gateway.session_results import execution_result
            await asyncio.to_thread(share, write_file(tmp_path))
            execution_result.get()["result"] = {"final_response": "", "messages": [], "failed": True,
                                                "error": "provider unavailable"}
            return ""
        runner._handle_message = handle
        turn = await run_turn(authority, service)
        assert tasks.get_task(service.db_path, turn.task["identity"])["status"] == "failed"
        assert not events(service, "message.member")
        row, = staged_rows(service)
        assert (row["state"], row["event_id"], row["viewer_access"]) == ("uploaded", None, 0)
        assert row["expires_at"] is not None  # the store's uncommitted-upload expiry reclaims it


@pytest.mark.asyncio
async def test_a_superseded_reply_publishes_no_files(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        async def handle(event):
            await asyncio.to_thread(share, write_file(tmp_path))
            # A newer request in the same thread arrives before the reply settles.
            await asyncio.to_thread(service.send, room_id="room", event_id="newer",
                                    payload={"thread_id": "thread", "text": "@writer Never mind."})
            return "Late report."
        runner._handle_message = handle
        await run_turn(authority, service)
        assert not events(service, "message.member")
        assert [row["state"] for row in staged_rows(service)] == ["uploaded"]


@pytest.mark.asyncio
async def test_only_the_settled_attempts_own_files_are_published(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        from dataclasses import replace
        from gateway.hosted_room_outputs import current_turn, hosted_turn_scope

        async def handle(event):
            # Another attempt of the same task (a retry's generation) staged a file of its own.
            other = replace(current_turn(), execution_generation=current_turn().execution_generation + 1)
            with hosted_turn_scope(other):
                await asyncio.to_thread(share, write_file(tmp_path, "other.txt", b"other attempt\n"))
            await asyncio.to_thread(share, write_file(tmp_path, "mine.txt", b"this attempt\n"))
            return "Done."
        runner._handle_message = handle
        await run_turn(authority, service)
        message, = events(service, "message.member")
        assert [a["name"] for a in message["payload"]["attachments"]] == ["mine.txt"]
        assert sorted((row["name"], row["state"]) for row in staged_rows(service)) == [
            ("mine.txt", "committed"), ("other.txt", "uploaded")]


@pytest.mark.asyncio
async def test_sharing_refuses_outside_a_turn_unsafe_paths_and_too_many_files(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        outside = share(write_file(tmp_path))
        assert outside["error"] == "File sharing is available only during a Group Chat turn."
        (tmp_path / ".env").write_text("SECRET=1\n")
        results = []

        async def handle(event):
            for path in (tmp_path / ".env", "relative.txt", tmp_path / "missing.txt"):
                results.append(await asyncio.to_thread(share, path))
            for index in range(9):
                results.append(await asyncio.to_thread(share, write_file(tmp_path, f"f{index}.txt", b"%d\n" % index)))
            results.append(await asyncio.to_thread(share, write_file(tmp_path, "f0.txt", b"changed\n")))
            return "Done."
        runner._handle_message = handle
        await run_turn(authority, service)
        assert all("error" in result for result in results[:3])
        assert [result.get("shared") for result in results[3:11]] == [True] * 8
        assert "at most 8 files" in results[11]["error"]
        assert "already shared in this turn" in results[12]["error"]
        message, = events(service, "message.member")
        assert [a["name"] for a in message["payload"]["attachments"]] == [f"f{index}.txt" for index in range(8)]


def test_a_client_upload_cannot_pose_as_a_bots_shared_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from gateway.session_hosted_attachments import upload
    from hermes_state_runtime import RuntimeStoreError
    service = SimpleNamespace()
    monkeypatch.setattr("gateway.session_hosted_attachments._authorize", lambda *args: "room")
    with pytest.raises(RuntimeStoreError, match="invalid_params"):
        upload(service, None, {"room_id": "room", "upload_id": "out:forged", "kind": "file", "name": "x.txt",
                               "mime": "text/plain", "data_base64": base64.b64encode(b"x").decode()})


def test_the_tool_reaches_group_chat_member_sessions_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from model_tools import get_tool_definitions
    from toolsets import session_disabled_toolsets

    def offered(toolsets, platform):
        return "share_group_file" in {tool["function"]["name"] for tool in get_tool_definitions(
            enabled_toolsets=toolsets, disabled_toolsets=session_disabled_toolsets(None, platform),
            quiet_mode=True)}
    # Member sessions run as platform bot_room with the bot_room toolset folded in.
    assert offered(["terminal", "bot_room"], "bot_room")
    # Nothing else pays its schema, not even an "all" toolset selection.
    assert not offered(["terminal", "bot_room"], "desktop")
    assert not offered(["all"], "telegram")
