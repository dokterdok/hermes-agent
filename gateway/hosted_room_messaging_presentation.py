"""Source #98073 presentation primitives on read-only canonical snapshots."""
from collections.abc import Mapping
from datetime import datetime
import math
import time
from typing import Any
import re

from gateway import hosted_rooms, hosted_room_driver as driver
from gateway.group_chat_messages import text as message

MAX_PREVIEW_CHARS = 180
MAX_GROUP_MEMBERS = 6
MAX_ROOM_CHOICES = 8


class RoomControlError(ValueError):
    pass


def _clean_line(value: Any, *, limit: int = MAX_PREVIEW_CHARS) -> str:
    """Collapse untrusted text to one bounded display line."""

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"



def _plain_display_label(value: Any, *, limit: int = MAX_PREVIEW_CHARS) -> str:
    """Neutralize markup-shaped labels before placing them in rich layouts."""

    text = _clean_line(value, limit=limit)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[\\`*_{}\[\]#|>~]", "", text).strip()
    return text.replace("@", "＠") or "Unnamed"



def _plain_preview_text(value: Any, *, limit: int = MAX_PREVIEW_CHARS) -> str:
    """Neutralize preview markup without deleting message content."""

    text = _clean_line(value, limit=limit)
    return text.translate(
        str.maketrans({
            "@": "＠",
            "\\": "＼",
            "`": "｀",
            "*": "＊",
            "_": "＿",
            "{": "｛",
            "}": "｝",
            "[": "［",
            "]": "］",
            "#": "＃",
            "|": "｜",
            ">": "＞",
            "~": "～",
        })
    )



def room_reference(room: Mapping[str, Any]) -> str:
    """Return the short messaging reference, with an internal-id fallback."""

    reference = room.get("messaging_ref")
    if isinstance(reference, int) and reference > 0:
        return str(reference)
    return str(room.get("room_id") or "")



def _room_member_count(room: Mapping[str, Any]) -> int:
    value = room.get("member_count")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    members = room.get("members")
    return len(members) if isinstance(members, list) else 0



def _room_status_icon(status: str) -> str:
    lowered = str(status or "").casefold()
    if any(
        word in lowered
        for word in ("attention", "blocked", "error", "offline", "unavailable")
    ):
        return "⚠️"
    if "connected" in lowered:
        return "⚪"
    if any(
        word in lowered
        for word in ("queued", "running", "stopping", "waiting", "working")
    ):
        return "🟡"
    return "🟢"



def _room_member_name(member: Mapping[str, Any]) -> str:
    return _clean_line(
        member.get("display_name")
        or member.get("displayName")
        or member.get("name")
        or member.get("handle")
        or "Bot",
        limit=48,
    )



def _room_member_handle(member: Mapping[str, Any]) -> str:
    handle = _clean_line(
        member.get("handle"),
        limit=driver.MAX_IDENTIFIER_CHARS,
    ).lstrip("@")
    return handle if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", handle) else ""



def _room_participant_lines(room: Mapping[str, Any]) -> list[str]:
    raw_members = room.get("members")
    if not isinstance(raw_members, list):
        return []
    lines: list[str] = []
    for raw in raw_members[:MAX_GROUP_MEMBERS]:
        if not isinstance(raw, Mapping):
            continue
        name = _room_member_name(raw)
        handle = _room_member_handle(raw)
        suffix = f" (`@{handle}`)" if handle else ""
        lines.append(f"• {_plain_display_label(name)}{suffix}")
    return lines



def _event_label(event: Mapping[str, Any], member_names: Mapping[str, str]) -> str:
    raw_actor = event.get("actor")
    actor: Mapping[str, Any] = raw_actor if isinstance(raw_actor, Mapping) else {}
    actor_id = str(actor.get("id") or "")
    display_name = _clean_line(actor.get("display_name"), limit=48)
    if display_name:
        return display_name
    if event.get("kind") == "message.member":
        raw_payload = event.get("payload")
        payload: Mapping[str, Any] = (
            raw_payload if isinstance(raw_payload, Mapping) else {}
        )
        member_id = str(payload.get("member_id") or actor_id)
        return member_names.get(member_id, "Bot")
    return "You"



