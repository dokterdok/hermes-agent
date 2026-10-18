"""Review: an older reasoning command deferred on idle contention cannot undo a newer command."""
import pytest

from tests.gateway.test_session_mutation_model_busy import _owner


@pytest.mark.asyncio
async def test_newer_reasoning_wins_after_idle_fence_contention(tmp_path, monkeypatch):
    from gateway import session_mutations
    from gateway.session_policy import policy_for_source

    async def handle(event):
        return "ok"
    monkeypatch.chdir(tmp_path)
    store, db, authority, owner, ref = _owner(tmp_path, monkeypatch, handle)
    authority.runner._set_session_reasoning_override = lambda *a: None
    authority.runner._apply_reasoning_selection = lambda *a, **k: "ok"
    real, raced = session_mutations.mutate_session, []

    async def racing(authority_, actor, ref_, params, *a, **k):
        if params.get("operation") == "reasoning" and params["payload"]["effort"] == "high" and len(raced) < 3:
            # Another viewer saves a title after this command read its own revision.
            raced.append(params["expected_revision"])
            row = db.get_session(ref.session_id)
            await real(authority_, actor, ref_, {
                "session_id": ref.session_id, "request_id": "title-" + str(len(raced)),
                "expected_revision": row["runtime_revision"], "operation": "rename",
                "payload": {"title": "title-" + str(len(raced))}})
        return await real(authority_, actor, ref_, params, *a, **k)

    monkeypatch.setattr(session_mutations, "mutate_session", racing)
    try:
        high = await owner.dispatch({"id": 1, "method": "config.set", "params": {
            "session_id": ref.session_id, "key": "reasoning", "value": "high", "scope": "session"}})
        low = await owner.dispatch({"id": 2, "method": "config.set", "params": {
            "session_id": ref.session_id, "key": "reasoning", "value": "low", "scope": "session"}})
        assert low.get("result", {}).get("value") == "low", low
        task = authority.sessions[ref.session_id].task
        if task:
            await task
        policy = policy_for_source(authority.runner, authority.sessions[ref.session_id].source)
        assert (policy.reasoning_config or {}).get("effort") == "low", (high, low, policy.reasoning_config)
    finally:
        await owner.close()
        store.close_all_db_handles()
