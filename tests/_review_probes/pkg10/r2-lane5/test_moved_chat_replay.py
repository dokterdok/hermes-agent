"""Round-2 review: a moved Bot Chat keeps its delivered identity on durable replay."""
import pytest

from tests.gateway.test_peer_dm_bot_chat_headless import AUTHOR, _api_bot_chat, headless
from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_run_replay_keeps_delivered_chat_after_memory_status_is_evicted(headless):
    from gateway.config import Platform

    db = headless.authority.db
    old = _api_bot_chat(db)
    body = {'input': 'one', 'session_id': old, 'author': AUTHOR}
    headers = {'Idempotency-Key': 'review-moved-chat'}
    first = await headless.cli.post('/v1/runs', json=body, headers=headers)
    assert first.status == 202, await first.text()
    run_id = (await first.json())['run_id']
    adapter = headless.authority.runner.adapters[Platform.API_SERVER]
    await async_wait_until(lambda: adapter._run_statuses[run_id]['status'] == 'completed', timeout=10)
    delivered = db.get_session_by_title('Bot Chat')['id']
    assert old != delivered
    adapter._run_statuses.pop(run_id)

    replay = await headless.cli.post('/v1/runs', json=body, headers=headers)
    replay_body = await replay.json()
    assert replay.status == 202 and replay_body['replayed'] is True, replay_body
    assert replay_body['run_id'] == run_id
    status = await (await headless.cli.get(f'/v1/runs/{run_id}')).json()
    assert status['session_id'] == delivered and status['status'] == 'completed', status
    assert headless.executed == [(delivered, headless.executed[0][1], 'one')]
