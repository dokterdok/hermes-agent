"""Human approval controls for exact canonical Group Chat requests."""
import hashlib
import json

from gateway.group_chat_messages import text
from gateway.group_chat_send import message_command_id
from gateway.group_chat_work import run_group_read, run_group_command_work
from gateway.group_home_consent import require_current
from gateway.hosted_room_messaging_presentation import _plain_display_label, _plain_preview_text, room_reference


def decision_code(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


async def approvals_text(runner, event, backend, room, command, stamp):
    pending = await run_group_read(lambda: backend.approvals(room))
    require_current(runner, event, stamp)
    if not pending:
        return text('group_files', 'no_approvals')
    lines = [text('group_files', 'approvals') + ' · ' + _plain_display_label(room['name']), '']
    for item in pending:
        code = decision_code(item)[:12]
        lines += ['**' + _plain_display_label(item['member_id']) + '**',
                  _plain_preview_text(item['description'], limit=512), _plain_preview_text(item['command'], limit=512),
                  f"Allow once: `{command} {room_reference(room)} approve {code}`",
                  f"Deny: `{command} {room_reference(room)} deny {code}`", '']
    return '\n'.join(lines)


async def decide_from_chat(runner, event, backend, room, code, choice, stamp):
    if choice not in {'once', 'deny'} or not isinstance(code, str) or not 8 <= len(code) <= 64:
        return 'Choose a request from Review approvals.'
    try:
        pending = await run_group_read(lambda: backend.approvals(room))
        require_current(runner, event, stamp)
        matches = [item for item in pending if decision_code(item).startswith(code)]
        if len(matches) != 1:
            return 'This request is no longer waiting for a decision. Open Review approvals again.'
        item = matches[0]
        decision = {key: item[key] for key in ('member_id', 'task_id', 'execution_generation', 'request_id')} | {'choice': choice}
        delivery = message_command_id(event, room, backend.authority.profile_id)
        command_id = 'approval-' + hashlib.sha256(json.dumps([delivery, decision], sort_keys=True).encode()).hexdigest()
        value = await run_group_command_work(runner, 'deny' if choice == 'deny' else 'approve',
            lambda: backend.decide(room=room, command_id=command_id, decision=decision))
        require_current(runner, event, stamp)
        return 'Already handled.' if value['status'] == 'already_resolved' else 'Allowed once.' if choice == 'once' else 'Denied.'
    except Exception:
        return 'This decision could not be confirmed. Check the request before trying again.'
