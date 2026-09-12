"""Selected accepted data contracts, not recovery activation or participant fault tests."""
import sqlite3

import pytest

from tests.gateway.passive_ingress_fixtures import pair  # noqa: F401


def test_v2_preview_preserves_original_member_home_and_unknown_tail(pair):
    from gateway.hosted_room_manual_recovery import prepare_recovery
    from tests.gateway.passive_ingress_fixtures import HOME, SUCCESSOR, TARGET
    pair.successor_fixture()  # Retained lineage fixture only; no promotion API.
    pair.enroll()
    pair.copy()
    pair.ingest_work()
    with sqlite3.connect(pair.target) as conn:
        before = list(conn.iterdump())
    preview = prepare_recovery(pair.target, room_id='room', target_gateway_id=TARGET)
    assert preview['room_id'] == 'room'
    assert preview['source_authority'] == {'gateway_id': SUCCESSOR, 'epoch': 2}
    original = next(member for member in preview['member_origins'] if member['member_id'] == 'writer')
    assert original['installation_id'] == HOME and original['profile'] == 'default'
    assert preview['accepted_tail'] == 'unverified' and preview['execution_authorized'] is False
    assert preview['reconciliation_required'] is True
    with sqlite3.connect(pair.target) as conn:
        assert list(conn.iterdump()) == before
        assert conn.execute("SELECT owner_kind FROM hosted_room_id_reservations WHERE room_id='room'").fetchone()[0] == 'replica'
        assert conn.execute("SELECT COUNT(*) FROM hosted_rooms WHERE room_id='room'").fetchone()[0] == 0


def test_custody_missing_ledger_or_legacy_binding_is_not_repaired(tmp_path):
    from gateway import hosted_room_local_custody as custody, hosted_room_custody_schema as schema
    from hermes_state_runtime import RuntimeStoreError
    path = tmp_path / 'custody.db'
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE hosted_room_local_sessions(room_id TEXT)')
        before = list(conn.iterdump())
        with pytest.raises(RuntimeStoreError, match='legacy_custody_migration_required'):
            custody.initialize(conn)
        assert list(conn.iterdump()) == before
        conn.execute('DROP TABLE hosted_room_local_sessions')
        schema.initialize_schema(conn)
        conn.execute(f'DROP TABLE {schema.MARKER}')
        before = list(conn.iterdump())
        with pytest.raises(RuntimeStoreError, match='original_custody_unavailable'):
            custody.initialize(conn)
        assert list(conn.iterdump()) == before


def test_read_install_id_never_mints_or_uses_a_cached_substitute(tmp_path):
    from hermes_cli.install_identity import read_install_id
    missing = tmp_path / 'missing'
    assert read_install_id(missing) is None and not missing.exists()
    path = tmp_path / 'install_id'
    path.write_text('not-a-valid-id')
    assert read_install_id(tmp_path) is None
    assert path.read_text() == 'not-a-valid-id'
    path.write_text('a' * 32 + '\n')
    assert read_install_id(tmp_path) == 'a' * 32
    path.unlink()
    assert read_install_id(tmp_path) is None
