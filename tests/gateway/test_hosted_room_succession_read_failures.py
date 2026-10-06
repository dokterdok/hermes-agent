"""Unreadable work is not an empty work set; a failed provider does not hide other online paths."""
import logging
import sqlite3

import pytest

from gateway import hosted_room_succession as succession
from gateway import hosted_room_succession_automatic as automatic
from gateway import hosted_room_succession_handover as handover
from gateway import hosted_room_succession_return as returning


@pytest.mark.parametrize('read', [succession.waiting_tasks, handover.unsettled_turns, returning._local_runs])
def test_unreadable_driver_state_is_not_reported_as_no_pending_work(tmp_path, read):
    path = tmp_path / 'state.db'
    path.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.Error):
        read(path, 'room')


def test_provider_sdk_failure_keeps_configured_messaging_online_paths(monkeypatch, caplog):
    from hermes_cli import runtime_provider
    from hermes_constants import get_hermes_home
    class ProviderSDKError(Exception):
        pass
    def failed_provider():
        raise ProviderSDKError('PRIVATE_PROVIDER_RESPONSE')
    get_hermes_home().joinpath('config.yaml').write_text('platforms:\n  telegram:\n    enabled: true\n')
    monkeypatch.setattr(runtime_provider, 'resolve_runtime_provider', failed_provider)
    monkeypatch.setattr(automatic, '_endpoints', None)
    with caplog.at_level(logging.WARNING):
        endpoints = automatic._known_endpoints()
    assert ('api.telegram.org', 443) in endpoints
    assert 'ProviderSDKError' in caplog.text
    assert 'PRIVATE_PROVIDER_RESPONSE' not in caplog.text
