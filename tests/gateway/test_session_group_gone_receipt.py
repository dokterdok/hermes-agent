"""A scoped member's missing run receipt cannot hold a deferred group chat forever."""

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web

from gateway import hosted_room_driver as driver, hosted_rooms, session_api_turn
from gateway.platforms import api_server_runs, api_server_room_grants
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_session_group_peer_discard import _never_admitted_deferred_turn
from tests.gateway import test_session_group_peers as peers
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError
from tui_gateway.hosted_room_peer_transport import build_member_dispatch

gateway, linked_peer = peers.gateway, peers.linked_peer


def _dispatch(service, db, identity):
    task = driver.get_task(db, identity)
    route = service.peer_routes[('linked', 'reviewer')]
    return build_member_dispatch(binding=service.bindings()[0], route=route, room_id='linked',
        task_id=identity.task_id, target_profile='default', execution_generation=1,
        source_event_seq=task['payload']['source_event_seq'], prompt=task['payload']['prompt'],
        trace_id=route.trace_id).as_mapping()


async def _reserved_receipt(gateway, linked_peer, monkeypatch, *, refuse=True):
    service, db, identity = await _never_admitted_deferred_turn(gateway, monkeypatch, linked_peer)
    route, client = service.peer_routes[('linked', 'reviewer')], service.peer_clients[('linked', 'reviewer')]
    dispatch = _dispatch(service, db, identity)
    entered, release = asyncio.Event(), asyncio.Event()

    async def admission(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 10)
        if refuse:
            raise RuntimeStoreError('runtime_draining')

    monkeypatch.setattr(session_api_turn, 'admit_api_turn_async', admission)
    reserve = gateway.adapter._run_idempotency_store.reserve

    def reserve_before_authority(*args, **kwargs):
        result = reserve(*args, **kwargs)
        gateway.adapter.gateway_runner = SimpleNamespace(session_authority=gateway.authority)
        return result

    monkeypatch.setattr(gateway.adapter._run_idempotency_store, 'reserve', reserve_before_authority)
    # A separate sender models the original HTTP request whose acceptance home lost.
    sender = PeerRunsHTTPClient(base_url=client.base_url, api_key='')
    pending = asyncio.create_task(asyncio.to_thread(sender.dispatch, dispatch=dispatch, grant=route.grant))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        result = await peers.call(gateway.owner, 'groups.discard', room_id='linked', member_id='reviewer',
                                 task_id=identity.task_id, execution_generation=1)
        assert result == 'session_busy', result
        assert client._receipt(identity.task_id, 1) is not None
    finally:
        release.set()
        if refuse:
            with pytest.raises(PeerRunsHTTPError):
                await pending
        else:
            await pending
        monkeypatch.setattr(gateway.adapter, 'gateway_runner', None)
        monkeypatch.setattr(gateway.adapter._run_idempotency_store, 'reserve', reserve)
    return service, db, identity, dispatch


@pytest.mark.asyncio
@pytest.mark.parametrize('linked_peer', [True], indirect=True)
@pytest.mark.parametrize('operation', ['discard', 'disband', 'retry', 'retry_closed'])
async def test_refused_reservation_receipt_recovers(gateway, linked_peer, monkeypatch, operation):
    service, db, identity, dispatch = await _reserved_receipt(gateway, linked_peer, monkeypatch)
    client, route = service.peer_clients[('linked', 'reviewer')], service.peer_routes[('linked', 'reviewer')]
    old = client._receipt(identity.task_id, 1)
    assert gateway.adapter._run_statuses == {}
    # Simulate home restart too: recovery must retire the durable receipt, not just memory.
    client._runs.clear()
    client._terminal_receipts.add((identity.task_id, 1))
    client._status_cache[old['run_id']] = {'status': {}, 'next_poll_at': float('inf'), 'delay': 1}
    params = dict(room_id='linked')
    if operation != 'disband':
        params.update(member_id='reviewer', task_id=identity.task_id, execution_generation=1)
    if operation == 'retry_closed':
        request = client._request
        def lose_close(path, **kwargs):
            result = request(path, **kwargs)
            if path == '/v1/room-members/runs/close':
                assert result['closed']
                raise PeerRunsHTTPError('close reply lost', retryable=True)
            return result
        with monkeypatch.context() as lost:
            lost.setattr(client, '_request', lose_close)
            assert await peers.call(gateway.owner, 'groups.discard', **params) == 'session_busy'
        assert client._receipt(identity.task_id, 1)['run_id'] == old['run_id']
    result = await peers.call(gateway.owner, 'groups.' + operation.removesuffix('_closed'), **params)
    assert isinstance(result, dict), result
    task = driver.get_task(db, identity)
    assert task['execution_generation'] == 1
    assert old['run_id'] not in client._status_cache
    assert (identity.task_id, 1) not in client._terminal_receipts
    if operation == 'retry':
        new = client._receipt(identity.task_id, 1)
        assert new['run_id'] != old['run_id']
        assert list(gateway.adapter._run_statuses) == [new['run_id']]
        assert task['status'] == 'indeterminate'
        replay = await asyncio.to_thread(client.dispatch, dispatch=dispatch, grant=route.grant)
        assert replay['run_id'] == new['run_id']
    else:
        assert task['status'] == 'cancelled'
        assert hosted_rooms.remote_run_receipt(db, record=old) is None
        assert gateway.adapter._run_statuses == {}
        if operation in {'discard', 'retry_closed'}:
            with pytest.raises(PeerRunsHTTPError) as refused:
                await asyncio.to_thread(client.dispatch, dispatch=dispatch, grant=route.grant)
            assert refused.value.error_code == 'idempotency_key_conflict'
            assert 'tombstone' in await peers.call(gateway.owner, 'groups.disband', room_id='linked')
        else:
            assert 'tombstone' in result


