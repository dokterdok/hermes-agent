"""Two install roots, one authenticated client, exact file versions after disconnect.

Linux processes with separate homes, install ids, and control sockets. This is
not two physical machines and not an installed Electron window. Inference is
the loopback RoomModel stub in the sibling test module.
"""
import asyncio
import base64
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

from tests.gateway.fixtures.local_recovery_probe import child_env, daemon, rpc, websocket
from tests.gateway.test_session_hosted_daemon import RoomModel

# Distinct valid 1x1 PNGs. Same filename, different bytes.
_PNG_V1 = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII=')
_PNG_V2 = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def _config(home, base, profiles):
    return {
        'gateway': {'multiplex_profiles': False},
        'hosted_rooms': {'profiles': profiles},
        'model': {'provider': 'custom', 'default': 'gpt-4o', 'base_url': base, 'supports_vision': True},
        'agent': {'image_input_mode': 'native'},
        'platform_toolsets': {'gui': [], 'bot_room': []},
        'auxiliary': {'title_generation': {'enabled': False}},
        'terminal': {'cwd': str(home)},
    }


def test_sibling_installs_recover_file_versions_after_client_close(tmp_path):
    root = Path(__file__).resolve().parents[2]
    user = tmp_path / 'user'
    user.mkdir()
    # Sibling install roots. The peer profile is NOT under the host root, so
    # the two processes do not share an install id or a room database.
    host = tmp_path / 'install-a'
    peer_root = tmp_path / 'install-b'
    peer = peer_root / 'profiles' / 'two'
    host.mkdir(mode=0o700)
    peer.mkdir(parents=True, mode=0o700)
    assert host.resolve() != peer_root.resolve()
    assert 'profiles' not in host.parts[-2:]
    assert peer_root not in host.resolve().parents

    model = ThreadingHTTPServer(('127.0.0.1', 0), RoomModel)
    model.requests = []
    model.blocked, model.release = threading.Event(), threading.Event()
    threading.Thread(target=model.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{model.server_port}/v1'
    (host / 'config.yaml').write_text(json.dumps(_config(host, base, {'two': str(peer)})))
    peer_cfg = _config(peer, base, {})
    peer_cfg['gateway'] = {'standalone': True}
    (peer / 'config.yaml').write_text(json.dumps(peer_cfg))
    env = child_env() | dict(
        HOME=str(user), USERPROFILE=str(user), PYTHONPATH=str(root),
        OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=base, PYTHONUNBUFFERED='1')
    members = [
        {'member_id': 'one', 'profile': 'default', 'handle': 'one'},
        {'member_id': 'two', 'profile': 'two', 'handle': 'two'},
    ]
    saved = {}

    async def identified(desc_host, desc_peer):
        async with websocket(host, desc_host) as ws, websocket(peer, desc_peer) as other:
            host_caps = await rpc(ws, 'groups.capabilities')
            peer_caps = await rpc(other, 'groups.capabilities')
            assert 'result' in host_caps and 'result' in peer_caps, (host_caps, peer_caps)
            assert host_caps['result']['authority_gateway_id'] != peer_caps['result']['authority_gateway_id']
            saved['host_authority'] = host_caps['result']['authority_gateway_id']
            saved['peer_authority'] = peer_caps['result']['authority_gateway_id']
            created = await rpc(ws, 'groups.create', room_id='owned', name='Owned', members=members)
            assert 'result' in created, created
            saved['members'] = created['result']['room']['members']
            versions = []
            for upload_id, blob in (('v1', _PNG_V1), ('v2', _PNG_V2)):
                uploaded = await rpc(
                    ws, 'groups.attachment.upload', room_id='owned', upload_id=upload_id,
                    kind='image', name='pixel.png', mime='image/png',
                    data_base64=base64.b64encode(blob).decode())
                assert 'result' in uploaded, uploaded
                versions.append({k: uploaded['result'][k] for k in ('attachment_id', 'kind', 'name', 'size', 'mime')})
            assert versions[0]['name'] == versions[1]['name'] == 'pixel.png'
            assert versions[0]['attachment_id'] != versions[1]['attachment_id']
            saved['versions'] = versions
            first = await rpc(
                ws, 'groups.send', room_id='owned', event_id='file-v1',
                payload={'text': 'FILE_V1', 'thread_id': 'thread', 'attachments': [versions[0]]})
            assert first['result']['accepted'], first
            saved['event_v1'] = first['result']['event']['event_id']
            async with asyncio.timeout(45):
                while True:
                    log = await rpc(ws, 'groups.log', room_id='owned')
                    assert 'result' in log, log
                    if sum(e['kind'] == 'turn.settled' for e in log['result']['events']) >= 2:
                        break
                    await asyncio.sleep(.1)
            second = await rpc(
                ws, 'groups.send', room_id='owned', event_id='file-v2',
                payload={'text': 'BLOCK_HOSTED FILE_V2', 'thread_id': 'thread', 'attachments': [versions[1]]})
            assert second['result']['accepted'], second
            saved['event_v2'] = second['result']['event']['event_id']
            assert await asyncio.to_thread(model.blocked.wait, 40), {
                'requests': len(model.requests),
                'last': str(model.requests[-1]['messages'])[:500] if model.requests else None,
            }
        # The authenticated client socket is closed. Both gateways must still
        # be alive and the accepted turn must still be inside the stub.
        return saved

    async def recovered(desc_host):
        assert model.blocked.is_set() and not model.release.is_set()
        model.release.set()
        async with websocket(host, desc_host) as ws:
            async with asyncio.timeout(45):
                while True:
                    log = await rpc(ws, 'groups.log', room_id='owned')
                    assert 'result' in log, log
                    events = log['result']['events']
                    if sum(e['kind'] == 'turn.settled' for e in events) >= 4:
                        break
                    await asyncio.sleep(.1)
            texts = [e['payload'].get('text') for e in events if e['kind'] == 'message.user']
            assert texts == ['FILE_V1', 'BLOCK_HOSTED FILE_V2'], texts
            state = await rpc(ws, 'groups.state', room_id='owned')
            assert 'result' in state, state
            assert state['result']['room']['members'] == saved['members']
            assert state['result']['driver_status']['counts'].get('settled', 0) >= 4, state
            for event_id, blob in ((saved['event_v1'], _PNG_V1), (saved['event_v2'], _PNG_V2)):
                manifest = saved['versions'][0 if blob is _PNG_V1 else 1]
                downloaded = await rpc(
                    ws, 'groups.attachment.download', room_id='owned', event_id=event_id,
                    attachment_id=manifest['attachment_id'])
                assert 'result' in downloaded, downloaded
                assert base64.b64decode(downloaded['result']['data_base64']) == blob
            seen = []
            for request in model.requests:
                for message in request['messages']:
                    content = message.get('content')
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if block.get('type') == 'image_url':
                            seen.append(base64.b64decode(block['image_url']['url'].split(',')[1]))
            assert _PNG_V1 in seen and _PNG_V2 in seen, [len(item) for item in seen]

    try:
        with daemon(root, peer, env | {'HERMES_HOME': str(peer)}, barrier=False) as (peer_proc, peer_desc), \
             daemon(root, host, env | {'HERMES_HOME': str(host)}, barrier=False) as (host_proc, host_desc):
            asyncio.run(identified(host_desc, peer_desc))
            assert host_proc.poll() is None and peer_proc.poll() is None
            host_install = (host / 'install_id').read_text().strip()
            peer_install = (peer_root / 'install_id').read_text().strip()
            assert host_install != peer_install
            assert saved['host_authority'] != saved['peer_authority']
            assert (host / 'state.db').exists() and (peer / 'state.db').exists()
            import sqlite3
            with sqlite3.connect(f'file:{host / "state.db"}?mode=ro', uri=True) as db:
                assert db.execute(
                    "SELECT room_id FROM hosted_rooms WHERE room_id='owned'").fetchone() == ('owned',)
            with sqlite3.connect(f'file:{peer / "state.db"}?mode=ro', uri=True) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'hosted_rooms' in tables:
                    assert db.execute(
                        "SELECT room_id FROM hosted_rooms WHERE room_id='owned'").fetchone() is None
            assert not (host / 'profiles' / 'two').exists()
            asyncio.run(recovered(host_desc))
            assert host_proc.poll() is None and peer_proc.poll() is None
    finally:
        model.release.set()
        model.shutdown()
        model.server_close()
