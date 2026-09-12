"""Canonical Run evidence + authored HTTP byte/ACK boundary, without an executor."""
import asyncio
import hashlib
import hmac
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_api_cutover_contract import api, owner  # noqa: F401
from tests.gateway.test_hosted_room_peer import _dispatch


def retained_output(api, owner, tmp_path):
    from gateway import hosted_rooms
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    from gateway.session_api_turn import admit_api_turn
    from gateway.session_results import finish_result
    from gateway.hosted_room_artifacts import RoomArtifactOutbox, RoomArtifactScope, terminal_artifact_manifest
    from hermes_state_runtime import claim_session_input
    owner.profile_id = str(tmp_path)
    install = hosted_rooms.local_authority_gateway_id()
    policy, catalog = _local_room_catalog(api, 'default', install)
    dispatch = _dispatch(target_profile='default', target_install_id=install, authority_epoch=1,
                         task_id='dtask:peer', execution_policy_digest=policy['policy_digest'],
                         capability_digest=catalog['catalog_digest'])
    identity = [dispatch.home_install_id, dispatch.room_id, dispatch.member_id, dispatch.target_profile]
    sid = 'room_' + hashlib.sha256('\0'.join(identity).encode()).hexdigest()[:32]
    args = dict(session_id=sid, user_message=dispatch.prompt, conversation_history=[],
                room_dispatch=dispatch.as_mapping(), room_execution_policy=policy, room_artifact_publication=True)
    # Canonical session generation is deliberately different from hosted generation.
    for run_id in ('earlier-run', 'file-run'):
        _, ref, row = admit_api_turn(api, active_run_id=run_id, **args)
        row = claim_session_input(owner.db, epoch=owner.epoch, session_id=ref.session_id)
        if run_id == 'earlier-run':
            finish_result(owner.db, epoch=owner.epoch, row=row, outcome='completed', response='prior')
    fields = ('room_id', 'task_id', 'execution_generation', 'member_id', 'target_profile',
              'home_install_id', 'target_install_id', 'authority_gateway_id', 'authority_epoch')
    scope = RoomArtifactScope.from_mapping({k: getattr(dispatch, k) for k in fields})
    outbox = RoomArtifactOutbox(owner.db.db_path)
    item = outbox.put_bytes(scope=scope, data=b'explicit peer result\n' * 2000, source_name='peer.txt')
    manifest = terminal_artifact_manifest(owner.db.db_path, scope)
    result = {'final_response': 'peer reply', 'artifacts': manifest, 'artifact_scope': scope.as_mapping()}
    finish_result(owner.db, epoch=owner.epoch, row=row, outcome='completed', response='peer reply',
                  result={'result': result, 'usage': {}})
    return dispatch, scope, outbox, item, manifest, row


def accepted_source_grant(api, dispatch, *, permissions=('status', 'artifact.read', 'artifact.ack')):
    """A signed #99159 issuer fixture; production mint permissions are not patched.

    Current verifier accepts authentic extended grants, but the parent still owns
    the issuer allowlist/invitation integration. Every HTTP check uses that verifier.
    """
    from gateway.hosted_room_peer import issue_room_grant, _split_token, _b64encode
    from gateway import hosted_rooms
    fields = ('room_id', 'home_install_id', 'authority_gateway_id', 'authority_epoch',
              'member_id', 'target_install_id', 'target_profile', 'execution_policy_digest')
    secret = api._room_grant_secret()
    token = issue_room_grant(secret, grant_id='fixture-grant', permissions=('status',),
                             **{k: getattr(dispatch, k) for k in fields})
    payload = json.loads(_split_token(token)[0])
    payload['permissions'] = sorted(permissions)
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('ascii')
    token = _b64encode(encoded) + '.' + _b64encode(hmac.new(secret, encoded, hashlib.sha256).digest())
    from gateway.hosted_room_grant_state import grant_state_db_paths, reserve_grant_state
    reserve_grant_state(grant_state_db_paths(), claims=payload, expires_at=payload['expires_at'])
    namespace = hashlib.sha256('\0'.join(str(payload[k]) for k in fields[:-1]).encode()).hexdigest()
    for run_id in ('earlier-run', 'file-run'):
        api._run_idempotency_store.reserve(namespace, run_id, 'a' * 64, run_id, {'status': 'completed'})
    return token


