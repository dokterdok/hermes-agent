"""Round-2 route revalidation for durable bare webhook deliveries."""
import json
import sqlite3

import pytest

from gateway.profile_routing import ProfileRoute
from gateway.session import SessionSource
from gateway.session_envelope import check_native_route
from hermes_state_runtime import RuntimeStoreError, get_session_admission
from tests.gateway.test_parked_profile_ingress import (
    _homes, _runner_with_beta_parked, _serve_beta_again, _webhook,
    _webhook_request, _cancel, _close,
)


@pytest.mark.asyncio
async def test_bare_webhook_queued_before_rerouting_cannot_replay_on_old_profile(tmp_path, monkeypatch):
    from gateway.platforms.webhook import _INSECURE_NO_AUTH

    homes = _homes(tmp_path, monkeypatch)
    beta = homes[1][1].resolve()
    routes = [ProfileRoute(name='events', platform='webhook', profile='beta', user_id='webhook:events')]
    runner = await _runner_with_beta_parked(tmp_path, monkeypatch, homes, profile_routes=routes)
    await _serve_beta_again(runner, beta)
    for authority in runner.session_authorities:
        monkeypatch.setattr(authority, '_schedule', lambda ref: None)
    adapter = _webhook(runner, {'events': {'secret': _INSECURE_NO_AUTH, 'prompt': 'event {id}'}})
    authority = runner.session_authorities.for_home(beta)
    try:
        response = await adapter._handle_webhook(_webhook_request('queued-before-reroute', {'id': 17}, profile=None))
        assert response.status == 202, (response.status, response.text)
        with sqlite3.connect(beta / 'state.db') as conn:
            aid, = conn.execute('SELECT admission_id FROM session_admissions').fetchone()
        row = get_session_admission(authority.db, admission_id=aid)
        assert row['status'] == 'queued'
        source = SessionSource.from_dict(row['payload']['native_text_v1']['source'])
        assert source.profile == 'beta'
        # Use the actual recovery/execution preflight, first proving unchanged provenance works.
        restored, _ = await check_native_route(runner, row['payload'], row['target_session_id'], source, adapter)
        assert restored.profile == 'beta'
        runner.config.profile_routes = [ProfileRoute(name='events', platform='webhook', profile='default',
                                                     user_id='webhook:events')]
        sid = row['target_session_id']
        try:
            await check_native_route(runner, row['payload'], sid, source, adapter)
            preflight = 'accepted'
        except RuntimeStoreError as exc:
            preflight = exc.reason
        recovery = await authority.recover_native_sessions([(sid, source, adapter)])
        assert (preflight, recovery) == ('profile_mismatch', {sid: 'profile_mismatch'}), (preflight, recovery)
        assert get_session_admission(authority.db, admission_id=aid)['status'] == 'queued'
    finally:
        _cancel(adapter)
        _close(runner, homes)


