"""#109338: raw maintenance must not retire a queued native reconnect owner."""

from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.session_authorities import owner_scope
from hermes_state_raw_delete import SessionLedgerProtectedError
from hermes_state_runtime import get_session_admission
from tests.gateway.test_native_reconnect_recovery import Adapter, ledger, queued, state


@pytest.mark.asyncio
@pytest.mark.parametrize('revoke_sender', [False, True])
async def test_maintenance_preserves_original_native_queue_for_authorized_reconnect(
        state, revoke_sender):
    runner = state.runner
    receipt, _ = await queued(state, 'alpha', chat='retained-history')
    authority = state.authorities['alpha']
    db = authority.db
    sid = receipt.ref.session_id
    live = authority.sessions[sid]
    source, route = live.source, live.route
    controls, stream, subscribers = live.controls, live.event_stream, live.subscribers
    original_route = runner.session_store.lookup_by_session_key(route)
    original_admission = get_session_admission(db, admission_id=receipt.admission_id)
    assert original_admission is not None and original_admission['status'] == 'queued'
    original_payload = original_admission['payload']
    before_ledger = ledger(state)
    before_routes = [dict(row) for row in db._read_all('SELECT * FROM gateway_routing')]

    # The deliberately old/ended raw row is maintenance-eligible even while
    # its real admission remains queued. A legacy control proves the sweep ran.
    db.end_session(sid, 'complete')
    db._execute_write(lambda conn: conn.execute(
        'UPDATE sessions SET started_at=1, ended_at=2, last_activity_at=1 WHERE id=?', (sid,)))
    db.create_session('old-unowned', source='tui')
    db.end_session('old-unowned', 'complete')
    db._execute_write(lambda conn: conn.execute(
        'UPDATE sessions SET started_at=1, ended_at=2, last_activity_at=1 WHERE id=?', ('old-unowned',)))
    protected_row = db.get_session(sid)
    assert protected_row is not None
    queued_row = dict(protected_row)
    transcript = state.homes['alpha'] / 'sessions' / (sid + '.json')
    transcript.parent.mkdir(exist_ok=True)
    transcript.write_text('original transcript')

    with pytest.raises(SessionLedgerProtectedError) as refusal:
        db.delete_session(sid, sessions_dir=transcript.parent)
    assert refusal.value.reason == 'runtime_coordination_required'
    report = {}
    assert db.prune_sessions(older_than_days=None, sessions_dir=transcript.parent, report=report) == 1
    assert report == {'removed': 1, 'skipped_protected': 1}
    assert db.get_session('old-unowned') is None
    assert db.get_session(sid) == queued_row
    assert transcript.read_text() == 'original transcript'
    assert ledger(state) == before_ledger
    retained = get_session_admission(db, admission_id=receipt.admission_id)
    assert retained is not None and retained['status'] == 'queued' and retained['payload'] == original_payload
    assert [dict(row) for row in db._read_all('SELECT * FROM gateway_routing')] == before_routes
    assert runner.session_store.lookup_by_session_key(route) is original_route
    assert live.source is source and live.route == route
    assert (live.controls, live.event_stream, live.subscribers) == (controls, stream, subscribers)
    assert db._read_all('PRAGMA foreign_key_check') == []

    if revoke_sender:
        runner.allowed.clear()
    replacement = Adapter('alpha')
    runner._profile_adapters['alpha'].pop(Platform.TELEGRAM)
    runner._secondary_reconnect_attempt = AsyncMock(return_value=(replacement, True))
    with owner_scope(authority):
        await runner._run_secondary_profile_reconnect('alpha', Platform.TELEGRAM)
    assert runner._profile_adapters['alpha'][Platform.TELEGRAM] is replacement
    assert runner.session_authorities.for_home(str(state.homes['alpha'])) is authority
    assert state.scheduled == ([] if revoke_sender else [
        (str(state.homes['alpha']), sid, str(state.homes['alpha']))])
    assert ledger(state) == before_ledger
    retained = get_session_admission(db, admission_id=receipt.admission_id)
    assert retained is not None and retained['status'] == 'queued' and retained['payload'] == original_payload
    assert [dict(row) for row in db._read_all('SELECT * FROM gateway_routing')] == before_routes
    assert runner.session_store.lookup_by_session_key(route) is original_route
    assert (live.controls, live.event_stream, live.subscribers) == (controls, stream, subscribers)
    assert db._read_all('PRAGMA foreign_key_check') == []
    if revoke_sender:
        assert live.source is source and live.route == route
    else:
        assert live.source is not source and live.route == route
        assert runner._delivery_adapter_for(live.source) is replacement
