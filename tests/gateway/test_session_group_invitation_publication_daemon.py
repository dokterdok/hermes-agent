"""Frozen native issuance survives a publication fault and keeps its original lineage."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import time

from tests.gateway.fixtures.local_recovery_probe import daemon, rpc, websocket
from tests.gateway.test_api_room_admission_cancellation import _signed
from tests.gateway.test_api_room_authority_lineage import dispatch_body
from tests.gateway.test_session_group_peer_daemons import _gateway, _model


async def invite(home, descriptor, params):
    async with websocket(home, descriptor) as ws:
        return await rpc(ws, 'groups.peer.invite', **params)


def saved_receipt(home, request_id):
    with sqlite3.connect(home / 'state.db') as conn:
        return conn.execute('SELECT response_json,publication_pending FROM hosted_room_setup_invitations WHERE request_id=?',
                            (request_id,)).fetchone()


def wait_for(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(.02)
    raise AssertionError('native publication probe did not reach its boundary')


def test_frozen_successor_replay_repairs_publication_without_readmitting_old_work(tmp_path):
    root = Path(__file__).resolve().parents[2]
    model = _model('NATIVE_SUCCESSOR_REPLY')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    home, env = _gateway(tmp_path, 'target', model, root, api_port=port)
    url = f'http://127.0.0.1:{port}'
    fixture = 'native_invitation_publication_daemon.py'
    first = dict(room_id='lineage-room', home_install_id='original', authority_gateway_id='original',
                 authority_epoch=1, member_id='reviewer', request_id='native-original-issuance', requested_at=time.time())
    second = first | dict(home_install_id='successor', authority_gateway_id='successor', authority_epoch=2,
        request_id='native-successor-issuance', previous_authority={key: first[key] for key in (
            'home_install_id', 'authority_gateway_id', 'authority_epoch')})
    try:
        with daemon(root, home, env, barrier=True, fixture=fixture) as (_, descriptor), ThreadPoolExecutor(max_workers=1) as worker:
            reply = asyncio.run(invite(home, descriptor, first))
            original = reply['result']
            old_body = dispatch_body(original, 'original', 1, 'old', 'CANCEL_BEFORE_CANONICAL_WRITE')
            captured = worker.submit(_signed, url, original, '/v1/runs', old_body)
            wait_for(lambda: (home / 'pre-admission-entered').exists())
            (home / 'fail-native-publication').touch()
            failed = asyncio.run(invite(home, descriptor, second))
            assert 'error' in failed, failed
            assert (home / 'native-publication-failed').exists()
            encoded, pending = saved_receipt(home, second['request_id'])
            assert pending == 1
            frozen = json.loads(encoded)
            (home / 'pre-admission-release').touch()
            rejected = captured.result(timeout=30)
            assert rejected[0] in {403, 409}, rejected
            assert not model.requests
            with sqlite3.connect(home / 'state.db') as conn:
                assert conn.execute("SELECT COUNT(*) FROM session_admissions WHERE principal_id='api'").fetchone()[0] == 0
        # A fresh process recovers only the pending projection, with the original frozen grant.
        with daemon(root, home, env, barrier=True, fixture=fixture) as (_, descriptor):
            replay = asyncio.run(invite(home, descriptor, second))['result']
            assert replay['grant'] == frozen['grant'] and replay['catalog'] == frozen['catalog']
            assert saved_receipt(home, second['request_id']) == (encoded, 0)
            # A completed replay must not revisit publication at all.
            (home / 'fail-native-publication').touch()
            assert asyncio.run(invite(home, descriptor, second))['result'] == replay
            assert (home / 'fail-native-publication').exists()
            (home / 'fail-native-publication').unlink()
            assert _signed(url, original, '/v1/runs', old_body)[0] in {401, 403, 409}
            winner = _signed(url, replay, '/v1/runs', dispatch_body(replay, 'successor', 2, 'winner', 'NATIVE_SUCCESSOR_SEND'))
            assert winner[0] == 202, winner

            def completed():
                with sqlite3.connect(home / 'state.db') as conn:
                    return conn.execute("SELECT status FROM session_admissions WHERE principal_id='api'").fetchall() == [('terminal',)]

            wait_for(completed)
            assert len(model.requests) == 1
            original_id = 'room_' + hashlib.sha256(b'original\0lineage-room\0reviewer\0default').hexdigest()[:32]
            with sqlite3.connect(home / 'state.db') as conn:
                assert conn.execute("SELECT id FROM sessions WHERE source='bot_room'").fetchall() == [(original_id,)]
    finally:
        (home / 'pre-admission-release').touch()
        (home / 'observer-release').touch()
        model.shutdown()
        model.server_close()
