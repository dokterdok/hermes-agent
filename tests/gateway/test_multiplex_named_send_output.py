"""Named executor under the launch coordinator's real multiplex runtime."""
import base64
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from tests.gateway.test_hosted_mux_runtime import mux  # noqa: F401; real registered owners/socket fixture




@pytest.mark.live_system_guard_bypass
def test_named_send_two_files_adopt_partial_delivery_on_next_attempt(mux, monkeypatch):
    from gateway import hosted_room_driver as tasks
    from gateway.session_authority import SessionAuthority
    from gateway.session_authorities import owner_scope
    from gateway.session_hosted_service import ensure_hosted_service
    from gateway.session_hosted_output import current_output_binding
    from gateway.session_results import execution_result
    from gateway.session_group_files import dispatch_group_files
    from gateway.session_contract import Principal
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore
    from tools.registry import registry
    from tools import hosted_room_artifact  # noqa: F401; real Files producer

    runner, homes, _, call = mux
    source = runner.session_authorities.require(homes['default'])
    target = runner.session_authorities.require(homes['beta'])
    contents = [b'first retained attachment\n', b'second retained attachment\n']
    paths = [homes['beta'] / name for name in ('first.txt', 'second.txt')]
    for path, data in zip(paths, contents):
        path.write_bytes(data)
    executions = []

    async def finite(event):
        assert current_output_binding() is not None
        executions.append(event.message_id)
        for path in paths:
            result = json.loads(registry.dispatch('share_group_file', {'path': str(path)}))
            assert result['ok'] is True, result
        execution_result.get()['result'] = {
            'final_response': 'Two reports ready.', 'messages': [], 'completed': True}
        return 'Two reports ready.'

    runner._handle_message = finite
    for authority in runner.session_authorities:
        monkeypatch.setattr(authority, '_schedule', SessionAuthority._schedule.__get__(authority))
    call(ensure_hosted_service(runner))
    from gateway import hosted_room_recipient_files as files
    retain = files.retain_recipient_bytes
    interrupted = []

    def interrupt_second(authority, *, identity, manifest, data, attempt):
        if identity['index'] == 1 and attempt == 1:
            if not interrupted:
                interrupted.append(True)
            raise TimeoutError('recipient transport interrupted before second receipt')
        return retain(authority, identity=identity, manifest=manifest, data=data, attempt=attempt)

    monkeypatch.setattr(files, 'retain_recipient_bytes', interrupt_second)
    from gateway import session_hosted_transport as private
    from hermes_state_runtime import RuntimeStoreError
    request = private.owner_request

    def interrupt_response(home, verb, params, **kwargs):
        try:
            return request(home, verb, params, **kwargs)
        except RuntimeStoreError as exc:
            if (interrupted and verb == 'hosted-producer'
                    and params.get('operation') in {'secondary_deliver', 'secondary_receipt'}):
                raise TimeoutError('recipient transport interrupted before second receipt') from exc
            raise

    monkeypatch.setattr(private, 'owner_request', interrupt_response)
    with owner_scope(source):
        service = source.hosted_room_service
        clock = {'now': time.time()}
        service._artifact_clock = lambda: clock['now']
        service.runtime.poll_interval_seconds = .05
        service.runtime.active_poll_interval_seconds = .05
        service.authorize_room('alice', 'two-file-room', create=True)
        service.create_room(room_id='two-file-room', name='Two files', members=[
            {'member_id': 'host', 'profile': 'default', 'handle': 'host'},
            {'member_id': 'helper', 'profile': 'beta', 'handle': 'helper'}])
        service.send(room_id='two-file-room', event_id='input',
                     payload={'text': '@helper Share two reports', 'thread_id': 'thread'})
    # The real worker may already have claimed this task after send returns.
    queued, = tasks.list_tasks(source.db.db_path, room_id='two-file-room')
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        settled = tasks.get_task(source.db.db_path, queued['identity'])
        with source.db._read_ctx() as conn:
            rows = conn.execute('SELECT * FROM hosted_room_secondary_publications').fetchall()
            completions = conn.execute('SELECT * FROM hosted_room_secondary_publication_completions').fetchall()
        if (settled['status'] == 'settled' and len(executions) == len(rows) == 1
                and rows[0]['reason_code'] == 'transient'):
            break
        if completions:
            pytest.fail(f'unexpected completion before partial adoption: {dict(completions[0])}, '
                        f'interrupted={interrupted}, artifacts={settled["result"].get("artifacts")}')
        time.sleep(.05)
    else:
        pytest.fail(f'partial delivery never reached: {service.runtime.status()}, {rows}, {completions}')
    assert interrupted == [True] and not completions
    with target.db._read_ctx() as conn:
        first, = conn.execute('SELECT * FROM hosted_room_recipient_receipts').fetchall()
        assert first['write_attempt'] == 1
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_attachment_blobs').fetchone()[0] == 1
    with owner_scope(source):
        service._artifact_clock = lambda: rows[0]['next_attempt_at'] + .01
        # This is the production caller: retry Output, transfer the missing file
        # through the served recipient, then complete from authenticated custody.
        done = service.publish_settled_invitation_secondary(service.bindings()[0], settled)
        assert done['completed'] and done['attempt'] == 2
        assert done['write_attempts'] == [1, 2]
        assert done['write_attempt'] is None
        again = service.complete_secondary_publication(
            settled, done['publication_id'], attempt=2)
        assert again == done
    with source.db._read_ctx() as conn:
        completion, = conn.execute('SELECT * FROM hosted_room_secondary_publication_completions').fetchall()
        assert json.loads(completion['write_attempts_json']) == [1, 2]
        assert completion['write_attempt'] is None
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_secondary_publications').fetchone()[0] == 0
    from gateway.session_hosted_secondary_delivery import read_authenticated_recipient
    with owner_scope(source):
        custody = read_authenticated_recipient(service, settled, done['publication_id'])
    assert custody['receipt_digest'] == completion['receipt_digest']
    assert [receipt['write_attempt'] for receipt in custody['receipts']] == [1, 2]
    with target.db._read_ctx() as conn:
        received = conn.execute('SELECT * FROM hosted_room_recipient_receipts ORDER BY receipt_key').fetchall()
        blobs = conn.execute('SELECT * FROM hosted_room_attachment_blobs').fetchall()
    assert len(received) == len(blobs) == 2
    by_index = {json.loads(row['identity_json'])['index']: row for row in received}
    assert [by_index[i]['write_attempt'] for i in range(2)] == [1, 2]
    assert by_index[0]['receipt_key'] == first['receipt_key']
    assert by_index[0]['blob_id'] == first['blob_id']
    assert all(blob['ref_count'] == 1 for blob in blobs)
    events = [e for e in service._events('two-file-room') if e['kind'] == 'message.member'
              and e['payload'].get('task_id') == queued['identity'].task_id]
    event, = events
    assert len(executions) == 1
    viewer = Principal('alice', str(homes['default']), frozenset({'session:read'}), 'viewer')
    for index, attachment in enumerate(event['payload']['attachments']):
        downloaded = dispatch_group_files(service, viewer, 'groups.attachment.download', {
            'room_id': 'two-file-room', 'event_id': event['event_id'],
            'attachment_id': attachment['attachment_id']})
        assert base64.b64decode(downloaded['data_base64']) == contents[index]
        row = by_index[index]
        assert HostedRoomAttachmentStore(target.db.db_path)._read_blob(
            blob_id=row['blob_id'], size=row['size'], sha256=row['sha256']) == contents[index]


