"""Signed peer bytes cross HTTP once and remain available to the durable FIFO."""
import asyncio
import base64
import hashlib
from pathlib import Path
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_api_cutover_contract import api, owner  # noqa: F401
from tests.gateway.test_roomlink_input_spool import _dispatch, _manifest


def grant_for(api, dispatch, permissions=None):
    from gateway.hosted_room_peer import issue_room_grant, decode_room_grant
    from gateway.hosted_room_grant_state import grant_state_db_paths, reserve_grant_state
    fields = ('room_id', 'home_install_id', 'authority_gateway_id', 'authority_epoch',
              'member_id', 'target_install_id', 'target_profile', 'execution_policy_digest')
    token = issue_room_grant(api._room_grant_secret(), grant_id='input-grant',
        **{k: getattr(dispatch, k) for k in fields}, **({'permissions': permissions} if permissions else {}))
    claims = decode_room_grant(api._room_grant_secret(), token, permission='status')
    reserve_grant_state(grant_state_db_paths(), claims=claims, expires_at=claims['status_expires_at'])
    return token


@pytest.mark.asyncio
@pytest.mark.parametrize('image', [False, True])
async def test_transport_retains_exact_input_through_queue_and_staging_expiry(api, owner, tmp_path, monkeypatch, image):
    from gateway import hosted_rooms, hosted_room_driver as tasks
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore
    from gateway.platforms import api_server_runs, api_server_room_attachments
    from gateway.platforms.api_server import body_limit_middleware, MAX_REQUEST_BYTES
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    from gateway.session_api_turn import prepare_api_execution
    from gateway.session_contract import SessionRef
    from gateway.session_ingress_media import restore_native_media
    from hermes_state_runtime import list_session_admissions
    from tui_gateway.hosted_room_driver import HostedRoomBinding
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient
    from tui_gateway.hosted_room_peer_transport import PeerHostedRoomTransport, PeerMemberRoute
    owner.profile_id = str(tmp_path)
    install = hosted_rooms.local_authority_gateway_id()
    policy, catalog = _local_room_catalog(api, 'default', install)
    dispatch = _dispatch(target_install_id=install, capability_digest=catalog['catalog_digest'],
                         execution_policy_digest=policy['policy_digest'])
    token = grant_for(api, dispatch)
    home = tmp_path / 'home' / 'state.db'
    hosted_rooms.create_room(home, room_id=dispatch.room_id, name='Files',
        authority_gateway_id=dispatch.authority_gateway_id,
        members=[dict(member_id=dispatch.member_id, profile='default', handle='reviewer')])
    store = HostedRoomAttachmentStore(home)
    data = (base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=')
            if image else b'peer-file\n' * 1_200_000)
    item = store.put(room_id=dispatch.room_id, upload_id='input', kind='image' if image else 'file',
                     name='pixel.png' if image else 'brief.txt', mime='image/png' if image else 'text/plain', data=data)
    item = {key: item[key] for key in ('attachment_id', 'kind', 'name', 'size', 'mime')}
    store.commit_message(room_id=dispatch.room_id, event_id='source', manifest=[item],
                         recipient_member_ids=[dispatch.member_id], viewer_access=True)
    hosted_rooms.append_event(home, room_id=dispatch.room_id, event_id='source', kind='message.user',
        actor=dict(kind='user', id='owner'), payload=dict(text=dispatch.prompt, attachments=[item]),
        authority_gateway_id=dispatch.authority_gateway_id, authority_epoch=dispatch.authority_epoch)
    attachment = {**item, 'event_id': 'source'}
    async def queued(*args, **kwargs):
        pass
    monkeypatch.setattr(api_server_runs, '_execute_run', queued)
    app = web.Application(middlewares=[body_limit_middleware], client_max_size=MAX_REQUEST_BYTES)
    for method, path, handler in api._http_route_table():
        app.router.add_route(method, path, handler)
    async with TestClient(TestServer(app)) as http:
        client = PeerRunsHTTPClient(base_url=str(http.make_url('')).rstrip('/'), api_key='')
        route = PeerMemberRoute(home_install_id=dispatch.home_install_id, member_id=dispatch.member_id,
            target_install_id=install, target_profile='default', capability_digest=catalog['catalog_digest'],
            cancellation_scope_id=dispatch.cancellation_scope_id, trace_id=dispatch.trace_id, grant=token,
            execution_policy_digest=policy['policy_digest'], attachments=True)
        rpc = PeerHostedRoomTransport(binding=HostedRoomBinding(dispatch.room_id, dispatch.authority_gateway_id, 1),
            route=route, client=client, task_id=dispatch.task_id, execution_generation=1, attachment_store=store)
        coords = dict(profile='default', source='bot_room')
        sid = (await asyncio.to_thread(rpc.create, **coords, title='Group: ' + dispatch.room_id))['session_id']
        request = dict(**coords, session_id=sid, prompt=dispatch.prompt,
            task=tasks.TaskIdentity(dispatch.room_id, dispatch.task_id, 'thread', 'turn'), execution_generation=1,
            on_terminal=lambda _: None, attachments=[attachment])
        accepted = await asyncio.to_thread(rpc.submit, **request)
        assert (await asyncio.to_thread(rpc.submit, **request))['run_id'] == accepted['run_id']
        from gateway.platforms.api_server_authority_runs import run_admission
        exact_owner, row = run_admission(api, accepted['run_id'])
        assert exact_owner is owner
        sid = row['target_session_id']
        assert list_session_admissions(owner.db, session_id=sid) == [row]
        settings = row['payload']['api_turn_v1']['settings']
        assert settings['room_input_media']['manifest'][0]['sha256'] == hashlib.sha256(data).hexdigest()
        spool = api_server_room_attachments._default_spool()
        spool.prune(now=time.time() + api_server_room_attachments.SPOOL_TTL_SECONDS + 1)
        prepared = prepare_api_execution(owner, SessionRef(owner.profile_id, sid), row['payload'])
        paths = restore_native_media(settings['room_input_media']['media'])
        assert Path(paths[0]).read_bytes() == data
        if image:
            assert any(part['type'] == 'image_url' for part in prepared['content'])
        else:
            assert paths[0] in prepared['content']
        # A later session setting cannot substitute the accepted input bytes.
        from gateway.session_api_turn import _SETTINGS_PREFIX
        owner.db._execute_write(lambda conn: conn.execute('UPDATE state_meta SET value=? WHERE key=?',
            ('{}', _SETTINGS_PREFIX + sid)))
        assert prepare_api_execution(owner, SessionRef(owner.profile_id, sid), row['payload'])['content'] == prepared['content']


