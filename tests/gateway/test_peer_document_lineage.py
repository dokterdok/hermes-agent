"""A signed document Send after a home move keeps the target's original member session."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from gateway.hosted_room_peer import HostedMemberDispatch
from gateway.hosted_room_proof import request_proof, verify_response, RESPONSE_HEADER, RESPONSE_NONCE_HEADER
from gateway.platforms.api_server_authority_runs import run_admission, stop_run
from gateway.runtime_ownership import process_ownership
from gateway.session_contract import SessionRef
from gateway.session_peer_documents import content
from hermes_state_input_custody import initialize_input_custody
from tests.gateway.test_api_group_owner_stop import OWNER, app_for, dispatch, invite
from tests.gateway.test_api_group_owner_stop_canonical import canonical as canonical


async def send_document(client, invitation, body):
    headers = {'Content-Type': 'application/json', 'Idempotency-Key': 'room:document-send:1'}
    authorization, key, mac, payload = request_proof(invitation['grant'],
        installation_id=invitation['catalog']['installation_id'], method='POST', path='/v1/runs',
        body=json.dumps(body, separators=(',', ':')).encode(), headers=headers)
    response = await client.post('/v1/runs', data=payload, headers={**headers, 'Authorization': authorization})
    decoded = verify_response(key, mac, response.status, await response.read(),
                              response.headers[RESPONSE_HEADER], response.headers[RESPONSE_NONCE_HEADER])
    return response.status, json.loads(decoded)


@pytest.mark.asyncio
async def test_document_send_uses_server_resolved_origin_at_both_canonical_bindings(canonical):
    adapter, authority, runner = canonical
    authority._schedule = lambda ref: None
    runner._handle_message = lambda event: pytest.fail('the queued document turn executed before this test released it')
    home = Path(authority.db.db_path).parent
    process_ownership.reserve([home])
    initialize_input_custody(authority.db)
    accepted_run = None
    try:
        async with TestClient(TestServer(app_for(adapter))) as client:
            original = await invite(client)
            original_dispatch = dispatch(original)
            session_id = await adapter._ensure_hosted_member_session(HostedMemberDispatch.from_mapping(original_dispatch))
            prior = {key: original_dispatch[key] for key in ('home_install_id', 'authority_gateway_id', 'authority_epoch')}
            response = await client.post('/v1/room-members/invitations', headers=OWNER, json={
                'room_id': original_dispatch['room_id'], 'member_id': original_dispatch['member_id'],
                'home_install_id': 'successor', 'authority_gateway_id': 'successor', 'authority_epoch': 2,
                'previous_authority': prior})
            assert response.status == 201, await response.text()
            successor = await response.json()
            data = b'Successor document bytes are retained by the original target session.'
            current = dispatch(successor, task='document-send')
            current.update(home_install_id='successor', authority_gateway_id='successor', authority_epoch=2)
            current['document_inputs'] = [{
                'event_id': 'document-event', 'attachment_id': 'att_' + '1' * 32,
                'recipient_member_id': current['member_id'], 'kind': 'file', 'name': 'notes.txt',
                'mime': 'text/plain', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]
            status, result = await send_document(client, successor, {'input': current['prompt'],
                'hosted_room_dispatch': current, 'document_bytes': [base64.b64encode(data).decode()]})
            assert status == 202, result
            accepted_run = result['run_id']
            _, admitted = run_admission(adapter, accepted_run)
            assert admitted['target_session_id'] == session_id
            settings = admitted['payload']['api_turn_v1']['settings']
            assert settings['room_dispatch']['home_install_id'] == 'successor'
            reference, = settings['room_document_inputs']['references']
            assert Path(reference['path']).read_bytes() == data
            assert 'notes.txt' in content(authority, SessionRef(authority.profile_id, session_id), admitted['payload'])
            with authority.db._read_ctx() as conn:
                assert conn.execute("SELECT COUNT(*) FROM sessions WHERE source='bot_room'").fetchone()[0] == 1
                assert conn.execute('SELECT COUNT(*) FROM input_custody_refs').fetchone()[0] == 1
            await stop_run(adapter, accepted_run)
            await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)
    finally:
        if accepted_run is not None and adapter._active_run_tasks:
            await stop_run(adapter, accepted_run)
            await asyncio.wait_for(asyncio.gather(*adapter._active_run_tasks.values()), 15)
        process_ownership.release(home)
