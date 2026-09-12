"""Reciprocal approval projection and exact, durably admitted owner decisions."""
import asyncio

from gateway.platforms.api_server_room_controls import _authorize, _response
from gateway.session_group_decisions import decide, pending_decisions


def http_routes(adapter):
    async def pending(request):
        try:
            authority, actor, room_id, member_id, token = _authorize(adapter, request)
            if request.query or request.can_read_body:
                raise ValueError('Unexpected fields')
            result = await asyncio.to_thread(pending_decisions, authority.hosted_room_service, room_id)
            if _authorize(adapter, request) != (authority, actor, room_id, member_id, token):
                raise PermissionError('Authorization changed')
            return _response({'room_id': room_id, 'approvals': result})
        except Exception:
            return _response({'error': {'code': 'invalid_room_control', 'message': 'These requests are no longer available.'}}, status=409)

    async def respond(request):
        from gateway.platforms.api_server import _reserve_pending_api_work
        try:
            _authorize(adapter, request)
            if request.query:
                raise ValueError('Unexpected query')
            with _reserve_pending_api_work(adapter):
                body, error = await adapter._read_json_body(request)
                if error is not None:
                    return error
                if not isinstance(body, dict) or set(body) != {'command_id', 'decision'} or not isinstance(body['decision'], dict):
                    raise ValueError('Invalid decision')
                # Decisions unblock existing work; the exact room/approval
                # fence, not the new-message maintenance gate, owns admission.
                authority, _actor, room_id, member_id, token = _authorize(adapter, request)
                room = authority.hosted_room_service._room(room_id)
                result = await asyncio.to_thread(decide, authority, room=room,
                    command_id=body['command_id'], params=body['decision'], member_id=member_id, token=token)
                return _response({'room_id': room_id, 'decision': body['decision'], 'result': result})
        except Exception:
            return _response({'error': {'code': 'room_control_unavailable',
                'message': 'This decision could not be confirmed. Check the request before trying again.'}}, status=409)

    return [('GET', '/v1/room-controls/{room_id}/approvals', pending),
            ('POST', '/v1/room-controls/{room_id}/approvals', respond)]
