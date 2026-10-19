"""Shared helpers for the #140297 live checks (not for merge).

Each check script is run with the tree's own interpreter (``scripts/run-in-hermes-env python <script>
--root <tree> --out <dir>``). It:
- rebinds HERMES_HOME / HOME / USERPROFILE / LOCALAPPDATA / APPDATA of this process and of every child
  to a fresh run directory (never ~/.hermes or %LOCALAPPDATA%\\hermes);
- puts the tree first on sys.path, so the repo's own control-channel helper is the tree under test;
- runs a loopback OpenAI-compatible model stub that records every request (path, Authorization, model);
- starts real ``python -m gateway.run`` / ``python -m hermes_cli.main`` processes and kills their whole
  process tree afterwards.
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WINDOWS = os.name == 'nt'


def log(*parts):
    print(time.strftime('%H:%M:%S'), *parts, flush=True)


class Ctx:
    pass


def setup(scenario, extra_args=None, argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True, help='checkout under test')
    ap.add_argument('--out', required=True, help='directory for run directories')
    ap.add_argument('--tag', default='')
    for name, kw in (extra_args or {}).items():
        ap.add_argument(name, **kw)
    args = ap.parse_args(argv)
    ctx = Ctx()
    ctx.args, ctx.scenario = args, scenario
    ctx.root = Path(args.root).resolve()
    ctx.head = git_head(ctx.root)
    ctx.base = Path(args.out).resolve() / f'{scenario}-{ctx.head}{("-" + args.tag) if args.tag else ""}'
    if ctx.base.exists():
        shutil.rmtree(ctx.base)
    ctx.home, ctx.user, ctx.work = ctx.base / 'home', ctx.base / 'user', ctx.base / 'work'
    ctx.home.mkdir(parents=True, mode=0o700)
    for d in (ctx.user, ctx.work, ctx.base / 'localappdata', ctx.base / 'appdata'):
        d.mkdir(parents=True, exist_ok=True)
    os.environ.update(HERMES_HOME=str(ctx.home), HOME=str(ctx.user), USERPROFILE=str(ctx.user),
                      LOCALAPPDATA=str(ctx.base / 'localappdata'), APPDATA=str(ctx.base / 'appdata'))
    sys.path.insert(0, str(ctx.root))
    os.chdir(ctx.root)
    ctx.results = {'scenario': scenario, 'head': ctx.head, 'platform': sys.platform,
                   'python': sys.version.split()[0], 'checks': []}
    ctx.procs = []
    log(f'[{scenario}] head={ctx.head} base={ctx.base}')
    return ctx


def git_head(root):
    return subprocess.run(['git', '-C', str(root), 'rev-parse', '--short=10', 'HEAD'], capture_output=True,
                          text=True).stdout.strip()


def check(ctx, name, ok, **detail):
    ctx.results['checks'].append({'check': name, 'ok': bool(ok), **detail})
    log(('PASS ' if ok else 'FAIL ') + name, json.dumps(detail, default=str)[:600])
    return ok


# ---------------------------------------------------------------- model stub
def last_user_text(body):
    for m in reversed(body.get('messages') or []):
        if m.get('role') == 'user':
            c = m.get('content')
            if isinstance(c, list):
                return ' '.join(p.get('text', '') for p in c if isinstance(p, dict))
            return c or ''
    return ''


class FakeModel(BaseHTTPRequestHandler):
    """OpenAI-compatible /v1/chat/completions (stream or not) replying ``REPLY <last user text>``;
    ``server.behavior(body)`` may return {'text': ...} instead."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self._send(200, json.dumps({'object': 'list', 'data': [{'id': 'fake-model', 'object': 'model'}]}).encode())

    def _send(self, status, payload, kind='application/json'):
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
        text = last_user_text(body)
        system = ' '.join(str(m.get('content', '')) for m in body.get('messages') or [] if m.get('role') == 'system')
        entry = {'t': time.time(), 'path': self.path, 'auth': self.headers.get('Authorization'),
                 'x_api_key': self.headers.get('x-api-key'), 'model': body.get('model'),
                 'stream': bool(body.get('stream')), 'last_user': text[:200], 'system': system[:160],
                 'tools': len(body.get('tools') or [])}
        with srv.lock:
            srv.requests.append(entry)
        spec = (srv.behavior(body) if srv.behavior else None) or {'text': 'REPLY ' + text[:80]}
        if body.get('stream'):
            return self._stream(spec['text'])
        payload = json.dumps({'id': 'fake', 'object': 'chat.completion', 'model': body.get('model'),
                              'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': spec['text']},
                                           'finish_reason': 'stop'}],
                              'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
        self._send(200, payload)

    def _stream(self, text):
        def frame(delta, finish=None):
            return ('data: ' + json.dumps({'id': 'fake', 'object': 'chat.completion.chunk', 'choices': [
                {'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n').encode()
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(frame({'role': 'assistant', 'content': ''}))
            for i in range(0, len(text), 8):
                self.wfile.write(frame({'content': text[i:i + 8]}))
                self.wfile.flush()
            self.wfile.write(frame({}, 'stop'))
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
    srv.behavior, srv.requests, srv.lock = behavior, [], threading.Lock()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.url = f'http://127.0.0.1:{srv.server_port}/v1'
    log('model stub at', srv.url)
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------- home + processes
def write_home(ctx, cfg, *, models=('fake-model',)):
    (ctx.home / 'config.yaml').write_text(json.dumps(cfg, indent=1))
    (ctx.home / 'models_dev_cache.json').write_text(json.dumps({'custom': {'id': 'custom', 'models': {
        name: {'id': name, 'name': name, 'limit': {'context': 1_000_000, 'output': 4000}} for name in models}}}))
    ctx.config = cfg


def base_env(ctx, **extra):
    keep = ('PATH', 'LANG', 'TZ', 'SYSTEMROOT', 'SystemRoot', 'COMSPEC', 'PATHEXT', 'WINDIR', 'TEMP', 'TMP',
            'TMPDIR', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(HOME=str(ctx.user), USERPROFILE=str(ctx.user), HERMES_HOME=str(ctx.home), PYTHONPATH=str(ctx.root),
               LOCALAPPDATA=str(ctx.base / 'localappdata'), APPDATA=str(ctx.base / 'appdata'),
               PYTHONUNBUFFERED='1')
    env.update({k: str(v) for k, v in extra.items()})
    return env


def popen(ctx, argv, *, env, logname, cwd=None, detached=False):
    """Start a child in its own process group (POSIX session / Windows group), logged to a file."""
    fh = (ctx.base / logname).open('w', encoding='utf-8', errors='replace')
    kw = {}
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if detached:
            flags |= subprocess.DETACHED_PROCESS  # no console, as a scheduled task
        kw['creationflags'] = flags
    else:
        kw['start_new_session'] = True  # no controlling terminal, as under cron
    proc = subprocess.Popen(argv, cwd=cwd or ctx.root, env=env, stdin=subprocess.DEVNULL, stdout=fh,
                            stderr=subprocess.STDOUT, **kw)
    proc.logfile = fh
    ctx.procs.append(proc)
    return proc


def kill_tree(proc):
    if proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGINT)
            proc.wait(timeout=20)
        except (ProcessLookupError, subprocess.TimeoutExpired, PermissionError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def control(home, verb, params=None):
    """Bootstrap control channel (as tests/gateway/test_normal_runtime_boot.py::control): private named
    pipe on Windows, AF_UNIX elsewhere. Inlined so the runtime interpreter (no pytest) can run it."""
    home = Path(home)
    request = json.dumps({'protocol': 1, 'id': 1, 'verb': verb, 'params': params or {}}).encode() + b'\n'
    if WINDOWS:
        from gateway.runtime_bootstrap_windows import query_runtime_control
        raw = query_runtime_control(home, request, 2)
    else:
        pointer = home / 'gateway.sock.path'
        path = pointer.read_text(encoding='utf-8').strip() if pointer.exists() else str(home / 'gateway.sock')
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(2)
            peer.connect(path)
            peer.sendall(request)
            with peer.makefile('rb') as stream:
                raw = stream.readline()
    reply = json.loads(raw.split(b'\n', 1)[0])
    if not reply.get('ok'):
        raise ValueError(reply)
    return reply['result']


def wait_ready(home, proc=None, timeout=150):
    deadline, desc = time.monotonic() + timeout, {}
    while time.monotonic() < deadline and (proc is None or proc.poll() is None):
        try:
            desc = control(home, 'identify')
            if desc.get('state') == 'ready':
                return desc
        except (OSError, ValueError, AssertionError):
            pass
        time.sleep(.2)
    return desc


@contextmanager
def gateway(ctx, env, *, name='gateway', args=(), python=None, root=None):
    """``python -m gateway.run``; ``python``/``root`` run another tree's build on the same home."""
    proc = popen(ctx, [python or sys.executable, '-m', 'gateway.run', *args], env=env, logname=f'{name}.log',
                 cwd=root)
    try:
        desc = wait_ready(ctx.home, proc)
        if desc.get('state') != 'ready':
            proc.logfile.flush()
            raise RuntimeError(f'{name} not ready (rc={proc.poll()}):\n'
                               + (ctx.base / f'{name}.log').read_text(errors='replace')[-4000:])
        log(f'{name} ready pid={proc.pid} api_origin={desc.get("api_origin")}')
        yield proc, desc
    finally:
        kill_tree(proc)
        log(f'{name} stopped rc={proc.returncode}')


def ws_call(home, desc, method, params, timeout=120):
    """One JSON-RPC call over the gateway WebSocket with an interactive session ticket."""
    import asyncio
    from websockets.asyncio.client import connect

    async def run():
        grant = control(home, 'session-ticket', {'profile_id': str(home), 'instance_id': desc['instance_id'],
                                                 'purpose': 'interactive'})
        async with connect(desc['api_origin'].replace('http:', 'ws:') + '/api/ws', max_size=2 ** 24,
                           subprotocols=['hermes-gateway-v1', 'hermes-gateway-ticket.' + grant['ticket']]) as ws:
            await ws.send(json.dumps({'jsonrpc': '2.0', 'id': 'c1', 'method': method, 'params': params}))
            async with asyncio.timeout(timeout):
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get('id') == 'c1':
                        return msg
    return asyncio.run(run())


def stop_all(ctx):
    for proc in ctx.procs:
        kill_tree(proc)
    # A CLI may have spawned an idle-exit gateway of its own for this home: stop it too.
    try:
        desc = control(ctx.home, 'identify')
        pid = desc.get('pid')
        if pid:
            if WINDOWS:
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True)
            else:
                os.kill(int(pid), signal.SIGINT)
            log('stopped a gateway the CLI started, pid', pid)
    except Exception:  # noqa: BLE001 - nothing listening is the normal case
        pass


def save(ctx, extra=None):
    data = dict(ctx.results, **(extra or {}))
    data['passed'] = all(c['ok'] for c in data['checks'])
    out = ctx.base / 'result.json'
    out.write_text(json.dumps(data, indent=1, default=str))
    log('result', out, 'passed' if data['passed'] else 'FAILED')
    return data


def grep(path, *needles, limit=30):
    try:
        lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    return [line[:300] for line in lines if any(n in line for n in needles)][-limit:]
