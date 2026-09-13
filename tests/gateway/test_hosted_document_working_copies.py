"""Working inputs are private, verified, and independent of native snapshots."""
import hashlib
import os
from pathlib import Path

import pytest

from gateway.hosted_room_attachments import HostedRoomAttachmentStore, default_attachment_root
from gateway.hosted_room_input_preparation import prepare_hosted_input
from gateway.session_ingress_media import capture_native_media, restore_native_media
from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_peer_media_retention_budget import owned_documents


@pytest.mark.parametrize('changed', ['working', 'native'])
def test_working_copies_never_link_sources_or_repair_changed_bytes(tmp_path, monkeypatch, changed):
    with owned_documents(tmp_path, monkeypatch) as (db, rpc, bound):
        store = HostedRoomAttachmentStore(db.db_path)
        sources = [path for path in store.blob_root.rglob('*') if path.is_file()]
        assert sources
        prepared = prepare_hosted_input(rpc, request_id='hosted:copies', prompt='read', attachments=bound)
        working = Path(prepared.payload['text'].split('file: ', 1)[1].split('\n')[0])
        captured = capture_native_media([working])
        native = Path(captured[0]['path'])
        assert working.read_bytes() == native.read_bytes() == b'A' * 2048
        assert not os.path.samefile(working, native)
        assert all(not os.path.samefile(path, source) for path in (working, native) for source in sources)
        other = default_attachment_root(tmp_path / 'profiles' / 'other' / 'state.db') / 'working-documents-v3'
        assert not other.exists()

        damaged = working if changed == 'working' else native
        damaged.write_bytes(b'changed private copy')
        if changed == 'working':
            with pytest.raises(RuntimeStoreError, match='storage_unavailable'):
                prepare_hosted_input(rpc, request_id='hosted:copies', prompt='read', attachments=bound)
            assert Path(restore_native_media(captured)[0]).read_bytes() == b'A' * 2048
        else:
            again = prepare_hosted_input(rpc, request_id='hosted:copies', prompt='read', attachments=bound)
            assert again.payload == prepared.payload
            with pytest.raises(RuntimeStoreError, match='storage_unavailable'):
                restore_native_media(captured)
        assert damaged.read_bytes() == b'changed private copy'
        assert store.read(room_id='room', attachment_id=bound[0]['attachment_id'],
            event_id='source', recipient_member_id='member').data == b'A' * 2048


@pytest.mark.parametrize('redirect', ['directory', 'file'])
def test_working_copy_symlink_refuses_without_modifying_target(tmp_path, monkeypatch, redirect):
    with owned_documents(tmp_path, monkeypatch) as (db, rpc, bound):
        digest = hashlib.sha256(b'A' * 2048).hexdigest()
        root = default_attachment_root(db.db_path) / 'working-documents-v3'
        root.mkdir(parents=True)
        outside = tmp_path / 'not-inputs'
        outside.mkdir()
        untouched = outside / '0.txt'
        untouched.write_bytes(b'A' * 2048)
        if redirect == 'directory':
            target = root / digest
            target.symlink_to(outside, target_is_directory=True)
        else:
            target = root / digest / '0.txt'
            target.parent.mkdir()
            target.symlink_to(untouched)
        with pytest.raises(RuntimeStoreError, match='storage_unavailable'):
            prepare_hosted_input(rpc, request_id='hosted:symlink', prompt='read', attachments=bound)
        assert target.is_symlink()
        assert untouched.read_bytes() == b'A' * 2048
