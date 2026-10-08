"""Real signed succession binds target grants, including a promised loser's same-epoch replacement."""
import asyncio
from contextlib import closing
import hashlib
import json
import time
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway import hosted_room_fence as fence
from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_backup as backup
from gateway import hosted_room_succession_move as move
from gateway import hosted_rooms as rooms
from gateway.hosted_room_peer import decode_room_grant
from gateway.platforms import api_server_room_grants as grants
from gateway.platforms.api_server_room_succession import continuation_minter
from gateway.platforms.api_server_run_authority import room_authority, room_run_scope
from tests.gateway.fixtures.succession import ROOM, context, copy_to, home_room, make_gateways
from tests.gateway.test_api_server_room_cancellation import _adapter, _headers
from tests.gateway.test_room_cancellation_retirement_http import app


@pytest.fixture
def net(tmp_path, monkeypatch):
    gateways = make_gateways(tmp_path, 'h', 'a', 'b', 'p')
    home_room(gateways, 'h', successors=('a', 'b'))
    for name in ('a', 'b', 'p'):
        copy_to(gateways['h'], gateways[name])
    target = gateways['p']
    with target.acting():
        adapter = _adapter(target.home / 'runs_idempotency.db')
    monkeypatch.setattr(grants, '_grant_db', lambda adapter: target.db)
    ctx = backup.BackupContext(custody_db=target.db, runs_store=adapter._run_idempotency_store,
        mint_grants=continuation_minter(adapter, target.db),
        replace_grants=continuation_minter(adapter, target.db))
    yield gateways, adapter, ctx
    adapter._run_idempotency_store.close()
    for gateway in gateways.values():
        gateway.close()


def invitation(adapter, home):
    return grants._issue_invitation(adapter, {'room_id': ROOM, 'home_install_id': home,
        'authority_gateway_id': home, 'authority_epoch': 1, 'member_id': 'reviewer', 'replication': False}, 'default')


def promise(gateways, ctx, candidate='a'):
    with gateways[candidate].acting(), closing(rooms._read_connection(gateways[candidate].db)) as conn:
        private, request = move._fence_request(ROOM, 2, succession.watermark_locked(conn, ROOM))
    with gateways['p'].acting():
        reply = backup.answer_fence(ctx, request)
    return backup.open_sealed_reply(private, reply, request)['continuation_grants'][0]


def winner_request(gateways):
    winner = gateways['b']
    with winner.acting():
        ctx = context(winner, gateways, down=('h', 'a', 'p'))
        preview = move.preview(ctx, ROOM, winner.install_id)
        move.continue_here(ctx, ROOM, winner.install_id, preview_id=preview['preview_id'], confirm=True)
        with closing(rooms._read_connection(winner.db)) as conn:
            latest = succession.latest_transition_locked(conn, ROOM)
        private, public = succession.reply_keypair()
        unsigned = {'room_id': ROOM, 'successor_install_id': winner.install_id, 'reply_key': public,
            'issued_at': time.time(), 'nonce': succession.nonce(), 'transition': latest['event'], 'fork_event': latest['fork_event']}
        return private, {**unsigned, 'signature': succession.sign(succession.LEARN, unsigned)}


def claims_for(adapter, invitation):
    return decode_room_grant(adapter._room_grant_secret(), invitation['grant'], permission='status')


def cancel_many(store, claims, prefix):
    for index in range(32):
        run_id = f'{prefix}-{index}'
        assert store.reserve(room_run_scope(claims), f'room:{prefix}-{index}:1', '', run_id,
            {'run_id': run_id, 'status': 'cancelled', 'admission_cancelled': True},
            cancel_if_missing=True, room_authority=room_authority(claims))[0] == 'created'


