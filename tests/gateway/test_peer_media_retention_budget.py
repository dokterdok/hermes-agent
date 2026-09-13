"""Composed peer holders and local/named document batches use the native media budget."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway import hosted_rooms
from gateway.hosted_room_attachments import HostedRoomAttachmentStore
from gateway.platforms.api_server_room_attachments import RoomAttachmentSpool
from gateway.session_api_turn import admit_api_turn
from gateway.session_hosted_attachments import submission_payload
from gateway.session_ingress_media import _media_root, release_admission_media, restore_native_media
from gateway.session_peer_input import check_peer_input, retain_peer_input
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_api_cutover_contract import api, owner  # noqa: F401
from tests.gateway.test_api_media_retention import PNG, set_holder_status, settled_native
from tests.gateway.test_roomlink_input_spool import _claims, _dispatch


def spool_inputs(db_path, root, items, **scope):
    manifest = [dict(attachment_id=f'att_{index:032x}', kind=kind, name=name, size=len(data), mime=mime,
        sha256=hashlib.sha256(data).hexdigest()) for index, (name, kind, mime, data) in enumerate(items)]
    dispatch = _dispatch(manifest, **scope)
    spool = RoomAttachmentSpool(db_path, root=root)
    spool.prepare(dispatch, manifest)
    for item, (_, _, _, data) in zip(manifest, items):
        spool.put(claims=_claims(dispatch), task_id=dispatch.task_id,
            execution_generation=dispatch.execution_generation, attachment_id=item['attachment_id'], data=data)
    return spool, dispatch


@pytest.mark.parametrize('status', ['queued', 'unknown', 'terminal'])
@pytest.mark.asyncio
async def test_peer_reference_survives_native_cleanup_of_same_path(api, owner, tmp_path, status):
    from gateway.platforms.api_server_room_grants import _local_room_catalog
    from gateway.platforms.base import get_image_cache_dir
    owner.profile_id = str(tmp_path)
    install = hosted_rooms.local_authority_gateway_id()
    policy, catalog = _local_room_catalog(api, 'default', install)
    spool, dispatch = spool_inputs(owner.db.db_path, tmp_path / 'spool', [('shared.png', 'image', 'image/png', PNG)],
        target_install_id=install, capability_digest=catalog['catalog_digest'], execution_policy_digest=policy['policy_digest'])
    media = retain_peer_input(spool, dispatch)
    session_id = await api._ensure_hosted_member_session(dispatch)
    _, _, row = admit_api_turn(api, session_id=session_id, request_id='peer', user_message=dispatch.prompt,
        conversation_history=[], room_dispatch=dispatch.as_mapping(), room_execution_policy=policy, room_input_media=media)
    set_holder_status(owner, row, status)
    staged = get_image_cache_dir() / 'shared.png'
    staged.write_bytes(PNG)
    native = settled_native(owner, [staged])
    assert native['payload']['attachments_v1']['media'] == media['media']
    assert release_admission_media(owner.db, native['admission_id']) == 0
    _, paths = check_peer_input(row['payload']['api_turn_v1']['settings'])
    assert Path(paths[0]).read_bytes() == PNG


def test_peer_document_batch_rejects_total_before_any_capture(tmp_path, monkeypatch):
    from gateway.platforms import base
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    items = [('one.txt', 'file', 'text/plain', b'a' * 2048), ('two.txt', 'file', 'text/plain', b'b' * 2048)]
    spool, dispatch = spool_inputs(tmp_path / 'state.db', tmp_path / 'spool', items)
    monkeypatch.setattr(base, 'get_inbound_media_max_bytes', lambda: 3072)
    assert not _media_root().exists()
    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        retain_peer_input(spool, dispatch)
    assert not _media_root().exists()
    monkeypatch.setattr(base, 'get_inbound_media_max_bytes', lambda: 4096)
    retained = retain_peer_input(spool, dispatch)
    assert [Path(path).read_bytes() for path in restore_native_media(retained['media'])] == [item[3] for item in items]


def local_documents(tmp_path, *, transferred, db=None):
    from gateway.hosted_room_input_custody import initialize_input_custody
    from hermes_state import SessionDB
    db_path = tmp_path / 'state.db'
    if db is None:
        with SessionDB(db_path) as fixture_db:
            initialize_input_custody(fixture_db)
    else:
        initialize_input_custody(db)
    hosted_rooms.create_room(db_path, room_id='room', name='Room', authority_gateway_id='home',
        members=[dict(member_id='member', profile='default', handle='member')])
    store = HostedRoomAttachmentStore(db_path)
    files = []
    for index in range(2):
        data = bytes([65 + index]) * 2048
        saved = store.put(room_id='room', upload_id=f'upload-{index}', kind='file', name=f'{index}.txt',
            mime='text/plain', data=data)
        files.append(({key: saved[key] for key in ('attachment_id', 'kind', 'name', 'size', 'mime')}, data))
    store.commit_message(room_id='room', event_id='source', manifest=[item for item, _ in files], recipient_member_ids=['member'])
    bound = [{**item, 'event_id': 'source'} for item, _ in files]
    rpc = SimpleNamespace(authority=SimpleNamespace(db=SimpleNamespace(db_path=db_path)), room_id='room', member_id='member')
    if transferred:
        rpc.hosted_attachment_data = [(item, data) for item, (_, data) in zip(bound, files)]
    return rpc, bound


@pytest.mark.parametrize('transferred', [False, True], ids=['local-store', 'named-owner-snapshot'])
def test_local_document_batch_rejects_total_before_capture(tmp_path, monkeypatch, transferred):
    from gateway.platforms import base
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    rpc, bound = local_documents(tmp_path, transferred=transferred)
    monkeypatch.setattr(base, 'get_inbound_media_max_bytes', lambda: 3072)
    assert not _media_root().exists()
    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        submission_payload(rpc, 'read', bound)
    assert not _media_root().exists()
    monkeypatch.setattr(base, 'get_inbound_media_max_bytes', lambda: 4096)
    payload = submission_payload(rpc, 'read', bound)
    assert payload['text'].count('[Shared attachment] file:') == 2


@pytest.mark.parametrize('limit', [0, -1])
def test_disabled_batch_budget_retains_existing_behavior(tmp_path, monkeypatch, limit):
    from gateway.platforms import base
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(base, 'get_inbound_media_max_bytes', lambda: limit)
    rpc, bound = local_documents(tmp_path, transferred=False)
    assert submission_payload(rpc, 'read', bound)['text'].count('[Shared attachment] file:') == 2