def resolve_room(rooms: list[dict[str, Any]], query: str) -> dict[str, Any]:
    """Resolve by stable messaging number, then id/name convenience matches."""

    needle = _clean_line(query, limit=hosted_rooms.MAX_ROOM_NAME_CHARS).casefold()
    if not needle:
        raise RoomControlError("Enter a Group Chat number or name.")

    if needle.isdecimal():
        numeric_ref = int(needle)
        matches = [room for room in rooms if room.get("messaging_ref") == numeric_ref]
        if len(matches) == 1:
            return matches[0]
        raise RoomControlError(f"No Group Chat is numbered {numeric_ref}.")

    if needle.startswith("id:"):
        internal_id = needle.removeprefix("id:")
        matches = [
            room
            for room in rooms
            if str(room.get("room_id") or "").casefold() == internal_id
        ]
        if len(matches) == 1:
            return matches[0]
        raise RoomControlError("No Group Chat matches that internal ID.")

    def _keys(room: Mapping[str, Any]) -> tuple[str, str]:
        return (
            str(room.get("room_id") or "").casefold(),
            str(room.get("name") or "").casefold(),
        )

    for match_kind in ("exact", "prefix", "contains"):
        matches: list[dict[str, Any]] = []
        for room in rooms:
            room_id, name = _keys(room)
            if match_kind == "exact" and needle in {room_id, name}:
                matches.append(room)
            elif match_kind == "prefix" and (
                room_id.startswith(needle) or name.startswith(needle)
            ):
                matches.append(room)
            elif match_kind == "contains" and (needle in room_id or needle in name):
                matches.append(room)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(
                (
                    f"{_clean_line(room.get('name') or room.get('room_id'), limit=48)} "
                    f"[{room_reference(room)}]"
                )
                for room in matches[:MAX_ROOM_CHOICES]
            )
            suffix = "…" if len(matches) > MAX_ROOM_CHOICES else ""
            raise RoomControlError(
                f"That matches several group chats: {names}{suffix}. Enter more of the name."
            )
    raise RoomControlError(f"No group chat matches “{_clean_line(query)}”.")



def text(key, **values):
    return message('group_presentation', key, **values)


def action(key, command):
    return text('action', label=text(key), command=f'`{command}`')


def read_actions(command, reference=None, *, can_send=False):
    result = ['', text('actions')]
    if reference is not None:
        if can_send:
            result += [action('send_message', f'{command} {reference} send <message>')]
        result += [action('view_group', f'{command} {reference}'), action('view_bots', f'{command} {reference} bots'),
                   message('group_files', 'command_hint', caption=message('group_files', 'files'), command=f'`{command} {reference} files`')]
    return result + [action('help', f'{command} help')]


def status_text(status):
    if status.get('counts', {}).get('stopping'):
        return 'stopping'
    if status.get('blocked') or status.get('needs_attention'):
        return 'needs attention'
    if status.get('working'):
        return 'work queued or running'
    return 'idle'

