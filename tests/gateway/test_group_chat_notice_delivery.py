"""Delivery accounting preserves private recipients and the watcher's at-most-once cursor."""
import asyncio

import pytest

from gateway import group_chat_notices as notices
from gateway.platforms.base import SendResult
from tests.gateway.group_chat_fixtures import OWNER
from tests.gateway.test_group_chat_hosts import advertised as advertised, setup as setup
from tests.gateway.test_group_chat_notices import hosting, moved_here, watched as watched


@pytest.mark.parametrize('kind', ['plain', 'actions'])
@pytest.mark.parametrize('unconfirmed', [None, SendResult(success=False, error_kind='forbidden')],
                         ids=['unknown-result', 'refused'])
def test_direct_notice_counts_only_confirmed_delivery_to_its_private_audience(watched, monkeypatch, kind,
                                                                            unconfirmed):
    attempts, results = [], [unconfirmed, SendResult(success=True)]

    async def send(chat_id, text, **kwargs):
        attempts.append(chat_id)
        return results.pop(0)

    monkeypatch.setattr(watched.bot, 'send', send)
    watched.state.gateway.status = hosting() if kind == 'actions' else hosting('ok', actions=[])
    watched.home('operator', user_id='operator')
    for expected in (0, 1):
        assert asyncio.run(notices.notify(watched.runner, 'mine', 'host_offline', {
            'host': 'Mac', 'minutes': 6})) == expected
    assert attempts == ['chat-1', 'chat-1']
    assert watched.home_sent == []


def test_watcher_still_consumes_an_incident_before_sending_when_delivery_is_refused(watched, monkeypatch):
    watched.notify()
    attempts = []

    async def refused(chat_id, text, **kwargs):
        attempts.append(chat_id)
        return SendResult(success=False, error_kind='forbidden')

    monkeypatch.setattr(watched.bot, 'send', refused)
    moved_here(watched)
    assert asyncio.run(notices.notify_all(watched.runner)) == 0
    assert asyncio.run(notices.notify_all(watched.runner)) == 0
    assert attempts == ['chat-1']
    assert notices._told(watched.state.authority, OWNER)['mine']['seq'] == 1
