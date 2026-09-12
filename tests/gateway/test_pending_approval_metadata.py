"""Only bounded producer metadata reaches native approval projections."""
from copy import deepcopy

import pytest

from gateway.session_events import SessionEvents
from gateway.session_pending_controls import PendingControls


def project(changes=None):
    data = {'request_id': 'request', 'command': 'fixture', 'description': 'Fixture operation',
            'allow_permanent': True, 'allow_session': True, 'smart_denied': False,
            'remember_key': 'a' * 64, 'remember_context': 'Local, folder /workspace',
            'choices': ['remember'], 'execution_generation': 999, 'untrusted_extra': 'ignored'}
    data.update(changes or {})
    original = deepcopy(data)
    events = SessionEvents()
    epoch, sequence = events.watermark()
    controls = PendingControls(events)
    controls.register('owned-session', 'owned-route', 7, data)
    prompt = controls.pending['request'][1]
    event, = events.since(epoch, sequence)['events']
    assert event['payload'] == prompt
    assert data == original and prompt['execution_generation'] == 7
    assert 'untrusted_extra' not in prompt and 'remember' not in prompt['choices']
    return prompt


def test_projection_preserves_flags_and_redacts_context_without_widening_choices():
    prompt = project({'remember_context': 'Local, folder /work/ghp_' + 'A' * 36})
    assert prompt['remember_key'] == 'a' * 64
    assert 'A' * 36 not in prompt['remember_context']
    assert prompt['allow_permanent'] is True and prompt['allow_session'] is True
    assert prompt['smart_denied'] is False
    assert prompt['choices'] == ['once', 'deny', 'session', 'always']


@pytest.mark.parametrize('changes', [
    {'remember_key': 'b' * 63}, {'remember_key': 'A' * 64}, {'remember_key': None},
    {'remember_context': ''}, {'remember_context': 'x' * 385}, {'remember_context': 'line\nbreak'},
    {'remember_context': ['not text']}, {'allow_permanent': False}, {'allow_session': False},
    {'allow_permanent': 'true'}, {'allow_session': 1}, {'smart_denied': True},
    {'smart_denied': 0}, {'edit': {'path': 'fixture.txt', 'preview': 'unchanged'}},
])
def test_invalid_or_disallowed_remember_metadata_is_not_projected(changes):
    prompt = project(changes)
    assert 'remember_key' not in prompt and 'remember_context' not in prompt
    for key in ('allow_permanent', 'allow_session', 'smart_denied'):
        if key in changes:
            if type(changes[key]) is bool:
                assert prompt[key] is changes[key]
            else:
                assert key not in prompt
    if changes.get('smart_denied') is True or 'edit' in changes:
        assert prompt['choices'] == ['once', 'deny']
