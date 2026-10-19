"""#140297 section 1: "LC-9 (#140314) on a long-lived gateway and an upgraded install".

One Hermes home, two builds. Phase A runs a pre-change build (``--old-root`` / ``--old-python``,
e.g. the 2026-10-17 canary) and settles keyed Chat Completions and Responses turns whose replies are
2,000 characters. Phase B upgrades in place to this tree and:
- re-sends every phase-A request with its key: each must replay the original answer byte for byte,
  as JSON and as a stream, with zero model calls (the rows were written in the old shape);
- runs many more turns, and compares the stored result bytes per turn before and after the change,
  and against the transcript bytes of the same turns.
"""
import json
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib140297 as L  # noqa: E402

FILLER = ('lorem ipsum dolor sit amet ' * 100)[:1900]


def reply_for(body):
    return {'text': f'REPLY {L.last_user_text(body)[:60]} {FILLER}'}


def post(port, key, path, body, headers=None, stream=False):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', method='POST', data=json.dumps(body).encode(),
                                 headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode()
            return resp.status, (raw if stream else json.loads(raw))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors='replace')[:500]


def chat_text(payload):
    return payload['choices'][0]['message']['content'] if isinstance(payload, dict) else None


def stream_text(raw):
    parts = []
    for line in (raw or '').splitlines():
        if line.startswith('data: ') and line != 'data: [DONE]':
            chunk = json.loads(line[6:])
            for choice in chunk.get('choices') or []:
                parts.append((choice.get('delta') or {}).get('content') or '')
    return ''.join(parts)


def responses_text(payload):
    if not isinstance(payload, dict):
        return None
    return ''.join(c.get('text', '') for item in payload.get('output') or [] if item.get('type') == 'message'
                   for c in item.get('content') or [])


def turns(model):
    """Model turns (the gateway also POSTs /api/show model-info probes at start)."""
    return sum(1 for r in model.requests if r['path'].endswith('/chat/completions'))

BOT_DELIVERY = {'profile': 'default', 'id': 'b0' * 16, 'message': 'bot delivery settled before the upgrade'}


def summarize(receipt):
    receipt = receipt or {}
    return {k: (str(receipt.get(k))[:70] if k == 'reply' else receipt.get(k))
            for k in ('status', 'admission_id', 'session_id', 'reply', 'error', 'reason')}


def settle_bot_delivery(ctx, desc, limit=90):
    """Deliver once, then repeat the same id until its receipt is terminal (the owner answers a
    redelivery with the settled receipt)."""
    deadline, receipt = time.monotonic() + limit, None
    while time.monotonic() < deadline:
        msg = L.ws_call(ctx.home, desc, 'bot_relay.deliver', BOT_DELIVERY)
        receipt = msg.get('result') or {'error': msg.get('error')}
        if receipt.get('status') in ('settled', 'failed', 'interrupted') or 'error' in msg:
            break
        time.sleep(1)
    L.check(ctx, 'old build settled a Bot delivery', (receipt or {}).get('status') == 'settled'
            and bool((receipt or {}).get('reply')), receipt=summarize(receipt))
    return receipt


def sizes(home):
    conn = sqlite3.connect(f'file:{home / "state.db"}?mode=ro', uri=True, timeout=10)
    try:
        rows, result_bytes = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(value)), 0) FROM state_meta "
                                          "WHERE key GLOB 'gateway.admission.result.v1.*'").fetchone()
        transcript = conn.execute('SELECT COALESCE(SUM(LENGTH(content)), 0) FROM messages').fetchone()[0]
        return {'result_rows': rows, 'result_bytes': result_bytes, 'transcript_bytes': transcript}
    finally:
        conn.close()


