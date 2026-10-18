"""A cancelled native caller must retain bytes until its shielded write finishes."""
import asyncio
from pathlib import Path
import threading

import pytest

from tests.waiting import async_wait_until


@pytest.mark.asyncio
async def test_cancelled_waiter_holds_media_through_shielded_commit(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.event import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from gateway.session_authority import SessionAuthority, initialize_session_authority
    from gateway.session_ingress_media import release_admission_media
    from gateway.session_runtime_workers import mutation_tasks
    from hermes_state_runtime import cancel_session_input, get_session_admission, list_session_admissions
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    monkeypatch.setenv('WHATSAPP_ALLOWED_USERS', '15551234567')
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    adapter = WhatsAppAdapter(PlatformConfig(enabled=True, extra={'session_path': str(tmp_path / 'wa')}))
    runner.adapters = {Platform.WHATSAPP: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='media-probe')
    source = adapter.build_source(chat_id='15551234567@s.whatsapp.net',
                                  user_id='15551234567@s.whatsapp.net', chat_type='dm')
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'video bytes' * 100)

    def event(mid):
        return MessageEvent(text='look', source=source, message_id=mid, message_type=MessageType.VIDEO,
                            media_urls=[str(clip)], media_types=['video/mp4'])

    entered, release = threading.Event(), threading.Event()
    real_write = authority._admit_native_write

    def held_write(**kwargs):
        entered.set()
        assert release.wait(30)
        return real_write(**kwargs)

    try:
        old = await authority.admit_native(event('old'))
        cancel_session_input(authority.db, epoch=authority.epoch, admission_id=old.admission_id)
        refs = get_session_admission(authority.db, admission_id=old.admission_id)['payload']['native_text_v1']['media']
        monkeypatch.setattr(authority, '_admit_native_write', held_write)
        pending = asyncio.create_task(authority.admit_native(event('new')))
        assert await asyncio.to_thread(entered.wait, 30)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        # Normal cleanup of an earlier cancelled row referencing the identical retained file.
        await asyncio.to_thread(release_admission_media, authority.db, old.admission_id)
        release.set()
        await async_wait_until(lambda: not mutation_tasks(authority), timeout=30)
        rows = list_session_admissions(authority.db, session_id=old.ref.session_id, pending_only=False)
        new = next(row for row in rows if row['request_id'] == 'new')
        assert new['status'] == 'queued'
        assert Path(refs[0]['path']).exists(), 'shielded write committed queued media whose file was collected'
    finally:
        release.set()
        await async_wait_until(lambda: not mutation_tasks(authority), timeout=30)
        runner.session_store.close_all_db_handles()
