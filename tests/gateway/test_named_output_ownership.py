"""Named Output must not reopen another profile owner's state database."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gateway import session_hosted_output
from tests.gateway.test_hosted_mux_runtime import mux  # noqa: F401


@pytest.mark.parametrize("ownership", ["served-named", "foreign-owner-transport"])
def test_named_output_refuses_foreign_database_binding(tmp_path, monkeypatch, ownership):
    service = SimpleNamespace(check_admission=Mock(side_effect=AssertionError("must not inspect foreign task")))
    ref = SimpleNamespace(session_id="session", profile_id="")
    row = {
        "request_id": 'hosted:[{"room_id":"room"},1]',
        "target_session_id": ref.session_id,
        "principal_id": "hosted-owner",
    }

    if ownership == "served-named":
        home = tmp_path / "profiles" / "reviewer"
        # This negative control has no owner-transport consent. Actual named
        # admission and consent are exercised by test_named_output_owner_rpc.
        monkeypatch.setattr("gateway.session_managed_worker.managed_policy", lambda *_: None)
        monkeypatch.setattr(session_hosted_output, "_is_owner_transport_admission", lambda *_: False)
    else:
        home = tmp_path / "reviewer-owner"
        monkeypatch.setattr("gateway.session_managed_worker.managed_policy", lambda *_: None)
        monkeypatch.setattr(session_hosted_output, "_is_owner_transport_admission", lambda *_: True)

    ref.profile_id = str(home)
    authority = SimpleNamespace(
        profile_id=str(home),
        db=SimpleNamespace(db_path=home / "state.db"),
        hosted_room_service=service,
        runner=object(),
        sessions={ref.session_id: SimpleNamespace(source=object())},
    )
    monkeypatch.setattr(
        "gateway.session_policy.policy_for_source",
        lambda *_: SimpleNamespace(source="bot_room", toolsets=("bot_room",)),
    )

    assert session_hosted_output._binding(authority, ref, row) is None
    service.check_admission.assert_not_called()
    assert not authority.db.db_path.exists()


@pytest.mark.parametrize("source_name,target_name", [("alpha", "beta"), ("beta", "alpha")])
def test_named_service_initializes_owner_output_before_target_capture(mux, source_name, target_name):
    from gateway import hosted_rooms, session_hosted_output_rpc as output
    from gateway.hosted_room_driver import TaskIdentity
    from gateway.session_authorities import owner_scope
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from hermes_state_runtime import RuntimeStoreError

    runner, homes, loop, _ = mux
    source = runner.session_authorities.require(homes[source_name])
    target = runner.session_authorities.require(homes[target_name])
    for authority in (source, target):
        with owner_scope(authority):
            authority.hosted_room_service = CanonicalHostedRoomService(authority, loop)
    service = target.hosted_room_service
    with target.db._read_ctx() as conn:
        provider = output._provider(service, conn)
    assert provider.db_path.resolve() == target.db.db_path.resolve()
    assert provider.root.resolve() == homes[target_name] / "hosted-room-artifact-outbox"
    assert service.runtime.publish_settled_secondary == service.publish_settled_invitation_secondary

    room_id = "owner-init"
    selector = {"room_id": room_id, "member_id": "helper", "profile": target_name}
    with owner_scope(source):
        source.hosted_room_service.authorize_room("alice", room_id, create=True)
        hosted_rooms.create_room(source.db.db_path, room_id=room_id, name="Owner init",
            authority_gateway_id=hosted_rooms.local_authority_gateway_id(),
            members=[{"member_id": "helper", "profile": target_name, "handle": "helper"}])
        attested = output.source_output_admission(
            source.hosted_room_service, selector, TaskIdentity(room_id, "task", "thread", "turn"),
            1, owner="alice", target_home=str(homes[target_name]))
    assert attested is not None
    binding = {"source_home": str(homes[source_name]), "target_home": str(homes[target_name]),
               "selector": selector}
    context = output.capture_owner_output_context(
        target, binding, {"owner_output_admission": attested}, "alice")
    assert context is not None
    assert context.authority is target and context.service is service
    assert context.source_authority is source
    with source.db._read_ctx() as foreign_conn:
        with pytest.raises(RuntimeStoreError, match="output_owner_unavailable"):
            output._provider(service, foreign_conn)
    with pytest.raises(RuntimeStoreError, match="permission_denied"):
        output.capture_owner_output_context(target,
            {**binding, "target_home": str(homes["default"])},
            {"owner_output_admission": attested}, "alice")
