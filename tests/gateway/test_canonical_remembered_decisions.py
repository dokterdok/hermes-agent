"""Remembered operations resolve exact native requests, never global always grants."""
import asyncio
import json

import pytest

from gateway.session_contract import Principal
from gateway.session_group_home_access import dispatch_home_access
from gateway.session_group_decisions import decide
from gateway.session_group_messaging_send import _capture
from gateway import session_group_rules as rules
from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_canonical_group_decisions import pending


def report(authority, service, task, row, live, answers, prompt_id, *, operation='a' * 64, allowed=True):
    authority.register_approval(row['target_session_id'], row['generation'], live.route, {
        'request_id': prompt_id, 'command': 'fixture operation', 'description': 'Fixture',
        'allow_session': True, 'allow_permanent': allowed, 'remember_key': operation,
        'remember_context': 'Local, folder /fixture'})
    live.controls.remote_responders[prompt_id] = lambda *args: answers.append(args)
    service._set_pending_action('room', 'writer', {'kind': 'approval', 'member_id': 'writer',
        'task_id': task['identity'].task_id, 'execution_generation': 1, 'request_id': prompt_id})


@pytest.mark.asyncio
async def test_exact_repeat_is_allowed_once_until_owner_revokes_rule(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        service.runtime.process_generation = 'decision-test'
        task, row, live, answers = await pending(authority, service)
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'first-prompt')
        args = dict(room=service._room('room'), guard=lambda: None,
            command_id='remember', params=dict(member_id='writer', task_id=task['identity'].task_id,
                execution_generation=1, request_id='first-prompt', choice='remember', remember_key='a' * 64))
        result = await asyncio.to_thread(decide, authority, **args)
        assert result['remembered'] is True
        assert await asyncio.to_thread(decide, authority, **args) == result
        assert len(answers) == 1
        rule, = rules.list_rules(service, 'room')
        assert rule['state'] == 'active'
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'second-prompt')
        assert answers == [('approval', 'first-prompt', 'once'), ('approval', 'second-prompt', 'once')]
        proof = _capture(authority, service._room('room'), guard=lambda: None)
        assert rules.revoke(service, proof, rule['rule_id'], rule['generation']) == 1
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'third-prompt')
        assert len(answers) == 2 and 'third-prompt' in live.controls.pending
        assert rules.list_rules(service, 'room') == []


@pytest.mark.asyncio
async def test_policy_metadata_and_current_rule_fence_prevent_automatic_widening(tmp_path, monkeypatch):
    from hermes_state_runtime import RuntimeStoreError
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        service.runtime.process_generation = 'decision-test'
        task, row, live, answers = await pending(authority, service)
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'first-prompt', allowed=False)
        args = dict(room=service._room('room'), guard=lambda: None, command_id='remember',
            params=dict(member_id='writer', task_id=task['identity'].task_id, execution_generation=1,
                        request_id='first-prompt', choice='remember', remember_key='a' * 64))
        with pytest.raises(RuntimeStoreError):
            await asyncio.to_thread(decide, authority, **args)
        assert answers == []
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'first-prompt')
        await asyncio.to_thread(decide, authority, **args)
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'different-op', operation='b' * 64)
        assert len(answers) == 1 and 'different-op' in live.controls.pending
        rule, = rules.list_rules(service, 'room')
        proof = _capture(authority, service._room('room'), guard=lambda: None)
        # An observed rule is not enough: revocation wins the actual acceptance writer.
        original = service.authority.db._execute_write
        fired = False
        def revoked(operation, *args, **kwargs):
            nonlocal fired
            if not fired:
                fired = True
                monkeypatch.setattr(service.authority.db, '_execute_write', original)
                rules.revoke(service, proof, rule['rule_id'], rule['generation'])
            return original(operation, *args, **kwargs)
        monkeypatch.setattr(service.authority.db, '_execute_write', revoked)
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'would-repeat')
        assert len(answers) == 1 and 'would-repeat' in live.controls.pending


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['owner', 'target', 'lease', 'rule'])
async def test_remembered_permission_never_follows_changed_authority_or_work(changed, tmp_path, monkeypatch):
    from gateway.session_hosted_service import _OWNER
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        service.runtime.process_generation = 'decision-test'
        task, row, live, answers = await pending(authority, service)
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'first-prompt')
        await asyncio.to_thread(decide, authority, room=service._room('room'), guard=lambda: None,
            command_id='remember', params=dict(member_id='writer', task_id=task['identity'].task_id,
                execution_generation=1, request_id='first-prompt', choice='remember', remember_key='a' * 64))
        rule, = rules.list_rules(service, 'room')
        def mutate(conn):
            def target():
                members = json.loads(conn.execute("SELECT members_json FROM hosted_rooms WHERE room_id='room'").fetchone()[0])
                members[0]['target']['profile'] = 'different'
                conn.execute("UPDATE hosted_rooms SET members_json=? WHERE room_id='room'", (json.dumps(members),))
            mutations = {
                'owner': lambda: conn.execute('UPDATE state_meta SET value=? WHERE key=?', ('different-owner', _OWNER + 'room')),
                'target': target,
                'lease': lambda: conn.execute("UPDATE hosted_room_driver_leases SET expires_at=0 WHERE room_id='room'"),
                'rule': lambda: conn.execute("UPDATE canonical_group_approval_rules SET generation=generation+1,state='revoked' WHERE rule_id=?", (rule['rule_id'],)),
            }
            mutations[changed]()
        authority.db._execute_write(mutate)
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'next-prompt')
        assert len(answers) == 1 and 'next-prompt' in live.controls.pending


@pytest.mark.asyncio
async def test_retained_rule_budget_keeps_recent_revocations_and_once_decisions_available(tmp_path, monkeypatch):
    from hermes_state_runtime import RuntimeStoreError
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        monkeypatch.setattr(rules, 'MAX_RETAINED_RULES', 2)
        service.runtime.process_generation = 'decision-test'
        task, row, live, answers = await pending(authority, service)
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        proof = _capture(authority, service._room('room'), guard=lambda: None)
        for number, letter in enumerate('abc'):
            prompt_id = 'first-prompt' if number == 0 else f'prompt-{number}'
            await asyncio.to_thread(report, authority, service, task, row, live, answers, prompt_id, operation=letter * 64)
            params = dict(member_id='writer', task_id=task['identity'].task_id, execution_generation=1,
                          request_id=prompt_id, choice='remember', remember_key=letter * 64)
            args = dict(room=service._room('room'), guard=lambda: None, command_id=f'remember-{number}', params=params)
            if number == 2:
                with pytest.raises(RuntimeStoreError, match='storage_unavailable'):
                    await asyncio.to_thread(decide, authority, **args)
                assert len(answers) == 2
                once = {key: value for key, value in params.items() if key != 'remember_key'} | {'choice': 'once'}
                result = await asyncio.to_thread(decide, authority, **{**args, 'command_id': 'once', 'params': once})
                assert result['status'] == 'resolved'
                break
            assert (await asyncio.to_thread(decide, authority, **args))['remembered'] is True
            rule, = rules.list_rules(service, 'room')
            assert rules.revoke(service, proof, rule['rule_id'], rule['generation']) == 1
        with authority.db._read_ctx() as conn:
            retained = conn.execute('SELECT state FROM canonical_group_approval_rules').fetchall()
        assert len(retained) == 2 and all(row['state'] == 'revoked' for row in retained)
