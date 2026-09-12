"""A valid later preparation cannot mix new custody proof with old coordinates."""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest

from gateway import hosted_room_local_custody as custody
from gateway import session_group_recovery as api
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch


def test_status_keeps_the_verified_snapshot_after_a_valid_custody_advance(tmp_path, monkeypatch):
    with SessionDB(tmp_path / 'state.db') as db:
        authority = SimpleNamespace(db=db, epoch=begin_runtime_epoch(db, instance_id='snapshot'))
        db.create_session('anchor', source='bot_room', profile_name='default')
        db.create_session('child', source='bot_room', profile_name='default', parent_session_id='anchor')
        db.end_session('anchor', 'compression')
        with db._read_ctx() as conn:
            chain = custody.metadata_chain(conn, 'anchor')
        row = dict(room_id='room', member_id='member', profile='default', gateway_id='install:original',
            session_id='anchor', session_started_at=chain[0][1], first_task_id='task', first_execution_generation=1,
            created_at=1.0, last_session_id='anchor', last_session_started_at=chain[0][1])
        def prepare(conn):
            custody.initialize(conn)
            custody.save(conn, row, chain[:1])
        db._execute_write(prepare)
        scope = dict(room_id='room', member_id='member', gateway_id='install:original', profile='default')
        before = custody.verify(db.db_path, **scope)
        original = api.readonly
        reads = []

        @contextmanager
        def advance_after_snapshot(path):
            with original(path) as conn:
                assert conn.in_transaction and conn.execute('PRAGMA query_only').fetchone()[0] == 1
                reads.append(True)
                yield conn
            advanced = {**row, 'last_session_id': 'child', 'last_session_started_at': chain[-1][1]}
            db._execute_write(lambda conn: custody.save(conn, advanced, chain))

        monkeypatch.setattr(api, 'readonly', advance_after_snapshot)
        result = api._status(authority, {'room_id': 'room', 'member_id': 'member'}, 'install:original')
        after = custody.verify(db.db_path, **scope)
        assert reads == [True]
        assert result['last_session_id'] == 'anchor' and result['custody_sha256'] == before
        assert before != after
        assert db._read_all(f'SELECT last_session_id FROM {custody.TABLE}')[0]['last_session_id'] == 'child'
        assert result['execution_authorized'] is False and result['old_admission_fenced'] is False
        assert result['accepted_tail'] == 'unverified'
        with sqlite3.connect(db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            with pytest.raises(RuntimeStoreError):
                custody.verify_locked(conn, **scope)
        with original(db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            verified, digest = custody.verify_locked(conn, **scope)
            assert verified['last_session_id'] == 'child' and digest == after
            assert custody.verify_locked(conn, **{**scope, 'member_id': 'absent'}, missing_ok=True) is None
            with pytest.raises(RuntimeStoreError):
                custody.verify_locked(conn, **{**scope, 'gateway_id': 'foreign'})
        with pytest.raises(RuntimeStoreError):
            custody.verify(tmp_path / 'absent.db', **{**scope, 'profile': 'named'})
        assert not (tmp_path / 'absent.db').exists()
