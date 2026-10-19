"""#140297 section 2, item 1: "Live recheck on the stub setup after it merges" (#140335, MP-15).

A ``providers:`` entry keyed like a built-in (``deepseek`` and ``anthropic``) with its own ``key_env``,
both the built-in's env key and the entry's key set. Real gateway, real CLI and API server; the model
endpoint is a loopback stub that records the credential each request carried. Every request to the
entry's endpoint must carry the entry's key, never the built-in's:
- a ``hermes -z`` turn (local route through the gateway),
- an API server ``/v1/chat/completions`` turn,
- the title-generation aux call pinned to ``custom:<name>``.
"""
import json
import secrets
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib140297 as L  # noqa: E402

ENTRY_KEY = 'entry-key-A-' + secrets.token_hex(6)
BUILTIN_KEY = 'builtin-key-B-' + secrets.token_hex(6)


def run_one(ctx, name, builtin_env):
    sub = ctx.base / name
    sub.mkdir()
    ctx.home = sub / 'home'
    ctx.home.mkdir(mode=0o700)
    api_key = secrets.token_urlsafe(32)
    port = free_port()
    with L.fake_model() as model:
        entry = {'name': name, 'api': model.url, 'transport': 'chat_completions', 'default_model': 'fake-model',
                 'key_env': 'ENTRY_KEY_VAR'}
        if ctx.args.transport == 'omitted':
            del entry['transport']
        L.write_home(ctx, {'gateway': {'multiplex_profiles': False},
                           'model': {'default': 'fake-model', 'provider': f'custom:{name}'},
                           'providers': {name: entry},
                           'auxiliary': {'title_generation': ({'enabled': True, 'provider': f'custom:{name}', 'model': 'fake-model'}
                                                             if ctx.args.aux == 'pinned' else {'enabled': True})},
                           'terminal': {'cwd': str(ctx.work)}})
        keys = {builtin_env: BUILTIN_KEY, 'ENTRY_KEY_VAR': ENTRY_KEY}
        where = ctx.args.keys  # where the two keys live: exported, in the profile .env, or both
        dotenv = ''.join(f'{k}={v}\n' for k, v in keys.items()) if where in ('dotenv', 'both') else ''
        (ctx.home / '.env').write_text(dotenv + f'API_SERVER_ENABLED=true\nAPI_SERVER_KEY={api_key}\n'
                                       f'API_SERVER_PORT={port}\n')
        env = L.base_env(ctx, **(keys if where in ('env', 'both') else {}))
        # A real install's auth store (the unit tests write the same empty pool section).
        (ctx.home / 'auth.json').write_text(json.dumps({'version': 1, 'providers': {}, 'credential_pool': {}}))
        with L.gateway(ctx, env, name=f'gateway-{name}'):
            z = L.subprocess.run([sys.executable, '-m', 'hermes_cli.main', '-z', f'{name} one-shot'], cwd=ctx.work,
                                 env=env, stdin=L.subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
            req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', method='POST',
                                         data=json.dumps({'model': 'hermes-agent', 'messages': [
                                             {'role': 'user', 'content': f'{name} api turn'}]}).encode(),
                                         headers={'Authorization': f'Bearer {api_key}',
                                                  'Content-Type': 'application/json'})
            api_status, api_reply = None, None
            for _ in range(60):
                try:
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        api_status, api_reply = resp.status, json.loads(resp.read())
                    break
                except OSError as exc:  # the API server binds after ready on some trees
                    api_status = repr(exc)
                    time.sleep(1)
            settle_requests(model)  # the title calls run after the turns
        creds = [{'path': r['path'], 'auth': (r['auth'] or '').replace('Bearer ', '')[:24],
                  'x_api_key': (r['x_api_key'] or '')[:24], 'user': r['last_user'][:60],
                  'title_call': 'title' in r['system'].lower(), 'tools': r['tools']} for r in model.requests]
        L.check(ctx, f'{name}: a title-generation call reached the entry', any(c['title_call'] for c in creds))
        sent = {c['auth'] or c['x_api_key'] for c in creds}
        L.check(ctx, f'{name}: hermes -z answered', z.returncode == 0 and 'REPLY' in z.stdout,
                rc=z.returncode, stdout=z.stdout[-200:], stderr=z.stderr[-300:])
        L.check(ctx, f'{name}: API server turn answered', api_status == 200,
                status=api_status, reply=(api_reply or {}).get('choices', [{}])[0].get('message', {}).get('content'))
        L.check(ctx, f'{name}: every request carried the entry key', bool(creds) and sent == {ENTRY_KEY[:24]},
                requests=creds, builtin_key_seen=BUILTIN_KEY[:24] in sent)


def settle_requests(model, quiet=5, limit=40):
    """Wait until the stub has seen no new request for ``quiet`` seconds (at most ``limit``)."""
    last, since, deadline = -1, time.monotonic(), time.monotonic() + limit
    while time.monotonic() < deadline:
        count = len(model.requests)
        if count != last:
            last, since = count, time.monotonic()
        elif time.monotonic() - since > quiet:
            return
        time.sleep(.5)


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    ctx = L.setup('mp15-shadow-key', {'--keys': {'choices': ['env', 'dotenv', 'both'], 'default': 'env'}, '--aux': {'choices': ['pinned', 'auto'], 'default': 'pinned'},
                                      '--transport': {'choices': ['declared', 'omitted'], 'default': 'declared'}})
    try:
        for name, builtin_env in (('deepseek', 'DEEPSEEK_API_KEY'), ('anthropic', 'ANTHROPIC_API_KEY')):
            try:
                run_one(ctx, name, builtin_env)
            except Exception as exc:  # noqa: BLE001 - recorded, next provider still runs
                L.check(ctx, f'{name}: run', False, error=repr(exc)[:2000])
    finally:
        L.stop_all(ctx)
    data = L.save(ctx)
    print(json.dumps(data, indent=1, default=str)[:8000])


if __name__ == '__main__':
    main()