def home_publication(tmp_path, scope, manifest):
    from gateway import hosted_rooms, hosted_room_driver as tasks
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore
    path = tmp_path / 'home' / 'state.db'
    hosted_rooms.create_room(path, room_id=scope.room_id, name='Files',
        authority_gateway_id=scope.authority_gateway_id,
        members=[dict(member_id=scope.member_id, profile='default', handle='peer')])
    identity = tasks.TaskIdentity(scope.room_id, scope.task_id, 'thread', 'turn')
    tasks.admit_task(path, identity, payload=dict(target_member_id=scope.member_id,
        target_profile='default', source_event_seq=1, prompt='write'), clock=time.time)
    lease = tasks.acquire_lease(path, room_id=scope.room_id, gateway_id=scope.authority_gateway_id,
        authority_epoch=scope.authority_epoch, process_generation='test', ttl_seconds=60, clock=time.time)
    attempt = tasks.start_task(path, identity, lease, expected_cancel_generation=0, clock=time.time)
    tasks.settle_task(path, attempt, settlement_id='file-run', status='settled',
        result={'artifact_scope': scope.as_mapping(), 'artifacts': manifest}, clock=time.time)
    return path, HostedRoomAttachmentStore(path)


@pytest.mark.asyncio
async def test_peer_bytes_ack_requires_exact_run_and_home_publication(api, owner, tmp_path):
    from gateway import hosted_rooms
    from gateway.platforms.api_server_room_artifacts import _http_routes
    from gateway.platforms.api_server_authority_runs import run_projection
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError
    from tui_gateway.hosted_room_peer_artifacts import read_artifact, acknowledge_artifacts
    from tui_gateway.hosted_room_artifact_service import prepare_output, acknowledge_published
    from gateway.hosted_room_artifacts import RoomArtifactError
    dispatch, scope, outbox, item, manifest, row = retained_output(api, owner, tmp_path)
    grant = accepted_source_grant(api, dispatch)
    projected = run_projection(api, 'file-run')
    assert projected['execution_generation'] != scope.execution_generation
    assert projected['room_artifact_scope'] == scope.as_mapping()
    assert projected['artifacts'] == manifest
    assert 'artifacts' not in run_projection(api, 'earlier-run')
    api._run_statuses['file-run'] = {'status': 'completed', 'artifacts': {'forged': True}}
    app = web.Application()
    for method, path, handler in _http_routes(api):
        app.router.add_route(method, path, handler)
    async with TestClient(TestServer(app)) as http:
        client = PeerRunsHTTPClient(base_url=str(http.make_url('')).rstrip('/'), api_key='')
        read = lambda s, artifact_id: (next(x for x in manifest['items'] if x['artifact_id'] == artifact_id),
            read_artifact(client, run_id='file-run', artifact_id=artifact_id, grant=grant))
        ack = lambda s, ids, message_event_id: acknowledge_artifacts(client, run_id='file-run', artifact_ids=ids,
            manifest_digest=manifest['manifest_digest'], message_event_id=message_event_id, grant=grant)
        assert (await asyncio.to_thread(read, scope, item['artifact_id']))[1] == outbox.read(scope, item['artifact_id'])[1]
        with pytest.raises(PeerRunsHTTPError):
            await asyncio.to_thread(read_artifact, client, run_id='earlier-run', artifact_id=item['artifact_id'], grant=grant)
        wrong = await http.post('/v1/runs/file-run/artifacts/ack', headers={'Authorization': 'HermesRoom ' + grant},
            json=dict(artifact_ids=[item['artifact_id']], manifest_digest=manifest['manifest_digest'], message_event_id='wrong'))
        assert wrong.status == 409
        home, store = home_publication(tmp_path, scope, manifest)
        with pytest.raises(RoomArtifactError, match='not published'):
            await asyncio.to_thread(acknowledge_published, store, scope=scope, manifest=manifest, acknowledge=ack)
        assert outbox.read(scope, item['artifact_id'])[1]
        prepared = await asyncio.to_thread(prepare_output, store, scope=scope, manifest=manifest,
            recipient_member_ids=[scope.member_id], read_artifact=read)
        hosted_rooms.append_event(home, room_id=scope.room_id, event_id=prepared['event_id'], kind='message.member',
            actor=dict(kind='member', id=scope.member_id, profile='default'),
            payload=dict(task_id=scope.task_id, attachments=prepared['payload']['attachments']),
            authority_gateway_id=scope.authority_gateway_id, authority_epoch=scope.authority_epoch)
        accepted = await asyncio.to_thread(acknowledge_published, store, scope=scope, manifest=manifest, acknowledge=ack)
        assert accepted['acknowledged'] is True and outbox.retirement_complete(scope)
        from gateway.hosted_room_artifacts import ACKNOWLEDGED_ARTIFACT_RETENTION_SECONDS
        assert outbox.prune_acknowledged_receipts(now=time.time() + ACKNOWLEDGED_ARTIFACT_RETENTION_SECONDS + 1) == 1
        assert (await asyncio.to_thread(acknowledge_published, store, scope=scope, manifest=manifest, acknowledge=ack))['changed'] == 0


