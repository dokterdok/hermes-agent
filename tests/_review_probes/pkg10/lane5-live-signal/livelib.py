"""Shared live-retest helpers for package 10 (#140719).

Each scenario script imports this first, passing ``--tree composed|main``. It:
- rebinds HERMES_HOME / HOME / USERPROFILE / LOCALAPPDATA / APPDATA of THIS process and of every
  child to a fresh scenario directory under live/<scenario>-<tree>/ (never ~/.hermes or
  %LOCALAPPDATA%\\hermes);
- puts the chosen worktree first on sys.path so the repo's own harness
  (tests/gateway/fixtures/local_recovery_probe.py) and gateway code are the tree under test;
- runs a loopback OpenAI-compatible fake model whose replies a scenario scripts;
- starts ``python -m gateway.run`` (the tree's test-env interpreter) and kills the whole process
  tree afterwards (taskkill /T /F), recording every PID it spawned in live/pids.log.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(os.environ.get('PKG10_ROOT') or Path(__file__).resolve().parent.parent)  # checkouts live under ROOT/<tree>, runs under ROOT/live

LIVE = (ROOT / 'live')
TREES = {'composed': (ROOT / 'composed'),
         'main': (ROOT / 'main')}
PIDS = LIVE / 'pids.log'


def log(*parts):
    print(time.strftime('%H:%M:%S'), *parts, flush=True)


class Ctx:
    pass


def setup(scenario, argv=None, extra_args=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', choices=sorted(TREES), required=True)
    for name, kw in (extra_args or {}).items():
        ap.add_argument(name, **kw)
    args = ap.parse_args(argv)
    ctx = Ctx()
    ctx.args, ctx.tree, ctx.scenario = args, args.tree, scenario
    ctx.root = TREES[args.tree]
    ctx.base = LIVE / f'{scenario}-{args.tree}'
    if ctx.base.exists():
        shutil.rmtree(ctx.base)
    ctx.home, ctx.user, ctx.work = ctx.base / 'home', ctx.base / 'user', ctx.base / 'work'
    ctx.home.mkdir(parents=True, mode=0o700)
    for d in (ctx.user, ctx.work, ctx.base / 'localappdata', ctx.base / 'appdata'):
        d.mkdir(parents=True, exist_ok=True)
    # Bind this helper process too: nothing it imports may resolve the real Hermes home.
    os.environ.update(HERMES_HOME=str(ctx.home), HOME=str(ctx.user), USERPROFILE=str(ctx.user),
                      LOCALAPPDATA=str(ctx.base / 'localappdata'), APPDATA=str(ctx.base / 'appdata'))
    sys.path.insert(0, str(ctx.root))
    os.chdir(ctx.root)
    ctx.results = {'scenario': scenario, 'tree': args.tree, 'head': git_head(ctx.root)}
    log(f'[{scenario}] tree={args.tree} head={ctx.results["head"]} base={ctx.base}')
    return ctx


def git_head(root):
    return subprocess.run(['git', '-C', str(root), 'rev-parse', '--short=10', 'HEAD'], capture_output=True,
                          text=True).stdout.strip()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------- fake model
def last_user_text(body):
    for m in reversed(body.get('messages') or []):
        if m.get('role') == 'user':
            c = m.get('content')
            if isinstance(c, list):
                return ' '.join(p.get('text', '') for p in c if isinstance(p, dict))
            return c or ''
    return ''


def is_summary_request(body):
    return any('You are a summarization agent' in str(m.get('content', '')) for m in body.get('messages') or [])


class FakeModel(BaseHTTPRequestHandler):
    """OpenAI-compatible /v1/chat/completions; ``server.behavior(body, handler)`` returns a spec:
    {'text': str} | {'tool_calls': [(name, args_dict), ...], 'text': optional} |
    {'error': status, 'body': dict} | {'chunks': iterable[str]} ; optional 'delay' seconds."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        payload = json.dumps({'object': 'list', 'data': [{'id': 'fake-model', 'object': 'model'}]}).encode()
        self._send(200, 'application/json', payload)

    def _send(self, status, kind, payload):
        try:
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            body = {}
        srv = self.server
        entry = {'t': time.time(), 'path': self.path, 'auth': self.headers.get('Authorization'),
                 'model': body.get('model'), 'stream': bool(body.get('stream')),
                 'n_messages': len(body.get('messages') or []), 'last_user': last_user_text(body)[:200],
                 'last_role': ((body.get('messages') or [{}])[-1] or {}).get('role'),
                 'tools': [t.get('function', {}).get('name') for t in body.get('tools') or []],
                 'summary': is_summary_request(body)}
        with srv.lock:
            srv.requests.append(entry)
            srv.bodies.append(body)
        spec = srv.behavior(body, self) if srv.behavior else {'text': 'ACK ' + entry['last_user'][:40]}
        entry['spec'] = {k: (v if k != 'chunks' else '<chunks>') for k, v in spec.items()}
        if spec.get('delay'):
            time.sleep(spec['delay'])
        if 'wait' in spec:
            spec['wait'].wait(120)
        if 'error' in spec:
            return self._send(spec['error'], 'application/json', json.dumps(spec['body']).encode())
        if body.get('stream'):
            return self._stream(spec)
        message = {'role': 'assistant', 'content': spec.get('text')}
        finish = 'stop'
        if spec.get('tool_calls'):
            message['tool_calls'] = [{'id': f'call_{i}_{int(time.time()*1000)}', 'type': 'function',
                                      'function': {'name': n, 'arguments': json.dumps(a)}}
                                     for i, (n, a) in enumerate(spec['tool_calls'])]
            finish = 'tool_calls'
        if 'chunks' in spec:
            message['content'] = ''.join(spec['chunks'])
        payload = json.dumps({'id': 'fake', 'object': 'chat.completion', 'model': body.get('model'),
                              'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                              'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
        self._send(200, 'application/json', payload)

    def _stream(self, spec):
        def frame(delta, finish=None):
            return ('data: ' + json.dumps({'id': 'fake', 'object': 'chat.completion.chunk', 'choices': [
                {'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n').encode()
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(frame({'role': 'assistant', 'content': ''}))
            finish = 'stop'
            if spec.get('text'):
                self.wfile.write(frame({'content': spec['text']}))
            for i, chunk in enumerate(spec.get('chunks') or ()):
                self.wfile.write(frame({'content': chunk}))
                if i % 256 == 0:
                    self.wfile.flush()
            if spec.get('tool_calls'):
                calls = [{'index': i, 'id': f'call_{i}_{int(time.time()*1000)}', 'type': 'function',
                          'function': {'name': n, 'arguments': json.dumps(a)}}
                         for i, (n, a) in enumerate(spec['tool_calls'])]
                self.wfile.write(frame({'tool_calls': calls}))
                finish = 'tool_calls'
            self.wfile.write(frame({}, finish))
            self.wfile.write(('data: ' + json.dumps({'id': 'fake', 'object': 'chat.completion.chunk', 'choices': [],
                                                     'usage': {'prompt_tokens': 10, 'completion_tokens': 5,
                                                               'total_tokens': 15}}) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


@contextmanager
def fake_model(behavior=None):
    srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeModel)
    srv.daemon_threads = True
    srv.behavior, srv.requests, srv.bodies, srv.lock = behavior, [], [], threading.Lock()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.url = f'http://127.0.0.1:{srv.server_port}/v1'
    log('fake model at', srv.url)
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------- home + daemon
def write_home(ctx, model_url, *, config_extra=None, context=1_000_000, models=('fake-model', 'summary')):
    cfg = {'gateway': {'multiplex_profiles': False},
           'model': {'provider': 'custom', 'default': 'fake-model', 'base_url': model_url},
           'auxiliary': {'title_generation': {'enabled': False}},
           'terminal': {'cwd': str(ctx.work)}}
    deep_update(cfg, config_extra or {})
    (ctx.home / 'config.yaml').write_text(json.dumps(cfg, indent=1))
    (ctx.home / 'models_dev_cache.json').write_text(json.dumps({'custom': {'id': 'custom', 'models': {
        name: {'id': name, 'name': name, 'limit': {'context': context, 'output': 4000}} for name in models}}}))
    ctx.config = cfg
    return cfg


def deep_update(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_update(dst[k], v)
        else:
            dst[k] = v


def env_for(ctx, model_url, **extra):
    from tests.gateway.fixtures.local_recovery_probe import child_env
    env = child_env()
    env.update(HOME=str(ctx.user), USERPROFILE=str(ctx.user), HERMES_HOME=str(ctx.home), PYTHONPATH=str(ctx.root),
               LOCALAPPDATA=str(ctx.base / 'localappdata'), APPDATA=str(ctx.base / 'appdata'),
               OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=model_url, PYTHONUNBUFFERED='1')
    env.update({k: str(v) for k, v in extra.items()})
    return env


def record_pid(ctx, pid, what):
    with PIDS.open('a') as fh:
        fh.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {ctx.scenario}-{ctx.tree} {what} {pid}\n')


def kill_tree(pid):
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True)


@contextmanager
def gateway(ctx, env, *, name='gateway', home=None, args=()):
    """``python -m gateway.run`` against ``home`` (default ctx.home); yields (proc, descriptor)."""
    from tests.gateway.test_normal_runtime_boot import control
    home = home or ctx.home
    logpath = ctx.base / f'{name}.log'
    with logpath.open('w+', encoding='utf-8', errors='replace') as fh:
        proc = subprocess.Popen([sys.executable, '-m', 'gateway.run', *args], cwd=ctx.root, env=env,
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT)
        record_pid(ctx, proc.pid, name)
        try:
            deadline = time.monotonic() + 150
            desc = {}
            while proc.poll() is None and time.monotonic() < deadline:
                try:
                    desc = control(home, 'identify')
                    if desc.get('state') == 'ready':
                        break
                except (OSError, ValueError, AssertionError):
                    pass
                time.sleep(.1)
            if desc.get('state') != 'ready':
                fh.flush()
                fh.seek(0)
                raise RuntimeError(f'gateway not ready (rc={proc.poll()}):\n' + fh.read()[-4000:])
            log(f'{name} ready pid={proc.pid} api_origin={desc.get("api_origin")}')
            yield proc, desc
        finally:
            kill_tree(proc.pid)
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            log(f'{name} stopped rc={proc.returncode}; log={logpath}')


def ws_connect(home, desc, max_size=2 ** 24):
    from tests.gateway.test_normal_runtime_boot import control
    from websockets.asyncio.client import connect
    grant = control(home, 'session-ticket', {'profile_id': str(home), 'instance_id': desc['instance_id'],
                                             'purpose': 'interactive'})
    return connect(desc['api_origin'].replace('http:', 'ws:') + '/api/ws', max_size=max_size,
                   subprotocols=['hermes-gateway-v1', 'hermes-gateway-ticket.' + grant['ticket']])


class Rpc:
    """JSON-RPC over one gateway WebSocket with a background reader: replies by id, notifications kept."""

    def __init__(self, ws):
        import asyncio
        self.ws, self.pending, self.events, self.n = ws, {}, [], 0
        self.closed = None
        self.task = asyncio.get_running_loop().create_task(self._reader())

    async def _reader(self):
        import asyncio
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                rid = msg.get('id')
                if rid is not None and rid in self.pending and ('result' in msg or 'error' in msg):
                    self.pending.pop(rid).set_result((time.monotonic(), msg))
                else:
                    self.events.append((time.monotonic(), msg))
        except Exception as exc:  # noqa: BLE001 - recorded for the report
            self.closed = repr(exc)
        else:
            self.closed = 'closed'
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(self.closed))
        await asyncio.sleep(0)

    async def send(self, method, **params):
        import asyncio
        self.n += 1
        rid = f'{method}#{self.n}'
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        sent = time.monotonic()
        await self.ws.send(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}))
        return sent, fut

    async def call(self, method, timeout=30, **params):
        import asyncio
        sent, fut = await self.send(method, **params)
        done, msg = await asyncio.wait_for(fut, timeout)
        return msg

    async def ok(self, method, timeout=30, **params):
        msg = await self.call(method, timeout=timeout, **params)
        assert 'result' in msg, (method, msg)
        return msg['result']


def save(ctx, results=None):
    data = dict(ctx.results, **(results or {}))
    out = ctx.base / 'result.json'
    out.write_text(json.dumps(data, indent=1, default=str))
    log('result written', out)
    print(json.dumps(data, indent=1, default=str)[:6000])
    return data


def grep_log(path, *needles, limit=40):
    out = []
    try:
        for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
            if any(n in line for n in needles):
                out.append(line[:400])
    except OSError:
        pass
    return out[-limit:]
