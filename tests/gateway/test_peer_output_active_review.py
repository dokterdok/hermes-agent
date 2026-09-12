"""Bounded publication and completed-custody lifecycle checks with local HTTP."""
from dataclasses import replace
import json

import pytest

from tests.gateway.test_api_cutover_contract import api, owner  # noqa: F401
from tests.gateway.test_canonical_peer_output_flow import (
    test_peer_tool_run_history_and_home_copy_precede_ack as peer_flow,
)


@pytest.mark.asyncio
async def test_completed_custody_does_not_depend_on_subsequently_revoked_grant(api, owner, tmp_path, monkeypatch):
    from gateway.session_peer_output_custody import PeerOutputCustody

    original = PeerOutputCustody.acknowledge
    retired = []

    def acknowledge_then_revoke(custody, scope, artifact_ids, *, message_event_id):
        result = original(custody, scope, artifact_ids, message_event_id=message_event_id)
        assert result['acknowledged'] is True
        if not retired:
            client, grant, run_id = custody._route(scope)
            receipt = client.revoke_grant_exact(grant=grant)
            assert receipt['revoked'] is True
            retired.append(run_id)
        return result

    monkeypatch.setattr(PeerOutputCustody, 'acknowledge', acknowledge_then_revoke)
    # Original flow has already published and downloaded the exact canonical bytes
    # before its second prepare_room. Revocation is ordinary bearer lifecycle only.
    await peer_flow(api, owner, tmp_path, monkeypatch)
    assert len(retired) == 1


@pytest.mark.asyncio
async def test_final_peer_publication_checks_run_member_generation_inside_writer(api, owner, tmp_path, monkeypatch):
    from gateway import hosted_room_output_fence as fence
    from gateway.hosted_room_artifacts import RoomArtifactError, RoomArtifactScope

    original = fence.require_output_publication
    observed = []

    def checked(conn, room_id, expected, **kwargs):
        scope = RoomArtifactScope.from_mapping(expected['scope'])
        if scope.target_install_id != scope.home_install_id:
            assert conn.in_transaction
            row = fence.require_output_task(conn, scope, expected['cancel_generation'])
            result = json.loads(row['result_json'])
            assert fence.require_peer_output_receipt(conn, scope, result)['run_id'] == result['peer_run_id']
            for changed in ({'peer_run_id': 'different-run', 'message_id': 'peer-run:different-run'},
                            {'message_id': 'peer-run:another-receipt'}):
                with pytest.raises(RoomArtifactError, match='Run changed'):
                    fence.require_peer_output_receipt(conn, scope, {**result, **changed})
            for changed_scope in (replace(scope, target_install_id='another-install'),
                                  replace(scope, target_profile='named'),
                                  replace(scope, execution_generation=scope.execution_generation + 1)):
                with pytest.raises(RoomArtifactError):
                    fence.require_output_task(conn, changed_scope, expected['cancel_generation'])
            with pytest.raises(RoomArtifactError, match='attempt changed'):
                fence.require_output_task(conn, scope, expected['cancel_generation'] + 1)
            observed.append(scope.task_id)
        return original(conn, room_id, expected, **kwargs)

    monkeypatch.setattr(fence, 'require_output_publication', checked)
    await peer_flow(api, owner, tmp_path, monkeypatch)
    assert observed
