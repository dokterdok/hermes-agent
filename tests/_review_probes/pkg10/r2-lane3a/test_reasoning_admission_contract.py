"""Review verification: reasoning rides the durable admission and its exact retry."""
from types import SimpleNamespace

import pytest

from tests.gateway.test_session_mutation_model_busy import _owner
from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_reasoning_survives_queued_owner_restart_and_exact_retry(tmp_path, monkeypatch):
    from gateway.session_cli_turn import one_turn_reasoning
    from hermes_state_runtime import list_session_admissions

    seen = []
    async def handle(event):
        turn = SimpleNamespace(_ctx=SimpleNamespace(inbound_message_id=event.message_id))
        seen.append((event.text, one_turn_reasoning(turn, {"enabled": True, "effort": "low"})))
        return "ok"

    monkeypatch.chdir(tmp_path)
    store, db, authority, owner, ref = _owner(tmp_path, monkeypatch, handle)
    monkeypatch.setattr(authority, "_schedule", lambda *_: None)
    params = {"session_id": ref.session_id, "submission_id": "once", "text": "high once",
              "model_override": {"model": "other", "provider": "custom", "reasoning": "high"}}
    await owner.dispatch({"id": 0, "method": "session.resume", "params": {"session_id": ref.session_id}})
    reply = await owner.dispatch({"id": 1, "method": "prompt.submit", "params": params})
    assert reply.get("result", {}).get("status") == "queued", reply
    first_id = reply["result"]["admission_id"]
    await owner.close()
    store.close_all_db_handles()

    store2, db2, authority2, owner2, ref2 = _owner(tmp_path, monkeypatch, handle)
    try:
        await owner2.dispatch({"id": 0, "method": "session.resume", "params": {"session_id": ref2.session_id}})
        assert ref2.session_id == ref.session_id
        rows = list_session_admissions(db2, session_id=ref.session_id)
        assert rows[0]["payload"]["cli_turn_v1"]["model_override"]["reasoning"] == "high"
        retry = await owner2.dispatch({"id": 2, "method": "prompt.submit", "params": params})
        assert retry.get("result", {}).get("admission_id") == first_id, retry
        await async_wait_until(lambda: len(seen) == 1, timeout=10, what="recovered admission executes")
        task = authority2.sessions[ref2.session_id].task
        if task:
            await task
        changed = {**params, "model_override": {**params["model_override"], "reasoning": "none"}}
        conflict = await owner2.dispatch({"id": 3, "method": "prompt.submit", "params": changed})
        assert conflict.get("error", {}).get("message") == "admission_conflict", conflict
        for submission_id, extra in (("disabled", {"model_override": {"model": "other", "reasoning": "none"}}),
                                     ("plain", {})):
            next_reply = await owner2.dispatch({"id": 4, "method": "prompt.submit", "params": {
                "session_id": ref2.session_id, "submission_id": submission_id, "text": submission_id, **extra}})
            assert "result" in next_reply, next_reply
            await authority2.sessions[ref2.session_id].task
        assert seen == [("high once", {"enabled": True, "effort": "high"}),
                        ("disabled", {"enabled": False}), ("plain", {"enabled": True, "effort": "low"})]
    finally:
        await owner2.close()
        store2.close_all_db_handles()

