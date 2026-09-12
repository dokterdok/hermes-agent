"""Canonical Home Files differ from recipient-filtered peer reads."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway import hosted_rooms
from gateway.hosted_room_attachments import HostedRoomAttachmentStore
from gateway.hosted_room_file_access import list_local_files, read_local_file
from gateway.hosted_room_file_contract import FileAccessError, MANIFEST_FIELDS
from gateway.hosted_room_file_lookup import resolve_local_file, selection_digest
from gateway.group_home_consent import disclosure_stamp
from gateway.hosted_room_messaging import current_room_backend
from gateway.session_authorities import owner_scope
from tests.gateway.test_canonical_messaging_views import view  # noqa: F401


def publish(authority, room_id, index, recipients, *, data=b'published bytes', name='same.txt'):
    with owner_scope(authority):
        store = HostedRoomAttachmentStore(authority.db.db_path)
        saved = store.put(room_id=room_id, upload_id=f'upload-{index}', kind='file', name=name, mime='text/plain', data=data)
        manifest = {key: saved[key] for key in MANIFEST_FIELDS}
        event_id = f'event-{index}'
        store.commit_message(room_id=room_id, event_id=event_id, manifest=[manifest],
                             recipient_member_ids=recipients, viewer_access=True, hold_until_event=True)
        hosted_rooms.append_event(authority.db.db_path, room_id=room_id, event_id=event_id,
            kind='message.user', actor={'kind': 'user', 'id': 'native'},
            authority_gateway_id=hosted_rooms.local_authority_gateway_id(), authority_epoch=1,
            payload={'text': 'Shared file', 'thread_id': 'thread', 'attachments': [manifest]})
        return {**manifest, 'event_id': event_id}


def test_named_nonparticipant_home_is_explicit_owner_viewer_but_peer_is_recipient_filtered(view):
    first = publish(view.receiving, 'room', 1, ['builder'])
    second = publish(view.receiving, 'room', 2, ['pm'], name='pm-only.txt')
    backend = current_room_backend(view.runner, view.event, disclosure_stamp(view.runner, view.event))
    assert backend.list_rooms() == []
    view.consent()
    room, = backend.list_rooms()
    # The receiving profile is 'home', absent from the Bot roster, not inferred as 'pm'.
    assert {item['attachment_id'] for item in backend.list_files(room=room)['items']} == {first['attachment_id'], second['attachment_id']}
    with owner_scope(view.receiving):
        assert [item['attachment_id'] for item in list_local_files(backend, room=room, member_id='builder')['items']] == [first['attachment_id']]
        selected = dict(room=room, event_id=first['event_id'], selected_id=first['attachment_id'])
        assert read_local_file(backend, member_id='builder', **selected).data == b'published bytes'
        with pytest.raises(FileAccessError):
            read_local_file(backend, member_id='pm', **selected)
        code = selection_digest(room, first)[:12]
        assert resolve_local_file(backend, room=room, member_id='builder', code=code)['attachment_id'] == first['attachment_id']
        with pytest.raises(FileAccessError):
            resolve_local_file(backend, room=room, member_id='pm', code=code)
    view.consent(False)
    with pytest.raises(PermissionError):
        backend.list_files(room=room)


def test_exact_versions_query_cursor_and_post_read_consent_recheck(view, monkeypatch):
    view.consent()
    first = publish(view.receiving, 'room', 1, ['builder'], data=b'old')
    publish(view.receiving, 'room', 2, ['builder'], data=b'new')
    backend = current_room_backend(view.runner, view.event, disclosure_stamp(view.runner, view.event))
    room, = backend.list_rooms()
    page = backend.list_files(room=room, limit=1)
    assert page['has_more']
    older = backend.list_files(room=room, limit=1, cursor=page['next_cursor'])
    assert older['items'][0]['attachment_id'] == first['attachment_id']
    with pytest.raises(FileAccessError):
        backend.list_files(room=room, limit=1, cursor=page['next_cursor'], query='changed')
    args = dict(room=room, event_id=first['event_id'], attachment_id=first['attachment_id'])
    assert backend.read_file(**args).data == b'old'
    with pytest.raises(FileAccessError):
        backend.read_file(**{**args, 'event_id': 'wrong'})
    read = HostedRoomAttachmentStore._read_blob
    def revoke_after_bytes(store, **kwargs):
        data = read(store, **kwargs)
        view.consent(False)
        return data
    monkeypatch.setattr(HostedRoomAttachmentStore, '_read_blob', revoke_after_bytes)
    with pytest.raises((PermissionError, FileAccessError)):
        backend.read_file(**args)


def test_shared_message_and_lookup_remain_bound_to_a_current_member(view):
    from gateway.hosted_room_shared_message_access import read_local_shared_message
    from gateway.hosted_room_file_lookup import latest_local_reply
    view.consent()
    backend = current_room_backend(view.runner, view.event, disclosure_stamp(view.runner, view.event))
    room, = backend.list_rooms()
    with owner_scope(view.receiving):
        hosted_rooms.append_event(backend.db_path, room_id='room', event_id='reply', kind='message.member',
            actor={'kind': 'member', 'id': 'builder'}, authority_gateway_id=room['authority_gateway_id'], authority_epoch=1,
            payload={'text': 'A shared reply', 'member_id': 'builder'})
        result = read_local_shared_message(backend, room=room, event_id='reply', member_id='pm')
        assert result['text'] == 'A shared reply'
        assert latest_local_reply(backend, room=room, member_id='pm')['event_id'] == 'reply'
        with pytest.raises(FileAccessError):
            read_local_shared_message(backend, room=room, event_id='reply', member_id='not-a-member')
