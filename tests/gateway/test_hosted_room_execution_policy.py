"""Execution-authority and typed-handoff regression tests for RoomLink."""

from __future__ import annotations

import hashlib

from gateway.hosted_room_execution_policy import (
    RoomExecutionPolicy,
    bind_room_execution_policy,
    execution_policy_mapping,
    reset_room_execution_policy,
)
from gateway.hosted_room_turn_context import (
    bind_room_turn_context,
    reset_room_turn_context,
    room_turn_context_from_mapping,
)
from tools import approval
from tools.hosted_room_handoff import handoff_group_task
from gateway.hosted_room_peer import (
    GatewayRoomCatalog,
    HostedMemberDispatch,
    catalog_mapping,
)
from tui_gateway.hosted_room_peer_http import PeerRunsHTTPError
from tui_gateway.hosted_room_service import _RouteStatusPeerClient


def _policy(*, approval_mode: str = "manual", max_turns: int = 12) -> dict:
    return execution_policy_mapping(
        target_profile="reviewer",
        config={
            "agent": {"max_turns": max_turns},
            "approvals": {"mode": approval_mode},
            "platform_toolsets": {
                "api_server": ["hermes-api-server", "web"]
            },
        },
    )


def test_execution_policy_digest_covers_tools_approvals_and_iteration_limit():
    value = _policy()
    checked = RoomExecutionPolicy.from_mapping(value)
    assert "bot_room" in checked.enabled_toolsets

    for field, replacement in (
        ("enabled_toolsets", ["bot_room"]),
        ("approval_mode", "off"),
        ("max_iterations", 99),
    ):
        tampered = {**value, field: replacement}
        try:
            RoomExecutionPolicy.from_mapping(tampered)
        except ValueError as exc:
            assert "policy_digest" in str(exc)
        else:
            raise AssertionError(f"tampered {field} was accepted")


def test_room_policy_overrides_broader_live_approval_config(monkeypatch):
    policy = RoomExecutionPolicy.from_mapping(_policy(approval_mode="manual"))
    monkeypatch.setattr(
        approval,
        "_get_approval_config",
        lambda: {"mode": "off"},
    )
    token = bind_room_execution_policy(policy)
    try:
        assert approval._get_approval_mode() == "manual"
    finally:
        reset_room_execution_policy(token)


def test_typed_handoff_is_recipient_scoped_and_rejects_unknown_bot():
    context = room_turn_context_from_mapping(
        {
            "member_id": "member-reviewer",
            "provenance": {"kind": "user", "user_event_id": "user-1"},
            "handoff_targets": [
                {"member_id": "member-build", "handle": "build"}
            ],
        }
    )
    token = bind_room_turn_context(context)
    try:
        assert '"ok": false' in handoff_group_task("@unknown", "Delete everything")
        assert '"ok": true' in handoff_group_task(
            "@build", "Review the user-requested patch."
        )
    finally:
        reset_room_turn_context(token)

    assert context.handoffs() == [
        {
            "recipient_member_id": "member-build",
            "recipient_handle": "build",
            "objective": "Review the user-requested patch.",
        }
    ]


def test_policy_drift_refreshes_target_grant_then_retries_once():
    old_policy = _policy(max_turns=7)
    new_policy = _policy(max_turns=5)
    old_catalog = GatewayRoomCatalog.from_mapping(
        catalog_mapping(
            installation_id="install-peer",
            persistent_process=True,
            target_profile="reviewer",
            execution_policy=old_policy,
        )
    )
    new_catalog = GatewayRoomCatalog.from_mapping(
        catalog_mapping(
            installation_id="install-peer",
            persistent_process=True,
            target_profile="reviewer",
            execution_policy=new_policy,
        )
    )
    prompt = "Review the user's patch."
    dispatch = HostedMemberDispatch.from_mapping(
        {
            "protocol_version": 2,
            "room_id": "room-1",
            "home_install_id": "install-home",
            "authority_gateway_id": "gateway-home",
            "authority_epoch": 1,
            "member_id": "member-reviewer",
            "target_install_id": "install-peer",
            "target_profile": "reviewer",
            "task_id": "task-1",
            "execution_generation": 1,
            "source_event_seq": 1,
            "cancellation_scope_id": "cancel-1",
            "prompt": prompt,
            "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(),
            "capability_digest": old_catalog.catalog_digest,
            "execution_policy_digest": old_policy["policy_digest"],
            "provenance": {"kind": "user", "user_event_id": "user-1"},
            "handoff_targets": [
                {"member_id": "member-build", "handle": "build"}
            ],
            "trace_id": "trace-1",
        }
    )

    class DriftClient:
        def __init__(self):
            self.dispatches = []

        def dispatch(self, **kwargs):
            self.dispatches.append(kwargs)
            if len(self.dispatches) == 1:
                raise PeerRunsHTTPError(
                    "policy changed",
                    status_code=403,
                    error_code="room_execution_policy_changed",
                    not_admitted=True,
                )
            return {"status": "accepted"}

        def refresh_grant(self, **_kwargs):
            return {
                "grant": "grant-new",
                "catalog": new_catalog.as_mapping()
                if hasattr(new_catalog, "as_mapping")
                else {
                    "installation_id": new_catalog.installation_id,
                    "protocol_versions": list(new_catalog.protocol_versions),
                    "link_modes": list(new_catalog.link_modes),
                    "persistent_process": new_catalog.persistent_process,
                    "text": new_catalog.text,
                    "attachments": new_catalog.attachments,
                    "execution_policy": new_catalog.execution_policy.as_mapping(),
                    "catalog_digest": new_catalog.catalog_digest,
                    "endpoint": new_catalog.endpoint_mapping(),
                },
            }

    refreshed = []
    client = DriftClient()
    tracked = _RouteStatusPeerClient(
        client,
        on_ready=lambda: None,
        on_reauthorization=lambda: None,
        on_unavailable=lambda: None,
        on_refreshed=lambda grant, catalog=None: refreshed.append((grant, catalog)),
    )

    assert tracked.dispatch(
        dispatch=dispatch.as_mapping(), grant="grant-old"
    ) == {"status": "accepted"}
    assert len(client.dispatches) == 2
    assert client.dispatches[1]["grant"] == "grant-new"
    assert (
        client.dispatches[1]["dispatch"]["execution_policy_digest"]
        == new_policy["policy_digest"]
    )
    assert refreshed[0][0] == "grant-new"