@pytest.mark.asyncio
async def test_peer_artifacts_do_not_infer_permission_or_use_named_owner(api, owner, tmp_path):
    from gateway.platforms.api_server_room_artifacts import _http_routes
    from gateway.platforms.api_server_authority_runs import run_projection
    from hermes_state_runtime import RuntimeStoreError
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError
    from tui_gateway.hosted_room_peer_artifacts import read_artifact
    dispatch, scope, outbox, item, manifest, row = retained_output(api, owner, tmp_path)
    grant = accepted_source_grant(api, dispatch, permissions=('status',))
    app = web.Application()
    for method, path, handler in _http_routes(api):
        app.router.add_route(method, path, handler)
    async with TestClient(TestServer(app)) as http:
        response = await http.get('/v1/runs/file-run/artifacts/' + item['artifact_id'],
                                  headers={'Authorization': 'HermesRoom ' + grant})
        assert response.status == 404
        client = PeerRunsHTTPClient(base_url=str(http.make_url('')).rstrip('/'), api_key='', target_profile='named')
        with pytest.raises(PeerRunsHTTPError, match='named peer'):
            read_artifact(client, run_id='file-run', artifact_id=item['artifact_id'], grant=grant)
    owner.profile_id = str(tmp_path / 'profiles' / 'named')
    with pytest.raises(RuntimeStoreError, match='storage_unavailable'):
        run_projection(api, 'file-run')
    assert outbox.read(scope, item['artifact_id'])[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('files_allowed', [False, True])
async def test_http_admission_retains_only_verified_files_permission(api, owner, tmp_path, monkeypatch, files_allowed):
    from gateway.platforms import api_server_runs
    from hermes_state_runtime import list_session_admissions
    dispatch, _, _, _, _, row = retained_output(api, owner, tmp_path)
    sid = row['target_session_id']
    permissions = ('status', 'dispatch', 'artifact.read', 'artifact.ack') if files_allowed else ('status', 'dispatch')
    grant = accepted_source_grant(api, dispatch, permissions=permissions)
    async def no_execution(*args, **kwargs):
        pass
    monkeypatch.setattr(api_server_runs, '_execute_run', no_execution)
    app = web.Application()
    app.router.add_post('/v1/runs', api._handle_runs)
    async with TestClient(TestServer(app)) as http:
        before = list_session_admissions(owner.db, session_id=sid, pending_only=False)
        forged = await http.post('/v1/runs', json={'input': 'plain', '_room_artifact_publication': True})
        assert forged.status == 400
        assert list_session_admissions(owner.db, session_id=sid, pending_only=False) == before
        response = await http.post('/v1/runs', headers={'Authorization': 'HermesRoom ' + grant,
            'Idempotency-Key': f'room:{dispatch.task_id}:{dispatch.execution_generation}'},
            json={'input': dispatch.prompt, 'hosted_room_dispatch': dispatch.as_mapping()})
        assert response.status == 202, await response.text()
    pending, = list_session_admissions(owner.db, session_id=sid)
    settings = pending['payload']['api_turn_v1']['settings']
    assert (settings.get('room_artifact_publication') is True) is files_allowed
    assert settings['room_dispatch'] == dispatch.as_mapping()


def test_run_output_never_uses_later_session_settings(api, owner, tmp_path):
    from gateway.platforms.api_server_authority_runs import run_projection
    from gateway.session_api_turn import _SETTINGS_PREFIX
    from hermes_state_runtime import RuntimeStoreError
    dispatch, scope, outbox, item, manifest, row = retained_output(api, owner, tmp_path)
    later = {**dispatch.as_mapping(), 'task_id': 'other-task'}
    owner.db._execute_write(lambda conn: conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?)',
        (_SETTINGS_PREFIX + row['target_session_id'], json.dumps({'room_dispatch': later}))))
    assert run_projection(api, 'file-run')['room_artifact_scope'] == scope.as_mapping()
    payload = row['payload']
    payload['api_turn_v1']['settings']['room_dispatch'] = later
    owner.db._execute_write(lambda conn: conn.execute('UPDATE session_admissions SET payload_json=? WHERE admission_id=?',
        (json.dumps(payload), row['admission_id'])))
    with pytest.raises(RuntimeStoreError, match='storage_unavailable'):
        run_projection(api, 'file-run')
    payload['api_turn_v1']['settings']['room_artifact_publication'] = False
    owner.db._execute_write(lambda conn: conn.execute('UPDATE session_admissions SET payload_json=? WHERE admission_id=?',
        (json.dumps(payload), row['admission_id'])))
    assert 'artifacts' not in run_projection(api, 'file-run')
