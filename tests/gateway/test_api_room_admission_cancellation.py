"""Canonical accepting-writer cancellation through real signed HTTP and daemon stores."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import time
import urllib.error
import urllib.request

import pytest

from gateway.hosted_room_proof import request_proof, verify_response, RESPONSE_HEADER, RESPONSE_NONCE_HEADER
from tests.gateway.fixtures.local_recovery_probe import daemon
from tests.gateway.test_session_group_peer_composition import _http
from tests.gateway.test_session_group_peer_daemons import _model, _gateway


def _signed(url, invitation, path, body):
    key = f"room:{body['hosted_room_dispatch']['task_id']}:1"
    headers = {'Content-Type': 'application/json', 'Idempotency-Key': key}
    authorization, proof_key, proof_mac, payload = request_proof(invitation['grant'],
        installation_id=invitation['catalog']['installation_id'], method='POST', path=path,
        body=json.dumps(body, separators=(',', ':')).encode(), headers=headers)
    request = urllib.request.Request(url + path, data=payload,
                                    headers={**headers, 'Authorization': authorization})
    try:
        response = urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if RESPONSE_HEADER not in response.headers:
            assert response.status in {404, 405}, response.status
            return response.status, {'unsupported': response.read().decode()}
        raw = verify_response(proof_key, proof_mac, response.status, response.read(),
            response.headers[RESPONSE_HEADER], response.headers[RESPONSE_NONCE_HEADER])
        return response.status, json.loads(raw)


@pytest.mark.parametrize('phase', ['absent', 'before_canonical_write', 'before_canonical_reauthorization',
                                  'before_observation', 'output_absent', 'output_before_observation'])
def test_key_stop_prevents_canonical_admission_and_execution_across_restart(tmp_path, phase):
    root = Path(__file__).resolve().parents[2]
    model = _model('MUST_NOT_EXECUTE')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    home, env = _gateway(tmp_path, 'target', model, root, api_port=port)
    url = f'http://127.0.0.1:{port}'
    fixture = 'peer_cancellation_daemon.py'

    def no_canonical_admission():
        with sqlite3.connect(home / 'state.db') as db:
            rows = db.execute("SELECT status,outcome FROM session_admissions WHERE principal_id='api'").fetchall()
        assert rows == ([('terminal', 'cancelled')] if phase.endswith('before_observation') else [])
        assert not model.requests

    def output_cancel_receipt():
        from gateway.hosted_room_peer import HostedMemberDispatch
        from gateway.hosted_room_peer_output import dispatch_digest
        from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient
        from tui_gateway.hosted_room_peer_output import _save_consent, stored_consent
        from hermes_state import SessionDB
        caller = tmp_path / 'caller-state.db'
        if phase == 'output_absent':
            with SessionDB(caller):
                pass
            if stored_consent(caller, dispatch) is None:
                target = {key: dispatch[key] for key in (
                    'target_install_id', 'target_profile', 'home_install_id', 'capability_digest',
                    'execution_policy_digest', 'cancellation_scope_id', 'trace_id')}
                _save_consent(caller, dispatch, None, {'target': target, 'contract': dispatch['document_output'],
                    'dispatched': True, 'dispatch': dispatch, 'provenance': 'capabilities-v1'})
        client = PeerRunsHTTPClient(base_url=url, api_key='', proof_install_id=catalog['installation_id'],
                                    receipt_db_path=caller)
        proof = client.cancel_dispatch(dispatch=dispatch, grant=invitation['grant'])
        expected = dispatch_digest(HostedMemberDispatch.from_mapping(dispatch))
        if phase == 'output_absent':
            assert proof['canonical_admission_absent'] == {'dispatch_digest': expected}
        else:
            assert proof.get('canonical_admission_absent') is None
        for _ in range(2):
            terminal = client.stop_receipt(task_id='task-cancel', execution_generation=1,
                                           grant=invitation['grant'])
            assert terminal['status'] == 'cancelled'
            if phase == 'output_before_observation':
                assert terminal['peer_output_dispatch_digest'] == expected and terminal['admission_id']
            no_canonical_admission()

    try:
        with daemon(root, home, env, barrier=True, fixture=fixture), ThreadPoolExecutor(max_workers=1) as worker:
            invitation_body = {
                'room_id': 'cancel-room', 'home_install_id': 'install:unreceived-home',
                'authority_gateway_id': 'install:unreceived-home', 'authority_epoch': 1, 'member_id': 'reviewer'}
            invitation = _http(url, '/v1/room-members/invitations', body=invitation_body)
            catalog = invitation['catalog']
            prompts = {'absent': 'CANCEL_ABSENT', 'output_absent': 'CANCEL_OUTPUT_ABSENT',
                       'before_observation': 'CANCEL_QUEUED_OBSERVER',
                       'output_before_observation': 'CANCEL_QUEUED_OBSERVER'}
            prompt = prompts.get(phase, 'CANCEL_BEFORE_CANONICAL_WRITE')
            dispatch = {
                'protocol_version': 2, 'room_id': 'cancel-room', 'home_install_id': 'install:unreceived-home',
                'authority_gateway_id': 'install:unreceived-home', 'authority_epoch': 1, 'member_id': 'reviewer',
                'target_install_id': catalog['installation_id'], 'target_profile': 'default',
                'task_id': 'task-cancel', 'execution_generation': 1, 'source_event_seq': 1,
                'cancellation_scope_id': 'cancel-task', 'trace_id': 'cancel-trace', 'prompt': prompt,
                'prompt_digest': hashlib.sha256(prompt.encode()).hexdigest(),
                'capability_digest': catalog['catalog_digest'],
                'execution_policy_digest': catalog['execution_policy']['policy_digest']}
            if phase.startswith('output_'):
                from gateway.hosted_room_peer_output import OUTPUT_CAPABILITY
                dispatch['document_output'] = dict(OUTPUT_CAPABILITY)
            body = {'input': prompt, 'hosted_room_dispatch': dispatch}
            original = None
            if phase.startswith('before_canonical'):
                original = worker.submit(_signed, url, invitation, '/v1/runs', body)
                deadline = time.monotonic() + 20
                while not (home / 'pre-admission-entered').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert (home / 'pre-admission-entered').exists()
                no_canonical_admission()
                with sqlite3.connect(home / 'runs_idempotency.db') as db:
                    assert db.execute('SELECT COUNT(*) FROM run_idempotency').fetchone()[0] == 1
            elif phase.endswith('before_observation'):
                original = worker.submit(_signed, url, invitation, '/v1/runs', body)
                assert original.result(timeout=20)[0] == 202
                deadline = time.monotonic() + 20
                while not (home / 'observer-entered').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert (home / 'observer-entered').exists()
            stopped = _signed(url, invitation, '/v1/runs/stop', body)
            if phase.startswith('output_'):
                output_cancel_receipt()
            if phase == 'before_canonical_reauthorization':
                assert _http(url, '/v1/room-members/grants/revoke-exact', body={},
                    authorization='HermesRoom ' + invitation['grant'])['revoked'] is True
            (home / 'pre-admission-release').touch()
            (home / 'observer-release').touch()
            if phase.endswith('before_observation'):
                deadline = time.monotonic() + 10
                while not (home / 'observer-finished').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert (home / 'observer-finished').exists(), 'cancelled queued observer never settled'
                late = _signed(url, invitation, '/v1/runs', body)
            else:
                late = original.result(timeout=20) if original is not None else _signed(url, invitation, '/v1/runs', body)
            # Primary oracle precedes endpoint-shape assertions: old heads execute the turn.
            no_canonical_admission()
            with sqlite3.connect(home / 'runs_idempotency.db') as db:
                records = db.execute('SELECT status_json FROM run_idempotency WHERE idempotency_key=?',
                                     ('room:task-cancel:1',)).fetchall()
            assert len(records) == 1, records
            assert json.loads(records[0][0])['status'] == 'cancelled'
            assert stopped[0] == 200 and stopped[1]['status'] in {'stopping', 'cancelled'}, stopped
            if phase == 'before_canonical_reauthorization':
                assert late[0] in {202, 403}, late
                invitation = _http(url, '/v1/room-members/invitations', body=invitation_body)
            else:
                assert late[0] == 202 and late[1]['status'] == 'cancelled', late
        if phase == 'output_absent':
            with sqlite3.connect(home / 'runs_idempotency.db') as db:
                [stored] = db.execute('SELECT status_json FROM run_idempotency WHERE idempotency_key=?',
                                      ('room:task-cancel:1',)).fetchall()
            from tui_gateway.hosted_room_peer_output import _save_consent, stored_consent
            caller = tmp_path / 'caller-state.db'
            for corruption in ({'peer_output_dispatch_digest': '0' * 64}, {'run_id': 'run_wrong'}):
                with sqlite3.connect(home / 'runs_idempotency.db') as db:
                    wrong = {**json.loads(stored[0]), **corruption}
                    db.execute('UPDATE run_idempotency SET status_json=? WHERE idempotency_key=?',
                               (json.dumps(wrong), 'room:task-cancel:1'))
                saved = stored_consent(caller, dispatch)
                without_proof = {key: value for key, value in saved.items() if key != 'canonical_admission_absent'}
                _save_consent(caller, dispatch, saved, without_proof)
                try:
                    with daemon(root, home, env, barrier=True, fixture=fixture):
                        from tui_gateway.hosted_room_peer_http import PeerRunsHTTPClient, PeerRunsHTTPError
                        fresh = PeerRunsHTTPClient(base_url=url, api_key='', proof_install_id=catalog['installation_id'],
                                                   receipt_db_path=caller)
                        retained = fresh.recover_accepted_dispatch(dispatch=dispatch, grant=invitation['grant'])
                        assert retained is not None
                        with pytest.raises(PeerRunsHTTPError) as invalid:
                            fresh.status(room_id=dispatch['room_id'], profile='default', session_id=retained['session_id'],
                                task_id='task-cancel', execution_generation=1, grant=invitation['grant'])
                        assert invalid.value.ambiguous and not invalid.value.not_admitted
                        assert stored_consent(caller, dispatch).get('canonical_admission_absent') is None
                        no_canonical_admission()
                finally:
                    with sqlite3.connect(home / 'runs_idempotency.db') as db:
                        db.execute('UPDATE run_idempotency SET status_json=? WHERE idempotency_key=?',
                                   (stored[0], 'room:task-cancel:1'))
        with daemon(root, home, env, barrier=True, fixture=fixture):
            replay = _signed(url, invitation, '/v1/runs', body)
            if phase.startswith('output_'):
                output_cancel_receipt()
            no_canonical_admission()
            assert replay[0] == 202 and replay[1]['status'] == 'cancelled', replay
            with sqlite3.connect(home / 'runs_idempotency.db') as db:
                rows = db.execute('SELECT status_json FROM run_idempotency WHERE idempotency_key=?',
                                  ('room:task-cancel:1',)).fetchall()
            assert len(rows) == 1 and json.loads(rows[0][0])['status'] == 'cancelled'
    finally:
        (home / 'pre-admission-release').touch()
        (home / 'observer-release').touch()
        model.shutdown()
        model.server_close()
