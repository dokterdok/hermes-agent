"""A learned winner must not call a retained loser's executed attempt absent."""
import asyncio
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_room_succession_backup as backup
from gateway import hosted_room_fence as fence
from gateway.platforms.api_server_run_idempotency import RunCancellationUnknown, RunIdempotencyStore
from gateway.platforms.api_server_run_scope import ROOM_RUN_SCOPE_FIELDS, room_run_scope_key
from tests.gateway.test_api_group_run_fence import reopen_runs, expire_observation
from tests.gateway.test_room_succession_authority_lineage import (
    net as net, invitation, claims_for, body_for, promise, winner_request)
from tests.gateway.test_api_server_room_cancellation import _headers
from tests.gateway.test_api_group_owner_stop import app_for as app


@pytest.fixture(params=['source', 'canonical'])
def execution(net, monkeypatch, request):
    gateways, adapter, _ = net
    calls = []
    if request.param == 'source':
        agent = MagicMock()
        def execute(*args, **kwargs):
            calls.append('executed')
            return {'final_response': 'executed on promised authority', 'messages': []}
        agent.run_conversation.side_effect = execute
        agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
        monkeypatch.setattr(adapter, '_create_agent', MagicMock(return_value=agent))
        yield calls
    else:
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore
        from gateway.session_authority import SessionAuthority
        from hermes_state_runtime import begin_runtime_epoch
        with gateways['p'].acting():
            db = adapter._ensure_session_db()
            runner = SimpleNamespace(_draining=False, session_store=SessionStore(
                config=GatewayConfig(), sessions_dir=gateways['p'].home / 'sessions'))
            runner.session_authority = SessionAuthority(runner, profile_id='default', instance_id='same-epoch',
                db=db, epoch=begin_runtime_epoch(db, instance_id='same-epoch'))
            runner._delivery_adapter_for = runner._intake_adapter_for = lambda source: adapter
            runner.agent = SimpleNamespace(interrupt=lambda: None)
            runner._cached_agent_for = lambda route: runner.agent
            async def execute(event):
                calls.append('executed')
                return 'executed on promised authority'
            runner._handle_message = execute
            adapter.gateway_runner = runner
        try:
            yield calls
        finally:
            db.close()


def identity(claims):
    return {key: claims[key] for key in ROOM_RUN_SCOPE_FIELDS}


