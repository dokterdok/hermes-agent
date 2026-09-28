"""Operator control consent is operation-specific and checked at Route's writer."""
import pytest

from gateway.session_controls import AuthorityConnection
from gateway.session_group_messaging_control import attest_room_control
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_messaging_inventory_binding import bound, enrolled, recipient
from tests.gateway.test_messaging_room_read_binding import room_grant_params, room_rpc


def owner(bound, *, approve=True, subject='native-alice'):
    caps = ['session:read', 'session:create', 'session:control']
    if approve:
        caps.append('session:approve')
    return AuthorityConnection(bound.authority, object(), {
        'user_id': subject, 'provider': 'local', 'profile_id': str(bound.home),
        'instance_id': bound.authority.instance_id, 'capabilities': caps,
        'native_bootstrap': True}, operator=True)


async def grant_read(bound):
    inventory = await enrolled(bound)
    response = await room_rpc(bound.alice, params=room_grant_params(inventory))
    assert 'result' in response, response
    return response['result']


def params(read, scope, **changed):
    return dict(request_id=f'{scope}-grant-1', recipient=recipient(), room_id='alice-room',
                room_read_binding_id=read['binding_id'],
                room_read_generation=read['generation'], expected_generation=0) | changed


async def control_rpc(connection, scope, verb, payload):
    return await connection.dispatch({'id': 19,
        'method': f'groups.messaging.room.{scope}.{verb}', 'params': payload})


@pytest.mark.asyncio
async def test_independent_operator_grants_and_same_writer_revocation(bound):
    read = await grant_read(bound)
    native = owner(bound)
    from gateway.session_group_messaging_control import prepare_native_control_binding
    prepare_native_control_binding(native, 'groups.messaging.room.stop.grant', params(read, 'stop'))
    stop = await control_rpc(native, 'stop', 'grant', params(read, 'stop'))
    assert 'result' in stop, stop
    stop = stop['result']
    assert stop['active'] and stop['generation'] == 1
    assert 'error' in await control_rpc(owner(bound, approve=False), 'approval',
                                        'grant', params(read, 'approval'))
    approved = await control_rpc(native, 'approval', 'grant', params(read, 'approval'))
    assert 'result' in approved, approved
    event = bound.event()
    event.text = '/group 1 stop'
    from gateway.session_group_messaging_send import _stable_message_identity
    digest = _stable_message_identity(event, recipient())[1]
    exact = {'room_id': 'alice-room', 'cancel_id': 'messaging-stop:' + digest}
    context = attest_room_control(bound.runner, event, read['room_ref'], 'stop', exact)
    assert context.actor.capabilities == frozenset({'session:read'})
    assert context.actor.subject != native.actor.subject
    assert context.delegated().delegation_identity
    with pytest.raises(RuntimeStoreError):
        attest_room_control(bound.runner, event, read['room_ref'], 'approval', exact | {'choice': 'always'})
    revoke = params(read, 'stop', request_id='stop-revoke-1',
                    expected_generation=stop['generation'], binding_id=stop['binding_id'])
    assert (await control_rpc(native, 'stop', 'revoke', revoke))['result']['active'] is False
    with pytest.raises(RuntimeStoreError, match='messaging_room_control_stale'):
        context.require_current()
    assert (await control_rpc(native, 'approval', 'revoke',
        params(read, 'approval', request_id='approval-revoke-1',
               expected_generation=approved['result']['generation'],
               binding_id=approved['result']['binding_id'])))['result']['active'] is False
    regrant = await control_rpc(native, 'stop', 'grant',
        params(read, 'stop', request_id='stop-grant-2', expected_generation=2))
    assert regrant['result']['binding_id'] != stop['binding_id']
    with pytest.raises(RuntimeStoreError):
        context.require_current()


@pytest.mark.asyncio
@pytest.mark.parametrize('scope', ['stop', 'approval'])
async def test_delegated_writer_rechecks_revoke_regrant_and_exact_request(bound, scope):
    read = await grant_read(bound)
    native = owner(bound)
    first = (await control_rpc(native, scope, 'grant', params(read, scope)))['result']
    event = bound.event()
    event.text = '/group 1 stop' if scope == 'stop' else '/group 1 approve 1 deny'
    if scope == 'stop':
        from gateway.session_group_messaging_send import _stable_message_identity
        digest = _stable_message_identity(event, recipient())[1]
        exact = {'room_id': 'alice-room', 'cancel_id': 'messaging-stop:' + digest}
    else:
        exact = {'room_id': 'alice-room', 'member_id': 'writer', 'task_id': 'exact-task',
                 'execution_generation': 1, 'request_id': 'exact-request', 'choice': 'deny'}
    control = attest_room_control(bound.runner, event, read['room_ref'], scope, exact)
    delegated = control.delegated()
    with pytest.raises(RuntimeStoreError):
        control.require_current(method='groups.approve' if scope == 'stop' else 'groups.stop',
                                params=exact)
    with pytest.raises(RuntimeStoreError):
        control.require_current(params=exact | {'room_id': 'bob-room'})
    from gateway.session_group_controls import dispatch_group_control
    if scope == 'stop':
        with pytest.raises(RuntimeStoreError):
            await dispatch_group_control(control, 'groups.stop',
                                         exact | {'cancel_id': 'other-cancel'})
    if scope == 'approval':
        for mismatch in ({'task_id': 'other-task'}, {'request_id': 'other-request'},
                         {'member_id': 'other-member'}, {'choice': 'once'},
                         {'execution_generation': 2}):
            with pytest.raises(RuntimeStoreError):
                await dispatch_group_control(control, 'groups.approve', exact | mismatch)
    event.source.chat_id = 'other-recipient'
    with pytest.raises(RuntimeStoreError):
        bound.db._execute_write(delegated.authorize_new)
    event.source.chat_id = 'private-chat'
    if scope == 'approval':
        with pytest.raises(RuntimeStoreError, match='invalid_params'):
            attest_room_control(bound.runner, event, read['room_ref'], scope,
                                exact | {'choice': 'always'})
    revoke = params(read, scope, request_id=f'{scope}-revoke-on-writer',
                    expected_generation=first['generation'], binding_id=first['binding_id'])
    assert (await control_rpc(native, scope, 'revoke', revoke))['result']['active'] is False
    for callback in (delegated.authorize_new, delegated.authorize_commit):
        with pytest.raises(RuntimeStoreError):
            bound.db._execute_write(callback)
    second = (await control_rpc(native, scope, 'grant',
        params(read, scope, request_id=f'{scope}-regrant-on-writer',
               expected_generation=2)))['result']
    assert second['binding_id'] != first['binding_id']
    with pytest.raises(RuntimeStoreError):
        bound.db._execute_write(delegated.authorize_new)


@pytest.mark.asyncio
async def test_control_grants_refuse_foreign_lineage_and_wrong_operator_right(bound):
    read = await grant_read(bound)
    prior = params(read, 'stop')
    for native, scope, payload in (
        (owner(bound, subject='native-bob'), 'stop', prior),
        (owner(bound), 'stop', prior | {'recipient': recipient(chat_id='foreign')}),
        (owner(bound), 'stop', prior | {'room_read_generation': read['generation'] + 1}),
        (owner(bound), 'stop', prior | {'room_id': 'bob-room'}),
        (owner(bound, approve=False), 'approval', params(read, 'approval')),
    ):
        assert 'error' in await control_rpc(native, scope, 'grant', payload)
