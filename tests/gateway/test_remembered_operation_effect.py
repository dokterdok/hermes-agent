"""Operation scope reaches the serialized responder and real peer HTTP boundary."""
import asyncio
from types import SimpleNamespace
import threading

import pytest

from gateway.session_contract import Principal
from gateway.session_group_decisions import decide
from gateway.session_group_home_access import dispatch_home_access
from hermes_state_runtime import RuntimeStoreError, claim_session_input
from tests.gateway.test_canonical_group_decisions import pending
from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_canonical_remembered_decisions import report
from tests.gateway.test_roomlink_review_grants import target  # noqa: F401
from tests.tui_gateway.test_peer_canonical_controls import peer_target, peer_run  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['operation', 'eligibility', 'deny'])
async def test_changed_request_at_serialized_response_does_not_inherit_consent(tmp_path, monkeypatch, change):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        service.runtime.process_generation = 'decision-test'
        task, row, live, answers = await pending(authority, service)
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'first-prompt')
        respond = authority.respond
        async def before_response(actor, ref, generation, prompt_id, response, **kwargs):
            if change == 'deny':
                await respond(actor, ref, generation, prompt_id, {'choice': 'deny'})
            else:
                authority.register_approval(ref.session_id, generation, live.route, {
                    'request_id': prompt_id, 'command': 'Changed fixture', 'allow_session': True,
                    'allow_permanent': change != 'eligibility', 'remember_key': 'b' * 64,
                    'remember_context': 'Local, folder /fixture'})
            return await respond(actor, ref, generation, prompt_id, response, **kwargs)
        monkeypatch.setattr(authority, 'respond', before_response)
        args = dict(room=service._room('room'), guard=lambda: None, command_id='remember',
                    params=dict(member_id='writer', task_id=task['identity'].task_id, execution_generation=1,
                                request_id='first-prompt', choice='remember', remember_key='a' * 64))
        if change == 'deny':
            result = await asyncio.to_thread(decide, authority, **args)
            assert result == {'status': 'already_resolved', 'prompt_id': 'first-prompt', 'remembered': False}
            assert answers == [('approval', 'first-prompt', 'deny')]
        else:
            with pytest.raises(RuntimeStoreError, match='approval_operation_changed'):
                await asyncio.to_thread(decide, authority, **args)
            assert answers == [] and 'first-prompt' in live.controls.pending


@pytest.mark.asyncio
async def test_peer_http_preserves_expected_operation_through_final_response(peer_run, monkeypatch):
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPError
    p = peer_run
    row = claim_session_input(p.authority.db, epoch=p.authority.epoch, session_id=p.row['target_session_id'])
    live = p.authority.sessions[row['target_session_id']]
    answers = []
    def publish(key):
        p.authority.register_approval(row['target_session_id'], row['generation'], live.route, {
            'request_id': 'peer-prompt', 'command': 'Fixture operation', 'allow_session': True,
            'allow_permanent': True, 'remember_key': key, 'remember_context': 'Local, folder /fixture'})
        live.controls.remote_responders['peer-prompt'] = lambda *args: answers.append(args)
    publish('a' * 64)
    respond = p.authority.respond
    async def replaced(actor, ref, generation, prompt_id, response, **kwargs):
        publish('b' * 64)
        return await respond(actor, ref, generation, prompt_id, response, **kwargs)
    monkeypatch.setattr(p.authority, 'respond', replaced)
    args = dict(task_id='task', execution_generation=7, request_id='peer-prompt', choice='once',
                grant=p.route.grant, expected_operation_key='a' * 64)
    with pytest.raises(PeerRunsHTTPError):
        await asyncio.to_thread(p.peer.approve_receipt, **args)
    assert answers == []
    monkeypatch.setattr(p.authority, 'respond', respond)
    result = await asyncio.to_thread(p.peer.approve_receipt, **{**args, 'expected_operation_key': 'b' * 64})
    assert result['status'] == 'resolved' and answers == [('approval', 'peer-prompt', 'once')]


def test_tool_waiter_checks_scope_before_removing_any_queue_entry(monkeypatch):
    from tools import approval
    def entry(key):
        return SimpleNamespace(data={'request_id': 'same-id', 'remember_key': key,
            'remember_context': 'Local, folder /fixture', 'allow_session': True, 'allow_permanent': True},
            result=None, event=threading.Event())
    current = entry('b' * 64)
    queues = {'fixture': [current]}
    monkeypatch.setattr(approval, '_gateway_queues', queues)
    with pytest.raises(ValueError, match='operation changed'):
        approval.resolve_gateway_approval('fixture', 'once', request_id='same-id', expected_operation_key='a' * 64)
    assert queues['fixture'] == [current] and current.result is None and not current.event.is_set()
    assert approval.resolve_gateway_approval('fixture', 'once', request_id='same-id', expected_operation_key='b' * 64) == 1
    assert queues == {} and current.result == 'once' and current.event.is_set()