@pytest.mark.asyncio
async def test_learned_same_epoch_winner_preserves_retained_attempt_and_controls(net, execution):
    gateways, adapter, ctx = net
    with gateways['p'].acting():
        invitation(adapter, gateways['h'].install_id)
    old = promise(gateways, ctx)
    with gateways['p'].acting():
        old_claims = claims_for(adapter, old)
        async with TestClient(TestServer(app(adapter))) as cli:
            body = body_for(old_claims, old['catalog'], 'same-epoch-executed')
            headers = {**_headers(old['grant']), 'Idempotency-Key': 'room:same-epoch-executed:1'}
            accepted = await cli.post('/v1/runs', json=body, headers=headers)
            assert accepted.status == 202, await accepted.json()
            original_id = (await accepted.json())['run_id']
            await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)
            assert len(execution) == 1
            if adapter.gateway_runner is not None:
                from hermes_state_logical_attempts import prepare_logical_attempt_index
                authority = adapter.gateway_runner.session_authority
                assert prepare_logical_attempt_index(authority.db, epoch=authority.epoch)['complete']
            stopped = await cli.post('/v1/runs/stop', json=body, headers=headers)
            stopped_body = await stopped.json()
            assert stopped.status == 200 and not stopped_body.get('admission_cancelled'), stopped_body
    private, request = winner_request(gateways)
    with gateways['p'].acting():
        reply = backup.answer_learn(ctx, request)
        winner = backup.open_sealed_reply(private, reply, request)['continuation_grants'][0]
        store = adapter._run_idempotency_store
        assert store._conn.execute('SELECT run_id FROM run_idempotency').fetchall() == [(original_id,)]
        claims = claims_for(adapter, winner)
        reopen_runs(adapter)
        store = adapter._run_idempotency_store
        for field, value in (('home_install_id', 'unrelated-origin'), ('authority_gateway_id', gateways['a'].install_id),
                             ('member_id', 'other-member'), ('target_install_id', 'other-target'),
                             ('target_profile', 'other-profile'), ('room_id', 'other-room')):
            assert store.successor_run_scope(original_id, successor={**identity(claims), field: value}) is None
        with pytest.raises(fence.RoomAuthorityConflict):
            fence.learn_authority(store.path, room_id=claims['room_id'], epoch=2, install_id=gateways['a'].install_id)
        async with TestClient(TestServer(app(adapter))) as cli:
            headers = {**_headers(winner['grant']), 'Idempotency-Key': 'room:same-epoch-executed:1'}
            observed = await cli.get(f'/v1/runs/{original_id}', headers=headers)
            assert observed.status == 200, await observed.json()
            replay = await cli.post('/v1/runs', json=body_for(claims, winner['catalog'],
                'same-epoch-executed'), headers=headers)
            assert replay.status == 202 and (await replay.json())['run_id'] == original_id
            assert len(execution) == 1
            cancelled = await cli.post('/v1/runs/stop', json=body_for(claims, winner['catalog'],
                'same-epoch-executed'), headers=headers)
            result = await cancelled.json()
            assert not result.get('admission_cancelled'), result
            assert observed.status == 200 and cancelled.status == 200
            assert result['run_id'] == original_id
            exact = await cli.post(f'/v1/runs/{original_id}/stop', headers=headers)
            assert exact.status == 200 and (await exact.json())['run_id'] == original_id
            # The winner retains normal new work and its own exact controls.
            current_headers = {**_headers(winner['grant']), 'Idempotency-Key': 'room:current:1'}
            current = await cli.post('/v1/runs', json=body_for(claims, winner['catalog'], 'current'), headers=current_headers)
            assert current.status == 202, await current.json()
            current_id = (await current.json())['run_id']
            await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)
            assert len(execution) == 2
            assert (await cli.get(f'/v1/runs/{current_id}', headers=current_headers)).status == 200
            assert store.successor_run_scope(current_id, successor=identity(old_claims)) is None
            denied = await cli.get(f'/v1/runs/{current_id}', headers=_headers(old['grant']))
            assert denied.status == 403 and (await denied.json())['error']['code'] == 'room_reauthorization_required'
            denied_stop = await cli.post(f'/v1/runs/{current_id}/stop', headers=_headers(old['grant']))
            assert denied_stop.status == 403 and not store.stop_requested(current_id)
            # An imported genuine negative receipt remains the same exact proof.
            negative_id = 'retained-negative'
            assert store.reserve(room_run_scope_key(identity(old_claims)), 'room:negative:1', '', negative_id,
                {'run_id': negative_id, 'status': 'cancelled', 'admission_cancelled': True},
                identity=identity(old_claims), cancel_if_missing=True)[0] == 'created'
            negative = await cli.post('/v1/runs/stop', json=body_for(claims, winner['catalog'], 'negative'),
                headers={**_headers(winner['grant']), 'Idempotency-Key': 'room:negative:1'})
            proof = await negative.json()
            assert negative.status == 200 and proof['admission_cancelled'] and proof['run_id'] == negative_id, proof
            # Once bounded observation ends, executed history is unknown, never absent.
            expire_observation(adapter, original_id)
            unknown = await cli.post('/v1/runs/stop', json=body_for(claims, winner['catalog'],
                'same-epoch-executed'), headers=headers)
            unknown_body = await unknown.json()
            assert unknown.status == 503 and not unknown_body.get('admission_cancelled'), unknown_body
            assert len(execution) == 2


def test_equal_epochs_without_a_target_origin_binding_never_transfer_controls_or_claim_absence(tmp_path):
    previous = {'room_id': 'room', 'home_install_id': 'loser', 'authority_gateway_id': 'loser',
                'authority_epoch': 2, 'member_id': 'member', 'target_install_id': 'target', 'target_profile': 'default'}
    successor = {**previous, 'home_install_id': 'winner', 'authority_gateway_id': 'winner'}
    scope = room_run_scope_key(previous)
    with closing(RunIdempotencyStore(str(tmp_path / 'runs.db'))) as store:
        store.reserve(scope, 'room:executed:1', 'accepted', 'old-run', {'status': 'completed'}, identity=previous)
        fence.fence_and_promise(store.path, room_id='room', fence_epoch=1, promise_epoch=2, candidate_install_id='loser')
        fence.learn_authority(store.path, room_id='room', epoch=2, install_id='winner')
        assert store.successor_run_scope('old-run', successor=successor) is None
        with pytest.raises(RunCancellationUnknown):
            store.cancellation_state(successor, 'room:executed:1')
        with pytest.raises(RunCancellationUnknown):
            store.reserve(room_run_scope_key(successor), 'room:executed:1', 'new', 'new-run',
                          {'status': 'queued'}, identity=successor)
        own, _, _ = store.cancellation_state(previous, 'room:executed:1')
        assert own['run_id'] == 'old-run'
        assert store._conn.execute('SELECT run_id FROM run_idempotency').fetchall() == [('old-run',)]
