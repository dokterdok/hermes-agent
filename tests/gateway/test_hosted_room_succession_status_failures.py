"""Durable succession upkeep keeps scope; unsuccessful notices remain eligible for delivery."""
from contextvars import ContextVar
import logging
import threading
from types import SimpleNamespace

import pytest

from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_status as status_module
from tests.gateway.fixtures.succession import ROOM, context
from tests.gateway.test_hosted_room_succession_status import gateways as gateways, loop as loop, beat
from tui_gateway.hosted_room_peer_http import room_grant_request_budget, room_grant_request_budget_remaining


def test_upkeep_keeps_installation_scope_without_the_callers_request_deadline(monkeypatch):
    marker = ContextVar('upkeep-installation', default=None)
    upkeep = status_module.SuccessionUpkeep(lambda: None)
    observed, ready = [], threading.Event()
    def observe():
        observed.append((marker.get(), room_grant_request_budget_remaining()))
        upkeep._stop.set()
        ready.set()
    monkeypatch.setattr(upkeep, 'run_once', observe)
    monkeypatch.setattr(upkeep, 'run_automatic', lambda: [])
    token = marker.set('originating-installation')
    try:
        with room_grant_request_budget(1):
            upkeep.start()
            assert ready.wait(3)
        assert upkeep.stop(timeout=5)
    finally:
        marker.reset(token)
        upkeep.stop(timeout=5)
    assert observed == [('originating-installation', None)]


@pytest.mark.parametrize('failure', ['refused', 'sdk_error'])
def test_failed_private_notice_is_not_acknowledged_and_retries_exact_audience(gateways, loop, monkeypatch, caplog, failure):
    class NoticeSDKError(Exception):
        pass
    failed, sent = [True], []
    async def send(chat_id, content, metadata=None):
        sent.append((chat_id, metadata))
        if failed[0] and failure == 'sdk_error':
            raise NoticeSDKError('PRIVATE_PROVIDER_PAYLOAD')
        return SimpleNamespace(success=not failed[0])
    adapter = SimpleNamespace(platform='telegram', send=send)
    async def refs(room_id):
        return [(adapter, 'private-chat', {'thread_id': '7'}, 3)]
    runner = SimpleNamespace(_group_chat_continue_refs=refs, _typed_command_prefix_for=lambda _: '/')
    backup = gateways['s']
    beat(backup, gateways, now=1000.0, down=())
    offline = 1000.0 + status_module.UNREACHABLE_AFTER_SECONDS + 60
    beat(backup, gateways, now=offline)
    with backup.acting(), caplog.at_level(logging.WARNING):
        monkeypatch.setattr(status_module.time, 'time', lambda: offline)
        ctx = context(backup, gateways, down=('h',))
        assert not status_module.notify(ctx, ROOM, runner, loop, now=offline)
        assert not (succession.load_record(ctx.db_path, ROOM, 'heartbeat') or {}).get('notified')
        failed[0] = False
        assert status_module.notify(ctx, ROOM, runner, loop, now=offline + 1)
        assert not status_module.notify(ctx, ROOM, runner, loop, now=offline + 2)
    assert sent == [('private-chat', {'thread_id': '7'})] * 2
    assert 'PRIVATE_PROVIDER_PAYLOAD' not in caplog.text
    if failure == 'sdk_error':
        assert 'NoticeSDKError' in caplog.text