def main():
    ctx = L.setup('lc9-upgrade', {'--old-root': {'required': True}, '--old-python': {'required': True},
                                  '--turns-old': {'type': int, 'default': 20},
                                  '--turns-new': {'type': int, 'default': 100}})
    old_root = Path(ctx.args.old_root).resolve()
    ctx.results['old_head'] = L.git_head(old_root)
    port, key = free_port(), secrets.token_urlsafe(32)
    sent = []
    try:
        with L.fake_model(reply_for) as model:
            L.write_home(ctx, {'gateway': {'multiplex_profiles': False},
                               'model': {'provider': 'custom', 'default': 'fake-model', 'base_url': model.url},
                               'auxiliary': {'title_generation': {'enabled': False}},
                               'terminal': {'cwd': str(ctx.work)}})
            (ctx.home / '.env').write_text(f'OPENAI_API_KEY=loopback-only\nAPI_SERVER_ENABLED=true\n'
                                           f'API_SERVER_KEY={key}\nAPI_SERVER_PORT={port}\n')
            # Phase A: the pre-change build
            env_old = L.base_env(ctx, OPENAI_API_KEY='loopback-only', PYTHONPATH=str(old_root))
            with L.gateway(ctx, env_old, name='gateway-old', python=ctx.args.old_python, root=old_root) as (_, desc):
                wait_api(port, key)
                bot_first = settle_bot_delivery(ctx, desc)
                for i in range(ctx.args.turns_old):
                    body = {'model': 'hermes-agent', 'messages': [{'role': 'user', 'content': f'old chat turn {i}'}]}
                    hdrs = {'X-Hermes-Session-Id': 'lc9-old-chat', 'Idempotency-Key': f'lc9-chat-{i}'}
                    status, payload = post(port, key, '/v1/chat/completions', body, hdrs)
                    sent.append(('chat', body, hdrs, status, chat_text(payload)))
                for i in range(5):
                    body = {'model': 'hermes-agent', 'input': f'old responses turn {i}'}
                    hdrs = {'Idempotency-Key': f'lc9-resp-{i}'}
                    status, payload = post(port, key, '/v1/responses', body, hdrs)
                    sent.append(('responses', body, hdrs, status, responses_text(payload)))
            after_old = sizes(ctx.home)
            calls_old = turns(model)
            L.check(ctx, 'old build settled every keyed turn', all(s[3] == 200 and s[4] for s in sent),
                    statuses=[s[3] for s in sent], **after_old)

            # Phase B: upgraded in place
            env_new = L.base_env(ctx, OPENAI_API_KEY='loopback-only')
            with L.gateway(ctx, env_new, name='gateway-new') as (_, desc):
                wait_api(port, key)
                before_bot = turns(model)
                bot_again = L.ws_call(ctx.home, desc, 'bot_relay.deliver', BOT_DELIVERY)
                again = bot_again.get('result') or {}
                L.check(ctx, 'a Bot receipt settled before the upgrade answers with its reply, no model call',
                        again.get('status') == 'settled' and again.get('reply') == (bot_first or {}).get('reply')
                        and again.get('admission_id') == (bot_first or {}).get('admission_id')
                        and turns(model) == before_bot,
                        before=summarize(bot_first), after=summarize(again), error=bot_again.get('error'))
                mismatches = []
                for kind, body, hdrs, _, first in sent:
                    path = '/v1/chat/completions' if kind == 'chat' else '/v1/responses'
                    status, payload = post(port, key, path, body, hdrs)
                    again = chat_text(payload) if kind == 'chat' else responses_text(payload)
                    if status != 200 or again != first:
                        mismatches.append({'kind': kind, 'key': hdrs['Idempotency-Key'], 'status': status,
                                           'got': (again or str(payload))[:120], 'want': (first or '')[:60]})
                kind, body, hdrs, _, first = sent[0]
                status, raw = post(port, key, '/v1/chat/completions', dict(body, stream=True), hdrs, stream=True)
                streamed = stream_text(raw)
                L.check(ctx, 'exact retries of pre-upgrade turns replay the original answer, no model call',
                        not mismatches and turns(model) == calls_old, retried=len(sent),
                        model_calls_during_retries=turns(model) - calls_old,
                        other_requests=sorted({r['path'] for r in model.requests[-6:]}), mismatches=mismatches[:5])
                L.check(ctx, 'a pre-upgrade chat key replays as a stream too', status == 200 and streamed == first,
                        status=status, got=streamed[:80])
                before_new = sizes(ctx.home)
                for i in range(ctx.args.turns_new):
                    body = {'model': 'hermes-agent', 'messages': [{'role': 'user', 'content': f'new chat turn {i}'}]}
                    post(port, key, '/v1/chat/completions', body,
                         {'X-Hermes-Session-Id': 'lc9-new-chat', 'Idempotency-Key': f'lc9-new-{i}'})
                after_new = sizes(ctx.home)
                status, payload = post(port, key, '/v1/chat/completions', body,
                                       {'X-Hermes-Session-Id': 'lc9-new-chat', 'Idempotency-Key': f'lc9-new-{i}'})
                L.check(ctx, 'a post-upgrade key replays too (control)', status == 200 and chat_text(payload),
                        status=status)
            old_rows = after_old['result_rows'] or 1
            new_rows = (after_new['result_rows'] - before_new['result_rows']) or 1
            per_old = after_old['result_bytes'] / old_rows
            per_new = (after_new['result_bytes'] - before_new['result_bytes']) / new_rows
            transcript_new = after_new['transcript_bytes'] - before_new['transcript_bytes']
            ctx.results['sizes'] = {'after_old': after_old, 'before_new': before_new, 'after_new': after_new,
                                    'bytes_per_result_row_old_build': round(per_old),
                                    'bytes_per_result_row_new_build': round(per_new),
                                    'new_turns_result_bytes': after_new['result_bytes'] - before_new['result_bytes'],
                                    'new_turns_transcript_bytes': transcript_new}
            L.check(ctx, 'a settled reply is stored once (row shrinks by about one reply)',
                    per_old - per_new > 1500, per_old=round(per_old), per_new=round(per_new))
    finally:
        L.stop_all(ctx)
    data = L.save(ctx)
    print(json.dumps(data, indent=1, default=str)[:6000])


def wait_api(port, key, limit=60):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/models',
                                         headers={'Authorization': f'Bearer {key}'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(.5)
    raise RuntimeError('API server did not answer')


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


if __name__ == '__main__':
    main()