@pytest.mark.asyncio
async def test_upload_rechecks_grant_under_writer_and_passive_grants_gain_no_files(api, owner, tmp_path, monkeypatch):
    from gateway import hosted_rooms
    from gateway.hosted_room_peer import invitation_permissions, decode_room_grant, attachment_manifest_digest
    from gateway.platforms.api_server_room_attachments import RoomAttachmentSpool, _http_routes
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    from gateway.hosted_room_grant_state import grant_state_db_paths, revoke_grant_state
    from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError
    assert not {'dispatch', 'attachment.stage', 'artifact.read', 'artifact.ack'} & set(
        invitation_permissions(True, True, passive_only=True))
    policy, catalog = _local_room_catalog(api, 'default', hosted_rooms.local_authority_gateway_id())
    manifest = _manifest()
    dispatch = _dispatch(manifest, target_install_id=catalog['installation_id'],
        capability_digest=catalog['catalog_digest'], execution_policy_digest=policy['policy_digest'])
    token = grant_for(api, dispatch)
    original_put = RoomAttachmentSpool.put
    def revoked_before_writer(spool, **kwargs):
        claims = decode_room_grant(api._room_grant_secret(), token, permission='status')
        revoke_grant_state(grant_state_db_paths(), claims=claims, expires_at=claims['status_expires_at'], exact=True)
        return original_put(spool, **kwargs)
    monkeypatch.setattr(RoomAttachmentSpool, 'put', revoked_before_writer)
    app = web.Application()
    for method, path, handler in _http_routes(api):
        app.router.add_route(method, path, handler)
    async with TestClient(TestServer(app)) as http:
        client = PeerRunsHTTPClient(base_url=str(http.make_url('')).rstrip('/'), api_key='')
        with pytest.raises(PeerRunsHTTPError) as error:
            await asyncio.to_thread(client.stage_attachments, dispatch=dispatch.as_mapping(),
                attachments=[{**manifest[0], 'data': b'hello'}], grant=token)
        assert error.value.status_code == 401
    from gateway.platforms.api_server_room_attachments import _default_spool, RoomAttachmentSpoolIncomplete
    with pytest.raises(RoomAttachmentSpoolIncomplete):
        _default_spool().require_complete(dispatch)