def timestamp(value, *, milliseconds=False):
    """Normalize known wire units; reject unknown or timezone-ambiguous values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        if not isinstance(value, str) or milliseconds:
            return None
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                return None
            number = instant.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    if milliseconds:
        number /= 1000
    return number if math.isfinite(number) and number > 0 else None


def event_age(event, *, desktop, now):
    stamp = timestamp(event.get("at") if desktop else event.get("created_at"), milliseconds=desktop)
    if stamp is None or stamp > now:
        return ""
    seconds = now - stamp
    if seconds < 60:
        return text("age_now")
    for unit, width, ceiling in (("minutes", 60, 3600), ("hours", 3600, 86400), ("days", 86400, math.inf)):
        if seconds < ceiling:
            return text("age_" + unit, count=int(seconds // width))
    return ""


def recent_heading(events, *, desktop, now):
    # The last visible event is authoritative. An older known time must not
    # masquerade as the latest when the newest event has no valid timestamp.
    age = event_age(events[-1], desktop=desktop, now=now) if events else ""
    return text("recent_latest", age=age) if age else text("recent")


def format_room_list(backend, rooms, command='/group', page=1):
    if not rooms:
        return '👥 **No shared Group Chats**\n\nOpen Hermes Desktop to check your groups.'
    count = (len(rooms) + MAX_ROOM_CHOICES - 1) // MAX_ROOM_CHOICES
    if not 1 <= page <= count:
        raise RoomControlError(f'There are only {count} Group Chat pages.')
    selected = sorted(rooms, key=lambda room: room['messaging_ref'])[(page-1)*MAX_ROOM_CHOICES:page*MAX_ROOM_CHOICES]
    lines = ['👥 **Group Chats**', text('page', current=page, total=count)]
    for room in selected:
        snapshot = backend.summary(room)
        name, state = _plain_display_label(snapshot['room']['name'], limit=72), status_text(snapshot['status'])
        members = len(snapshot['room']['members'])
        lines += ['', f"{_room_status_icon(state)} **{room_reference(room)}. {name}** · {state} · {members} Bot{'s' if members != 1 else ''}"]
    if page > 1:
        lines += ['', text('action', label=text('go_to_page', page=page-1), command=f'`{command} list {page-1}`')]
    if page < count:
        lines += ['', text('action', label=text('go_to_page', page=page+1), command=f'`{command} list {page+1}`')]
    return '\n'.join(lines + read_actions(command))


def format_room_detail(backend, room, command='/group', *, native=False, snapshot=None):
    snapshot = backend.summary(room) if snapshot is None else snapshot
    current = snapshot['room']
    state = status_text(snapshot['status'])
    lines = [f"💬 **{_plain_display_label(current['name'], limit=72)}**", f'{_room_status_icon(state)} {state}', '', '🤖 **Bots**', *_room_participant_lines(current)]
    names = {member['member_id']: _room_member_name(member) for member in current['members']}
    visible = [event for event in snapshot['events'] if event.get('kind') in {'message.user', 'message.member'}][-(3 if native else 5):]
    now = time.time()
    lines += ['', recent_heading(visible, desktop=False, now=now)]
    for event in visible:
        age = event_age(event, desktop=False, now=now)
        lines += [f"• **{_plain_display_label(_event_label(event, names))}**" + (f' · _{age}_' if age else ''),
                  _plain_preview_text(event.get('payload', {}).get('text'))]
    if not visible:
        lines += ['No messages yet.']
    return '\n'.join(lines + ([] if native else read_actions(command, room_reference(room), can_send='send' in snapshot.get('control_actions', []))))


def format_room_bots(backend, room, command='/group', selected=None, *, native=False, snapshot=None):
    current = (backend.summary(room) if snapshot is None else snapshot)['room']
    members = current['members'][:MAX_GROUP_MEMBERS]
    if selected is not None:
        candidates = [member for index, member in enumerate(members, 1) if selected == str(index) or selected.lstrip('@').casefold() == _room_member_handle(member).casefold()]
        if len(candidates) != 1:
            raise RoomControlError('No Bot in this Group Chat matches that number or handle.')
        members = candidates
    lines = [f"🤖 **Bots in {_plain_display_label(current['name'], limit=72)}**"]
    for index, member in enumerate(members, 1):
        name, handle = _plain_display_label(_room_member_name(member)), _room_member_handle(member)
        lines += ['', f"{index}. **{name}**" + (f' · `@{handle}`' if handle else '')]
    return '\n'.join(lines + ([] if native else read_actions(command, room_reference(room))))


def format_room_files(backend, room, command='/group', query=''):
    from gateway.hosted_room_file_lookup import selection_digest
    page = backend.list_files(room=room, query=query, limit=8)
    lines = [message('group_files', 'title', name=_plain_display_label(room['name'])), '']
    for item in page['items']:
        name = _plain_display_label(item['name'], limit=80)
        producer = _plain_display_label(item['producer']['label'], limit=48)
        lines += [f"• **{name}** · {producer} · {item['size']} B · `{selection_digest(room, item)[:12]}`"]
    if not page['items']:
        lines += [message('group_files', 'no_match' if query else 'empty')]
    if page['has_more']:
        lines += [message('group_files', 'search_with', command=f'`{command} {room_reference(room)} files <query>`')]
    return '\n'.join(lines + read_actions(command, room_reference(room)))
