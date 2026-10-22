"""A Bot shares files in a gateway Group Chat: staged per attempt, published only with its reply."""
import asyncio
import base64
import json
import sqlite3
import time

import pytest

from gateway import hosted_room_driver as tasks
from gateway import hosted_rooms
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
        # Publishing the failure ended the file's hold; the store reclaims it like an abandoned upload.
        assert row["expires_at"] is not None
        HostedRoomAttachmentStore(service.db_path).prune(now=row["expires_at"])
        assert staged_rows(service) == []


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
        row, = staged_rows(service)
        assert row["state"] == "uploaded" and row["expires_at"] is not None  # released, reclaimable


@pytest.mark.asyncio
async def test_a_shared_file_waits_for_a_late_publication(tmp_path, monkeypatch):
    """Seam: the attachment store's clock, advanced past the uncommitted-upload expiry."""
    now = [time.time()]
    original = HostedRoomAttachmentStore.__init__

    def clocked(self, db_path, **kwargs):
        kwargs["clock"] = lambda: now[0]
        original(self, db_path, **kwargs)
    monkeypatch.setattr(HostedRoomAttachmentStore, "__init__", clocked)
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        runner._handle_message = sharing([write_file(tmp_path)], "(pass)")
        turn = await run_turn(authority, service, publish=False)
        # The gateway stops before publishing and comes back a day later: the held file is kept.
        now[0] += 24 * 60 * 60
        HostedRoomAttachmentStore(service.db_path).prune()
        service.prepare_room(turn.binding)
        message, = events(service, "message.member")
        assert message["payload"]["text"] == "Shared report.txt."
        assert [a["name"] for a in message["payload"]["attachments"]] == ["report.txt"]


