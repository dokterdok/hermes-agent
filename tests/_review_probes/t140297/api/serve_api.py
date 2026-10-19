"""Long-running live rig for a real external client (Open WebUI) against the Hermes API server.

Starts the loopback model stub and a real ``python -m gateway.run`` with the OpenAI-compatible API
server on 127.0.0.1:<--api-port>, writes ``rig.json`` (API base URL, key, home), then waits until
``<run dir>/STOP`` exists. Every few seconds it snapshots the Hermes sessions (``sessions.json``) and
the model requests (``requests.json``), so the client's effect on Hermes can be read while it runs.
"""
import json
import secrets
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib140297 as L  # noqa: E402


def snapshot(ctx, model):
    db = ctx.home / 'state.db'
    out = {'t': time.strftime('%H:%M:%S'), 'sessions': []}
    if db.exists():
        conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        try:
            for sid, source, title, started in conn.execute(
                    'SELECT id, source, title, started_at FROM sessions ORDER BY started_at'):
                msgs = conn.execute('SELECT role, substr(content, 1, 80) FROM messages WHERE session_id=? '
                                    'ORDER BY id', (sid,)).fetchall()
                out['sessions'].append({'id': sid, 'source': source, 'title': title, 'started_at': started,
                                        'messages': msgs})
            out['retired'] = [k for (k,) in conn.execute(
                "SELECT key FROM state_meta WHERE key LIKE '%retired%' ORDER BY key")]
        except sqlite3.Error as exc:
            out['error'] = repr(exc)
        finally:
            conn.close()
    (ctx.base / 'sessions.json').write_text(json.dumps(out, indent=1, default=str))
    (ctx.base / 'requests.json').write_text(json.dumps(model.requests, indent=1, default=str))


def main():
    ctx = L.setup('rig-api', {'--api-port': {'type': int, 'default': 18642}})
    key = secrets.token_urlsafe(32)
    try:
        with L.fake_model() as model:
            L.write_home(ctx, {'gateway': {'multiplex_profiles': False},
                               'model': {'provider': 'custom', 'default': 'fake-model', 'base_url': model.url},
                               'auxiliary': {'title_generation': {'enabled': False}},
                               'terminal': {'cwd': str(ctx.work)}})
            (ctx.home / '.env').write_text(f'OPENAI_API_KEY=loopback-only\nAPI_SERVER_ENABLED=true\n'
                                           f'API_SERVER_KEY={key}\nAPI_SERVER_PORT={ctx.args.api_port}\n')
            env = L.base_env(ctx, OPENAI_API_KEY='loopback-only')
            with L.gateway(ctx, env):
                rig = {'api_base': f'http://127.0.0.1:{ctx.args.api_port}/v1', 'api_key': key,
                       'home': str(ctx.home), 'base': str(ctx.base), 'head': ctx.head}
                (ctx.base / 'rig.json').write_text(json.dumps(rig, indent=1))
                L.log('rig ready', json.dumps({k: v for k, v in rig.items() if k != 'api_key'}))
                while not (ctx.base / 'STOP').exists():
                    snapshot(ctx, model)
                    time.sleep(3)
                snapshot(ctx, model)
    finally:
        L.stop_all(ctx)
    L.log('rig stopped')


if __name__ == '__main__':
    main()
