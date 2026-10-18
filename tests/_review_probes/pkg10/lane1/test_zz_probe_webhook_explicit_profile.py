"""G1 probe: does ``_coalesced_to_parked`` refuse a ``/p/alpha/`` coalesced webhook that admission
would have run on alpha?

Greptile: admission keeps ``source.profile = alpha`` and skips the ``profile_routes`` rule, so the
503 blocks work that could run. Checked against the real admission path:
``BasePlatformAdapter.build_source`` applies the rule first (``profile_route_rejected`` /
``_rejected_route_profile`` while beta is parked, ``profile=beta`` while it is served), and
``session_ingress_context._binding`` matches the same rule again and refuses
``source.profile (alpha) != runtime_profile (beta)`` with ``profile_mismatch``. So with beta
SERVED the same delivery is still never admitted anywhere: the rule and the route conflict.
"""
import json
from unittest.mock import MagicMock

import pytest

from tests.gateway.test_parked_profile_ingress import (
    _admissions, _cancel, _close, _homes, _runner_with_beta_parked, _serve_beta_again, _webhook)
from tests.waiting import async_wait_until


def _request(delivery_id, body, profile):
    raw = json.dumps(body).encode()
    request = MagicMock()
    request.headers = {'X-GitHub-Delivery': delivery_id}
    request.content_length = len(raw)
    request.match_info = {'route_name': 'events', 'profile': profile}
    request.method = 'POST'

    async def read():
        return raw

    request.read = read
    return request


def _homes_with_alpha(tmp_path, monkeypatch):
    homes = _homes(tmp_path, monkeypatch)
    alpha = homes[0][1] / 'profiles' / 'alpha'
    alpha.mkdir(parents=True, mode=0o700)
    (alpha / 'config.yaml').write_text('{}')
    return [*homes, ('alpha', alpha)]  # homes[1] stays beta for _runner_with_beta_parked


async def _deliver(tmp_path, monkeypatch, *, serve_beta):
    from gateway.platforms.webhook import _INSECURE_NO_AUTH
    from gateway.profile_routing import ProfileRoute
    homes = _homes_with_alpha(tmp_path, monkeypatch)
    routes = [ProfileRoute(name='hooks', platform='webhook', profile='beta', user_id='webhook:events')]
    runner = await _runner_with_beta_parked(tmp_path, monkeypatch, homes, profile_routes=routes)
    if serve_beta:
        await _serve_beta_again(runner, homes[1][1].resolve())
    adapter = _webhook(runner, {'events': {'secret': _INSECURE_NO_AUTH, 'prompt': 'event {id}', 'profile': 'alpha',
                                           'coalesce': {'key': 'id', 'window_seconds': 0.1}}})
    settled = lambda: not adapter._coalescer.pending and not adapter._background_tasks
    try:
        answer = await adapter._handle_webhook(_request('delivery-1', {'id': 17}, 'alpha'))
        await async_wait_until(settled, what='the batch dispatched')
        return answer, (_admissions(homes[2][1]), _admissions(homes[1][1]))
    finally:
        _cancel(adapter)
        _close(runner, homes)


@pytest.mark.asyncio
async def test_g1_claim_head_refuses_explicit_alpha_while_beta_is_parked(tmp_path, monkeypatch):
    """The observable the comment describes: head answers 503 (FAILS on head); main answers 202 and
    then drops the batch at admission (FAILS on main at the admission assert)."""
    answer, admitted = await _deliver(tmp_path, monkeypatch, serve_beta=False)
    assert answer.status == 202, (answer.status, answer.text)
    assert admitted == (1, 0), f'(alpha, beta) admissions: {admitted}'


@pytest.mark.asyncio
async def test_g1_refutation_with_beta_served_the_same_delivery_never_runs_on_alpha(tmp_path, monkeypatch):
    """PASSES on head and main: with beta served, /p/alpha/ is still admitted nowhere
    (``_binding`` profile_mismatch), so the parked 503 blocks nothing that could run."""
    answer, admitted = await _deliver(tmp_path, monkeypatch, serve_beta=True)
    assert answer.status == 202, (answer.status, answer.text)
    assert admitted == (0, 0), f'(alpha, beta) admissions: {admitted}'
