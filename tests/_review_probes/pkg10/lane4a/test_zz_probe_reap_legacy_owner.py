"""Probe (lane 4a review, RT-43): a viewer's reap of a compression segment must not stamp it ended while
the gateway's ledger holds the conversation's turn. The ledger names a local conversation by its
creation id; ``_end_unless_ledger_busy`` adds the row's ``chat_id`` only when it starts with ``local-``.
An upgraded (adopted) Desktop/TUI conversation keeps its legacy id (``20261015_111010_fe1164``) as the
owner and as every segment's ``chat_id`` (#134889 report of 2026-10-18), so that id is never checked.

Same fixture shape as tests/tui_gateway/test_reap_respects_canonical_ledger.py; the receipt/lineage
needs only the rows' ``chat_id`` (what the reap reads); the admission is the store's real verb.
"""
import json
import threading

import pytest

from hermes_state import SessionDB
from hermes_state_runtime import admit_session_input, begin_runtime_epoch, claim_session_input
from tui_gateway.server import _teardown_session


def _viewer_session(tmp_path, monkeypatch, owner, segment, session_key):
    from run_agent import AIAgent
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    db = SessionDB(db_path=home / 'state.db')
    db.create_session(owner, source='gui', chat_id=owner)
    db.end_session(owner, 'compression')
    db.create_session(segment, source='gui', parent_session_id=owner, chat_id=owner)
    agent = AIAgent.__new__(AIAgent)
    agent.session_id = segment
    agent._session_db = db
    agent._owns_session_db = True
    agent._end_session_on_close = True
    agent._session_messages = []
    agent._active_children_lock = threading.Lock()
    agent._active_children = []
    agent._memory_manager = None
    session = {'agent': agent, 'history': [{'role': 'user', 'content': 'x'}], 'history_lock': None,
               'session_key': session_key, 'profile_home': str(home), 'source': 'gui'}
    return home / 'state.db', db, session, agent


@pytest.mark.parametrize('owner', ['local-' + 'a' * 16, '20261015_111010_fe1164'], ids=['native', 'upgraded'])
@pytest.mark.parametrize('claimed', [True, False], ids=['started', 'queued'])
def test_reap_of_a_segment_respects_the_owner_ledger(tmp_path, monkeypatch, owner, claimed):
    segment = '20261016_170020_c7fd05'
    path, db, session, agent = _viewer_session(tmp_path, monkeypatch, owner, segment, session_key=segment)
    epoch = begin_runtime_epoch(db, instance_id='gateway')
    admit_session_input(db, epoch=epoch, principal_id='owner', session_id=owner, request_id='r1',
                        payload={'text': 'long turn'})
    if claimed:
        claim_session_input(db, epoch=epoch, session_id=owner)

    _teardown_session(session, end_reason='ws_orphan_reap')

    check = SessionDB(db_path=path)
    try:
        row = check.get_session(segment)
        assert (row['ended_at'], row['end_reason']) == (None, None)
        assert agent._end_session_on_close is False
    finally:
        check.close()