def body_for(claims, catalog, task):
    prompt = 'Continue once.'
    return {'input': prompt, 'hosted_room_dispatch': {
        **{key: claims[key] for key in ('room_id', 'home_install_id', 'authority_gateway_id', 'authority_epoch',
                                      'member_id', 'target_install_id', 'target_profile', 'execution_policy_digest')},
        'protocol_version': 2, 'task_id': task, 'execution_generation': 1, 'source_event_seq': 1,
        'cancellation_scope_id': 'cancel', 'capability_digest': catalog['catalog_digest'], 'trace_id': 'trace',
        'prompt': prompt, 'prompt_digest': hashlib.sha256(prompt.encode()).hexdigest()}}


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_consent', [False, True])
async def test_verified_winner_compacts_both_predecessors_fences_captured_loser_and_runs(net, monkeypatch, legacy_consent):
    gateways, adapter, ctx = net
    store = adapter._run_idempotency_store
    entered, release = asyncio.Event(), asyncio.Event()
    original_history = adapter._conversation_history_for_session
    async def pause(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_history(*args, **kwargs)
    create = MagicMock(side_effect=AssertionError('the promised loser executed'))
    monkeypatch.setattr(adapter, '_create_agent', create)
    monkeypatch.setattr(adapter, '_conversation_history_for_session', pause)
    with gateways['p'].acting():
        first = invitation(adapter, gateways['h'].install_id)
        first_claims = claims_for(adapter, first)
        cancel_many(store, first_claims, 'original')
        nonconsenting = grants._issue_invitation(adapter, {'room_id': ROOM,
            'home_install_id': gateways['h'].install_id, 'authority_gateway_id': gateways['h'].install_id,
            'authority_epoch': 1, 'member_id': 'nonconsenting', 'replication': False, 'continuation': False}, 'default')
        nonconsenting_claims = claims_for(adapter, nonconsenting)
        cancel_many(store, nonconsenting_claims, 'nonconsenting')
        if legacy_consent:
            with rooms._transaction(gateways['p'].db, immediate=True) as conn:
                options = succession.consents_locked(conn, ROOM)[0]['options']
                options.pop('authority')
                options.pop('origin_install_id')
                conn.execute(f'UPDATE {succession.CONSENT} SET options_json=? WHERE room_id=?', (json.dumps(options), ROOM))
    promised = promise(gateways, ctx)
    with gateways['p'].acting():
        promised_claims = claims_for(adapter, promised)
        assert store._conn.execute('SELECT COUNT(*) FROM run_idempotency').fetchone()[0] == 0
        cancel_many(store, promised_claims, 'promised')
        # This member now follows the promise, but opts out of further grant
        # minting. A learned winner must still fence its writer and compact its
        # terminal receipts when replacing the reservation's shared authority.
        nonconsenting = grants._issue_invitation(adapter, {'room_id': ROOM,
            'home_install_id': gateways['a'].install_id, 'authority_gateway_id': gateways['a'].install_id,
            'authority_epoch': 2, 'member_id': 'nonconsenting', 'replication': False, 'continuation': False,
            'previous_authority': {key: nonconsenting_claims[key] for key in (
                'home_install_id', 'authority_gateway_id', 'authority_epoch')}}, 'default')
        nonconsenting_claims = claims_for(adapter, nonconsenting)
        cancel_many(store, nonconsenting_claims, 'nonconsenting-promised')
        assert store._conn.execute('SELECT COUNT(*) FROM run_room_authorities').fetchone()[0] == 2
        # An ordinary owner invitation cannot claim the proof-only same-epoch exception.
        wrong = {key: promised_claims[key] for key in ('room_id', 'home_install_id', 'authority_gateway_id',
                                                      'authority_epoch', 'member_id')}
        wrong.update(home_install_id=gateways['b'].install_id, authority_gateway_id=gateways['b'].install_id,
                     previous_authority={key: promised_claims[key] for key in (
                         'home_install_id', 'authority_gateway_id', 'authority_epoch')})
        with pytest.raises(ValueError):
            grants._issue_invitation(adapter, wrong, 'default')
    private, request = winner_request(gateways)
    try:
        with gateways['p'].acting():
            async with TestClient(TestServer(app(adapter))) as cli:
                body = body_for(promised_claims, promised['catalog'], 'paused')
                pending = asyncio.create_task(cli.post('/v1/runs', json=body,
                    headers={**_headers(promised['grant']), 'Idempotency-Key': 'room:paused:1'}))
                await asyncio.wait_for(entered.wait(), 15)
                forged = {**request, 'signature': '00' * 64}
                with pytest.raises(succession.SuccessionError):
                    backup.answer_learn(ctx, forged)
                reply = backup.answer_learn(ctx, request)
                winner = backup.open_sealed_reply(private, reply, request)['continuation_grants'][0]
                assert store._conn.execute('SELECT COUNT(*) FROM run_idempotency').fetchone()[0] == 0
                release.set()
                refused = await pending
                assert refused.status == 409, await refused.json()
                assert not create.called
                # Neither the public proof API nor its writer can return to the old incarnation.
                with pytest.raises(fence.RoomAuthorityConflict):
                    fence.learn_authority(store.path, room_id=ROOM, epoch=2, install_id=gateways['a'].install_id)
                with pytest.raises(ValueError, match='epoch holder'):
                    store.observe_verified_room_authority(promised_claims, wrong['previous_authority'], gateways['h'].install_id)
                current = claims_for(adapter, winner)
                agent = MagicMock()
                agent.run_conversation.return_value = {'final_response': 'winner', 'messages': []}
                agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
                create.side_effect, create.return_value = None, agent
                accepted = await cli.post('/v1/runs', json=body_for(current, winner['catalog'], 'new'),
                    headers={**_headers(winner['grant']), 'Idempotency-Key': 'room:new:1'})
                assert accepted.status == 202, await accepted.json()
                await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)
                assert agent.run_conversation.call_count == 1
                from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
                path = store.path
                store.close()
                store = adapter._run_idempotency_store = RunIdempotencyStore(str(path))
                for stale in (first_claims, promised_claims, nonconsenting_claims):
                    assert store.reserve(room_run_scope(stale), 'room:late:1', 'f', 'late', {'status': 'queued'},
                        room_authority=room_authority(stale)) == ('authority_retired', None)
                store.retire_room_authority(room_run_scope(current), room_authority(current))
                with pytest.raises(ValueError, match='already advanced'):
                    store.observe_verified_room_authority(current, {key: current[key] for key in (
                        'home_install_id', 'authority_gateway_id', 'authority_epoch')}, gateways['h'].install_id)
    finally:
        release.set()


def test_verified_continuation_cannot_absorb_an_unrelated_owner_origin(net):
    gateways, adapter, ctx = net
    with gateways['p'].acting():
        unrelated = invitation(adapter, 'unrelated-origin')
        claims = claims_for(adapter, unrelated)
        cancel_many(adapter._run_idempotency_store, claims, 'unrelated')
    with pytest.raises(ValueError, match='another origin'):
        promise(gateways, ctx)
    assert adapter._run_idempotency_store._conn.execute('SELECT COUNT(*) FROM run_idempotency').fetchone()[0] == 32
    assert adapter._run_idempotency_store._conn.execute('SELECT COUNT(*) FROM run_room_authority_aliases').fetchone()[0] == 0
