"""Canonical hosted producer reaches passive capture without an explicit publisher."""
import json
from typing import cast

import pytest

from hermes_state import SessionDB
from tests.gateway.test_canonical_hosted_outputs import owner


@pytest.mark.asyncio
async def test_canonical_send_and_stop_retain_driver_evidence(tmp_path, monkeypatch):
    from gateway import hosted_room_work_records as work
    async with owner(tmp_path, monkeypatch) as (authority, service, _):
        db = cast(SessionDB, authority.db)
        service.authorize_room('alice', 'capture', create=True)
        service.create_room(room_id='capture', name='Room', members=[
            {'member_id': 'one', 'profile': 'default', 'handle': 'one'},
            {'member_id': 'two', 'profile': 'other', 'handle': 'two',
             'target': {'kind': 'peer', 'installation_id': 'peer', 'profile': 'other',
                        'peer_id': 'peer', 'capability_digest': 'a' * 64}}])
        service.send(room_id='capture', event_id='input', payload={'text': '@one hello', 'thread_id': 'thread'})
        def evidence():
            with db._read_ctx() as conn:
                row = conn.execute(f'SELECT record_json FROM {work.SOURCE_TABLE}').fetchone()
                assert row is not None
                return work.validate(json.loads(row[0]))
        before = evidence()
        assert before['availability'] == 'available'
        assert before['tasks'][0]['phase'] == 'queued'
        service.stop_room('capture', cancel_id='stop')
        after = evidence()
        assert after['revision'] > before['revision']
        assert after['tasks'][0]['phase'] == 'cancelled'
        assert after['stop']['cancel_id'] == 'stop'
        assert after['stop']['revocation_complete'] is False
