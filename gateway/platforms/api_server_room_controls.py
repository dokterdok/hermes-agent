"""Room-scoped reciprocal reads on the receiving canonical profile.

Ports the #98073 return-control wire contract without its legacy global server.
Mutations are not exposed until delegation reaches their final acceptance fence.
"""
from collections.abc import Mapping

from aiohttp import web

from gateway import hosted_room_controls as controls
from gateway.session_group_delegation import (
    _delegate_actor, _schema, _service, dispatch_delegated_group_control,
)
from hermes_state_runtime import RuntimeStoreError, _epoch


MAX_CONTROL_TEXT_CHARS = 64 * 1024
MAX_CONTROL_EVENTS = 5


def _control_token(request):
    scheme, separator, token = str(request.headers.get('Authorization') or '').partition(' ')
    if not separator or scheme.casefold() != 'hermesroomcontrol' or not token.strip():
        raise RuntimeStoreError('permission_denied')
    return token.strip()


def _coordinates(request):
    return (controls._identifier(request.match_info.get('room_id'), label='room_id'),
            controls._identifier(request.headers.get('X-Hermes-Room-Member'), label='member_id'),
            _control_token(request))


def _authority(adapter):
    from gateway.session_authorities import active_authority, served_profile_name
    from gateway.platforms.api_server_room_grants import _effective_room_profile
    from gateway.platforms.api_server import _api_request_profile
    authority = active_authority(getattr(adapter, 'gateway_runner', None))
    if (authority is None or served_profile_name(authority.profile_id)
            != _effective_room_profile(_api_request_profile)):
        raise RuntimeStoreError('profile_mismatch')
    _service(authority)
    return authority


def _authorize(adapter, request):
    authority = _authority(adapter)
    room_id, member_id, token = _coordinates(request)
    actor = _delegate_actor(authority, room_id=room_id, member_id=member_id,
                            token=token, capability='session:read')
    return authority, actor, room_id, member_id, token


def _visible_events(delta):
    visible = []
    for event in delta.get('events', []):
        if not isinstance(event, Mapping) or event.get('kind') not in {'message.user', 'message.member'}:
            continue
        actor = event.get('actor') if isinstance(event.get('actor'), Mapping) else {}
        payload = event.get('payload') if isinstance(event.get('payload'), Mapping) else {}
        visible.append({'kind': event['kind'], 'created_at': event.get('created_at'),
            'actor': {key: str(actor.get(key) or '')[:128 if key == 'display_name' else 256]
                      for key in ('id', 'display_name')},
            'payload': {'member_id': str(payload.get('member_id') or '')[:256],
                        'text': str(payload.get('text') or '')[:MAX_CONTROL_TEXT_CHARS]}})
    return visible[-MAX_CONTROL_EVENTS:]


async def _summary(adapter, request):
    authority, actor, room_id, member_id, token = _authorize(adapter, request)
    async def read(method, **params):
        return await dispatch_delegated_group_control(authority, room_id=room_id,
            member_id=member_id, token=token, method=method, params={'room_id': room_id, **params})
    state = await read('groups.state')
    room = state['room']
    delta = await read('groups.log', since_seq=max(0, int(room.get('latest_seq') or 0) - 80), limit=80)
    raw_status = state.get('driver_status') or {}
    counts = raw_status.get('counts') or {}
    # Do not release a summary assembled across token revocation or owner change.
    fresh_actor = _delegate_actor(authority, room_id=room_id, member_id=member_id,
                                  token=token, capability='session:read')
    if fresh_actor != actor:
        raise RuntimeStoreError('permission_denied')
    return {
        'room': {key: room[key] for key in ('room_id', 'name', 'authority_gateway_id', 'authority_epoch', 'latest_seq')}
                | {'members': [{key: str(member.get(key) or '')[:256]
                                for key in ('member_id', 'handle', 'display_name')}
                               for member in room['members']]},
        'status': {'working': raw_status.get('working') is True,
                   'blocked': raw_status.get('blocked') is True,
                   'counts': {key: int(counts.get(key) or 0) for key in (
                       'queued', 'running', 'stopping', 'deferred', 'indeterminate',
                       'settled', 'failed', 'cancelled') if int(counts.get(key) or 0) > 0}},
        'events': _visible_events(delta),
        'control_actions': [],
    }


def _revoke(adapter, request):
    # A bearer can revoke only itself, including after room retirement and on
    # response-lost retries. It does not need a currently readable room.
    authority = _authority(adapter)
    room_id, member_id, token = _coordinates(request)
    def write(conn):
        _epoch(conn, authority.epoch)
        _schema(conn)
        return controls.revoke_home_control_token_value(authority.db.db_path,
            room_id=room_id, member_id=member_id, control_token=token, _conn=conn)
    return {'revoked': authority.db._execute_write(write)}


def _response(result, *, status=200):
    return web.json_response(result, status=status, headers={'Cache-Control': 'no-store'})


def _http_routes(adapter):
    async def handle(request):
        try:
            if request.query or request.can_read_body:
                return _response({'error': {'code': 'invalid_room_control',
                                            'message': 'This request accepts no extra fields.'}}, status=400)
            if request.method == 'DELETE':
                import asyncio
                result = await asyncio.to_thread(_revoke, adapter, request)
            else:
                result = await _summary(adapter, request)
            return _response(result)
        except (RuntimeStoreError, controls.HostedRoomControlError):
            return _response({'error': {'code': 'invalid_room_control',
                'message': 'Group Chat access is unavailable or expired.'}}, status=401)
        except Exception:
            return _response({'error': {'code': 'room_control_unavailable',
                'message': 'Group Chat status could not be loaded.'}}, status=409)
    return [('GET', '/v1/room-controls/{room_id}', handle),
            ('DELETE', '/v1/room-controls/{room_id}', handle)]