@pytest.mark.asyncio
@pytest.mark.parametrize('linked_peer', [True], indirect=True)
async def test_first_retry_after_a_home_restart_checks_the_saved_run(gateway, linked_peer, monkeypatch):
    service, _, identity, _ = await _reserved_receipt(gateway, linked_peer, monkeypatch)
    key = ('linked', 'reviewer')
    old = service.peer_clients[key]
    saved = old._receipt(identity.task_id, 1)
    # A restarted home builds a new client, with no group chat scope bound until it's first used.
    fresh = service.peer_clients[key] = PeerRunsHTTPClient(
        base_url=old.base_url, api_key='', receipt_db_path=old.receipt_db_path)
    result = await peers.call(gateway.owner, 'groups.retry', room_id='linked', member_id='reviewer',
                              task_id=identity.task_id, execution_generation=1)
    assert isinstance(result, dict), result
    new = fresh._receipt(identity.task_id, 1)
    assert new['run_id'] != saved['run_id']
    assert list(gateway.adapter._run_statuses) == [new['run_id']]


async def _aged_out_receipt(gateway, linked_peer, monkeypatch):
    service, db, identity = await peers._deferred_unreceipted_peer_turn(gateway, monkeypatch, linked_peer)
    client, route = service.peer_clients[('linked', 'reviewer')], service.peer_routes[('linked', 'reviewer')]
    dispatch = _dispatch(service, db, identity)
    assert not await asyncio.to_thread(service.close_peer_admission, service.bindings()[0],
                                      driver.get_task(db, identity))
    old = client._receipt(identity.task_id, 1)
    gateway.adapter._set_run_status(old['run_id'], 'completed')
    store = gateway.adapter._run_idempotency_store
    # A terminal record past retention is pruned by the next admission/close. A restarted
    # adapter has no transient run state; retain the real SQLite store and HTTP routes.
    with store._lock:
        store._conn.execute('UPDATE run_idempotency SET retention_until=1')
        store._conn.commit()
    assert store.find_or_close('unrelated', 'prune')[0] == 'closed'
    for table in (gateway.adapter._run_statuses, gateway.adapter._run_owners,
                  gateway.adapter._active_run_tasks):
        table.clear()
    return service, db, identity, client, route, dispatch, old


