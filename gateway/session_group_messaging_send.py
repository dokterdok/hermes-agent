"""Message acceptance under current per-room consent, never a second executor."""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from typing import Callable

from gateway import hosted_room_controls as controls, hosted_rooms
from gateway.session_group_delegation import _delegate_actor, _owner, _prefix, _record, _service
from gateway.session_group_home_access import home_access_granted_locked
from hermes_state_runtime import RuntimeStoreError, _epoch


@dataclass(frozen=True)
class _SendPermission:
    authority: object = field(repr=False)
    room_id: str
    owner: str
    gateway_id: str
    room_epoch: int
    runtime_epoch: int
    guard: Callable | None = field(default=None, repr=False)
    member_id: str | None = None
    token: str | None = field(default=None, repr=False)

    def check(self, conn):
        _epoch(conn, self.runtime_epoch)
        _owner(self.authority, conn, self.room_id, self.owner)
        if not controls._active_room_scope(conn, room_id=self.room_id,
                authority_gateway_id=self.gateway_id, authority_epoch=self.room_epoch,
                member_id=self.member_id):
            raise RuntimeStoreError('permission_denied')
        if self.member_id is None:
            if not callable(self.guard):
                raise RuntimeStoreError('permission_denied')
            self.guard()
            if not home_access_granted_locked(self.authority, conn, self.room_id):
                raise RuntimeStoreError('permission_denied')
        else:
            record = _record(conn, self.room_id, self.member_id, self.gateway_id, self.room_epoch)
            prefix = _prefix(self.authority, self.owner)
            request = str(record['request_id']) if record else ''
            if (not hmac.compare_digest(request[:len(prefix)], prefix)
                    or not controls.verify_home_control_token(self.authority.db.db_path,
                        room_id=self.room_id, member_id=self.member_id,
                        authority_gateway_id=self.gateway_id, authority_epoch=self.room_epoch,
                        control_token=self.token, _conn=conn)):
                raise RuntimeStoreError('permission_denied')
        return True


def _capture(authority, room, *, guard=None, member_id=None, token=None):
    service = _service(authority)
    room_id = controls._identifier(room.get('room_id'), label='room_id')
    if member_id is not None:
        actor = _delegate_actor(authority, room_id=room_id, member_id=member_id,
                                token=token, capability='session:submit')
        owner = actor.subject
    else:
        if not callable(guard):
            raise RuntimeStoreError('permission_denied')
        guard()
        owner = service._owner(room_id)
    gateway, epoch = service._owned_authority(room_id)
    if (room.get('authority_gateway_id'), room.get('authority_epoch')) != (gateway, epoch):
        raise RuntimeStoreError('stale_generation')
    proof = _SendPermission(authority, room_id, owner, gateway, epoch, authority.epoch, guard, member_id, token)
    with authority.db._read_ctx() as conn:
        proof.check(conn)
    return proof


def _send(proof, command_id, text, actor):
    command_id = controls._identifier(command_id, label='command_id')
    if not isinstance(text, str) or not text.strip() or len(text) > 64 * 1024:
        raise RuntimeStoreError('invalid_params')
    if not isinstance(actor, dict) or actor.get('kind') != 'user':
        raise RuntimeStoreError('invalid_params')
    material = json.dumps([proof.member_id or 'home', command_id], separators=(',', ':')).encode()
    event_id = hosted_rooms.user_event_id('control:' + hashlib.sha256(material).hexdigest())
    return _service(proof.authority).send(room_id=proof.room_id, event_id=event_id,
        payload={'text': text, 'thread_id': event_id}, actor=actor, authorize_write=proof.check)


def send_from_home(authority, *, room, guard, command_id, text, actor):
    """Guard must recheck the receiving Home, admin and audience at acceptance."""
    return _send(_capture(authority, room, guard=guard), command_id, text, actor)


def send_from_peer(authority, *, room, member_id, token, command_id, text, actor_display_name):
    proof = _capture(authority, room, member_id=member_id, token=token)
    return _send(proof, command_id, text, {'kind': 'user', 'id': 'peer:' + member_id,
        'display_name': ' '.join(str(actor_display_name or 'Messaging').split())[:128]})
