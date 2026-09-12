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
                  f"Deny: `{command} {room_reference(room)} deny {code}`"]
        if room.get('_room_mode') != 'remote' and item.get('remember_key'):
            lines.append(f"Always allow in this chat: `{command} {room_reference(room)} remember {code}`")
        lines.append('')
    return '\n'.join(lines)


async def decide_from_chat(runner, event, backend, room, code, choice, stamp):
    if choice not in {'once', 'deny', 'remember'} or not isinstance(code, str) or not 8 <= len(code) <= 64:
        return 'Choose a request from Review approvals.'
    try:
        pending = await run_group_read(lambda: backend.approvals(room))
        require_current(runner, event, stamp)
        matches = [item for item in pending if decision_code(item).startswith(code)]
        if len(matches) != 1:
            return 'This request is no longer waiting for a decision. Open Review approvals again.'
        item = matches[0]
        decision = {key: item[key] for key in ('member_id', 'task_id', 'execution_generation', 'request_id')} | {'choice': choice}
        if choice == 'remember':
            if room.get('_room_mode') == 'remote' or not item.get('remember_key'):
                return 'This request can only be allowed once here.'
            decision['remember_key'] = item['remember_key']
        delivery = message_command_id(event, room, backend.authority.profile_id)
        command_id = 'approval-' + hashlib.sha256(json.dumps([delivery, decision], sort_keys=True).encode()).hexdigest()
        value = await run_group_command_work(runner, 'deny' if choice == 'deny' else 'approve',
            lambda: backend.decide(room=room, command_id=command_id, decision=decision))
        require_current(runner, event, stamp)
        if value['status'] == 'already_resolved':
            return 'Already handled. Check the Group Chat for the outcome.'
        if choice == 'remember':
            return 'Allowed. This exact operation is now allowed in this Group Chat.' if value.get('remembered') is True else 'Allowed once. The saved permission could not be confirmed.'
        return 'Allowed once.' if choice == 'once' else 'Denied.'
    except Exception:
        return 'This decision could not be confirmed. Check the request before trying again.'


def remember_warning(item):
    return '\n\n'.join(['Always allow in this chat?',
        'This Bot may repeat this exact command in this folder without asking again. Other commands are not covered by this permission.',
        _plain_display_label(item['member_id']), _plain_preview_text(item['command'], limit=512),
        _plain_preview_text(item.get('remember_context', ''), limit=384),
        'Remove this permission at any time from Manage permissions. Commands already approved may still finish.'])


async def remember_from_chat(runner, event, backend, room, argument, command, stamp):
    parts = argument.split()
    if not 1 <= len(parts) <= 2 or (len(parts) == 2 and parts[1] != 'confirm'):
        return 'Choose a request from Review approvals.'
    pending = await run_group_read(lambda: backend.approvals(room))
    matches = [item for item in pending if decision_code(item).startswith(parts[0]) and item.get('remember_key')]
    if room.get('_room_mode') == 'remote' or len(matches) != 1:
        return 'This request cannot be remembered here.'
    item = matches[0]
    code = decision_code(item)
    require_current(runner, event, stamp)
    if len(parts) == 2 and parts[0] == code:
        return await decide_from_chat(runner, event, backend, room, code, 'remember', stamp)
    return remember_warning(item) + f'\n\nConfirm: `{command} {room_reference(room)} remember {code} confirm`'


async def permissions_text(runner, event, backend, room, command, stamp, *, forget=None, page=1):
    rules = await run_group_read(lambda: backend.permissions(room))
    require_current(runner, event, stamp)
    if forget is not None:
        selected = [rule for rule in rules if rule['rule_id'].startswith(forget)] if len(forget) >= 8 else []
        if len(selected) != 1:
            return 'Choose a permission ID from Manage permissions.'
        rule = selected[0]
        changed = await run_group_command_work(runner, 'deny',
            lambda: backend.forget_permission(room, rule['rule_id'], rule['generation']))
        require_current(runner, event, stamp)
        return 'Future requests will ask again. A command already approved may still finish.' if changed else 'This permission changed. Refresh the list.'
    if not rules:
        return 'No remembered permissions in this Group Chat.'
    pages = (len(rules) + 7) // 8
    if not 1 <= page <= pages:
        return f'Choose a page from 1 to {pages}.'
    lines = ['Manage permissions · ' + _plain_display_label(room['name']),
             text('group_presentation', 'page', current=page, total=pages), '']
    for rule in rules[(page - 1) * 8:page * 8]:
        state = 'Allowed' if rule['state'] == 'active' else 'Not confirmed'
        lines += [state + ' · ' + _plain_display_label(rule['member_id']), _plain_preview_text(rule['command_text'], limit=512),
                  _plain_preview_text(rule['context_text'], limit=384),
                  f"Remove permission: `{command} {room_reference(room)} forget {rule['rule_id'][:12]}`", '']
    for target in (page - 1, page + 1):
        if 1 <= target <= pages:
            lines.append(f'Go to page {target}: `{command} {room_reference(room)} permissions {target}`')
    return '\n'.join(lines)


async def dispatch_decision_command(runner, event, backend, room, kind, argument, command, stamp):
    async def approvals():
        if argument:
            return f'Use `{command} {room_reference(room)} approvals` to review requests.'
        from gateway.group_chat_menu import show_group_menu
        if await show_group_menu(runner, event, backend, command, stamp, room=room, view='approvals'):
            return None
        return await approvals_text(runner, event, backend, room, command, stamp)

    async def permissions():
        if argument and (not argument.isascii() or not argument.isdecimal() or len(argument) > 4):
            return f'Use `{command} {room_reference(room)} permissions [page]`.'
        return await permissions_text(runner, event, backend, room, command, stamp, page=int(argument or '1'))

    handlers = {
        'approvals': approvals,
        'approve': lambda: decide_from_chat(runner, event, backend, room, argument, 'once', stamp),
        'deny': lambda: decide_from_chat(runner, event, backend, room, argument, 'deny', stamp),
        'remember': lambda: remember_from_chat(runner, event, backend, room, argument, command, stamp),
        'permissions': permissions,
        'forget': lambda: permissions_text(runner, event, backend, room, command, stamp, forget=argument),
    }
    return await handlers[kind]()
