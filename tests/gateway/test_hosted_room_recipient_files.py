"""Served Files custody on the target's real registered SessionAuthority."""
import hashlib
import os
import sqlite3
import time
from pathlib import Path

import pytest

from tests.gateway.test_hosted_mux_runtime import mux  # noqa: F401 - real served homes


def _binding(source, target, payload, *, publication='one', until=None):
    return {
        'source_home': source.profile_id, 'target_home': target.profile_id,
        'publication_id': publication, 'index': 0,
        'valid_until': time.time() + 60 if until is None else until,
    }, {
        'attachment_id': 'att_' + '1' * 32, 'kind': 'file',
        'name': 'custody.txt', 'mime': 'text/plain', 'size': len(payload),
        'sha256': hashlib.sha256(payload).hexdigest(),
    }


@pytest.mark.live_system_guard_bypass
def test_target_owner_cold_read_and_registered_writer(mux, monkeypatch):
    from gateway import hosted_room_recipient_files as files
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore, AttachmentIntegrityError
    from hermes_state_runtime import RuntimeStoreError

    runner, homes, _, _ = mux
    source = runner.session_authorities.require(homes['default'])
    target = runner.session_authorities.require(homes['beta'])
    data = b'actual served target bytes\n'
    identity, manifest = _binding(source, target, data)
    assert source.db.db_path != target.db.db_path
    with target.db._read_ctx() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hosted_room_recipient_receipts'").fetchone() is None

    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('recipient read opened a connection, initialized schema or pruned')
        guard.setattr(HostedRoomAttachmentStore, '_connect', forbidden)
        guard.setattr(HostedRoomAttachmentStore, '_recipient_table', forbidden)
        guard.setattr(HostedRoomAttachmentStore, 'prune', forbidden)
        with pytest.raises(AttachmentIntegrityError):
            files.read_recipient_bytes(target, identity=identity, manifest=manifest, attempt=1)
    with target.db._read_ctx() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hosted_room_recipient_receipts'").fetchone() is None
    # The established Files subsystem performs its ordinary startup once;
    # the recipient provider must never independently bootstrap it.
    HostedRoomAttachmentStore(target.db.db_path)
    receipt = files.retain_recipient_bytes(target, identity=identity, manifest=manifest,
                                           data=data, attempt=1)
    assert receipt['write_attempt'] == 1
    assert files.read_recipient_bytes(target, identity=identity, manifest=manifest,
                                      attempt=2)['receipt_key'] == receipt['receipt_key']
    with target.db._read_ctx() as conn:
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 1
    with source.db._read_ctx() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hosted_room_recipient_receipts'").fetchone() is None
    for changed in ({**identity, 'publication_id': 'forged'},
                    {**identity, 'target_home': source.profile_id}):
        with pytest.raises(AttachmentIntegrityError):
            files.read_recipient_bytes(target, identity=changed, manifest=manifest, attempt=2)
    with pytest.raises(AttachmentIntegrityError):
        files.read_recipient_bytes(target, identity=identity, manifest=manifest, attempt=0)
    # A detached authority cannot borrow the served target's admission even if
    # it retains the same DB object and epoch.
    assert runner.session_authorities.remove(homes['beta']) is target
    try:
        with pytest.raises(RuntimeStoreError, match='runtime_draining'):
            files.retain_recipient_bytes(target, identity=identity, manifest=manifest,
                                         data=data, attempt=2)
        with pytest.raises(RuntimeStoreError, match='runtime_draining'):
            files.read_recipient_bytes(target, identity=identity, manifest=manifest, attempt=2)
    finally:
        runner.session_authorities.add(homes['beta'], target, name='beta')


