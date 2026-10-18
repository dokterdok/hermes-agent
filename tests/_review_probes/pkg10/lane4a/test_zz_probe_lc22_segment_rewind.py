"""Probe (lane 4a review, LC-22 widening): ``_owner_ref`` now moves authorization to the lineage owner
for EVERY operation, but the write still names the listed segment. A generation-fenced rewind addressed
by a compression segment's id must keep the same fences as the same rewind by the conversation id:
queued input in the owner's FIFO refuses it (``session_busy``), and an applied rewind bumps the
conversation's generation and evicts its cached agent.

Real authority, SQLite, compression publish (tests/gateway/test_local_resume_compression_child), real
store admission verb, AuthorityConnection.dispatch.
"""
import pytest

import hermes_state_runtime as rt
from tests.gateway.test_local_resume_compression_child import _compressed

CAPS = ['session:create', 'session:read', 'session:submit', 'session:control']


def _connection(authority):
    from gateway.session_controls import AuthorityConnection
    return AuthorityConnection(authority, object(), {
        'provider': 'local', 'user_id': 'owner', 'profile_id': 'fixture', 'instance_id': authority.instance_id,
        'capabilities': CAPS})


async def _rewind(authority, sid, target, request_id):
    row = authority.db.get_session(sid)
    return await _connection(authority).dispatch({'id': 1, 'method': 'session.mutate', 'params': {
        'session_id': sid, 'request_id': request_id, 'expected_revision': row['runtime_revision'],
        'expected_generation': row['runtime_generation'], 'operation': 'rewind',
        'payload': {'target_message_id': target}}})


async def _setup(tmp_path, monkeypatch):
    compressed = await _compressed(tmp_path, monkeypatch)
    authority = compressed.warm
    evicted = []
    authority.runner._evict_cached_agent = lambda route, *a, **k: evicted.append(route)
    db = authority.db
    db.append_message('child-2', role='user', content='LIVE_Q')
    db.append_message('child-2', role='assistant', content='LIVE_A')
    target = next(m['id'] for m in db.get_messages('child-2') if m['content'] == 'LIVE_Q')
    return compressed, authority, db, target, evicted


@pytest.mark.asyncio
@pytest.mark.parametrize('addressed', ['owner', 'segment'])
async def test_rewind_refuses_while_the_owner_fifo_holds_queued_input(tmp_path, monkeypatch, addressed):
    compressed, authority, db, target, _ = await _setup(tmp_path, monkeypatch)
    rt.admit_session_input(db, epoch=authority.epoch, principal_id='owner', session_id=compressed.owner,
                           request_id='queued-1', payload={'text': 'queued after LIVE_A'})
    sid = compressed.owner if addressed == 'owner' else 'child-2'
    reply = await _rewind(authority, sid, target, f'rewind-{addressed}')
    assert reply.get('error', {}).get('message') in {'session_busy', 'storage_unavailable', 'not_found'}, reply
    assert [m['content'] for m in db.get_messages('child-2')][-2:] == ['LIVE_Q', 'LIVE_A']


@pytest.mark.asyncio
async def test_an_applied_segment_rewind_projects_on_the_conversation(tmp_path, monkeypatch):
    compressed, authority, db, target, evicted = await _setup(tmp_path, monkeypatch)
    before = db.get_session(compressed.owner)['runtime_generation']
    reply = await _rewind(authority, 'child-2', target, 'rewind-segment')
    if 'error' in reply:
        return  # a named refusal is fine
    assert db.get_session(compressed.owner)['runtime_generation'] > before, (reply, before)
    assert evicted, 'the cached agent of the conversation route was not evicted'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,payload', [
    ('workspace', {'cwd': 'MOVED'}),
    ('reasoning', {'effort': 'high'}),
    ('compress', {}),
    ('reset', {}),
    ('branch', {}),
])
async def test_policy_ops_addressed_by_a_segment_are_refused_or_land_on_the_conversation(
        tmp_path, monkeypatch, operation, payload):
    """Desktop's Move to project names the sidebar row (``session_key``), which after a compression
    is the tip segment. Either a named refusal, or the conversation's policy/target moves; never a
    write on the segment alone with the conversation's live state untouched."""
    from hermes_state_local import local_receipt
    compressed, authority, db, target, evicted = await _setup(tmp_path, monkeypatch)
    if operation == 'workspace':
        moved = tmp_path / 'moved'
        moved.mkdir()
        payload = {'cwd': str(moved)}
    before = local_receipt(db, compressed.owner)
    owner_before = db.get_session(compressed.owner)
    row = db.get_session('child-2')
    reply = await _connection(authority).dispatch({'id': 1, 'method': 'session.mutate', 'params': {
        'session_id': 'child-2', 'request_id': f'{operation}-segment', 'expected_revision': row['runtime_revision'],
        'expected_generation': row['runtime_generation'], 'operation': operation, 'payload': payload}})
    print(f'{operation}: {reply}' + chr(10), end='')
    if 'error' in reply:
        assert reply['error']['message'] != 'internal_error', reply
        return
    if operation == 'branch':
        return  # a new conversation: nothing of the owner's to move
    after = local_receipt(db, compressed.owner)
    owner_after = db.get_session(compressed.owner)
    assert (after != before) or (owner_after['runtime_generation'] != owner_before['runtime_generation']), (
        reply, 'segment write left the conversation untouched')
