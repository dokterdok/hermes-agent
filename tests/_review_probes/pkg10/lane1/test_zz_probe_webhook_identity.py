"""G1: explicit healthy profile must override the default bot's profile_routes."""
import json
from unittest.mock import MagicMock

import pytest

from gateway.profile_routing import ProfileRoute
from gateway.platforms.webhook import _INSECURE_NO_AUTH
from tests.gateway.test_parked_profile_ingress import (
    _homes, _runner_with_beta_parked, _webhook, _admissions, _cancel, _close,
)
from tests.waiting import async_wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize('coalesce', [False, True])
async def test_healthy_explicit_profile_ignores_default_route(tmp_path, monkeypatch, coalesce):
    homes = _homes(tmp_path, monkeypatch)
    alpha = homes[0][1] / 'profiles' / 'alpha'
    alpha.mkdir(parents=True)
    (alpha / 'config.yaml').write_text('{}')
    homes.append(('alpha', alpha))
    runner = await _runner_with_beta_parked(tmp_path, monkeypatch, homes, profile_routes=[
        ProfileRoute(name='default-hook', platform='webhook', user_id='webhook:events', profile='beta')])
    route = {'secret': _INSECURE_NO_AUTH, 'profile': 'alpha', 'prompt': 'event {id}'}
    if coalesce:
        route['coalesce'] = {'key': 'id', 'window_seconds': 0.1}
    adapter = _webhook(runner, {'events': route})
    raw = json.dumps({'id': 17}).encode()
    request = MagicMock()
    request.headers = {'X-GitHub-Delivery': 'alpha-delivery'}
    request.content_length = len(raw)
    request.match_info = {'route_name': 'events', 'profile': 'alpha'}
    request.method = 'POST'

    async def read():
        return raw

    request.read = read
    try:
        response = await adapter._handle_webhook(request)
        assert response.status == 202, (response.status, response.text)
        await async_wait_until(lambda: _admissions(alpha) == 1,
                               what='healthy alpha webhook durably admitted')
        assert _admissions(homes[1][1]) == 0
    finally:
        _cancel(adapter)
        _close(runner, homes)
