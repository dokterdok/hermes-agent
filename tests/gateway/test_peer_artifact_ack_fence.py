"""Fresh authorization at the Files ACK transaction, using ordinary DB changes."""
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_api_cutover_contract import api, owner  # noqa: F401
from tests.gateway.test_canonical_peer_artifacts import retained_output, accepted_source_grant


@pytest.mark.asyncio
@pytest.mark.parametrize('change,repeat', [('revoked', False), ('reservation', False),
                                          ('expired', False), ('revoked', True)])
async def test_ack_checks_current_grant_at_outbox_boundary(api, owner, tmp_path, monkeypatch, change, repeat):
    from gateway import hosted_rooms
    from gateway.hosted_room_peer import decode_room_grant
    from gateway.hosted_room_artifacts import RoomArtifactOutbox
    from gateway.platforms.api_server_room_artifacts import _http_routes
    dispatch, scope, outbox, item, manifest, _ = retained_output(api, owner, tmp_path)
    token = accepted_source_grant(api, dispatch)
    claims = decode_room_grant(api._room_grant_secret(), token, permission='artifact.ack')
    event_id = 'dmessage:' + scope.task_id.removeprefix('dtask:')
    if repeat:
        outbox.acknowledge(scope, [item['artifact_id']], message_event_id=event_id)
    method = 'retirement_complete' if repeat else 'acknowledge'
    original = getattr(RoomArtifactOutbox, method)
    changed, transactions = [], []

    def at_boundary(box, *args, **kwargs):
        if box.authorize_write is not None and not changed:
            changed.append(True)
            if change == 'revoked':
                # The local enforcing copy must be checked through the writer,
                # even when the shared preflight copy has not changed.
                hosted_rooms.revoke_room_grant_id(owner.db.db_path, claims=claims,
                                                  expires_at=claims['status_expires_at'])
            elif change == 'reservation':
                owner.db._execute_write(lambda conn: conn.execute(
                    'UPDATE hosted_room_peer_reservations SET expires_at=? WHERE room_id=?',
                    (time.time() - 1, scope.room_id)))
            else:
                monkeypatch.setattr(time, 'time', lambda: claims['expires_at'] + 1)
            guard = box.authorize_write
            def observed(conn, checked_scope):
                transactions.append(conn.in_transaction)
                return guard(conn, checked_scope)
            box.authorize_write = observed
        return original(box, *args, **kwargs)

    monkeypatch.setattr(RoomArtifactOutbox, method, at_boundary)
    app = web.Application()
    for verb, path, handler in _http_routes(api):
        app.router.add_route(verb, path, handler)
    async with TestClient(TestServer(app)) as http:
        response = await http.post('/v1/runs/file-run/artifacts/ack',
            headers={'Authorization': 'HermesRoom ' + token}, json=dict(
                artifact_ids=[item['artifact_id']], manifest_digest=manifest['manifest_digest'],
                message_event_id=event_id))
        assert response.status == 409, await response.text()
    assert changed and transactions and all(transactions)
    if not repeat:
        assert not outbox.retirement_complete(scope)
        assert outbox.read(scope, item['artifact_id'])[1]
