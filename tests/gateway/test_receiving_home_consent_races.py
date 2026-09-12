"""Forwardported 5ee4a941 cancellation/persistence races, without Stop controls."""
import asyncio
from threading import Event
import time

import pytest

from gateway import group_home_consent as consent
from hermes_cli import config as configuration
from tests.gateway.test_receiving_home_consent import (
    home, view, command, confirm_command, saved, select_home,  # noqa: F401
)


def hold(monkeypatch, stage):
    entered, release, finished = Event(), Event(), Event()
    owner, name = (configuration, 'load_config') if stage == 'before_save' else (consent, '_persist')
    if stage == 'during_save':
        owner, name = configuration, 'save_config'
    original = getattr(owner, name)
    held = False

    def controlled(*args, **kwargs):
        nonlocal held
        if held:
            return original(*args, **kwargs)
        held = True
        try:
            value = original(*args, **kwargs) if stage in {'before_save', 'after_save'} else None
            entered.set()
            assert release.wait(10)
            return value if stage in {'before_save', 'after_save'} else original(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(owner, name, controlled)
    return entered, release, finished


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before_persist', 'before_save'])
@pytest.mark.parametrize('action', ['cancel', 'replace'])
async def test_cancel_or_replace_before_commit_never_writes_or_deletes_new_prompt(home, monkeypatch, stage, action):
    await select_home(home)
    home.consent()
    await command(home, '!group')
    old = next(iter(home.runner._group_home_confirmations.values()))
    entered, release, finished = hold(monkeypatch, stage)
    task = asyncio.create_task(command(home, confirm_command(home)))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        reply = await asyncio.wait_for(command(home, '!group cancel' if action == 'cancel' else '!group'), 3)
        newer = home.runner._group_home_confirmations.get(old.key)
        if action == 'cancel':
            assert 'cancelled' in reply and newer is None
        else:
            assert 'Everyone who can read this chat' in reply and newer is not old
    finally:
        release.set()
    result = await task
    assert finished.is_set() and not saved(home).get('group_audience_ack')
    assert 'home secret' not in result
    assert home.runner._group_home_confirmations.get(old.key) is newer


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['cancel', 'replace'])
async def test_during_save_remains_responsive_but_cannot_disclose_old_chooser(home, monkeypatch, action):
    await select_home(home)
    home.consent()
    await command(home, '!group')
    entered, release, _ = hold(monkeypatch, 'during_save')
    task = asyncio.create_task(command(home, confirm_command(home)))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        reply = await asyncio.wait_for(command(home, '!group cancel' if action == 'cancel' else '!group'), 3)
        assert 'Saving has already started' in reply
        assert 'home secret' not in reply
    finally:
        release.set()
    result = await task
    assert saved(home).get('group_audience_ack') and 'home secret' not in result
    assert not home.runner._group_home_confirmations


@pytest.mark.asyncio
async def test_old_completed_writer_cannot_remove_reselected_prompt(home, monkeypatch):
    await select_home(home)
    home.consent()
    await command(home, '!group')
    entered, release, _ = hold(monkeypatch, 'after_save')
    task = asyncio.create_task(command(home, confirm_command(home)))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        await select_home(home)
        assert 'Everyone who can read this chat' in await command(home, '!group')
        current = next(iter(home.runner._group_home_confirmations.values()))
    finally:
        release.set()
    result = await task
    assert home.runner._group_home_confirmations.get(current.key) is current
    assert not saved(home).get('group_audience_ack') and 'home secret' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['cancel', 'expire'])
async def test_cancel_or_expiry_during_chooser_read_withholds_private_output(home, monkeypatch, action):
    from gateway.hosted_room_messaging import MessagingRoomBackend

    await select_home(home)
    home.consent()
    await command(home, '!group')
    entered, release = Event(), Event()
    original = MessagingRoomBackend.list_rooms
    def paused(*args):
        result = original(*args)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(MessagingRoomBackend, 'list_rooms', paused)
    task = asyncio.create_task(command(home, confirm_command(home)))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        if action == 'cancel':
            assert 'Saving has already started' in await command(home, '!group cancel')
        else:
            next(iter(home.runner._group_home_confirmations.values())).deadline = time.monotonic() - 1
    finally:
        release.set()
    assert 'home secret' not in await task


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before_persist', 'during_save'])
@pytest.mark.parametrize('action', ['task_cancel', 'timeout'])
async def test_cancelled_or_timed_out_writer_settles_without_output_or_retry_trap(home, monkeypatch, stage, action):
    await select_home(home)
    home.consent()
    await command(home, '!group')
    entered, release, finished = hold(monkeypatch, stage)
    worker_finished = Event()
    original = consent._persist
    def observed(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            worker_finished.set()
    monkeypatch.setattr(consent, '_persist', observed)
    if action == 'timeout':
        # A deterministic wait_for substitute cancels only once the worker is
        # inside the chosen persistence boundary; no wall-clock race assertion.
        wait_for = asyncio.wait_for
        async def expire_when_entered(awaitable, *, timeout):
            worker = asyncio.ensure_future(awaitable)
            assert await asyncio.to_thread(entered.wait, 5)
            return await wait_for(worker, timeout=0)
        monkeypatch.setattr(consent.asyncio, 'wait_for', expire_when_entered)
    task = asyncio.create_task(command(home, confirm_command(home)))
    assert await asyncio.to_thread(entered.wait, 5)
    try:
        if action == 'task_cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert 'expired' in await task
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait, 5)
    assert await asyncio.to_thread(worker_finished.wait, 5)
    assert bool(saved(home).get('group_audience_ack')) is (stage == 'during_save')
    assert not home.runner._group_home_confirmations
