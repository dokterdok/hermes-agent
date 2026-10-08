"""Compacted executed history must never become per-task non-admission proof."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_room_succession_authority_lineage import (
    net as net, invitation, claims_for, body_for, promise, winner_request)
from tests.gateway.test_api_server_room_cancellation import _headers
from tests.gateway.test_room_cancellation_retirement_http import app


def prune_inventory(store):
    store.reserve('ordinary-scope', 'old-terminal', 'fingerprint', 'ordinary-run', {'status': 'completed'})
    store._conn.execute("UPDATE run_idempotency SET retention_until=1,updated_at=0 WHERE run_id='ordinary-run'")
    store._conn.commit()
    assert store.lookup('ordinary-scope', 'old-terminal', 'fingerprint') == ('missing', None)


@pytest.mark.asyncio
@pytest.mark.parametrize('upgrade', [False, True])
@pytest.mark.parametrize('same_epoch', [False, True])
async def test_source_only_successor_stop_stays_unknown_after_compaction_and_scope_prune(net, monkeypatch, upgrade, same_epoch):
    gateways, adapter, ctx = net
    agent = MagicMock()
    agent.run_conversation.return_value = {'final_response': 'already executed', 'messages': []}
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    monkeypatch.setattr(adapter, '_create_agent', MagicMock(return_value=agent))
    with gateways['p'].acting():
        old = invitation(adapter, gateways['h'].install_id)
    if same_epoch:
        old = promise(gateways, ctx)
    with gateways['p'].acting():
        old_claims = claims_for(adapter, old)
        async with TestClient(TestServer(app(adapter))) as cli:
            original = body_for(old_claims, old['catalog'], 'executed')
            accepted = await cli.post('/v1/runs', json=original,
                headers={**_headers(old['grant']), 'Idempotency-Key': 'room:executed:1'})
            assert accepted.status == 202, await accepted.json()
            await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)
            assert agent.run_conversation.call_count == 1
            stopped = await cli.post('/v1/runs/stop', json=original,
                headers={**_headers(old['grant']), 'Idempotency-Key': 'room:executed:1'})
            stopped_body = await stopped.json()
            assert stopped.status == 200 and not stopped_body.get('admission_cancelled'), stopped_body
    if same_epoch:
        from gateway import hosted_room_succession_backup as backup
        private, request = winner_request(gateways)
        with gateways['p'].acting():
            reply = backup.answer_learn(ctx, request)
        successor = backup.open_sealed_reply(private, reply, request)['continuation_grants'][0]
    else:
        successor = promise(gateways, ctx)
    with gateways['p'].acting():
        store = adapter._run_idempotency_store
        assert store._conn.execute('SELECT COUNT(*) FROM run_idempotency').fetchone()[0] == 0
        claims = claims_for(adapter, successor)
        async with TestClient(TestServer(app(adapter))) as cli:
            body = body_for(claims, successor['catalog'], 'executed')
            headers = {**_headers(successor['grant']), 'Idempotency-Key': 'room:executed:1'}
            before = await cli.post('/v1/runs/stop', json=body, headers=headers)
            assert before.status == 503, await before.json()
            # Normal terminal pruning removes empty old scope inventory too.
            prune_inventory(store)
            assert store._conn.execute('SELECT COUNT(*) FROM group_run_scopes').fetchone()[0] == 0
            from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
            path = store.path
            store.close()
            store = adapter._run_idempotency_store = RunIdempotencyStore(str(path))
            if upgrade:
                from gateway.config import GatewayConfig
                from gateway.session import SessionStore
                from gateway.session_authority import SessionAuthority
                from hermes_state_runtime import begin_runtime_epoch
                from hermes_state_logical_attempts import prepare_logical_attempt_index
                db = adapter._ensure_session_db()
                runner = SimpleNamespace(_draining=False, session_store=SessionStore(
                    config=GatewayConfig(), sessions_dir=gateways['p'].home / 'sessions'))
                runner.session_authority = SessionAuthority(runner, profile_id='default', instance_id='upgrade',
                    db=db, epoch=begin_runtime_epoch(db, instance_id='upgrade'))
                runner._delivery_adapter_for = runner._intake_adapter_for = lambda source: adapter
                adapter.gateway_runner = runner
                runner.session_authority._schedule = lambda ref: None
                assert prepare_logical_attempt_index(db, epoch=runner.session_authority.epoch)['complete']
                with db._read_ctx() as conn:
                    assert conn.execute("SELECT COUNT(*) FROM logical_attempts WHERE principal_id='api'").fetchone()[0] == 0
            after = await cli.post('/v1/runs/stop', json=body, headers=headers)
            after_body = await after.json()
            assert after.status == 503 and not after_body.get('admission_cancelled'), after_body
            assert agent.run_conversation.call_count == 1
            assert store._conn.execute('SELECT COUNT(*) FROM run_room_history').fetchone()[0] == 1
            known = body_for(claims, successor['catalog'], 'known-current')
            known_headers = {**_headers(successor['grant']), 'Idempotency-Key': 'room:known-current:1'}
            accepted = await cli.post('/v1/runs', json=known, headers=known_headers)
            assert accepted.status == 202, await accepted.json()
            if not upgrade:
                await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)
            stopped = await cli.post('/v1/runs/stop', json=known, headers=known_headers)
            assert stopped.status == 200 and not (await stopped.json()).get('admission_cancelled')
            await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)


@pytest.mark.asyncio
async def test_only_negative_history_can_be_forgotten_without_poisoning_successor_absence(net, monkeypatch):
    gateways, adapter, ctx = net
    create = MagicMock(side_effect=AssertionError('a cancellation executed'))
    monkeypatch.setattr(adapter, '_create_agent', create)
    with gateways['p'].acting():
        old = invitation(adapter, gateways['h'].install_id)
        claims = claims_for(adapter, old)
        async with TestClient(TestServer(app(adapter))) as cli:
            for index in range(32):
                body = body_for(claims, old['catalog'], f'cancelled-{index}')
                response = await cli.post('/v1/runs/stop', json=body,
                    headers={**_headers(old['grant']), 'Idempotency-Key': f'room:cancelled-{index}:1'})
                assert response.status == 200 and (await response.json())['admission_cancelled']
    successor = promise(gateways, ctx)
    with gateways['p'].acting():
        store = adapter._run_idempotency_store
        assert store._conn.execute('SELECT COUNT(*) FROM run_idempotency').fetchone()[0] == 0
        assert store._conn.execute('SELECT COUNT(*) FROM run_room_history').fetchone()[0] == 0
        prune_inventory(store)
        claims = claims_for(adapter, successor)
        async with TestClient(TestServer(app(adapter))) as cli:
            body = body_for(claims, successor['catalog'], 'cancelled-0')
            response = await cli.post('/v1/runs/stop', json=body,
                headers={**_headers(successor['grant']), 'Idempotency-Key': 'room:cancelled-0:1'})
            assert response.status == 200 and (await response.json())['admission_cancelled']
        assert not create.called


def test_history_is_bounded_and_the_final_cancellation_writer_rechecks_it(tmp_path):
    from gateway.platforms.api_server_run_authority import room_authority, room_namespace, room_run_scope
    from gateway.platforms.api_server_run_idempotency import RunCancellationUnknown, RunIdempotencyStore
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    base = {'room_id': 'room', 'home_install_id': 'home', 'authority_gateway_id': 'home',
            'member_id': 'member', 'target_install_id': 'target', 'target_profile': 'default'}
    try:
        for epoch in range(1, 5):
            identity = {**base, 'authority_epoch': epoch}
            authority, scope = room_authority(identity), room_run_scope(identity)
            assert store.observe_room_authority(scope, authority, namespace=room_namespace(identity), claims=identity)
            for index in range(128):
                run_id = f'run-{epoch}-{index}'
                store.reserve(scope, f'room:task-{index}:1', 'executed', run_id,
                    {'status': 'completed', 'run_id': run_id}, identity=identity, room_authority=authority)
                store.request_stop(scope, run_id)
            newer = {**identity, 'authority_epoch': epoch + 1}
            store.observe_room_authority(room_run_scope(newer), room_authority(newer),
                namespace=room_namespace(newer), claims=newer)
            prune_inventory(store)
            assert store._conn.execute('SELECT COUNT(*) FROM run_idempotency').fetchone()[0] == 0
            assert store._conn.execute('SELECT evidence FROM run_room_history').fetchall() == [('unindexed',)]
        # Another lineage retains its own authentic, exact negative receipt.
        fresh = {**base, 'room_id': 'fresh-room', 'authority_epoch': 1}
        authority, scope = room_authority(fresh), room_run_scope(fresh)
        store.observe_room_authority(scope, authority, namespace=room_namespace(fresh), claims=fresh)
        store.reserve(scope, 'room:negative:1', '', 'negative', {'status': 'cancelled', 'admission_cancelled': True},
                      identity=fresh, room_authority=authority, cancel_if_missing=True)
        _, _, captured = store.cancellation_state(fresh, 'room:missing:1')
        store.reserve(scope, 'room:executed:1', 'executed', 'expired', {'status': 'completed'},
                      identity=fresh, room_authority=authority, retention_until=1)
        assert store.lookup(scope, 'room:executed:1', 'executed') == ('missing', None)
        with pytest.raises(RunCancellationUnknown):
            store.reserve(scope, 'room:missing:1', '', 'false-proof', {'status': 'cancelled', 'admission_cancelled': True},
                          identity=fresh, room_authority=authority, cancel_if_missing=True, cancellation_snapshot=captured)
        own, _, _ = store.cancellation_state(fresh, 'room:negative:1')
        assert own['status']['admission_cancelled']
        assert store._conn.execute('SELECT COUNT(*) FROM run_room_history').fetchone()[0] == 2
    finally:
        store.close()


def test_preexisting_authority_without_history_metadata_migrates_conservatively(tmp_path):
    from gateway.platforms.api_server_run_authority import room_authority, room_namespace, room_run_scope
    from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
    path = tmp_path / 'runs.db'
    identity = {'room_id': 'room', 'home_install_id': 'home', 'authority_gateway_id': 'home', 'authority_epoch': 2,
                'member_id': 'member', 'target_install_id': 'target', 'target_profile': 'default'}
    store = RunIdempotencyStore(str(path))
    store.observe_room_authority(room_run_scope(identity), room_authority(identity),
                                namespace=room_namespace(identity), claims=identity)
    store._conn.execute('DROP TABLE run_room_history')
    store._conn.commit()
    store.close()
    store = RunIdempotencyStore(str(path))
    try:
        own, prior, snapshot = store.cancellation_state(identity, 'room:lost-history:1')
        assert own is None and not prior
        assert snapshot[2] == 'unindexed'
        assert store.accepts_room_authority(room_authority(identity))
    finally:
        store.close()
