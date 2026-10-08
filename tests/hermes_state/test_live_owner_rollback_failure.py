"""Rollback diagnostics must preserve the dependent caller's primary failure."""

import logging
import sqlite3

import pytest

from hermes_state import SessionDB


def test_live_owner_rollback_error_is_visible_without_replacing_primary_failure(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    connection = db._conn
    rollback_error = sqlite3.OperationalError("owner rollback unavailable")
    primary_error = ValueError("dependent operation refused")

    class RollbackFailure:
        def __getattr__(self, name):
            return getattr(connection, name)

        def rollback(self):
            raise rollback_error

    db._conn = RollbackFailure()
    try:
        with caplog.at_level(logging.WARNING, logger="hermes_state"):
            with pytest.raises(ValueError) as raised:
                with db.live_write_connection() as owner:
                    owner.execute("INSERT INTO state_meta(key,value) VALUES('audit.rollback','pending')")
                    raise primary_error
        assert raised.value is primary_error
        assert any(rollback_error in record.args for record in caplog.records)
        assert connection.in_transaction
        connection.rollback()
        assert db.get_meta("audit.rollback") is None
    finally:
        db._conn = connection
        connection.rollback()
        db.close()
