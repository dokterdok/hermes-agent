"""REPLACE must not erase frozen receipts through either declared unique key."""

import sqlite3
import json
from contextlib import closing

import pytest

from gateway.platforms import api_server_run_idempotency as storage
from gateway.platforms.api_server_run_scope import room_run_scope_key
from tests.gateway.test_group_run_freeze_store import reserve
from tests.gateway.test_group_run_scope import IDENTITY


def _replace(conn, operation, scope, key, run_id):
    if operation == "insert":
        conn.execute("""INSERT OR REPLACE INTO run_idempotency
            (scope,idempotency_key,fingerprint,run_id,status_json,owner_pid,owner_started,created_at,updated_at)
            SELECT ?,?,fingerprint,?,status_json,owner_pid,owner_started,created_at,updated_at
            FROM run_idempotency WHERE run_id='attacker'""", (scope, key, run_id))
    else:
        conn.execute("""UPDATE OR REPLACE run_idempotency
            SET scope=?, idempotency_key=?, run_id=? WHERE run_id='attacker'""", (scope, key, run_id))


@pytest.mark.parametrize("collision", ["run-id", "primary-key", "both"])
@pytest.mark.parametrize("operation", ["insert", "update"])
def test_replace_preserves_frozen_victims_and_all_statement_rows(tmp_path, collision, operation):
    path = tmp_path / "runs.db"
    other = {**IDENTITY, "room_id": "unfrozen"}
    with closing(storage.RunIdempotencyStore(str(path))) as store:
        reserve(store, "victim", status="running")
        reserve(store, "attacker", identity=other)
        reserve(store, "bystander", identity=other)
        before = store.freeze_room_scope(IDENTITY, "stop")
    with closing(sqlite3.connect(path)) as legacy:
        legacy.execute("PRAGMA recursive_triggers=OFF")
        assert legacy.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        rows = legacy.execute("SELECT * FROM run_idempotency ORDER BY run_id").fetchall()
        with closing(storage.RunIdempotencyStore(str(path))) as store:
            scope, key, run_id = room_run_scope_key(other), "replacement", "victim"
            if collision == "primary-key":
                scope, key, run_id = room_run_scope_key(IDENTITY), "key-victim", "replacement"
            elif collision == "both":
                key = "key-bystander"
            denied = False
            try:
                _replace(legacy, operation, scope, key, run_id)
                legacy.commit()
            except sqlite3.IntegrityError as exc:
                legacy.rollback()
                assert str(exc) == "group run scope frozen"
                denied = True
            # Check evidence first: a replaced victim would leave an empty successful snapshot.
            assert store.room_stop_snapshot("stop") == before
            assert denied
            assert legacy.execute("SELECT * FROM run_idempotency ORDER BY run_id").fetchall() == rows
            assert reserve(store, "victim", status="running")[0] == "reused"
        with closing(storage.RunIdempotencyStore(str(path))) as restarted:
            assert restarted.room_stop_snapshot("stop") == before


@pytest.mark.parametrize("operation", ["insert", "update"])
def test_unfrozen_replacements_and_frozen_bookkeeping_pruning_remain_usable(tmp_path, operation):
    path = tmp_path / "runs.db"
    other = {**IDENTITY, "room_id": "unfrozen"}
    with closing(storage.RunIdempotencyStore(str(path))) as store, closing(sqlite3.connect(path)) as legacy:
        legacy.execute("PRAGMA recursive_triggers=OFF")
        for run_id, status in (("active", "running"), ("terminal", "completed")):
            reserve(store, run_id, status=status)
        store.freeze_room_scope(IDENTITY, "stop")
        for run_id in ("attacker", "by-key", "by-id"):
            reserve(store, run_id, identity=other)
        # Both unique constraints can replace unfrozen victims, even beside a freeze.
        _replace(legacy, operation, room_run_scope_key(other), "key-by-key", "by-id")
        legacy.commit()
        assert store.lookup(room_run_scope_key(other), "key-by-key", "fingerprint-attacker")[0] == "reused"
        assert legacy.execute("SELECT count(*) FROM run_idempotency WHERE run_id='by-key'").fetchone()[0] == 0
        with legacy:
            legacy.execute("""UPDATE OR REPLACE run_idempotency
                SET scope=scope,idempotency_key=idempotency_key,run_id=run_id,
                    status_json='{"status":"stopping"}',retention_until=9999999999,acknowledged_at=1
                WHERE run_id='active'""")
        assert store.room_stop_snapshot("stop")["runs"][0]["status"] == "stopping"
        assert store.extend_retention(room_run_scope_key(IDENTITY), "active", 9999999999)
        with legacy:
            legacy.execute("DELETE FROM run_idempotency")
        snapshot = store.room_stop_snapshot("stop")
        assert snapshot["counts"] == {"total": 2, "terminal": 1, "nonterminal": 0, "unknown": 1}
        assert snapshot["missing_runs"] == 1 and snapshot["truncated"]


