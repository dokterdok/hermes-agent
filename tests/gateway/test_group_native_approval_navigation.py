"""Native group detail reaches the existing fenced approval decision path."""

import sqlite3

import pytest

from gateway import hosted_room_messaging_approvals as approvals
from gateway.choice_picker import ChoicePage
from tests.gateway.test_group_native_menu_navigation import english, menu_for, token
from tests.gateway.test_hosted_room_file_access import file_state
from tests.gateway.test_hosted_room_messaging_files import consumer


def pending(state, request_id="approval-1"):
    approvals.persist_pending_approval(
        state.db, room_id="room-1", member_id="ops",
        action={
            "kind": "approval", "authority_gateway_id": state.authority,
            "authority_epoch": 1, "task_id": "task-1", "execution_generation": 1,
            "request_id": request_id,
            "approval": {"description": "Remove obsolete draft", "command": "rm -rf ./obsolete-draft",
                         "choices": ["once", "deny"]},
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["once", "deny"])
async def test_group_detail_opens_native_approval_and_journals_one_exact_decision(consumer, decision):
    state, _, _ = consumer
    menu = await menu_for(consumer)
    page = await menu.room_page()
    assert not any(action[0] == "approvals" for action in menu.actions.values())
    pending(state)
    page = await menu.room_page()
    assert "waiting for your approval" in page.title
    page = await menu.choose("chat", token(menu, page, "approvals"))
    assert "Approval needed · Files" in page.title
    assert "rm -rf ./obsolete-draft" in page.title
    assert all(choice["full_width"] for choice in page.choices)
    assert not approvals.list_pending_approval_commands(state.db, room_id="room-1")
    index = 0 if decision == "once" else 1
    value = page.choices[index]["value"]
    result = await menu.choose("chat", value)
    assert isinstance(result, ChoicePage)
    assert "Decision sent for ops." in result.title
    commands = approvals.list_pending_approval_commands(state.db, room_id="room-1")
    assert [(row["choice"], row["request_id"]) for row in commands] == [(decision, "approval-1")]
    await menu.choose("chat", value)
    assert len(approvals.list_pending_approval_commands(state.db, room_id="room-1")) == 1
    result = await menu.choose("chat", token(menu, result, "room"))
    assert isinstance(result, ChoicePage)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["wrong_chat", "permission", "authority", "request", "expired"])
async def test_nested_approval_never_applies_a_stale_or_unauthorized_choice(consumer, monkeypatch, change):
    state, runner, _ = consumer
    menu = await menu_for(consumer)
    pending(state)
    page = await menu.room_page()
    page = await menu.choose("chat", token(menu, page, "approvals"))
    selected = page.choices[0]["value"]
    if change == "permission":
        monkeypatch.setattr(runner, "_can_approve_group_chats", lambda event: False)
    elif change == "authority":
        with sqlite3.connect(state.db) as conn:
            conn.execute("UPDATE hosted_rooms SET authority_epoch=2 WHERE room_id='room-1'")
    elif change == "request":
        pending(state, "approval-2")
    elif change == "expired":
        menu.deadline = 0
    await menu.choose("other" if change == "wrong_chat" else "chat", selected)
    assert not approvals.list_pending_approval_commands(state.db, room_id="room-1")
