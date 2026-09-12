"""The typed file command sends native bytes, never a success-shaped text fallback."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import weakref

import pytest

from gateway.group_home_consent import disclosure_stamp
from gateway.hosted_room_file_lookup import selection_digest
from gateway.hosted_room_messaging import current_room_backend
from gateway.native_document_guard import mark_native_document_guard, check_document_fallback
from tests.gateway.test_canonical_messaging_views import view, Adapter  # noqa: F401
from tests.gateway.test_canonical_messaging_files import publish


class DocumentAdapter(Adapter):
    def __init__(self, config):
        super().__init__(config)
        self.sent = []
        self.fallback = False

    @mark_native_document_guard
    async def send_document(self, *, chat_id, file_path, reply_to, metadata, file_name):
        self.sent.append((chat_id, file_name, Path(file_path).read_bytes(), reply_to, metadata))
        if self.fallback:
            check_document_fallback()
        return SimpleNamespace(success=True)


@pytest.mark.asyncio
async def test_file_command_sends_exact_bytes_and_does_not_replay(view):
    view.consent()
    item = publish(view.receiving, 'room', 1, ['pm'], name='report.txt', data=b'report bytes')
    adapter = DocumentAdapter(view.adapter.config)
    view.runner._profile_adapters['home'][view.event.source.platform] = adapter
    view.event.source._transport_adapter_ref = weakref.ref(adapter)
    backend = current_room_backend(view.runner, view.event, disclosure_stamp(view.runner, view.event))
    room, = backend.list_rooms()
    code = selection_digest(room, item)[:12]
    event = replace(view.event, text='!group 1 file ' + code, message_id='get-file-1')
    assert await view.runner._handle_group_command(event) == 'File sent.'
    assert adapter.sent[0][:4] == ('42', 'report.txt', b'report bytes', 'get-file-1')
    assert await view.runner._handle_group_command(event) == 'File sent.'
    assert len(adapter.sent) == 1
    assert not any(Path(backend.db_path).parent.glob('group-file-delivery-tmp/*/report.txt'))
    # A failed native upload is ambiguous, not delivered just because a text notice could succeed.
    adapter.fallback = True
    second = replace(event, message_id='get-file-2')
    result = await view.runner._handle_group_command(second)
    assert 'may already be in this chat' in result
    assert await view.runner._handle_group_command(second) == result
    assert len(adapter.sent) == 2


@pytest.mark.asyncio
async def test_file_command_rechecks_consent_after_read_before_native_send(view, monkeypatch):
    view.consent()
    item = publish(view.receiving, 'room', 1, ['pm'], name='private.txt')
    adapter = DocumentAdapter(view.adapter.config)
    view.runner._profile_adapters['home'][view.event.source.platform] = adapter
    view.event.source._transport_adapter_ref = weakref.ref(adapter)
    backend = current_room_backend(view.runner, view.event, disclosure_stamp(view.runner, view.event))
    room, = backend.list_rooms()
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore
    original = HostedRoomAttachmentStore._read_blob
    def revoked(store, *args, **kwargs):
        data = original(store, *args, **kwargs)
        view.consent(False)
        return data
    monkeypatch.setattr(HostedRoomAttachmentStore, '_read_blob', revoked)
    event = replace(view.event, message_id='get', text='!group 1 file ' + selection_digest(room, item)[:12])
    assert await view.runner._handle_group_command(event) != 'File sent.'
    assert adapter.sent == []