def test_old_frozen_store_migrates_only_negative_cancellation_rows(tmp_path):
    """The legacy trigger is the negative control, with its future schema column already present."""
    path = tmp_path / 'runs.db'
    scope, other_scope = room_run_scope_key(IDENTITY), room_run_scope_key({**IDENTITY, 'room_id': 'other'})
    with closing(storage.RunIdempotencyStore(str(path))) as store:
        reserve(store, 'victim', status='running')
        store.freeze_room_scope(IDENTITY, 'stop')
    with closing(sqlite3.connect(path)) as legacy:
        # Compose the prerequisite even on the old head: RED must be the old freeze trigger.
        if 'stop_requested' not in {row[1] for row in legacy.execute('PRAGMA table_info(run_idempotency)')}:
            legacy.execute('ALTER TABLE run_idempotency ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0')
        for trigger in ('group_run_frozen_insert_v3', 'group_run_frozen_identity_v3',
                        'group_run_frozen_insert_v2', 'group_run_frozen_identity_v2'):
            legacy.execute(f'DROP TRIGGER IF EXISTS {trigger}')
        victim = """SELECT 1 FROM run_idempotency AS victim JOIN group_run_freezes AS frozen
            ON frozen.scope=victim.scope WHERE victim.run_id=NEW.run_id OR
              (victim.scope=NEW.scope AND victim.idempotency_key=NEW.idempotency_key)"""
        legacy.execute(f"""CREATE TRIGGER group_run_frozen_insert_v2 BEFORE INSERT ON run_idempotency
            WHEN EXISTS (SELECT 1 FROM group_run_freezes WHERE scope=NEW.scope) OR EXISTS ({victim})
            BEGIN SELECT RAISE(ABORT, 'group run scope frozen'); END""")
        legacy.execute(f"""CREATE TRIGGER group_run_frozen_identity_v2 BEFORE UPDATE ON run_idempotency
            WHEN (EXISTS (SELECT 1 FROM group_run_freezes WHERE scope IN (OLD.scope,NEW.scope))
              AND (NEW.scope IS NOT OLD.scope OR NEW.idempotency_key IS NOT OLD.idempotency_key
                OR NEW.fingerprint IS NOT OLD.fingerprint OR NEW.run_id IS NOT OLD.run_id
                OR NEW.owner_pid IS NOT OLD.owner_pid OR NEW.owner_started IS NOT OLD.owner_started))
              OR EXISTS ({victim} AND NOT (victim.scope=OLD.scope AND victim.idempotency_key=OLD.idempotency_key))
            BEGIN SELECT RAISE(ABORT, 'group run scope frozen'); END""")
        legacy.commit()
    with closing(storage.RunIdempotencyStore(str(path))) as reopened, closing(sqlite3.connect(path)) as client:
        client.execute('PRAGMA recursive_triggers=OFF')

        def insert(label, **changes):
            values = dict(scope=scope, key='cancel-' + label, fingerprint='', run_id=label,
                owner_pid=0, owner_started=0, stop_requested=1)
            values.update(changes)
            values.setdefault('status_json', json.dumps(
                {'run_id': values['run_id'], 'status': 'cancelled', 'admission_cancelled': True}))
            client.execute("""INSERT OR REPLACE INTO run_idempotency
                (scope,idempotency_key,fingerprint,run_id,status_json,owner_pid,owner_started,stop_requested,created_at,updated_at)
                VALUES (:scope,:key,:fingerprint,:run_id,:status_json,:owner_pid,:owner_started,:stop_requested,1,1)""", values)

        insert('absent')
        client.commit()
        assert reopened.is_scope_frozen(scope)
        assert client.execute("""SELECT stop_requested,owner_pid,owner_started,fingerprint FROM run_idempotency
            WHERE run_id='absent'""").fetchone() == (1, 0, 0, '')
        for changes in ({'stop_requested': 0}, {'fingerprint': 'actual-admission'}, {'owner_pid': 123},
                        {'owner_started': 456}, {'status_json': '{}'}, {'status_json': 'null'},
                        {'status_json': 'not-json'},
                        {'status_json': json.dumps({'run_id': 'invalid', 'status': None})},
                        {'status_json': json.dumps({'run_id': 'wrong', 'status': 'cancelled'})},
                        {'status_json': json.dumps({'run_id': 'invalid', 'status': 'unknown'})}):
            with pytest.raises(sqlite3.IntegrityError, match='group run scope frozen'):
                insert('invalid', **changes)
            client.rollback()
        for changes in ({'run_id': 'victim', 'scope': other_scope}, {'key': 'key-victim'},
                        {'run_id': 'absent'}):
            with pytest.raises(sqlite3.IntegrityError, match='group run scope frozen'):
                insert('replacement', **changes)
            client.rollback()
        with pytest.raises(sqlite3.IntegrityError, match='group run scope frozen'):
            client.execute("UPDATE run_idempotency SET stop_requested=0 WHERE run_id='absent'")
        client.rollback()
        assert client.execute("SELECT run_id FROM run_idempotency ORDER BY run_id").fetchall() == [('absent',), ('victim',)]
        assert reopened.is_scope_frozen(scope)
