"""Probe (lane 4a review, RT-39/RT-44 guard): RT-44's ``_rewind`` guard (active user row of the physical
target) must refuse every target David's dropped RT-39 guard (81e4666af9: active row of the physical
target) refused, with no write: a compaction-archived row, a row an earlier rewind removed, the
superseded original of a carried tail, and a row of another session (a rotated compression's parent).
A live user row of the target still rewinds. Real SessionDB and the canonical mutation verb.
"""
import pytest

import hermes_state_runtime as rt
from hermes_state import SessionDB


def _rewind(db, epoch, sid, target, request_id):
    row = db.get_session(sid)
    return rt.mutate_runtime_session(db, epoch=epoch, principal_id='human', session_id=sid, request_id=request_id,
        expected_revision=row['runtime_revision'], expected_generation=row['runtime_generation'],
        operation='rewind', payload={'target_message_id': target})


def _ids(db, sid, **kw):
    return {m['content']: m['id'] for m in db.get_messages(sid, **kw)}


def _setup(db):
    db.create_session('s', source='test')
    db.append_messages_batch('s', [{'role': 'user', 'content': 'ARCHIVED_Q'}, {'role': 'assistant', 'content': 'A0'},
                                   {'role': 'user', 'content': 'REWOUND_Q'}, {'role': 'assistant', 'content': 'A1'}])
    db.rewind_to_message('s', _ids(db, 's')['REWOUND_Q'])
    db.append_messages_batch('s', [{'role': 'user', 'content': 'TAIL_Q'}, {'role': 'assistant', 'content': 'TAIL_A'}])
    every = {m['content']: m['id'] for m in db.get_messages('s', include_inactive=True)}
    db.archive_and_compact('s', [{'role': 'user', 'content': '[CONTEXT SUMMARY] earlier'},
                                 {'role': 'user', 'content': 'TAIL_Q'}, {'role': 'assistant', 'content': 'TAIL_A'}],
                           tail_count=2)
    db.create_session('other', source='test')
    db.append_message('other', role='user', content='OTHER_Q')
    return every


@pytest.mark.parametrize('kind', ['archived', 'rewound', 'carried_original', 'other_session', 'assistant', 'missing'])
def test_rewind_refuses_every_non_live_target_without_a_write(tmp_path, kind):
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        every = _setup(db)
        epoch = rt.begin_runtime_epoch(db, instance_id='owner')
        target = {'archived': every['ARCHIVED_Q'], 'rewound': every['REWOUND_Q'],
                  'carried_original': every['TAIL_Q'], 'other_session': _ids(db, 'other')['OTHER_Q'],
                  'assistant': _ids(db, 's')['TAIL_A'], 'missing': 10**6}[kind]
        live = [m['content'] for m in db.get_messages('s')]
        generation = db.get_session('s')['runtime_generation']
        with pytest.raises(rt.RuntimeStoreError, match='invalid_params'):
            _rewind(db, epoch, 's', target, f'r-{kind}')
        assert [m['content'] for m in db.get_messages('s')] == live
        assert db.get_session('s')['runtime_generation'] == generation


def test_a_live_user_row_still_rewinds(tmp_path):
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        _setup(db)
        epoch = rt.begin_runtime_epoch(db, instance_id='owner')
        receipt = _rewind(db, epoch, 's', _ids(db, 's')['TAIL_Q'], 'r-live')
        assert receipt['rewound_count'] == 2
        assert [m['content'] for m in db.get_messages('s')] == ['[CONTEXT SUMMARY] earlier']
