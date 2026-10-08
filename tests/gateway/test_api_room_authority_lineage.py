"""A target-authorized home move keeps one canonical transcript and fences its old writer."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import socket
import sqlite3
import time

from tests.gateway.fixtures.local_recovery_probe import daemon
from tests.gateway.test_api_room_admission_cancellation import _signed
from tests.gateway.test_session_group_peer_composition import _http
from tests.gateway.test_session_group_peer_daemons import _model, _gateway


def dispatch_body(invitation, home, epoch, task, prompt):
    catalog = invitation['catalog']
    return {'input': prompt, 'hosted_room_dispatch': {
        'protocol_version': 2, 'room_id': 'lineage-room', 'home_install_id': home,
        'authority_gateway_id': home, 'authority_epoch': epoch, 'member_id': 'reviewer',
        'target_install_id': catalog['installation_id'], 'target_profile': 'default',
        'task_id': task, 'execution_generation': 1, 'source_event_seq': 1,
        'cancellation_scope_id': 'cancel-task', 'trace_id': 'trace', 'prompt': prompt,
        'prompt_digest': hashlib.sha256(prompt.encode()).hexdigest(),
        'capability_digest': catalog['catalog_digest'],
        'execution_policy_digest': catalog['execution_policy']['policy_digest']}}


def test_changed_home_refuses_paused_canonical_writer_and_accepts_successor_after_restart(tmp_path):
    root = Path(__file__).resolve().parents[2]
    model = _model('SUCCESSOR_REPLY')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    home, env = _gateway(tmp_path, 'target', model, root, api_port=port)
    url = f'http://127.0.0.1:{port}'
    fixture = 'peer_cancellation_daemon.py'
    try:
        with daemon(root, home, env, barrier=True, fixture=fixture), ThreadPoolExecutor(max_workers=1) as worker:
            invite = {'room_id': 'lineage-room', 'home_install_id': 'original',
                      'authority_gateway_id': 'original', 'authority_epoch': 1, 'member_id': 'reviewer'}
            original = _http(url, '/v1/room-members/invitations', body=invite)
            old_body = dispatch_body(original, 'original', 1, 'old', 'CANCEL_BEFORE_CANONICAL_WRITE')
            pending = worker.submit(_signed, url, original, '/v1/runs', old_body)
            deadline = time.monotonic() + 30
            while not (home / 'pre-admission-entered').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert (home / 'pre-admission-entered').exists()
            successor = _http(url, '/v1/room-members/invitations', body={**invite,
                'home_install_id': 'successor', 'authority_gateway_id': 'successor', 'authority_epoch': 2,
                'previous_authority': {key: invite[key] for key in (
                    'home_install_id', 'authority_gateway_id', 'authority_epoch')}})
            (home / 'pre-admission-release').touch()
            refused = pending.result(timeout=30)
            assert refused[0] in {403, 409}, refused
            assert not model.requests
            with sqlite3.connect(home / 'state.db') as db:
                assert db.execute("SELECT COUNT(*) FROM session_admissions WHERE principal_id='api'").fetchone()[0] == 0
        with daemon(root, home, env, barrier=True, fixture=fixture):
            assert _signed(url, original, '/v1/runs', old_body)[0] in {401, 403, 409}
            winner = dispatch_body(successor, 'successor', 2, 'winner', 'SUCCESSOR_NEW_SEND')
            accepted = _signed(url, successor, '/v1/runs', winner)
            assert accepted[0] == 202, accepted
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with sqlite3.connect(home / 'state.db') as db:
                    states = db.execute("SELECT status FROM session_admissions WHERE principal_id='api'").fetchall()
                if states == [('terminal',)]:
                    break
                time.sleep(.05)
            assert states == [('terminal',)] and len(model.requests) == 1
            original_id = 'room_' + hashlib.sha256(b'original\0lineage-room\0reviewer\0default').hexdigest()[:32]
            with sqlite3.connect(home / 'state.db') as db:
                assert db.execute("SELECT id FROM sessions WHERE source='bot_room'").fetchall() == [(original_id,)]
    finally:
        (home / 'pre-admission-release').touch()
        (home / 'observer-release').touch()
        model.shutdown()
        model.server_close()