@pytest.mark.asyncio
@pytest.mark.parametrize('linked_peer', [True], indirect=True)
async def test_a_crash_while_replacing_a_saved_run_keeps_a_way_to_stop_it(gateway, linked_peer, monkeypatch):
    service, db, identity, client, route, dispatch, old = await _aged_out_receipt(gateway, linked_peer, monkeypatch)
    sender = PeerRunsHTTPClient(base_url=client.base_url, api_key='')
    new = await asyncio.to_thread(sender.dispatch, dispatch=dispatch, grant=route.grant)

    class Crash(Exception):
        pass

    def crash(*_, **__):
        raise Crash

    with monkeypatch.context() as crashing:
        for write in ('upsert_remote_run_receipt', 'replace_remote_run_receipt'):
            crashing.setattr(hosted_rooms, write, crash, raising=False)
        with pytest.raises(Crash):
            await asyncio.to_thread(client.stop_receipt, task_id=identity.task_id,
                                    execution_generation=1, grant=route.grant)
    # A restarted home still holds a run to ask about, so Disband reaches the bot's gateway.
    service.peer_clients[('linked', 'reviewer')] = PeerRunsHTTPClient(
        base_url=client.base_url, api_key='', receipt_db_path=client.receipt_db_path)
    assert await peers.call(gateway.owner, 'groups.disband', room_id='linked') == 'room_stop_pending'
    assert gateway.adapter._run_statuses[new['run_id']]['status'] == 'stopping'
    assert hosted_rooms.remote_run_receipt(db, record=old)['run_id'] == new['run_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('linked_peer', [True], indirect=True)
@pytest.mark.parametrize('replacement', [False, True])
async def test_aged_out_receipt_closes_or_stops_replacement(gateway, linked_peer, monkeypatch, replacement):
    _, db, identity, client, route, dispatch, old = await _aged_out_receipt(gateway, linked_peer, monkeypatch)
    if replacement:
        sender = PeerRunsHTTPClient(base_url=client.base_url, api_key='')
        new = await asyncio.to_thread(sender.dispatch, dispatch=dispatch, grant=route.grant)
        assert new['run_id'] != old['run_id']
    control = dict(room_id='linked', member_id='reviewer', task_id=identity.task_id, execution_generation=1)
    result = await peers.call(gateway.owner, 'groups.discard', **control)
    if replacement:
        assert result == 'session_busy'
        assert gateway.adapter._run_statuses[new['run_id']]['status'] == 'stopping'
        assert client._receipt(identity.task_id, 1)['run_id'] == new['run_id']
        gateway.adapter._set_run_status(new['run_id'], 'cancelled')
        result = await peers.call(gateway.owner, 'groups.discard', **control)
    assert isinstance(result, dict) and result['discarded'], result
    assert driver.get_task(db, identity)['status'] == 'cancelled'
    if not replacement:
        with pytest.raises(PeerRunsHTTPError) as refused:
            await asyncio.to_thread(client.dispatch, dispatch=dispatch, grant=route.grant)
        assert refused.value.error_code == 'idempotency_key_conflict'


@pytest.mark.asyncio
@pytest.mark.parametrize('linked_peer', [True], indirect=True)
@pytest.mark.parametrize('fault', ['untyped_404', 'offline', 'old_404', 'old_405', 'admitted_late'])
async def test_saved_receipt_stop_requires_member_evidence(gateway, linked_peer, monkeypatch, fault):
    service, db, identity, _ = await _reserved_receipt(gateway, linked_peer, monkeypatch, refuse=False)
    client = service.peer_clients[('linked', 'reviewer')]
    run_id = client._receipt(identity.task_id, 1)['run_id']
    async def no_route(*args, **kwargs):
        return web.Response(status=405 if fault == 'old_405' else 404, text='Not Found')
    if fault == 'untyped_404':
        monkeypatch.setattr(api_server_runs, '_handle_stop_run', no_route)
    elif fault.startswith('old_'):
        monkeypatch.setattr(api_server_room_grants, '_handle_room_member_run_close', no_route)
    elif fault == 'offline':
        await linked_peer[0].close()
    control = dict(room_id='linked', member_id='reviewer', task_id=identity.task_id, execution_generation=1)
    if fault not in {'untyped_404', 'offline'}:
        gateway.adapter._set_run_status(run_id, 'cancelled')
    result = await peers.call(gateway.owner, 'groups.discard', **control)
    if fault in {'untyped_404', 'offline'}:
        assert result == 'session_busy', result
        assert driver.get_task(db, identity)['status'] == 'deferred'
        assert await peers.call(gateway.owner, 'groups.disband', room_id='linked') == 'room_stop_pending'
    else:
        assert isinstance(result, dict) and result['discarded'], result
        assert driver.get_task(db, identity)['status'] == 'cancelled'
    assert list(gateway.adapter._run_statuses) == [run_id]


@pytest.mark.asyncio
@pytest.mark.parametrize('linked_peer', [True], indirect=True)
async def test_replacement_grant_keeps_receipt_scope(gateway, linked_peer, monkeypatch):
    service, db, identity = await peers._deferred_unreceipted_peer_turn(gateway, monkeypatch, linked_peer)
    assert not await asyncio.to_thread(service.close_peer_admission, service.bindings()[0],
                                      driver.get_task(db, identity))
    old = service.peer_clients[('linked', 'reviewer')]._receipt(identity.task_id, 1)
    _, url, catalog, room = linked_peer
    replacement, registered = await peers._seat(gateway, room, url, catalog)
    assert registered['registered']
    client = service.peer_clients[('linked', 'reviewer')]
    assert (await asyncio.to_thread(client.recover_dispatch, dispatch=_dispatch(service, db, identity),
                                   grant=replacement))['run_id'] == old['run_id']
    other = await peers.linked_room(gateway, catalog, room_id='another')
    foreign = (await peers.invite(gateway, other))['grant']
    assert await peers.call(gateway.owner, 'groups.peer.register', room_id='linked', member_id='reviewer',
                            target_url=url, target_profile='default', grant=foreign,
                            catalog=catalog) == 'peer_target_mismatch'
    with pytest.raises(PeerRunsHTTPError) as denied:
        await asyncio.to_thread(client._request, '/v1/runs/' + old['run_id'], room_grant=foreign)
    assert denied.value.error_code == 'run_not_found'
    body = {'task_id': identity.task_id, 'execution_generation': 1}
    assert (await asyncio.to_thread(client._scoped_post, '/v1/room-members/runs/close', foreign,
                                   body=body))['closed']
    found = await asyncio.to_thread(client._scoped_post, '/v1/room-members/runs/close', replacement, body=body)
    assert not found['closed'] and found['run_id'] == old['run_id']
    gateway.adapter._set_run_status(old['run_id'], 'cancelled')
    result = await peers.call(gateway.owner, 'groups.discard', room_id='linked', member_id='reviewer',
                              task_id=identity.task_id, execution_generation=1)
    assert isinstance(result, dict) and result['discarded'], result
