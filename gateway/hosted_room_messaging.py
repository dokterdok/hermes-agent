"""Canonical read backend for #98073; no global Desktop service or classic mailbox."""
from pathlib import Path

from gateway import hosted_room_controls as controls, hosted_rooms
from gateway.group_home_consent import DisclosureChanged
from gateway.hosted_room_control_client import RoomControlHTTPClient, RoomControlClientError
from gateway.session_authorities import owner_scope, served_profile_name
from gateway.session_group_delegation import _service
from gateway.session_group_home_access import home_access_granted
from hermes_state_runtime import _epoch

MAX_MESSAGING_ROOMS = 4096


class MessagingRoomBackend:
    def __init__(self, *, authority=None, db_path=None, service=None, guard=None):
        self.authority = authority or getattr(service, 'authority', None)
        if self.authority is None:
            raise DisclosureChanged('Canonical room authority is unavailable')
        self.service = _service(self.authority)
        self.db_path = self.authority.db.db_path
        if db_path is not None and Path(db_path).resolve() != Path(self.db_path).resolve():
            raise DisclosureChanged('Canonical room profile changed')
        self.profile = served_profile_name(Path(self.authority.profile_id))
        self.guard = guard

    def _current(self):
        if not callable(self.guard):
            raise DisclosureChanged('A Home disclosure guard is required')
        self.guard()
        with self.authority.db._read_ctx() as conn:
            _epoch(conn, self.authority.epoch)

    def check(self, room):
        self._current()
        with owner_scope(self.authority):
            if room.get('_room_mode') == 'remote':
                return _remote_control_link(self, room)
            if not home_access_granted(self.authority, room['room_id']):
                raise DisclosureChanged('Home access is no longer granted')
            current = hosted_rooms.room_state(self.db_path, room_id=room['room_id'])
            if (current['authority_gateway_id'], current['authority_epoch']) != (
                    room['authority_gateway_id'], room['authority_epoch']):
                raise DisclosureChanged('Room authority changed')
            return current

    def _refs(self, rooms):
        """Retain the source's durable, nonreused presentation numbers in this owner DB."""
        def write(conn):
            self.guard()
            _epoch(conn, self.authority.epoch)
            conn.execute('CREATE TABLE IF NOT EXISTS hosted_room_messaging_refs '
                         '(room_ref INTEGER PRIMARY KEY AUTOINCREMENT, room_id TEXT NOT NULL UNIQUE)')
            for room in sorted(rooms, key=lambda row: (row.get('created_at', 0), row['room_id'])):
                conn.execute('INSERT OR IGNORE INTO hosted_room_messaging_refs(room_id) VALUES(?)', (room['room_id'],))
            return {row['room_id']: row['room_ref'] for row in conn.execute('SELECT * FROM hosted_room_messaging_refs')}
        return self.authority.db._execute_write(write)

    def list_rooms(self):
        self._current()
        with owner_scope(self.authority):
            visible, local_ids = [], set()
            offset = 0
            while offset < MAX_MESSAGING_ROOMS:
                self._current()
                page = hosted_rooms.list_rooms(self.db_path, offset=offset, limit=hosted_rooms.MAX_ROOM_LIST_LIMIT)
                for room in page:
                    local_ids.add(room['room_id'])
                    if home_access_granted(self.authority, room['room_id']):
                        visible.append({**room, '_room_mode': 'hosted'})
                if len(page) < hosted_rooms.MAX_ROOM_LIST_LIMIT:
                    break
                offset += len(page)
            else:
                raise ValueError('There are too many Group Chats to list safely.')
            for link in controls.load_peer_control_links(self.db_path).links:
                self._current()
                if link.room_id in local_ids:
                    continue
                if not _reservation(self, link):
                    continue
                room = {**link.as_status(), 'name': link.room_name, '_remote_member_id': link.member_id,
                        '_room_mode': 'remote'}
                try:
                    summary = self.summary(room)
                except RoomControlClientError:
                    # A missing facade or revoked grant must not appear as a working view.
                    continue
                visible.append({**room, **summary['room'], '_room_mode': 'remote'})
                local_ids.add(link.room_id)
            refs = self._refs(visible) if visible else {}
            for room in visible:
                self.check(room)
            self._current()
            return [{**room, 'messaging_ref': refs[room['room_id']]} for room in visible]

    def summary(self, room):
        with owner_scope(self.authority):
            selected = self.check(room)
            if room.get('_room_mode') == 'remote':
                link = selected
                result = RoomControlHTTPClient(link).summary()
                actual = result.get('room')
                if (not isinstance(actual, dict) or actual.get('room_id') != link.room_id
                        or actual.get('authority_gateway_id') != link.authority_gateway_id
                        or type(actual.get('authority_epoch')) is not int or actual['authority_epoch'] != link.authority_epoch
                        or not isinstance(actual.get('members'), list) or len(actual['members']) > 64
                        or not isinstance(result.get('status'), dict) or not isinstance(result.get('events'), list)):
                    raise RoomControlClientError('This Group Chat returned mismatched status data.')
                if self.check(room) != link:
                    raise DisclosureChanged('Peer grant changed during the read')
                return result
            latest = int(selected.get('latest_seq') or 0)
            events = hosted_rooms.read_events(self.db_path, room_id=room['room_id'], since_seq=max(0, latest - 80), limit=80)['events']
            status = self.service.status(room['room_id'])
            self.check(room)
            return {'room': selected, 'status': status, 'events': events, 'control_actions': ['send']}

    def list_files(self, *, room, **options):
        from gateway.hosted_room_file_access import list_room_files
        with owner_scope(self.authority):
            self.check(room)
            result = list_room_files(self, room=room, profile=self.profile, **options)
            self.check(room)
            return result

    def read_file(self, *, room, **selection):
        from gateway.hosted_room_file_access import read_room_file
        with owner_scope(self.authority):
            self.check(room)
            result = read_room_file(self, room=room, profile=self.profile, **selection)
            self.check(room)
            return result

    def resolve_file(self, *, room, code):
        from gateway.hosted_room_file_lookup import resolve_file
        with owner_scope(self.authority):
            self.check(room)
            result = resolve_file(self, room=room, code=code, profile=self.profile)
            self.check(room)
            return result

    def latest_reply(self, *, room):
        from gateway.hosted_room_file_lookup import latest_reply
        with owner_scope(self.authority):
            self.check(room)
            result = latest_reply(self, room=room, profile=self.profile)
            self.check(room)
            return result

    def read_reply(self, *, room, event_id):
        from gateway.hosted_room_shared_message_access import read_shared_message
        with owner_scope(self.authority):
            self.check(room)
            result = read_shared_message(self, room=room, event_id=event_id, profile=self.profile)
            self.check(room)
            return result

    def send(self, *, room, command_id, text, actor, write_guard):
        with owner_scope(self.authority):
            self.check(room)
            write_guard()
            if room.get('_room_mode') == 'remote':
                link = _remote_control_link(self, room)
                if self.check(room) != link:
                    raise DisclosureChanged('Peer grant changed before sending')
                write_guard()
                result = RoomControlHTTPClient(link).mutate(action='send', command_id=command_id,
                    text=text, actor_display_name=actor['display_name'])
                event = result.get('event')
                from gateway.session_group_messaging_send import control_message_event_id
                if (result.get('accepted') is not True or result.get('action') != 'send'
                        or not isinstance(event, dict) or event.get('room_id') != room['room_id']
                        or event.get('event_id') != control_message_event_id(link.member_id, command_id)
                        or event.get('authority_epoch') != room['authority_epoch']
                        or event.get('kind') != 'message.user' or event.get('payload', {}).get('text') != text):
                    raise RoomControlClientError('Message acceptance could not be confirmed.')
                self.check(room)
                return event
            from gateway.session_group_messaging_send import send_from_home
            def guard():
                self.guard()
                write_guard()
            return send_from_home(self.authority, room=room, guard=guard,
                                  command_id=command_id, text=text, actor=actor)


def _reservation(backend, link):
    return controls.peer_reservation_matches(backend.db_path, room_id=link.room_id, member_id=link.member_id,
        target_profile=backend.profile, authority_gateway_id=link.authority_gateway_id, authority_epoch=link.authority_epoch)


def _remote_control_link(backend, room):
    backend._current()
    selected = [link for link in controls.load_peer_control_links(backend.db_path).links
                if link.room_id == room['room_id'] and link.member_id == room.get('_remote_member_id')
                and link.authority_gateway_id == room['authority_gateway_id']
                and link.authority_epoch == room['authority_epoch'] and _reservation(backend, link)]
    if len(selected) != 1:
        raise DisclosureChanged('This Group Chat is no longer available here.')
    return selected[0]


def current_room_backend(runner, event, stamp):
    from gateway.group_chat_policy import receiving_group_context
    from gateway.group_home_consent import require_current
    require_current(runner, event, stamp)
    context = receiving_group_context(runner, event.source)
    return MessagingRoomBackend(authority=context.authority, guard=lambda: require_current(runner, event, stamp))