@pytest.mark.asyncio
async def test_a_disbanded_rooms_held_files_are_reclaimed(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        runner._handle_message = sharing([write_file(tmp_path)], "Done.")
        await run_turn(authority, service, publish=False)
        gateway_id, epoch = service._owned_authority("room")
        hosted_rooms.disband_room(service.db_path, room_id="room", expected_gateway_id=gateway_id,
                                  expected_epoch=epoch)
        store = HostedRoomAttachmentStore(service.db_path)
        store.prune()
        row, = staged_rows(service)
        assert row["state"] == "disbanded" and row["expires_at"] is not None
        store.prune(now=row["expires_at"])
        assert staged_rows(service) == []


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


@pytest.mark.asyncio
async def test_a_file_keeps_its_requested_name_wherever_its_bytes_are_read(tmp_path, monkeypatch):
    """Seam: gateway.media_fetch._active_remote_env returns a fake remote terminal environment
    over a local directory; fetch_remote_media itself runs unchanged."""
    import shutil
    from gateway import media_fetch
    sandbox = tmp_path / "sandbox"
    (sandbox / "workspace").mkdir(parents=True)
    (sandbox / "workspace" / "report.txt").write_bytes(b"remote result\n")

    class RemoteEnv:
        _remote_home = "/home/agent"

        def fetch_realpath(self, path):
            return path

        def fetch_file(self, path, dest, max_bytes):
            shutil.copyfile(sandbox / path.lstrip("/"), dest)
    monkeypatch.setattr(media_fetch, "_active_remote_env", lambda: RemoteEnv())
    target = write_file(tmp_path, "v3.txt", b"third draft\n")
    (target.parent / "latest.txt").symlink_to(target)
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        results = []

        async def handle(event):
            # Each fetch reads the remote file into a fresh cache copy; the share is still one file.
            for path in ("/workspace/report.txt", "/workspace/report.txt", target.parent / "latest.txt"):
                results.append(await asyncio.to_thread(share, path))
            return "Done."
        runner._handle_message = handle
        await run_turn(authority, service)
        assert [result["name"] for result in results] == ["report.txt", "report.txt", "latest.txt"]
        message, = events(service, "message.member")
        assert [a["name"] for a in message["payload"]["attachments"]] == ["report.txt", "latest.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pad", ["", " ", "\t", "\n "])
async def test_a_client_upload_cannot_pose_as_a_bots_shared_file(tmp_path, monkeypatch, pad):
    from types import SimpleNamespace
    from gateway.hosted_room_attachments import AttachmentError
    from gateway.hosted_room_outputs import current_turn, output_prefix
    from gateway.session_hosted_attachments import upload
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        actor = SimpleNamespace(profile_id=service.authority.profile_id, capabilities={"session:submit"},
                                subject="alice")
        refused = []

        async def handle(event):
            # The room owner uploads under the running attempt's own staging id, padded so that
            # only the store's normalization would turn it into that id.
            turn = current_turn()
            params = {"room_id": "room", "upload_id": pad + output_prefix(turn.task_id, turn.execution_generation) + "0",
                      "kind": "file", "name": "forged.txt", "mime": "text/plain",
                      "data_base64": base64.b64encode(b"x").decode()}
            try:
                await asyncio.to_thread(upload, service, actor, params)
            except AttachmentError as exc:
                refused.append(str(exc))
            return "My own reply."
        runner._handle_message = handle
        await run_turn(authority, service)
        assert refused and "reserved for files a Bot shares" in refused[0]
        message, = events(service, "message.member")
        assert "attachments" not in message["payload"]
        assert staged_rows(service) == []


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


@pytest.mark.asyncio
async def test_only_turns_that_can_share_files_are_told_how(tmp_path, monkeypatch):
    from gateway import hosted_room_discussion as discussion
    rule = "call share_group_file"
    async with owner(tmp_path, monkeypatch) as (_authority, service, _runner):
        service.send(room_id="room", event_id="ask", payload={"thread_id": "t", "text": "@writer Draft it"})
        task, = tasks.list_tasks(service.db_path, room_id="room", status="queued")
        assert rule in task["payload"]["prompt"]  # the canonical owner binds file sharing
        room = service._room("room")
        snapshot = service._policy_snapshot(room)
        # A room server that cannot bind it (the standalone server) plans without the rule.
        plain = discussion.plan_next_task(room, snapshot.events, local_profiles=service.room_profiles(room),
                                          initial_watermarks=snapshot.watermarks, freeze_input_context=True)
        assert rule not in plain.task.payload["prompt"]


def _blob(service, row):
    return HostedRoomAttachmentStore(service.db_path).blob_root / row["blob_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["deleted", "overwritten"])
@pytest.mark.parametrize("reply, published", [
    ("Here is the report.", "Here is the report.\n\nCould not share report.txt."),
    ("(pass)", "Could not share report.txt.")])
async def test_a_file_whose_bytes_are_gone_is_named_and_the_reply_still_published(
        tmp_path, monkeypatch, damage, reply, published):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        runner._handle_message = sharing([write_file(tmp_path)], reply)
        turn = await run_turn(authority, service, publish=False)
        row, = staged_rows(service)
        if damage == "deleted":
            _blob(service, row).unlink()
        else:
            _blob(service, row).write_bytes(b"X" * row["size"])
        # Publishing reads no byte, so the room is not stalled: the reply goes out and names the file.
        service.prepare_room(turn.binding)
        message, = events(service, "message.member")
        assert message["payload"]["text"] == published and "attachments" not in message["payload"]
        assert events(service, "turn.settled")
        row, = staged_rows(service)
        assert (row["state"], row["expires_at"] is not None) == ("uploaded", True)  # reclaimable


@pytest.mark.asyncio
async def test_finishing_a_publication_repeats_the_reply_already_in_the_log(tmp_path, monkeypatch):
    """Seam: hosted_rooms.append_event refuses the first turn.settled append, as when another
    event lands between the reply and its terminal event."""
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        runner._handle_message = sharing(
            [write_file(tmp_path, "kept.txt", b"kept\n"), write_file(tmp_path, "lost.txt", b"lost\n")], "Two files.")
        turn = await run_turn(authority, service, publish=False)
        lost = next(row for row in staged_rows(service) if row["name"] == "lost.txt")
        _blob(service, lost).unlink()
        append, refused = hosted_rooms.append_event, []

        def once(db_path, **kwargs):
            if kwargs["kind"] == "turn.settled" and not refused:
                refused.append(kwargs["event_id"])
                raise hosted_rooms.EventCursorConflictError("room changed before event publication")
            return append(db_path, **kwargs)
        monkeypatch.setattr(hosted_rooms, "append_event", once)
        service.prepare_room(turn.binding)
        first, = events(service, "message.member")
        assert not events(service, "turn.settled")
        # The bytes come back before the retry: the logged reply is still the outcome.
        _blob(service, lost).write_bytes(b"lost\n")
        service.prepare_room(turn.binding)
        message, = events(service, "message.member")
        assert message == first and events(service, "turn.settled")
        assert message["payload"]["text"] == "Two files.\n\nCould not share lost.txt."
        assert [a["name"] for a in message["payload"]["attachments"]] == ["kept.txt"]
        assert sorted((row["name"], row["state"]) for row in staged_rows(service)) == [
            ("kept.txt", "committed"), ("lost.txt", "uploaded")]


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [["@reviewer", "extra.txt"], ["notes@reviewer(1).txt"], ["notes@all(1).txt"]])
async def test_the_sentence_for_a_pass_that_shares_files_summons_nobody(tmp_path, monkeypatch, names):
    from gateway import hosted_room_discussion as discussion
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        runner._handle_message = sharing(
            [write_file(tmp_path, name, b"%d\n" % index) for index, name in enumerate(names)], "(pass)")
        turn = await run_turn(authority, service, text="@writer Only you")
        message, = events(service, "message.member")
        assert message["payload"]["text"] == "Shared " + ", ".join(n.replace("@", "@\u2060") for n in names) + "."
        assert [a["name"] for a in message["payload"]["attachments"]] == names
        assert not discussion.is_pass_text(message["payload"]["text"])
        # The file names wake no other Bot: the round settles.
        service.prepare_room(turn.binding)
        assert not tasks.list_tasks(service.db_path, room_id="room", status="queued")


def _place(directory, name, data):
    """Write ``data`` at exactly ``directory / name``."""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.mark.asyncio
async def test_secrets_and_private_hermes_files_are_never_shared(tmp_path, monkeypatch):
    import os
    work = tmp_path / "work"
    vault_key = _place(tmp_path / "vault", "vault.key", b"VAULT-KEY\n")
    secrets = [vault_key, _place(tmp_path / "vault", "vault.json.enc", b"ciphertext\n"),
               _place(work / "project", ".env", b"AWS_SECRET_ACCESS_KEY=1\n"),
               _place(work / "project", ".envrc", b"export TOKEN=1\n"),
               _place(tmp_path / "skills" / ".hub", "injected.md", b"# injected\n"),
               _place(tmp_path, ".env", b"OPENAI_API_KEY=1\n")]
    (work / "notes.txt").symlink_to(vault_key)
    os.link(vault_key, work / "key-copy.txt")
    results = []
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        # Another profile's stores, which main's read guard does not cover.
        other = tmp_path / "profiles" / "reviewer"
        secrets += [_place(other / "vault", "vault.key", b"OTHER-KEY\n"),
                    _place(other, "auth.json", b"{}\n"),
                    _place(other / "mcp-tokens", "token.json", b"{}\n"),
                    _place(other / "skills" / ".hub", "x.md", b"# x\n")]

        async def handle(event):
            for path in (*secrets, work / "notes.txt", work / "key-copy.txt"):
                results.append(await asyncio.to_thread(share, path))
            results.append(await asyncio.to_thread(share, work / "project" / ".env", name="notes.txt"))
            results.append(await asyncio.to_thread(share, write_file(tmp_path, "fine.txt", b"fine\n")))
            return "Done."
        runner._handle_message = handle
        await run_turn(authority, service)
        *refused, control = results
        assert [result.get("error") for result in refused] == [
            "That file cannot be shared. Share a file from your workspace or a Hermes media folder."] * len(refused)
        assert control["shared"] is True
        message, = events(service, "message.member")
        assert [a["name"] for a in message["payload"]["attachments"]] == ["fine.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
async def test_the_room_file_store_is_never_reshared_by_another_path(tmp_path, monkeypatch, linked):
    import os
    real = tmp_path / "elsewhere" / "store"
    if linked:  # the store moved to another disk behind a symlink
        real.mkdir(parents=True)
        (tmp_path / "hosted-room-attachments").symlink_to(real, target_is_directory=True)
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        store = HostedRoomAttachmentStore(service.db_path)
        item = store.put(room_id="room", upload_id="pending", kind="file", name="private.txt",
                         mime="text/plain", data=b"UNPUBLISHED\n")
        with sqlite3.connect(service.db_path) as conn:
            blob_id, = conn.execute("SELECT blob_id FROM hosted_room_attachments WHERE attachment_id=?",
                                    (item["attachment_id"],)).fetchone()
        paths = [store.blob_root / blob_id, (tmp_path / "hosted-room-attachments" / "blobs" / blob_id).resolve()]
        os.link(paths[1], tmp_path / "hard-link.txt")
        paths.append(tmp_path / "hard-link.txt")
        results = []
        runner._handle_message = sharing(paths, "Done.", shared=results)
        await run_turn(authority, service)
        assert all("error" in result for result in results)
        assert "attachments" not in events(service, "message.member")[0]["payload"]


@pytest.mark.asyncio
async def test_a_sandbox_secret_is_refused_by_its_name_there(tmp_path, monkeypatch):
    """Seam: gateway.media_fetch._active_remote_env, a fake remote terminal over a local directory;
    fetch_remote_media itself runs unchanged."""
    import shutil
    from gateway import media_fetch
    sandbox = tmp_path / "sandbox"
    for name in (".env", "readme.txt"):
        _place(sandbox / "workspace", name, b"remote %s\n" % name.encode())
    _place(sandbox / "home" / "agent" / ".hermes" / "vault", "vault.key", b"remote key\n")
    fetched = []

    class RemoteEnv:
        _remote_home = "/home/agent"

        def fetch_realpath(self, path):
            return "/workspace/.env" if path == "/workspace/notes.txt" else path

        def fetch_file(self, path, dest, max_bytes):
            fetched.append(path)
            shutil.copyfile(sandbox / path.lstrip("/"), dest)
    monkeypatch.setattr(media_fetch, "_active_remote_env", lambda: RemoteEnv())
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        results = []
        runner._handle_message = sharing(
            ["/workspace/.env", "/workspace/notes.txt", "/home/agent/.hermes/vault/vault.key",
             "/workspace/readme.txt"], "Done.", shared=results)
        await run_turn(authority, service)
        assert [result.get("shared", False) for result in results] == [False, False, False, True]
        # A requested secret is refused before any byte is fetched; a link to one, once its target is known.
        assert fetched == ["/workspace/.env", "/workspace/readme.txt"]
