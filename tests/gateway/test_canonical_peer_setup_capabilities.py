"""Protocol support and scoped endpoint readiness remain separate capabilities."""
import pytest

from gateway.session_group_controls import dispatch_group_control
from tests.gateway.test_canonical_group_peer_setup import owner  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint,enabled', [('', False), ('https://peer.example.test/hermes', True), ('file:///fixture', False)])
async def test_setup_features_require_usable_explicit_endpoint_and_preserve_scope(owner, monkeypatch, endpoint, enabled):
    connection, service, profile = owner
    monkeypatch.setenv('HERMES_ROOM_LINK_URL', endpoint)
    value = await dispatch_group_control(connection, 'groups.capabilities', {})
    assert value['room_link']['enabled'] is enabled
    assert value['room_link']['profile'] == profile
    assert 'peer_invitation_request_id' in value['features']
    assert 'reciprocal_room_control_setup' in value['features']
    assert 'canonical_session_owner' in value['features'] and value['driver'] is False
    if enabled:
        assert value['room_link']['catalog']['execution_policy']['target_profile'] == profile
        assert value['room_link']['endpoint']['url'] == endpoint
        assert value['room_link']['reason'] is None
    else:
        assert value['room_link']['reason'] != 'canonical_peer_controls_required'
