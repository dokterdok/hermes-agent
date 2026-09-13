"""Profile allowlist reloads and room Remember operate on separate grant state."""
import asyncio

import pytest
import yaml

from gateway import session_group_rules as rules
from gateway.session_contract import Principal
from gateway.session_group_decisions import decide
from gateway.session_group_home_access import dispatch_home_access
from gateway.session_group_messaging_send import _capture
from tests.gateway.test_canonical_group_decisions import pending
from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_canonical_remembered_decisions import report
from tools import approval


@pytest.mark.asyncio
async def test_reload_and_new_profile_grant_neither_replace_nor_reactivate_room_permission(tmp_path, monkeypatch):
    async with owner(tmp_path, monkeypatch) as (authority, service, _runner):
        # Only durable records and approval callbacks are exercised, no executor.
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
        monkeypatch.setattr(approval, '_permanent_approved', set())
        monkeypatch.setattr(approval, '_permanent_approved_by_home', {})
        monkeypatch.setattr(approval, '_permanent_baseline_by_home', {})
        monkeypatch.setattr(approval, '_session_approved', {})
        config = tmp_path / 'config.yaml'
        config.write_text(yaml.safe_dump({'command_allowlist': ['profile-only']}))
        approval.load_permanent_allowlist()
        before = config.read_bytes()
        service.runtime.process_generation = 'decision-test'
        task, row, live, answers = await pending(authority, service)
        actor = Principal('alice', authority.profile_id, frozenset({'session:control'}), 'owner')
        dispatch_home_access(authority, actor, 'groups.control.home.set', {'room_id': 'room', 'enabled': True})
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'first-prompt')
        decision = dict(member_id='writer', task_id=task['identity'].task_id, execution_generation=1,
                        request_id='first-prompt', choice='remember', remember_key='a' * 64)
        args = dict(room=service._room('room'), guard=lambda: None, command_id='first-grant', params=decision)
        result = await asyncio.to_thread(decide, authority, **args)
        assert result['remembered'] is True
        first_rule, = rules.list_rules(service, 'room')
        assert answers == [('approval', 'first-prompt', 'once')]
        assert config.read_bytes() == before
        assert approval._session_approved == {}

        config.write_text(yaml.safe_dump({'command_allowlist': []}))
        assert approval.load_permanent_allowlist() == set()
        approval._persist_choice('separate-profile-session', 'always', [('profile-new', 'Fixture', False)])
        after_profile_grant = config.read_bytes()
        assert rules.list_rules(service, 'room') == [first_rule]
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'repeat-prompt')
        assert answers[-1] == ('approval', 'repeat-prompt', 'once')

        proof = _capture(authority, service._room('room'), guard=lambda: None)
        assert rules.revoke(service, proof, first_rule['rule_id'], first_rule['generation']) == 1
        assert approval.load_permanent_allowlist() == {'profile-new'}
        # An exact old decision receipt may replay its outcome, not resurrect its rule.
        assert await asyncio.to_thread(decide, authority, **args) == result
        assert rules.list_rules(service, 'room') == []
        await asyncio.to_thread(report, authority, service, task, row, live, answers, 'new-prompt')
        assert len(answers) == 2 and 'new-prompt' in live.controls.pending
        granted = await asyncio.to_thread(decide, authority, **{
            **args, 'command_id': 'new-grant', 'params': {**decision, 'request_id': 'new-prompt'}})
        assert granted['remembered'] is True
        new_rule, = rules.list_rules(service, 'room')
        assert new_rule['rule_id'] == first_rule['rule_id']
        assert new_rule['generation'] > first_rule['generation']
        assert answers[-1] == ('approval', 'new-prompt', 'once')
        assert config.read_bytes() == after_profile_grant
        assert approval._session_approved == {'separate-profile-session': {'profile-new'}}
