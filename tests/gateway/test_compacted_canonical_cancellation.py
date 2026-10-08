"""Immutable canonical history can prove absence; an empty replacement index cannot."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway.platforms.api_server_run_history import certified_session
from hermes_state import SessionDB
from hermes_state_logical_attempts import prepare_logical_attempt_index
from tests.gateway.test_api_group_owner_stop import app_for
from tests.gateway.test_api_group_owner_stop_canonical import canonical as canonical
from tests.gateway.test_api_group_run_fence import HOME, SUCCESSOR, invite, promise, scoped, submit
from tests.gateway.test_api_room_inherited_cancellation import _cancel
from tests.gateway.test_compacted_source_cancellation import prune_inventory


@pytest.mark.asyncio
async def test_indexed_history_survives_compaction_and_preserves_certified_absence(canonical, tmp_path):
    adapter, authority, runner = canonical
    authority._schedule = lambda ref: None
    async def no_execution(event):
        pytest.fail('a queued, cancelled turn executed')
    runner._handle_message = no_execution
    async with TestClient(TestServer(app_for(adapter))) as client:
        old = await invite(client)
        original = scoped(old, task='indexed-task')
        accepted = await submit(client, old, original)
        assert accepted.status == 202, await accepted.json()
        store = adapter._run_idempotency_store
        assert store._conn.execute('SELECT canonical_history FROM run_idempotency').fetchone()[0]
        stopped = await _cancel(client, old, original)
        assert stopped.status == 200 and not (await stopped.json()).get('admission_cancelled')
        await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)
        promise(adapter)
        successor = await invite(client, SUCCESSOR, 2, previous={
            'home_install_id': HOME, 'authority_gateway_id': HOME, 'authority_epoch': 1})
        prune_inventory(store)
        assert store._conn.execute('SELECT COUNT(*) FROM run_idempotency').fetchone()[0] == 0
        assert store._conn.execute('SELECT COUNT(*) FROM group_run_scopes').fetchone()[0] == 0
        proof, = store._conn.execute('SELECT evidence FROM run_room_history').fetchone()
        assert prepare_logical_attempt_index(authority.db, epoch=authority.epoch)['complete']
        assert certified_session(authority.db, proof) is not None
        repeated = await _cancel(client, successor, scoped(successor, SUCCESSOR, 2, 'indexed-task'))
        assert repeated.status == 503 and not (await repeated.json()).get('admission_cancelled')
        absent = await _cancel(client, successor, scoped(successor, SUCCESSOR, 2, 'new-task'))
        assert absent.status == 200 and (await absent.json())['admission_cancelled']
        replay = await _cancel(client, successor, scoped(successor, SUCCESSOR, 2, 'new-task'))
        assert replay.status == 200 and (await replay.json())['admission_cancelled']
        with SessionDB(tmp_path / 'replacement.db') as empty:
            assert prepare_logical_attempt_index(empty)['complete']
            assert certified_session(empty, proof) is None
