"""Post-settlement caller of Output secondary retained publication.

The invitation→NEW hosted-room path calls this after a task has settled.
History and info do not. Primary ``publish_terminal`` does not. ``prepare_room``
does not. Send-consent is not passed and is not publication authority. A
missing contract fails closed before any secondary write.

When the task is otherwise eligible and primary terminal events are not in the
log yet, the call returns ``SecondaryAwaitingPrimary`` and writes nothing. The
runtime retries that task only after primary evidence appears, from outside
``publish_terminal`` and ``prepare_room``.
"""
from collections.abc import Mapping

from gateway.hosted_room_artifacts import RoomArtifactError


class SecondaryAwaitingPrimary:
    """Settled invitation notify ran before primary terminal events existed.

    Not a publication. Not consent. The runtime keeps this task for catch-up.
    """

    __slots__ = ()


def call_settled_invitation_secondary(service, binding, task):
    """Publish one settled invitation→NEW task through the secondary consumer.

    Returns None when this task is not that publication. Returns
    ``SecondaryAwaitingPrimary`` when it would publish but the primary
    ``dmessage``/``dterminal`` digest is still empty. Does not insert rows
    itself and does not call primary publication.
    """
    if not isinstance(task, Mapping) or task.get('status') != 'settled':
        return None
    result = task.get('result')
    payload = task.get('payload') if isinstance(task.get('payload'), Mapping) else {}
    if (not isinstance(result, Mapping) or not result.get('artifacts')
            or not payload.get('recipient_member_ids')):
        return None
    key = service._output_key(task)
    with service.authority.db._read_ctx() as conn:
        if not service._output_events_digest(conn, key):
            return SecondaryAwaitingPrimary()
    rpc = service._resolve_member_transport(binding, task)
    from gateway.session_hosted_rpc import HostedRoomAuthorityRPC
    if type(rpc) is not HostedRoomAuthorityRPC:
        return None
    caller = getattr(rpc, 'publish_secondary_retained', None)
    if not callable(caller):
        raise RoomArtifactError('Group Chat secondary publication is not registered')
    return caller(task)
