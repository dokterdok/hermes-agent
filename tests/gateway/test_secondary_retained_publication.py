"""Output-owned secondary retained publication.

Missing registration fails closed. A registered publication can be published,
retried and completed with the owner's provenance, and stale or unauthorized
routes do not publish. Send-consent is not publication authority. The runner's
session authority and the primary retry rows stay put.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from gateway.hosted_room_artifacts import RoomArtifactError
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_canonical_hosted_outputs import execute_group_turn, owner


def _bind_retention_safety():
    """Point the Output fence at the Retention safety module when it is overlaid.

    The class alias is only the symbol Output's hosted_rooms module does not
    export. The quarantine read is the Retention function.
    """
    import gateway.hosted_room_safety as safety
    import gateway.hosted_rooms as rooms

    rooms.room_safety = safety
    if not hasattr(rooms, 'RoomQuarantinedError'):
        class RoomQuarantinedError(rooms.AuthorityConflictError):
            reason = 'room_authority_quarantined'
        rooms.RoomQuarantinedError = RoomQuarantinedError
    return safety


def _ensure_quarantine_table(db):
    def create(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS hosted_room_quarantine (
            room_id TEXT PRIMARY KEY, reason TEXT NOT NULL, detected_at REAL NOT NULL)''')
    db._execute_write(create)


def _primary(db):
    with db._read_ctx() as conn:
        def rows(sql):
            return [tuple(row) for row in conn.execute(sql)]
        return {
            'events': rows('SELECT event_id, kind, payload_json FROM hosted_room_events ORDER BY seq'),
            'retries': rows('SELECT room_id, task_id, execution_generation, operation, metadata_json, blocked '
                            'FROM hosted_room_artifact_retries ORDER BY task_id'),
            'completions': rows('SELECT room_id, task_id, execution_generation, operation, event_digest '
                                'FROM hosted_room_artifact_completions ORDER BY task_id'),
        }


def _secondary_counts(db):
    with db._read_ctx() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'hosted_room_secondary_publications' not in tables:
            return 0, 0
        registered = conn.execute('SELECT COUNT(*) FROM hosted_room_secondary_publications').fetchone()[0]
        completed = conn.execute('SELECT COUNT(*) FROM hosted_room_secondary_publication_completions').fetchone()[0]
        return registered, completed


async def _settled(tmp_path, monkeypatch):
    _bind_retention_safety()
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        _ensure_quarantine_table(authority.db)
        output = tmp_path / 'cache' / 'report.txt'
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'explicit secondary bytes')

        async def handle(event):
            from tools.registry import registry
            result = json.loads(await asyncio.to_thread(
                registry.dispatch, 'share_group_file', {'path': str(output)}))
            assert result.get('ok') is True, result
            return 'Shared report.'

        runner._handle_message = handle
        from tools import hosted_room_artifact  # registry discovery
        del hosted_room_artifact
        _rpc, _request, _receipt, task, _binding = await execute_group_turn(authority, service)
        from gateway import hosted_room_driver as tasks
        stored = tasks.get_task(service.db_path, task['identity'])
        assert stored['status'] == 'settled', stored
        yield authority, service, runner, stored


@pytest.mark.asyncio
async def test_unregistered_secondary_publication_fails_closed(tmp_path, monkeypatch):
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        before = _primary(authority.db)
        pointer = runner.session_authority
        with pytest.raises(RoomArtifactError, match='not registered'):
            service.publish_secondary_publication(task, 'missing')
        with pytest.raises(RoomArtifactError, match='not registered'):
            service.retry_secondary_publication(task, 'missing')
        with pytest.raises(RoomArtifactError, match='not registered'):
            service.complete_secondary_publication(task, 'missing', attempt=1)
        changes = authority.db._conn.total_changes
        with pytest.raises(RoomArtifactError, match='send consent is not publication authority'):
            service.publish_secondary_from_consent(task, {'permissions': ['publish'], 'expires_at': 10 ** 12})
        assert authority.db._conn.total_changes == changes
        assert _secondary_counts(authority.db) == (0, 0)
        assert _primary(authority.db) == before
        assert runner.session_authority is pointer is authority
        assert authority.hosted_room_service is service


@pytest.mark.asyncio
async def test_secondary_publish_retry_and_completion_keep_provenance(tmp_path, monkeypatch):
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        before = _primary(authority.db)
        pointer = runner.session_authority
        clock = {'now': time.time()}
        service._artifact_clock = lambda: clock['now']
        registered = service.register_secondary_publication(task)
        assert registered['accepted'] is True and registered['published'] is False
        assert registered['provenance']['owner_epoch'] == service._output_epoch
        assert registered['provenance']['owner_instance'] == service._output_instance
        assert registered['provenance']['publication']
        assert registered['valid_until'] == registered['provenance']['valid_until']
        again = service.register_secondary_publication(task)
        assert again['publication_id'] == registered['publication_id']
        assert again['valid_until'] == registered['valid_until']
        published = service.publish_secondary_publication(task, registered['publication_id'])
        assert published['accepted'] is True and published['published'] is True
        assert published['attempt'] == 1
        assert published['provenance']['work'] == registered['provenance']['work']
        assert published['provenance']['route'] == registered['provenance']['route']
        assert published['provenance']['member_id'] == registered['provenance']['member_id']
        assert published['valid_until'] == registered['valid_until']
        repeat = service.publish_secondary_publication(task, registered['publication_id'])
        assert repeat['attempt'] == 1 and repeat['published'] is True
        early = service.retry_secondary_publication(task, registered['publication_id'])
        assert early['accepted'] is False and early['attempt'] == 1
        failed = service.record_secondary_publication_failure(
            task, registered['publication_id'], attempt=1, error=ConnectionError('reset'))
        assert failed['accepted'] is False and failed['reason_code'] == 'transient' and failed['blocked'] is False
        assert failed['provenance'] == published['provenance']
        assert 'reset' not in json.dumps(failed['provenance'])
        clock['now'] = failed['next_attempt_at'] + 1
        retried = service.retry_secondary_publication(task, registered['publication_id'])
        assert retried['accepted'] is True and retried['attempt'] == 2
        assert retried['provenance']['publication'] == published['provenance']['publication']
        assert retried['valid_until'] == registered['valid_until']
        completed = service.complete_secondary_publication(
            task, registered['publication_id'], attempt=2)
        assert completed['completed'] is True
        assert completed['event_digest'] == published['provenance']['publication']
        assert completed['valid_until'] == registered['valid_until']
        assert _secondary_counts(authority.db) == (0, 1)
        reopened = service.publish_secondary_publication(task, registered['publication_id'])
        assert reopened['completed'] is True and _secondary_counts(authority.db) == (0, 1)
        denied = service.retry_secondary_publication(task, registered['publication_id'])
        assert denied['completed'] is True and _secondary_counts(authority.db) == (0, 1)
        assert _primary(authority.db) == before
        assert runner.session_authority is pointer is authority
        assert authority.hosted_room_service is service


@pytest.mark.asyncio
async def test_secondary_publication_rejects_unauthorized_and_stale_routes(tmp_path, monkeypatch):
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        with pytest.raises(RoomArtifactError, match='route is unauthorized'):
            service.register_secondary_publication(task, route='forged-route')
        assert _secondary_counts(authority.db) == (0, 0)
        clock = {'now': time.time()}
        service._artifact_clock = lambda: clock['now']
        registered = service.register_secondary_publication(task)
        def mutate(conn):
            row = conn.execute(
                'SELECT result_json FROM hosted_room_driver_tasks WHERE room_id=? AND task_id=?',
                ('room', task['identity'].task_id)).fetchone()
            result = json.loads(row[0])
            result['secondary_probe'] = 'changed'
            conn.execute(
                'UPDATE hosted_room_driver_tasks SET result_json=? WHERE room_id=? AND task_id=?',
                (json.dumps(result), 'room', task['identity'].task_id))
        authority.db._execute_write(mutate)
        with pytest.raises(RoomArtifactError, match='snapshot changed'):
            service.publish_secondary_publication(task, registered['publication_id'])
        assert _secondary_counts(authority.db) == (1, 0)
        task['result']['secondary_probe'] = 'changed'
        refused = service.publish_secondary_publication(task, registered['publication_id'])
        assert refused['publication_id'] == registered['publication_id']
        assert refused['accepted'] is False and refused['blocked'] is True
        assert refused['reason_code'] == 'stale_binding' and refused['published'] is False
        assert refused['attempt'] == 0 and refused['valid_until'] == registered['valid_until']
        with pytest.raises(RoomArtifactError, match='completion refused'):
            service.complete_secondary_publication(task, registered['publication_id'], attempt=1)
        assert _secondary_counts(authority.db) == (1, 0)
        assert runner.session_authority is authority
        assert authority.hosted_room_service is service


@pytest.mark.asyncio
async def test_secondary_publication_expires_without_extending_the_grant(tmp_path, monkeypatch):
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        before = _primary(authority.db)
        clock = {'now': time.time()}
        service._artifact_clock = lambda: clock['now']
        registered = service.register_secondary_publication(task)
        published = service.publish_secondary_publication(task, registered['publication_id'])
        assert published['attempt'] == 1 and published['valid_until'] == registered['valid_until']
        horizon = registered['valid_until']
        clock['now'] = horizon + 1
        expired = service.publish_secondary_publication(task, registered['publication_id'])
        assert expired['accepted'] is False and expired['published'] is False and expired['blocked'] is True
        assert expired['reason_code'] == 'expired_grant' and expired['valid_until'] == horizon
        assert expired['attempt'] == 1
        retried = service.retry_secondary_publication(task, registered['publication_id'])
        assert retried['accepted'] is False and retried['blocked'] is True
        assert retried['reason_code'] == 'expired_grant' and retried['attempt'] == 1
        with pytest.raises(RoomArtifactError, match='completion refused'):
            service.complete_secondary_publication(task, registered['publication_id'], attempt=1)
        with pytest.raises(RoomArtifactError, match='lifetime expired'):
            service.register_secondary_publication(task)
        assert _secondary_counts(authority.db) == (1, 0)
        assert _primary(authority.db) == before
        assert runner.session_authority is authority
        assert authority.hosted_room_service is service


@pytest.mark.asyncio
async def test_secondary_publication_lifetime_and_owner_fail_closed(tmp_path, monkeypatch):
    async for authority, service, runner, task in _settled(tmp_path, monkeypatch):
        clock = {'now': time.time()}
        service._artifact_clock = lambda: clock['now']
        registered = service.register_secondary_publication(task)
        published = service.publish_secondary_publication(task, registered['publication_id'])
        assert published['published'] is True
        blocked = service.record_secondary_publication_failure(
            task, registered['publication_id'], attempt=published['attempt'],
            error=RoomArtifactError('denied'))
        assert blocked['blocked'] is True and blocked['reason_code'] == 'authorization_or_verification'
        clock['now'] = blocked['next_attempt_at'] + 1
        still = service.retry_secondary_publication(task, registered['publication_id'])
        assert still['accepted'] is False and still['blocked'] is True
        with pytest.raises(RoomArtifactError, match='completion refused'):
            service.complete_secondary_publication(task, registered['publication_id'], attempt=published['attempt'])

        pointer = runner.session_authority
        runner.session_authority = SimpleNamespace()
        try:
            with pytest.raises(RoomArtifactError, match='owner changed'):
                service.register_secondary_publication(task)
            changes = authority.db._conn.total_changes
            with pytest.raises(RoomArtifactError, match='send consent is not publication authority'):
                service.publish_secondary_from_consent(task, {'ok': True})
            assert authority.db._conn.total_changes == changes
            assert runner.session_authority is not authority
        finally:
            runner.session_authority = pointer

        authority.epoch += 1
        try:
            with pytest.raises(RoomArtifactError, match='owner changed'):
                service.publish_secondary_publication(task, registered['publication_id'])
        finally:
            authority.epoch -= 1

        runner._draining = True
        try:
            with pytest.raises(RuntimeStoreError, match='runtime_draining'):
                service.retry_secondary_publication(task, registered['publication_id'])
        finally:
            runner._draining = False

        authority.db._db_replaced = True
        try:
            with pytest.raises(RuntimeStoreError, match='output_owner_unavailable'):
                service.complete_secondary_publication(task, registered['publication_id'], attempt=1)
        finally:
            authority.db._db_replaced = False

        authority.hosted_room_service = None
        try:
            with pytest.raises(RoomArtifactError, match='owner changed'):
                service.register_secondary_publication(task)
        finally:
            authority.hosted_room_service = service
        assert runner.session_authority is authority
        assert authority.hosted_room_service is service
        assert _secondary_counts(authority.db)[1] == 0
