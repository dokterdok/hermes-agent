"""Real messaging controls remain responsive with the supported DELETE journal."""
from contextlib import closing, contextmanager
import sqlite3

from tests.gateway import test_group_chat_messaging_journey as messaging


def test_shared_chat_control_with_delete_journal(tmp_path, monkeypatch):
    original = messaging.gateway

    @contextmanager
    def gateway(home):
        with original(home, journal_mode='delete') as run:
            with closing(sqlite3.connect(run.home.path / 'state.db')) as conn:
                assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
            yield run

    monkeypatch.setattr(messaging, 'gateway', gateway)
    messaging.test_a_shared_chat_is_its_own_audience_rules_and_grant(tmp_path)