@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize('lose_response', [False, True, 'refuse', 'later-retry', 'later-retry-revoke',
                                           'source-revoke', 'target-close', 'target-read-close', 'target-replace'])
def test_named_send_finite_execution_and_retained_publication(mux, monkeypatch, lose_response):
    from gateway import hosted_room_driver as tasks
    from gateway.session_authority import SessionAuthority
    from gateway.session_authorities import owner_scope
    from gateway.session_hosted_service import ensure_hosted_service
    from gateway.session_hosted_transport import HostedRoomOwnerRPC
    from gateway.session_hosted_output import current_output_binding
    from gateway.session_results import execution_result
    from gateway.session_policy import policy_for_source
    from gateway.session_group_files import dispatch_group_files
    from gateway.session_contract import Principal
    from hermes_constants import get_hermes_home
    from hermes_state_runtime import RuntimeStoreError, list_session_admissions
    from tools.registry import registry
    from tools import hosted_room_artifact  # noqa: F401; register real Files producer

    def admissions(db):
        with db._read_ctx() as conn:
            return [tuple(row) for row in conn.execute('SELECT * FROM session_admissions ORDER BY admission_id')]

    runner, homes, _, call = mux
    root, target_home = homes['default'], homes['beta']
    source = runner.session_authorities.require(root)
    target = runner.session_authorities.require(target_home)
    assert source is runner.session_authority and source is not target
    assert source.db is not target.db and Path(target.db.db_path).parent == target_home
    content = b'named executor retained output bytes\n'
    output = target_home / 'report.txt'
    output.write_bytes(content)
    executions = []

    async def finite(event):
        binding = current_output_binding()
        assert binding is not None
        executions.append((str(get_hermes_home()), binding.authority.profile_id,
                           binding.scope.as_mapping(), event.message_id))
        value = json.loads(registry.dispatch('share_group_file', {'path': str(output)}))
        assert value['ok'] is True, value
        execution_result.get()['result'] = {
            'final_response': 'Named report ready.', 'messages': [], 'completed': True}
        return 'Named report ready.'

    runner._handle_message = finite
    for authority in runner.session_authorities:
        monkeypatch.setattr(authority, '_schedule', SessionAuthority._schedule.__get__(authority))
    call(ensure_hosted_service(runner))
    entered, release = threading.Event(), threading.Event()
    if lose_response == 'source-revoke':
        with owner_scope(source):
            service = source.hosted_room_service
            original_read = service.attachments.read_range
            def held_read(**kwargs):
                result = original_read(**kwargs)
                entered.set()
                assert release.wait(8)
                return result
            monkeypatch.setattr(service.attachments, 'read_range', held_read)
    if lose_response in {'target-close', 'target-read-close', 'target-replace'}:
        from gateway import session_hosted_secondary_delivery as delivery
        original_target = delivery.target_secondary_operation
        def held_target(authority, binding, operation, params, attested):
            if operation == ('secondary_receipt' if lose_response == 'target-read-close' else 'secondary_deliver'):
                entered.set()
                assert release.wait(8)
            return original_target(authority, binding, operation, params, attested)
        monkeypatch.setattr(delivery, 'target_secondary_operation', held_target)
    if lose_response:
        from gateway import session_hosted_transport as private
        request = private.owner_request
        dropped = []
        def lose_after_target_write(home, verb, params, **kwargs):
            if (lose_response == 'refuse' and verb == 'hosted-producer'
                    and params.get('operation') in {'secondary_deliver', 'secondary_receipt'}):
                raise ConnectionError('recipient unavailable before write')
            result = request(home, verb, params, **kwargs)
            if (verb == 'hosted-producer' and params.get('operation') == 'secondary_deliver'
                    and lose_response in (True, 'later-retry', 'later-retry-revoke', 'target-read-close') and not dropped):
                dropped.append(True)
                raise TimeoutError('response lost after recipient write')
            if (verb == 'hosted-producer' and params.get('operation') == 'secondary_receipt'
                    and lose_response in {'later-retry', 'later-retry-revoke'} and len(dropped) == 1):
                dropped.append(True)
                raise TimeoutError('receipt response lost after recipient write')
            return result
        monkeypatch.setattr(private, 'owner_request', lose_after_target_write)
    with owner_scope(source):
        service = source.hosted_room_service
        service.runtime.poll_interval_seconds = .05
        service.runtime.active_poll_interval_seconds = .05
        service.authorize_room('alice', 'named-room', create=True)
        service.create_room(room_id='named-room', name='Named send', members=[
            {'member_id': 'host', 'profile': 'default', 'handle': 'host'},
            {'member_id': 'helper', 'profile': 'beta', 'handle': 'helper'}])
        default_before = admissions(source.db)
        # A target profile label on the launch socket never becomes its authority.
        wrong = HostedRoomOwnerRPC(home=root, source_home=root,
            room_id='named-room', member_id='helper', profile='beta')
        with pytest.raises(RuntimeStoreError, match='profile_mismatch'):
            wrong.create(profile='beta', source='bot_room', title='Group: named-room')
        unserved = root / 'profiles' / 'unserved'
        unserved.mkdir()
        with pytest.raises(RuntimeStoreError, match='runtime_draining|profile_mismatch'):
            HostedRoomOwnerRPC(home=unserved, source_home=root,
                room_id='named-room', member_id='helper', profile='unserved').create(
                    profile='unserved', source='bot_room', title='Group: named-room')
        assert admissions(source.db) == default_before
        assert admissions(target.db) == []
        service.send(room_id='named-room', event_id='input',
                     payload={'text': '@helper Share the report', 'thread_id': 'thread'})
    queued, = tasks.list_tasks(source.db.db_path, room_id='named-room')
    if lose_response in {'source-revoke', 'target-close', 'target-read-close', 'target-replace'}:
        assert entered.wait(12), 'real byte read / target Files boundary never reached'
        from gateway.hosted_room_attachments import default_attachment_root
        blob_root = default_attachment_root(target.db.db_path) / 'blobs'
        blobs_before = {p.name for p in blob_root.iterdir()} if blob_root.exists() else set()
        try:
            if lose_response == 'source-revoke':
                from gateway import hosted_rooms as rooms
                with owner_scope(source):
                    room = service._room('named-room')
                    rooms.disband_room(source.db.db_path, room_id='named-room',
                        expected_gateway_id=room['authority_gateway_id'],
                        expected_epoch=room['authority_epoch'])
            elif lose_response == 'target-replace':
                original_path = Path(target.db.db_path)
                os.replace(original_path, target_home / 'retired-target.db')
                with sqlite3.connect(original_path):
                    pass
                pristine = original_path.read_bytes()
            else:
                target.db.close()
        finally:
            release.set()
    identity = queued['identity']
    assert queued['payload']['target_profile'] == 'beta'
    assert queued['payload']['target_member_id'] == 'helper'
    assert queued['payload']['recipient_member_ids'] == ['host', 'helper']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        settled = tasks.get_task(source.db.db_path, identity)
        if lose_response == 'source-revoke':
            with source.db._read_ctx() as conn:
                events = [row for row in conn.execute(
                    "SELECT event_id FROM hosted_room_events WHERE room_id=? AND kind='message.member'",
                    ('named-room',))]
        else:
            events = [e for e in service._events('named-room') if e['kind'] == 'message.member'
                      and e['payload'].get('task_id') == identity.task_id]
        with source.db._read_ctx() as conn:
            secondary = conn.execute('SELECT * FROM hosted_room_secondary_publications').fetchall()
            completed = conn.execute('SELECT * FROM hosted_room_secondary_publication_completions').fetchall()
        if (lose_response in {'refuse', 'source-revoke', 'target-close', 'target-read-close', 'target-replace'} and settled['status'] == 'settled'
                and len(events) == len(secondary) == len(executions) == 1
                and service.runtime.status()['last_error']):
            break
        if (lose_response in {'later-retry', 'later-retry-revoke'} and settled['status'] == 'settled'
                and len(events) == len(secondary) == len(executions) == 1
                and secondary[0]['reason_code'] == 'transient'):
            break
        if settled['status'] == 'settled' and len(events) == len(completed) == len(executions) == 1 and not secondary:
            break
        time.sleep(.05)
    else:
        pytest.fail(f'named worker did not publish: {service.runtime.status()}, task={settled}, '
                    f'executions={executions}, events={events}, secondary={secondary}, completed={completed}')
    if lose_response in {'later-retry', 'later-retry-revoke'}:
        assert dropped == [True, True] and completed == []
        with target.db._read_ctx() as conn:
            original, = conn.execute('SELECT * FROM hosted_room_recipient_receipts').fetchall()
            assert original['write_attempt'] == 1
        if lose_response == 'later-retry-revoke':
            from gateway import hosted_rooms as rooms
            with owner_scope(source):
                room = service._room('named-room')
                rooms.disband_room(source.db.db_path, room_id='named-room',
                    expected_gateway_id=room['authority_gateway_id'],
                    expected_epoch=room['authority_epoch'])
                from gateway.session_hosted_secondary_delivery import read_authenticated_recipient
                with pytest.raises(RuntimeStoreError):
                    read_authenticated_recipient(service, settled, secondary[0]['publication_id'])
            with source.db._read_ctx() as conn:
                assert conn.execute('SELECT COUNT(*) FROM hosted_room_secondary_publication_completions').fetchone()[0] == 0
            with target.db._read_ctx() as conn:
                assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 1
            return
        # Drive the real Output retry gate with its injectable clock; no synthetic
        # receipt or confirmation is supplied. The authenticated target is read again.
        retry_at = secondary[0]['next_attempt_at'] + .01
        service._artifact_clock = lambda: retry_at
        with owner_scope(source):
            retried = service.consume_secondary_retained_publication(settled)
            assert retried['attempt'] == 2 and retried['published']
            from gateway.session_hosted_secondary_delivery import read_authenticated_recipient
            adopted = read_authenticated_recipient(service, settled, retried['publication_id'])
            assert adopted['receipts'][0]['write_attempt'] == 1
            done = service.complete_secondary_publication(settled, retried['publication_id'],
                attempt=2, recipient_receipt=adopted)
            assert done['attempt'] == 2 and done['write_attempt'] == 1
        with source.db._read_ctx() as conn:
            completed = conn.execute('SELECT * FROM hosted_room_secondary_publication_completions').fetchall()
            secondary = conn.execute('SELECT * FROM hosted_room_secondary_publications').fetchall()
    if lose_response in {'refuse', 'source-revoke', 'target-close', 'target-read-close', 'target-replace'}:
        assert completed == [] and len(secondary) == 1
        if lose_response in {'source-revoke', 'target-close', 'target-replace'}:
            assert ({p.name for p in blob_root.iterdir()} if blob_root.exists() else set()) == blobs_before
        if lose_response in {'target-close', 'target-read-close', 'target-replace'}:
            with sqlite3.connect(f'file:{target.db.db_path}?mode=ro', uri=True) as conn:
                present = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='hosted_room_recipient_receipts'").fetchone()[0]
                assert present == (1 if lose_response == 'target-read-close' else 0)
                if present:
                    assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 1
            if lose_response == 'target-replace':
                assert Path(target.db.db_path).read_bytes() == pristine
        else:
            with target.db._read_ctx() as conn:
                assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='hosted_room_recipient_receipts'").fetchone()[0] == 0
        assert secondary[0]['reason_code'] in {'pending', 'transient'}
        assert len(executions) == len(events) == 1
        if lose_response == 'refuse':
            with owner_scope(source), pytest.raises(ConnectionError, match='recipient unavailable'):
                service.complete_secondary_publication(
                    settled, secondary[0]['publication_id'], attempt=secondary[0]['attempts'])
        with source.db._read_ctx() as conn:
            assert conn.execute('SELECT COUNT(*) FROM hosted_room_secondary_publication_completions').fetchone()[0] == 0
        return
    if lose_response is True:
        assert dropped == [True]
    scope = executions[0][2]
    assert executions[0][:2] == (str(target_home), str(target_home))
    assert scope['room_id'] == 'named-room' and scope['task_id'] == identity.task_id
    assert scope['member_id'] == 'helper' and scope['target_profile'] == 'beta'
    assert scope['execution_generation'] == settled['execution_generation']
    assert settled['result']['artifact_scope'] == scope
    assert settled['result']['owner_output_receipt']['target_session_id'] in target.sessions
    assert policy_for_source(runner, target.sessions[
        settled['result']['owner_output_receipt']['target_session_id']].source).model == 'fixture-beta'
    assert not source.sessions
    row, = list_session_admissions(target.db, session_id=settled['result']['owner_output_receipt']['target_session_id'], pending_only=False)
    assert row['status'] == 'terminal' and row['outcome'] == 'completed'
    assert row['target_session_id'] == settled['result']['owner_output_receipt']['target_session_id']
    assert admissions(source.db) == default_before
    event, = events
    assert event['payload']['recipient_member_ids'] == ['host', 'helper']
    attachment, = event['payload']['attachments']
    viewer = Principal('alice', str(root), frozenset({'session:read'}), 'viewer')
    downloaded = dispatch_group_files(service, viewer, 'groups.attachment.download', {
        'room_id': 'named-room', 'event_id': event['event_id'],
        'attachment_id': attachment['attachment_id']})
    assert base64.b64decode(downloaded['data_base64']) == content
    item, = settled['result']['artifacts']['items']
    assert item['sha256'] == hashlib.sha256(content).hexdigest()
    publication, = completed
    provenance = json.loads(publication['metadata_json'])
    assert secondary == []
    assert publication['operation'] == 'publish'
    assert publication['attempt'] == (2 if lose_response == 'later-retry' else 1)
    assert publication['write_attempt'] == 1
    assert provenance['owner_epoch'] == source.epoch
    assert provenance['owner_instance'] == source.instance_id
    with source.db._read_ctx() as conn:
        live = service._output_metadata(conn, service._output_key(settled))
        assert all(provenance[k] == live[k] for k in ('work', 'route', 'lineage', 'member_id'))
        assert provenance['publication'] == service._output_events_digest(conn, service._output_key(settled))
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_secondary_publication_completions').fetchone()[0] == 1
    from gateway.session_hosted_secondary_delivery import read_authenticated_recipient
    with owner_scope(source):
        recipient = read_authenticated_recipient(service, settled, publication['publication_id'])
    assert recipient['receipt_digest'] == publication['receipt_digest']
    assert recipient['identity']['target_home'] == str(target_home)
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore
    one, = recipient['receipts']
    assert one['sha256'] == hashlib.sha256(content).hexdigest()
    assert one['write_attempt'] == 1
    with target.db._read_ctx() as conn:
        received, = conn.execute('SELECT * FROM hosted_room_recipient_receipts').fetchall()
        assert conn.execute('SELECT COUNT(*) FROM hosted_rooms').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_attachment_blobs').fetchone()[0] == 1
        assert received['write_attempt'] == 1
    assert received['manifest_json'] == json.dumps(one['manifest'], sort_keys=True, separators=(',', ':'))
    assert HostedRoomAttachmentStore(target.db.db_path)._read_blob(
        blob_id=received['blob_id'], size=received['size'], sha256=received['sha256']) == content
    if lose_response is False:
        from gateway import hosted_room_recipient_files as files
        from gateway.hosted_room_attachments import AttachmentQuotaError, AttachmentIntegrityError
        with monkeypatch.context() as quota:
            quota.setattr(files, '_store', lambda authority: HostedRoomAttachmentStore(
                authority.db.db_path, _defer_initialization=True, gateway_quota_count=2))
            second = {**one['identity'], 'publication_id': 'second-distinct-publication',
                      'valid_until': time.time() + .2}
            third = {**one['identity'], 'publication_id': 'third-distinct-publication'}
            saved = files.retain_recipient_bytes(target, identity=second,
                manifest=one['manifest'], data=content, attempt=1)
            assert saved['write_attempt'] == 1
            with target.db._read_ctx() as conn:
                assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 2
                assert conn.execute('SELECT ref_count FROM hosted_room_attachment_blobs').fetchone()[0] == 2
            files.retain_recipient_bytes(target, identity=second,
                manifest=one['manifest'], data=content, attempt=2)
            with pytest.raises(AttachmentQuotaError):
                files.retain_recipient_bytes(target, identity=third,
                    manifest=one['manifest'], data=content, attempt=1)
            for field, changed in (('publication_id', 'changed'), ('event_digest', 'changed'),
                                   ('route', 'changed'), ('member_id', 'wrong-recipient')):
                with pytest.raises(AttachmentIntegrityError):
                    files.read_recipient_bytes(target, identity={**one['identity'], field: changed},
                        manifest=one['manifest'], attempt=2)
            time.sleep(.23)
            with pytest.raises(AttachmentIntegrityError):
                files.read_recipient_bytes(target, identity=second, manifest=one['manifest'], attempt=2)
            with target.db._read_ctx() as conn:
                assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 2
            # The Files maintenance path also reclaims receipt metadata and
            # precisely one shared reference without a recipient read doing so.
            maintained = HostedRoomAttachmentStore(target.db.db_path, _defer_initialization=True)
            assert maintained.prune(now=time.time()) >= 1
            with target.db._read_ctx() as conn:
                assert conn.execute('SELECT ref_count FROM hosted_room_attachment_blobs').fetchone()[0] == 1
            files.retain_recipient_bytes(target, identity=third,
                manifest=one['manifest'], data=content, attempt=1)
            with target.db._read_ctx() as conn:
                assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 2
                assert conn.execute('SELECT ref_count FROM hosted_room_attachment_blobs').fetchone()[0] == 2
                assert conn.execute('SELECT COUNT(*) FROM hosted_room_attachment_blobs').fetchone()[0] == 1
    with owner_scope(source):
        again = service.complete_secondary_publication(
            settled, publication['publication_id'], attempt=publication['attempt'])
    assert again['completed'] is True
    def corrupt(conn):
        conn.execute('UPDATE hosted_room_recipient_receipts SET sha256=? WHERE receipt_key=?',
                     ('0' * 64, received['receipt_key']))
    target.db._execute_write(corrupt)
    with owner_scope(source), pytest.raises((RuntimeStoreError, ValueError)):
        service.complete_secondary_publication(
            settled, publication['publication_id'], attempt=publication['attempt'])
    with owner_scope(source), pytest.raises((RuntimeStoreError, ValueError)):
        service.publish_settled_invitation_secondary(service.bindings()[0], settled)
    with target.db._read_ctx() as conn:
        rows = conn.execute('SELECT * FROM hosted_room_output_artifacts').fetchall()
        assert len(rows) == 1 and rows[0]['artifact_id'] == item['artifact_id']
        assert rows[0]['acknowledged_at'] is not None
    before = service.runtime.status()['cycles']
    deadline = time.monotonic() + 5
    while service.runtime.status()['cycles'] < before + 2 and time.monotonic() < deadline:
        time.sleep(.05)
    assert service.runtime.status()['cycles'] >= before + 2
    assert len(executions) == 1
    assert len([e for e in service._events('named-room') if e['kind'] == 'message.member'
                and e['payload'].get('task_id') == identity.task_id]) == 1
    assert len(admissions(target.db)) == 1
