"""A Bot on another profile of this host shares files through the private owner transport."""
import asyncio
import base64
import hashlib
import threading
from dataclasses import asdict
from pathlib import Path

import pytest

from gateway import hosted_room_driver as tasks
from gateway.hosted_room_outputs import HostedTurn, _PUSH_CHUNK_BYTES, attempt_outputs, output_prefix, stage
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_session_hosted_transport import _server, _SourceTask


def _chunks(source, data, name="report.txt", **overrides):
    digest = hashlib.sha256(data).hexdigest()
    params = {**source.params, "name": name, "size": len(data), "sha256": digest, **overrides}
    params.pop("prompt", None)
    params.pop("attachments", None)
    for offset in range(0, len(data), _PUSH_CHUNK_BYTES):
        yield {**params, "offset": offset,
               "data_base64": base64.b64encode(data[offset:offset + _PUSH_CHUNK_BYTES]).decode("ascii")}


def _push(source, data, **kwargs):
    result = None
    for chunk in _chunks(source, data, **kwargs):
        result = source.service.attest(source.selector, "output", chunk)
    return result


def test_the_owner_stages_a_pushed_file_for_the_exact_running_attempt(tmp_path, monkeypatch):
    source = _SourceTask(tmp_path, monkeypatch, b"input")
    with source.db:
        data = b"named profile result\n" * 5000
        result = _push(source, data)
        stored = result["stored"]
        assert (stored["name"], stored["size"], stored["sha256"]) == (
            "report.txt", len(data), hashlib.sha256(data).hexdigest())
        assert result["owner"] == "alice"
        # Publication finds it like a file the owner's own Bot staged.
        outputs = attempt_outputs(source.service, source.service._room("room"), source.task)
        assert [item["attachment_id"] for item in outputs] == [stored["attachment_id"]]
        # The same file again is the same staged file.
        assert _push(source, data)["stored"]["attachment_id"] == stored["attachment_id"]
        assert not list((Path(tmp_path) / "hosted-room-attachments" / "incoming").glob("*.part"))


def test_pushed_chunks_are_fenced_to_the_running_attempt_and_their_order(tmp_path, monkeypatch):
    source = _SourceTask(tmp_path, monkeypatch, b"input")
    with source.db:
        data = b"x" * (_PUSH_CHUNK_BYTES + 10)
        first, second = list(_chunks(source, data))
        for bad in ({"execution_generation": 99}, {"task": {**first["task"], "task_id": "other"}},
                    {"_target_home": str(tmp_path / "elsewhere")}):
            with pytest.raises(RuntimeStoreError, match="permission_denied"):
                source.service.attest(source.selector, "output", {**first, **bad})
        with pytest.raises(RuntimeStoreError, match="invalid_params"):
            source.service.attest(source.selector, "output", second)  # out of order
        assert source.service.attest(source.selector, "output", first) == {
            "owner": "alice", "target_home": source.homes["other"], "received": _PUSH_CHUNK_BYTES}
        with pytest.raises(RuntimeStoreError, match="invalid_params"):
            source.service.attest(source.selector, "output", {**second, "sha256": "0" * 64})
        # Stop fences the attempt: a later chunk is refused before any byte is written.
        tasks.begin_task_cancel(source.db.db_path, source.task["identity"], cancel_id="stop",
                                expected_cancel_generation=0, clock=lambda: 1.0)
        with pytest.raises(RuntimeStoreError, match="permission_denied"):
            source.service.attest(source.selector, "output", second)
        assert attempt_outputs(source.service, source.service._room("room"), source.task) == []


def test_a_refused_file_reports_why_before_its_bytes_move(tmp_path, monkeypatch):
    source = _SourceTask(tmp_path, monkeypatch, b"input")
    with source.db:
        for index in range(8):
            assert "stored" in _push(source, b"%d\n" % index, name=f"f{index}.txt")
        first = next(_chunks(source, b"ninth\n", name="f8.txt"))
        assert source.service.attest(source.selector, "output", first)["refused"] == (
            "A turn can share at most 8 files.")
        assert "refused" in _push(source, b"not a png", name="picture.png")


def test_a_named_profile_turn_pushes_its_file_over_the_owner_socket(tmp_path, monkeypatch):
    from gateway.session_hosted_transport import install_hosted_transport
    source = _SourceTask(tmp_path, monkeypatch, b"input")
    with source.db:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever)
        thread.start()
        server = _server(tmp_path)
        sizes = []
        handle = server.handle_request_line

        def measured(raw, *args):
            sizes.append(len(raw) + 1)
            return handle(raw, *args)
        server.handle_request_line = measured
        install_hosted_transport(server, source.authority, loop, attest=source.service.attest)
        assert asyncio.run_coroutine_threadsafe(server.start(), loop).result()
        try:
            identity = source.task["identity"]
            turn = HostedTurn(identity.room_id, identity.task_id, source.task["execution_generation"],
                              tmp_path / "profiles" / "other" / "state.db", task=asdict(identity),
                              transport=source.binding)
            data = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20000
            stored = stage(turn, name="chart.png", data=data)
            assert (stored["kind"], stored["mime"], stored["size"]) == ("image", "image/png", len(data))
            chunk_requests = [size for size in sizes if size > 1024]  # besides each request's identify probe
            assert len(chunk_requests) == -(-len(data) // _PUSH_CHUNK_BYTES) and max(sizes) < 65536
            row, = source.store.staged_uploads(
                room_id="room", upload_prefix=output_prefix(identity.task_id, source.task["execution_generation"]))
            assert row["attachment_id"] == stored["attachment_id"]
            # The target never opened the owner's database or a path of its own store.
            assert not (tmp_path / "profiles" / "other" / "hosted-room-attachments").exists()
        finally:
            asyncio.run_coroutine_threadsafe(server.stop(), loop).result()
            loop.call_soon_threadsafe(loop.stop)
            thread.join()
            loop.close()


def test_a_member_turn_binds_its_owner_transport_when_it_has_one(tmp_path):
    import json
    from types import SimpleNamespace
    from gateway.hosted_room_outputs import hosted_turn_for
    from gateway.session_hosted_transport import _BINDING
    from hermes_state import SessionDB
    with SessionDB(tmp_path / "state.db") as db:
        authority = SimpleNamespace(db=db)
        identity = {"room_id": "room", "task_id": "dtask:1", "thread_id": "t", "turn_id": "u"}
        row = {"request_id": "hosted:" + json.dumps([identity, 2], sort_keys=True, separators=(",", ":"))}
        local = hosted_turn_for(authority, SimpleNamespace(session_id="local"), row)
        assert (local.room_id, local.task_id, local.execution_generation, local.transport) == (
            "room", "dtask:1", 2, None)
        binding = {"source_home": "/owner", "selector": {"room_id": "room", "member_id": "two", "profile": "other"},
                   "target_home": "/other", "owner": "alice"}
        db._execute_write(lambda conn: conn.execute(
            "INSERT INTO state_meta(key, value) VALUES (?, ?)", (_BINDING + "named", json.dumps(binding))))
        assert hosted_turn_for(authority, SimpleNamespace(session_id="named"), row).transport == binding
        assert hosted_turn_for(authority, SimpleNamespace(session_id="x"), {"request_id": "native:1"}) is None
