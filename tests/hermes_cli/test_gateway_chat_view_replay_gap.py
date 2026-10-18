"""GatewayChatView (`chat -q`, stream-json, `-z`, the thin chat) across a ``session.replay_gap``: it re-attaches;
a one-shot prints the reply once; stream-json emits no ``text`` after the gap and its ``result`` carries the
whole reply with ``replay_gap: true``. Adoption wakes a waiting one-shot; a control blocks only an admission
the snapshot still lists; an ack received mid-resume settles against that snapshot; ``unknown`` settles."""
import asyncio
import json

import pytest

from tests.gateway.fixtures.replay_gap_owner import FINAL, Owner, frame_cap  # frame_cap: autouse
from tests.waiting import async_wait_until as until

GAP = {"method": "event", "params": {"type": "session.replay_gap", "session_id": "s", "payload": {}}}


async def real_oneshot(tmp_path, mode, **view_kwargs):
    from hermes_cli.gateway_chat_view import GatewayChatView
    owner = Owner(tmp_path / "state.db", pause=mode)
    try:
        view = GatewayChatView(owner, await owner.conn.resume(owner.ref, {}), **view_kwargs)
        code = await asyncio.wait_for(view.run("write a long answer", oneshot=True), 30)
    finally:
        await asyncio.to_thread(owner.finish)
    assert owner.gaps, "probe did not overflow the fanout (vacuous)"
    return code


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ended", "live"])
async def test_stream_json_result_is_authoritative_and_no_text_follows_the_gap(tmp_path, capsys, mode):
    from hermes_cli.stream_json import StreamJsonEmitter
    assert await real_oneshot(tmp_path, mode, emitter=StreamJsonEmitter(model="m", session_id="s")) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    text = "".join(r.get("text", "") for r in records if r["type"] == "text")
    assert FINAL.startswith(text) and len(text) < len(FINAL), "text events after the gap (a hole or a splice)"
    assert records[-1]["type"] == "result" and records[-1]["text"] == FINAL and records[-1]["replay_gap"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ended", "live"])
async def test_quiet_oneshot_prints_the_answer_once(tmp_path, capsys, mode):
    assert await real_oneshot(tmp_path, mode, quiet=True) == 0
    assert capsys.readouterr().out.count(FINAL) == 1


class Peer:
    def __init__(self, answer):
        self.events, self.answer, self.acked = asyncio.Queue(), answer, False

    async def rpc(self, method, **params):
        result = await self.answer(method, params)
        self.acked |= method == "prompt.submit"
        return result


async def gapped(answer, run):
    """``run(view)`` on a view of ``s`` whose admission ``a`` is acked, then cut by a gap."""
    from hermes_cli.gateway_chat_view import GatewayChatView
    view = GatewayChatView(Peer(answer), {"stored_session_id": "s"})
    task = asyncio.create_task(run(view))
    try:
        await until(lambda: view.finite_admission or (view.client.acked and not view.finite) or task.done())
        view.client.events.put_nowait(GAP)
        return view, await asyncio.wait_for(asyncio.shield(task), 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("own, code", [("queued", 3), (None, 0)])
async def test_adopted_approval_ends_the_oneshot_only_while_the_snapshot_lists_it(own, code):
    """Queued behind another turn's approval: adoption wakes the one-shot, which detaches (3). Ended in the hole
    while a successor waits for approval: its own receipt settles it (0)."""
    async def answer(method, params):
        if method == "prompt.submit":
            return {"admission_id": "a", "status": "queued"}
        if method == "session.resume":
            return {"pending": [{"admission_id": "b", "status": "started"}, *([{"admission_id": "a", "status": own}]
                                                                             if own else [])],
                    "prompts": [{"prompt_id": "p", "kind": "approval", "execution_generation": 8}]}
        return {"admission_id": "a", "status": "terminal", "outcome": "completed",
                "result": {"final_response": "DONE", "completed": True}}
    view, result = await gapped(answer, lambda view: view.run("q", oneshot=True))
    assert result == code and (own or view.completions["a"]["text"] == "DONE")


@pytest.mark.asyncio
async def test_unknown_row_settles_the_interactive_turn(capsys):
    async def answer(method, params):
        if method == "prompt.submit":
            return {"admission_id": "a", "status": "queued"}
        return {"pending": [{"admission_id": "a", "status": "unknown"}], "prompts": []}

    async def interactive(view):
        renderer = asyncio.create_task(view.render())
        await view.submit("q")
        await until(lambda: "a" in view.completions or view.failure is not None)
        renderer.cancel()
        return view.failure
    view, failure = await gapped(answer, interactive)
    assert failure is None and view.completions["a"]["outcome"] == "unknown"
    assert "outcome is unknown" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ack_during_the_reattach_settles_against_its_snapshot(capsys):
    from hermes_cli.gateway_chat_view import GatewayChatView
    entered, release = asyncio.Event(), asyncio.Event()

    def event(kind, payload):
        return {"method": "event", "params": {"type": kind, "session_id": "s", "admission_id": "a", "payload": payload}}

    async def answer(method, params):
        if method == "prompt.submit":
            peer.events.put_nowait(GAP)
            await entered.wait()
            return {"admission_id": "a"}
        entered.set()  # the snapshot is captured while a runs; its completion follows live
        peer.events.put_nowait(event("message.delta", {"text": "Hello"}))
        peer.events.put_nowait(event("message.complete", {"text": "Hello", "outcome": "completed"}))
        await release.wait()
        return {"pending": [{"admission_id": "a", "status": "started"}], "prompts": []}

    peer = Peer(answer)
    view = GatewayChatView(peer, {"stored_session_id": "s"})
    renderer = asyncio.create_task(view.render())
    try:
        submitted = asyncio.create_task(view.submit("q"))
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        await asyncio.wait_for(submitted, 5)
        await until(lambda: peer.events.empty() and "a" in view.completions)
    finally:
        renderer.cancel()
        await asyncio.gather(renderer, return_exceptions=True)
    assert capsys.readouterr().out == "Hello\n"


@pytest.mark.asyncio
async def test_an_answer_past_4_mib_that_ended_in_the_gap_settles_from_one_read(tmp_path, capsys, monkeypatch):
    """The turn ends in the hole with a 5 MiB answer. Its saved result repeats the answer in the turn's
    transcript rows, and both copies outgrow the client's frame limit: the recovery read takes it once, and
    so does ``/usage`` reading the turn's ledger afterwards."""
    from unittest.mock import AsyncMock
    from gateway.session_results import finish_result
    from hermes_cli import gateway_chat_commands
    from hermes_cli.gateway_chat_view import GatewayChatView
    from hermes_cli.gateway_client import MAX_FRAME_BYTES, GatewayClientError
    from hermes_state_runtime import admit_session_input, claim_session_input
    answer = "word " * (1024 * 1024)

    class Hole(Owner):
        async def rpc(self, method, _timeout=None, **params):
            if method == "prompt.submit":
                admit_session_input(self.db, epoch=self.epoch, principal_id=self.conn.actor.subject, session_id="s",
                                    request_id=params["input_id"], payload={"text": params["text"]})
                row = claim_session_input(self.db, epoch=self.epoch, session_id="s")
                finish_result(self.db, epoch=self.epoch, row=row, response=answer, outcome="completed", result={
                    "result": {"final_response": answer, "completed": True, "input_tokens": 21,
                               "current_turn_user_idx": 0, "messages": [
                        {"role": "user", "content": params["text"]}, {"role": "assistant", "content": answer}]},
                    "usage": {}})
                self.events.put_nowait(GAP)
                return {"admission_id": row["admission_id"], "status": "queued"}
            if method == "prompt.receipt":  # the wire contract and handler, then the client's frame limit
                reply = await self.conn.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                if len(json.dumps(reply)) > MAX_FRAME_BYTES:
                    raise GatewayClientError("Gateway disconnected")  # the socket closes with 1009
                return reply["result"]
            return await super().rpc(method, _timeout=_timeout, **params)

    owner = Hole(tmp_path / "state.db")
    try:
        view = GatewayChatView(owner, await owner.conn.resume(owner.ref, {}), quiet=True)
        assert await asyncio.wait_for(view.run("write a long answer", oneshot=True), 30) == 0
        assert capsys.readouterr().out.count(answer) == 1
        monkeypatch.setattr(gateway_chat_commands, "forward", AsyncMock())  # the owner's own report
        assert await view.command("/usage") is True
        assert "Input tokens: 21" in capsys.readouterr().out
    finally:
        await asyncio.to_thread(owner.finish)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["gap", "live"])
async def test_an_owner_from_before_omit_messages_still_gives_a_oneshot_its_result(tmp_path, monkeypatch, path):
    """An owner running the previous build refuses ``omit_messages`` by name. The one-shot asks again
    without it, so the turn's committed result (a partial one: exit 2) still decides the exit code, after a
    gap (the recovery read) and live (the ledger read)."""
    from dataclasses import replace
    from gateway.session_results import finish_result
    from hermes_cli.gateway_chat_view import GatewayChatView
    from hermes_cli.gateway_client import rpc_error
    from hermes_state_runtime import admit_session_input, claim_session_input
    from tui_gateway.contracts.base import Params
    from tui_gateway.contracts.registry import CANONICAL_METHODS

    class PreviousReceiptParams(Params):
        session_id: str
        admission_id: str
        include_result: bool | None = None
    monkeypatch.setitem(CANONICAL_METHODS, "prompt.receipt", replace(
        CANONICAL_METHODS["prompt.receipt"], params=PreviousReceiptParams))

    class PreviousOwner(Owner):
        async def rpc(self, method, _timeout=None, **params):
            if method == "prompt.submit":
                admit_session_input(self.db, epoch=self.epoch, principal_id=self.conn.actor.subject, session_id="s",
                                    request_id=params["input_id"], payload={"text": params["text"]})
                row = claim_session_input(self.db, epoch=self.epoch, session_id="s")
                finish_result(self.db, epoch=self.epoch, row=row, response="partial", outcome="completed", result={
                    "result": {"final_response": "partial", "completed": False, "partial": True}, "usage": {}})
                self.events.put_nowait(GAP if path == "gap" else {"method": "event", "params": {
                    "type": "message.complete", "session_id": "s", "admission_id": row["admission_id"],
                    "payload": {"text": "partial", "outcome": "completed"}}})
                return {"admission_id": row["admission_id"], "status": "queued"}
            if method == "prompt.receipt":
                reply = await self.conn.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                if "error" in reply:
                    raise rpc_error(reply["error"])
                return reply["result"]
            return await super().rpc(method, _timeout=_timeout, **params)

    owner = PreviousOwner(tmp_path / "state.db")
    try:
        view = GatewayChatView(owner, await owner.conn.resume(owner.ref, {}), quiet=True)
        view.unattended = True
        assert await asyncio.wait_for(view.run("q", oneshot=True), 30) == 2
    finally:
        await asyncio.to_thread(owner.finish)