@pytest.mark.live_system_guard_bypass
def test_count_expiry_replay_and_shared_blob_reference(mux, monkeypatch):
    from gateway import hosted_room_recipient_files as files
    from gateway.hosted_room_attachments import (
        HostedRoomAttachmentStore, AttachmentQuotaError, AttachmentIntegrityError,
    )
    runner, homes, _, _ = mux
    source = runner.session_authorities.require(homes['default'])
    target = runner.session_authorities.require(homes['beta'])
    data = b'bounded retained payload\n'
    identity, manifest = _binding(source, target, data)
    HostedRoomAttachmentStore(target.db.db_path)
    monkeypatch.setattr(files, '_store', lambda authority: HostedRoomAttachmentStore(
        authority.db.db_path, _defer_initialization=True, gateway_quota_count=2))
    first = files.retain_recipient_bytes(target, identity=identity, manifest=manifest,
                                         data=data, attempt=1)
    short, _ = _binding(source, target, data, publication='short', until=time.time() + .25)
    second = files.retain_recipient_bytes(target, identity=short, manifest=manifest,
                                          data=data, attempt=1)
    assert second['receipt_key'] != first['receipt_key']
    replay = files.retain_recipient_bytes(target, identity=identity, manifest=manifest,
                                          data=data, attempt=2)
    assert replay['write_attempt'] == 1
    assert replay['receipt_key'] == first['receipt_key']
    third, _ = _binding(source, target, data, publication='third')
    with pytest.raises(AttachmentQuotaError):
        files.retain_recipient_bytes(target, identity=third, manifest=manifest, data=data, attempt=1)
    with target.db._read_ctx() as conn:
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 2
        assert conn.execute('SELECT ref_count FROM hosted_room_attachment_blobs').fetchone()[0] == 2
    time.sleep(.3)
    with pytest.raises(AttachmentIntegrityError):
        files.read_recipient_bytes(target, identity=short, manifest=manifest, attempt=2)
    with target.db._read_ctx() as conn:
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 2
    # Normal Files maintenance retires one receipt and one shared reference.
    assert HostedRoomAttachmentStore(target.db.db_path, _defer_initialization=True).prune(now=time.time()) >= 1
    with target.db._read_ctx() as conn:
        assert conn.execute('SELECT ref_count FROM hosted_room_attachment_blobs').fetchone()[0] == 1
    files.retain_recipient_bytes(target, identity=third, manifest=manifest, data=data, attempt=1)
    with target.db._read_ctx() as conn:
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_recipient_receipts').fetchone()[0] == 2
        assert conn.execute('SELECT ref_count FROM hosted_room_attachment_blobs').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM hosted_room_attachment_blobs').fetchone()[0] == 1


@pytest.mark.live_system_guard_bypass
@pytest.mark.parametrize('loss', ['closed', 'replaced'])
def test_target_generation_lost_after_binding_refuses_write_and_read(mux, loss):
    from gateway import hosted_room_recipient_files as files
    from gateway.hosted_room_attachments import HostedRoomAttachmentStore
    from hermes_state_errors import StateDbReplacedError
    from hermes_state_runtime import RuntimeStoreError

    runner, homes, _, _ = mux
    target = runner.session_authorities.require(homes['beta'])
    source = runner.session_authorities.require(homes['default'])
    data = b'not delivered to a retired target\n'
    identity, manifest = _binding(source, target, data)
    HostedRoomAttachmentStore(target.db.db_path)
    old_path = Path(target.db.db_path)
    pristine = None
    if loss == 'closed':
        target.db.close()
    else:
        os.replace(old_path, old_path.with_name('retired-target.db'))
        with sqlite3.connect(old_path):
            pass
        pristine = old_path.read_bytes()
    with pytest.raises((RuntimeStoreError, StateDbReplacedError, sqlite3.Error)):
        files.retain_recipient_bytes(target, identity=identity, manifest=manifest,
                                     data=data, attempt=1)
    with pytest.raises((RuntimeStoreError, StateDbReplacedError, sqlite3.Error)):
        files.read_recipient_bytes(target, identity=identity, manifest=manifest, attempt=1)
    if loss == 'replaced':
        assert old_path.read_bytes() == pristine
        with sqlite3.connect(old_path) as conn:
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hosted_room_recipient_receipts'").fetchone() is None
