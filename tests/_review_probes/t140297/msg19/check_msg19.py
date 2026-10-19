"""#140297 section 1, MSG-19 (#140319): a Bot Chat held by an older Desktop backend.

One Hermes home, two builds, as on an upgraded multi-profile Mac:
- Phase A (pre-upgrade, ``--old-root``/``--old-python``: a tree before #106742): the Desktop pool
  backend for the secondary profile ``beta`` (``hermes --profile beta serve --host 127.0.0.1 --port 0``
  with ``HERMES_DESKTOP=1``, as Desktop's ``main.ts`` spawns it). A scripted window speaks the same
  ``/api/ws`` JSON-RPC the old Desktop Bot Mode plugin sends (``hermes-bots/canonical-chat.ts``):
  ``session.create`` titled "Bot Chat", ``session.title``, then the kickoff ``prompt.submit``. That
  classic prompt path claims the ``desktop`` lease on the legacy Bot Chat; the window stays open.
- Phase B (this tree): the gateway (``python -m gateway.run``, ``gateway.multiplex_profiles: true``)
  starts on the same home. A default-profile Bot Chat turn (``hermes chat -c "Bot Chat"
  --create-if-missing -q``) runs ``message_agent(target='beta')`` through the real tool path; the
  model is a loopback stub that emits that tool call and echoes the tool's answer back.
- Then the window closes (ws disconnect; the backend process too if the lease outlives it), the
  same send runs again, and a same-id attempt / replay of whatever the first send left is tried.
"""
import json
import os
import queue
import re
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib140297 as L  # noqa: E402

CHAT = 'Bot Chat'


# ---------------------------------------------------------------- model stub with tool calls
class ToolModel(L.FakeModel):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            body = {}
        srv = self.server
        msgs = body.get('messages') or []
        last = msgs[-1] if msgs else {}
        tools = [((t.get('function') or {}).get('name')) for t in body.get('tools') or []]
        text = L.last_user_text(body)
        system = ' '.join(str(m.get('content', '')) for m in msgs if m.get('role') == 'system')
        spec = decide(last, tools, text)
        entry = {'t': time.time(), 'model': body.get('model'), 'stream': bool(body.get('stream')),
                 'last_role': last.get('role'), 'last_user': text[:300], 'tools_has_message_agent':
                 'message_agent' in tools, 'n_tools': len(tools), 'system_head': system[:120],
                 'tool_result': (str(last.get('content'))[:4000] if last.get('role') == 'tool' else None),
                 'answer': spec}
        with srv.lock:
            srv.requests.append(entry)
        if 'tool_call' in spec:
            return self._tool_call(body, spec['tool_call'])
        if body.get('stream'):
            return self._stream(spec['text'])
        payload = json.dumps({'id': 'fake', 'object': 'chat.completion', 'model': body.get('model'),
                              'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': spec['text']},
                                           'finish_reason': 'stop'}],
                              'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
        self._send(200, payload)

    def _tool_call(self, body, call):
        call_id = 'call_' + secrets.token_hex(6)
        args = json.dumps(call['arguments'])
        if not body.get('stream'):
            payload = json.dumps({'id': 'fake', 'object': 'chat.completion', 'model': body.get('model'),
                                  'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
                                      'role': 'assistant', 'content': None, 'tool_calls': [{
                                          'id': call_id, 'type': 'function',
                                          'function': {'name': call['name'], 'arguments': args}}]}}],
                                  'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
            return self._send(200, payload)

        def frame(delta, finish=None):
            return ('data: ' + json.dumps({'id': 'fake', 'object': 'chat.completion.chunk', 'choices': [
                {'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n').encode()
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(frame({'role': 'assistant', 'content': None, 'tool_calls': [{
                'index': 0, 'id': call_id, 'type': 'function', 'function': {'name': call['name'], 'arguments': ''}}]}))
            self.wfile.write(frame({'tool_calls': [{'index': 0, 'function': {'arguments': args}}]}))
            self.wfile.write(frame({}, 'tool_calls'))
            self.wfile.write(('data: ' + json.dumps({'id': 'fake', 'object': 'chat.completion.chunk', 'choices': [],
                                                     'usage': {'prompt_tokens': 10, 'completion_tokens': 5,
                                                               'total_tokens': 15}}) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def decide(last, tools, text):
    if last.get('role') == 'tool':
        return {'text': 'TOOL-ANSWER ' + str(last.get('content'))[:3000]}
    match = re.search(r'MSG19-SEND-(\w+)', text or '')
    if match and last.get('role') == 'user':
        if 'message_agent' not in tools:
            return {'text': 'NO-MESSAGE-AGENT-TOOL'}
        return {'tool_call': {'name': 'message_agent',
                              'arguments': {'target': 'beta', 'message': f'ping {match.group(1)} from hermes'}}}
    return {'text': 'REPLY ' + (text or '')[:120]}


def tool_model():
    from contextlib import contextmanager
    from http.server import ThreadingHTTPServer

    @contextmanager
    def run():
        srv = ThreadingHTTPServer(('127.0.0.1', 0), ToolModel)
        srv.daemon_threads = True
        srv.behavior, srv.requests, srv.lock = None, [], threading.Lock()
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        srv.url = f'http://127.0.0.1:{srv.server_port}/v1'
        L.log('tool model stub at', srv.url)
        try:
            yield srv
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=5)
    return run()


# ---------------------------------------------------------------- the pre-upgrade Desktop window
class DesktopWindow:
    """A scripted Desktop window on the pre-upgrade backend's ``/api/ws`` (JSON-RPC 2.0)."""

    def __init__(self, port, token, log_path):
        from websockets.sync.client import connect
        self.ws = connect(f'ws://127.0.0.1:{port}/api/ws?token={token}', open_timeout=30, max_size=None,
                          proxy=None)
        self.replies, self.events, self.next_id = {}, [], 0
        self.cond = threading.Condition()
        self.log = open(log_path, 'a', encoding='utf-8')
        self.closed = False
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for raw in self.ws:
                self.log.write(raw if isinstance(raw, str) else raw.decode('utf-8', 'replace'))
                self.log.write('\n')
                self.log.flush()
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                with self.cond:
                    if frame.get('id') is not None and ('result' in frame or 'error' in frame):
                        self.replies[frame['id']] = frame
                    else:
                        self.events.append(frame)
                    self.cond.notify_all()
        except Exception as exc:  # noqa: BLE001 - socket closed
            self.log.write(f'# reader ended: {exc!r}\n')
        finally:
            with self.cond:
                self.closed = True
                self.cond.notify_all()

    def call(self, method, params, timeout=120):
        with self.cond:
            self.next_id += 1
            rid = self.next_id
        frame = {'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}
        self.log.write('>> ' + json.dumps(frame) + '\n')
        self.ws.send(json.dumps(frame))
        deadline = time.monotonic() + timeout
        with self.cond:
            while rid not in self.replies and not self.closed:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f'{method} timed out')
                self.cond.wait(left)
            return self.replies.get(rid)

    def wait_event(self, kind, sid, timeout=120):
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for frame in self.events:
                    params = frame.get('params') or {}
                    if params.get('type') == kind and params.get('session_id') == sid:
                        return frame
                left = deadline - time.monotonic()
                if left <= 0 or self.closed:
                    return None
                self.cond.wait(left)

    def close(self):
        self.ws.close()
        self.reader.join(timeout=10)
        self.log.close()


# ---------------------------------------------------------------- evidence readers
def leases(profile_home):
    path = Path(profile_home) / 'runtime' / 'active_sessions.json'
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    entries = data.get('entries', data) if isinstance(data, dict) else data
    return entries if isinstance(entries, list) else [entries]


def held_by_desktop(profile_home, sid):
    return [e for e in leases(profile_home) if e.get('session_id') == sid]


def query(db_path, sql, args=()):
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.Error as exc:
        return [{'error': repr(exc)}]
    finally:
        conn.close()


def bot_admissions(db_path):
    return query(db_path, "SELECT * FROM session_admissions WHERE request_id LIKE 'bot:%' ORDER BY rowid")


def dm_listing(dm_dir):
    return sorted(p.name for p in dm_dir.iterdir()) if dm_dir.is_dir() else None


def mailbox(profile_home):
    root = Path(profile_home) / 'runtime' / 'bot_live_delivery'
    out = {}
    if root.is_dir():
        for p in sorted(root.iterdir()):
            try:
                out[p.name] = json.loads(p.read_text()) if p.suffix == '.json' else '(not json)'
            except (OSError, ValueError) as exc:
                out[p.name] = repr(exc)
    return out


def log_lines(ctx, *needles):
    hits = {}
    for path in list(ctx.base.glob('*.log')) + list(ctx.home.rglob('*.log')):
        lines = L.grep(path, *needles, limit=60)
        if lines:
            hits[str(path.relative_to(ctx.base))] = lines
    return hits


def file_and_stream(logs):
    """(lines from any <home>/**/logs/gateway.log file, lines from the gateway process's own output)."""
    files = [line for name, lines in logs.items() if name.endswith('logs/gateway.log') for line in lines]
    stream = [line for name, lines in logs.items() if name == 'gateway-new.log' for line in lines]
    return files, stream


def refusal_line(line):
    return 'bot_relay.deliver refused' in line and 'runtime_coordination_required' in line


def settled_line(line, delivery_id):
    return 'settled as not sent' in line and bool(delivery_id) and delivery_id in line


def tool_answers(model, since):
    return [r['tool_result'] for r in model.requests[since:] if r.get('tool_result')]


def wait_until(pred, timeout, step=.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(step)
    return pred()


@contextmanager
def new_gateway(ctx, env):
    """The upgraded gateway: ``hermes gateway run`` (what the launchd service runs) or ``python -m gateway.run``."""
    if ctx.args.gateway_cmd == 'cli':
        argv = [sys.executable, '-m', 'hermes_cli.main', 'gateway', 'run'] + (['-v'] if ctx.args.verbose else [])
    else:
        argv = [sys.executable, '-m', 'gateway.run']
    proc = L.popen(ctx, argv, env=env, logname='gateway-new.log')
    try:
        desc = L.wait_ready(ctx.home, proc)
        if desc.get('state') != 'ready':
            proc.logfile.flush()
            raise RuntimeError(f'gateway not ready (rc={proc.poll()}):\n'
                               +(ctx.base / 'gateway-new.log').read_text(errors='replace')[-4000:])
        L.log(f'gateway ready pid={proc.pid} argv={argv[1:]}')
        ctx.results['gateway_argv'] = argv[1:]
        yield proc, desc
    finally:
        L.kill_tree(proc)
        L.log(f'gateway stopped rc={proc.returncode}')


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------- run
def send(ctx, env, model, n, tag):
    """One default-profile Bot Chat turn that calls message_agent(target='beta')."""
    since = len(model.requests)
    proc = L.subprocess.run([sys.executable, '-m', 'hermes_cli.main', 'chat', '--in', str(ctx.user), '-c', CHAT,
                             '--create-if-missing', '-Q', '-q', f'MSG19-SEND-{n}: tell beta ping {n}'],
                            cwd=ctx.work, env=env, stdin=L.subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=300)
    (ctx.base / f'send-{tag}.out').write_text(f'rc={proc.returncode}\n--stdout--\n{proc.stdout}\n--stderr--\n{proc.stderr}')
    answers = tool_answers(model, since)
    answer = None
    for raw in answers:
        try:
            answer = json.loads(raw)
        except ValueError:
            answer = {'raw': raw}
    L.log(f'send {tag}: rc={proc.returncode} answer={json.dumps(answer)[:400]}')
    return proc, answer, answers


def main():
    ctx = L.setup('msg19-held', {'--old-root': {'required': True}, '--old-python': {'required': True},
                                 '--gateway-cmd': {'choices': ['cli', 'module'], 'default': 'cli'},
                                 '--verbose': {'action': 'store_true'}})
    old_root = Path(ctx.args.old_root).resolve()
    ctx.results['old_head'] = L.git_head(old_root)
    tmp = ctx.base / 'tmp'
    tmp.mkdir()
    os.environ['TMPDIR'] = str(tmp)  # the DM folder is <tempdir>/hermes-dm-<uid>: keep it in this run
    dm_dir = tmp / f'hermes-dm-{os.getuid()}'
    beta = ctx.home / 'profiles' / 'beta'
    beta.mkdir(parents=True, mode=0o700)
    beta_db = beta / 'state.db'
    evidence = {}
    window = None
    try:
        with tool_model() as model:
            cfg = {'gateway': {'multiplex_profiles': True},
                   'model': {'provider': 'custom', 'default': 'fake-model', 'base_url': model.url},
                   'auxiliary': {'title_generation': {'enabled': False}},
                   'terminal': {'cwd': str(ctx.work)}}
            L.write_home(ctx, cfg)
            (beta / 'config.yaml').write_text(json.dumps(cfg, indent=1))
            for home, title in ((ctx.home, 'Hermes'), (beta, 'Beta')):
                (home / '.env').write_text('OPENAI_API_KEY=loopback-only\n')
                # What Desktop's Bot Mode writes: ui_meta['hermes-bots'] marks the profile Bot-Mode-managed.
                (home / 'profile.yaml').write_text(json.dumps({'ui_meta': {'hermes-bots': {'title': title}}}))
            (beta / 'models_dev_cache.json').write_text((ctx.home / 'models_dev_cache.json').read_text())

            # ---------------- Phase A: the pre-upgrade Desktop backend holds beta's legacy Bot Chat
            token = secrets.token_urlsafe(32)
            ready = ctx.base / 'desktop-ready.json'
            env_old = L.base_env(ctx, OPENAI_API_KEY='loopback-only', PYTHONPATH=str(old_root), TMPDIR=str(tmp),
                                 HERMES_DESKTOP='1', HERMES_DASHBOARD_SESSION_TOKEN=token,
                                 HERMES_PARENT_PID=str(os.getpid()), HERMES_DESKTOP_READY_FILE=str(ready))
            backend = L.popen(ctx, [ctx.args.old_python, '-m', 'hermes_cli.main', '--profile', 'beta', 'serve',
                                    '--host', '127.0.0.1', '--port', '0'], env=env_old,
                              logname='desktop-backend-old.log', cwd=old_root)
            got = wait_until(lambda: ready.is_file() and json.loads(ready.read_text() or '{}').get('port'), 180)
            if not got:
                backend.logfile.flush()
                raise RuntimeError('old Desktop backend not ready:\n'
                                   + (ctx.base / 'desktop-backend-old.log').read_text(errors='replace')[-4000:])
            port = int(got)
            L.log('pre-upgrade Desktop backend ready pid', backend.pid, 'port', port)
            window = DesktopWindow(port, token, ctx.base / 'desktop-window-ws.jsonl')
            created = window.call('session.create', {'profile': 'beta', 'title': CHAT, 'hidden': True,
                                                     'follow_profile_config': True})
            res = (created or {}).get('result') or {}
            runtime, stored = res.get('session_id'), res.get('stored_session_id')
            titled = window.call('session.title', {'session_id': runtime, 'title': CHAT})
            submitted = window.call('prompt.submit', {'session_id': runtime,
                                                      'text': 'Bot Mode kickoff: introduce yourself (desktop turn)'})
            complete = window.wait_event('message.complete', runtime, timeout=180)
            row = query(beta_db, 'SELECT id, source, title, hidden, ended_at, end_reason, model FROM sessions WHERE title=?', (CHAT,))
            sid = row[0]['id'] if row and 'id' in row[0] else stored
            lease = held_by_desktop(beta, sid)
            evidence['phase_a'] = {'create': created, 'title': titled, 'submit': submitted,
                                   'complete': (complete or {}).get('params'), 'bot_chat_row': row,
                                   'lease': lease, 'stored_session_id': stored, 'runtime_session_id': runtime}
            L.check(ctx, 'A: the pre-upgrade Desktop backend minted a legacy desktop Bot Chat on beta',
                    bool(row) and row[0].get('source') == 'desktop' and sid == stored, row=row, stored=stored)
            L.check(ctx, 'A: its classic prompt path holds a desktop lease on that chat',
                    any(e.get('surface') == 'desktop' and int(e.get('pid') or 0) == backend.pid for e in lease),
                    lease=lease, backend_pid=backend.pid, kickoff_complete=bool(complete))

            # ---------------- Phase B: the upgraded gateway (multiplexed) on the same home
            env_new = L.base_env(ctx, OPENAI_API_KEY='loopback-only', TMPDIR=str(tmp))
            with new_gateway(ctx, env_new) as (gw, desc):
                from hermes_cli.gateway_runtime import discover_gateway_endpoint
                beta_ready = wait_until(lambda: discover_gateway_endpoint(beta, timeout=5).state == 'ready', 120, 1)
                L.check(ctx, 'B: the gateway serves beta (multiplexed)', beta_ready, root_desc=desc)

                proc1, ans1, raw1 = send(ctx, env_new, model, 1, 'held')
                ans1 = ans1 or {}
                delivery1 = ans1.get('delivery_id')
                evidence['send_held'] = {'rc': proc1.returncode, 'stdout': proc1.stdout[-2000:],
                                         'stderr': proc1.stderr[-2000:], 'tool_answers': raw1}
                evidence['after_held'] = {
                    'dm_folder': dm_listing(dm_dir), 'mailbox': mailbox(beta),
                    'admissions': bot_admissions(beta_db), 'lease': held_by_desktop(beta, sid),
                    'bot_chat_messages': query(beta_db, 'SELECT session_id, role, substr(content,1,200) AS content '
                                                        'FROM messages WHERE session_id=? ORDER BY id', (sid,)),
                    'log_lines': log_lines(ctx, 'Bot Chat delivery', 'runtime_coordination_required', 'settle',
                                           *( [delivery1] if delivery1 else []))}
                L.check(ctx, 'held: the tool ran (message_agent reached the model as a tool result)', bool(raw1),
                        stdout=proc1.stdout[-300:], stderr=proc1.stderr[-600:])
                err = str(ans1.get('error') or '')
                L.check(ctx, "held: answer says 'held by another session writer ... NOT sent'",
                        'held by another session writer' in err and 'NOT sent' in err, answer=ans1)
                L.check(ctx, "held: answer is not 'ambiguous ... Do not resend'",
                        ans1.get('status') != 'ambiguous' and 'Do not resend' not in err, answer=ans1)
                left = [n for n in (evidence['after_held']['dm_folder'] or [])
                        if n.startswith('dm-') and (n.endswith('.txt') or n.endswith('.live.json'))]
                L.check(ctx, 'held: no dm-*.txt or *.live.json left in the DM folder',
                        evidence['after_held']['dm_folder'] is not None and not left,
                        dm_dir=str(dm_dir), listing=evidence['after_held']['dm_folder'])
                receipt = evidence['after_held']['mailbox'].get(f'{delivery1}.json') if delivery1 else None
                L.check(ctx, 'held: a refused <id>.json receipt in the mailbox',
                        isinstance(receipt, dict) and receipt.get('refused') is True and receipt.get('status') == 'failed'
                        and not receipt.get('admission_id'), receipt=receipt, mailbox=list(evidence['after_held']['mailbox']))
                logs = evidence['after_held']['log_lines']
                files, stream = file_and_stream(logs)
                L.check(ctx, "held: gateway.log (file) has the refusal line",
                        any(refusal_line(line) for line in files), file_lines=files, stream_lines=stream)
                L.check(ctx, "held: gateway.log (file) has 'settled as not sent'",
                        any(settled_line(line, delivery1) for line in files), file_lines=files)
                L.check(ctx, "held: the gateway's own output has the refusal line (WARNING)",
                        any(refusal_line(line) for line in stream), stream_lines=stream)
                if ctx.args.verbose:
                    L.check(ctx, "held: the gateway's -v output has 'settled as not sent' (INFO)",
                            any(settled_line(line, delivery1) for line in stream), stream_lines=stream)
                L.check(ctx, 'held: nothing admitted on beta', evidence['after_held']['admissions'] == [],
                        admissions=evidence['after_held']['admissions'])
                L.check(ctx, 'held: the Desktop lease is still there', bool(evidence['after_held']['lease']),
                        lease=evidence['after_held']['lease'])

                # ---------------- the window closes
                window.close()
                window = None
                released = wait_until(lambda: not held_by_desktop(beta, sid), 30)
                how = 'window (ws) closed'
                if not released:
                    L.kill_tree(backend)
                    released = wait_until(lambda: not held_by_desktop(beta, sid), 30)
                    how = 'window closed and the Desktop backend process exited'
                evidence['close'] = {'how': how, 'released': bool(released), 'leases': leases(beta),
                                     'bot_chat_row': query(beta_db, 'SELECT id, source, ended_at, end_reason FROM sessions WHERE id=?', (sid,))}
                L.check(ctx, 'close: the Desktop lease is released', bool(released), how=how, leases=leases(beta))

                # ---------------- resend after the window closed
                proc2, ans2, raw2 = send(ctx, env_new, model, 2, 'resend')
                ans2 = ans2 or {}
                delivery2 = ans2.get('delivery_id')
                beta_turn = wait_until(lambda: [r for r in model.requests if 'ping 2 from hermes' in (r['last_user'] or '')
                                                and r['last_role'] == 'user'], 90)
                time.sleep(3)
                evidence['send_resend'] = {'rc': proc2.returncode, 'stdout': proc2.stdout[-2000:],
                                           'stderr': proc2.stderr[-2000:], 'tool_answers': raw2}
                L.check(ctx, 'resend: queued for the live owner', ans2.get('status') in {'queued', 'claimed', 'settled'}
                        and delivery2 and delivery2 != delivery1, answer=ans2)
                # ---------------- a same-id attempt / replay of what the held send left
                replay = {}
                kept = [n for n in (dm_listing(dm_dir) or []) if n.startswith('dm-') and n.endswith('.txt')]
                if delivery1 and receipt:
                    from tools.bot_live_delivery import deliver_to_live_owner, find_canonical_live_owner
                    try:
                        replay['same_id_deliver'] = deliver_to_live_owner(
                            beta, find_canonical_live_owner(beta), receipt['message'], delivery_id=delivery1,
                            author=receipt.get('author'))
                    except Exception as exc:  # noqa: BLE001
                        replay['same_id_deliver'] = {'error': repr(exc), 'data': getattr(exc, 'data', None)}
                for name in kept:  # a pre-fix tree keeps the payload + intent: run its runner once
                    dm_file = dm_dir / name
                    intent = json.loads(Path(str(dm_file) + '.live.json').read_text()) \
                        if Path(str(dm_file) + '.live.json').exists() else {}
                    argv = [sys.executable, str(ctx.root / 'tools' / 'bot_mode_dm.py'), '--run-delivery']
                    if intent.get('author'):
                        argv += ['--author', json.dumps(intent['author'], separators=(',', ':'))]
                    argv += ['query-file', str(dm_file), '--profile-home', str(beta), 'hermes', '-p', 'beta']
                    run = L.subprocess.run(argv, cwd=ctx.root, env=env_new, capture_output=True, text=True, timeout=400)
                    replay[f'runner {name}'] = {'rc': run.returncode, 'stdout': run.stdout[-1500:], 'stderr': run.stderr[-1500:]}
                time.sleep(3)
                admissions = bot_admissions(beta_db)
                dm_turns = [r for r in model.requests if r['last_role'] == 'user'
                            and re.search(r'ping [12] from hermes', r['last_user'] or '') and 'MSG19' not in r['last_user']]
                evidence['after_resend'] = {
                    'dm_folder': dm_listing(dm_dir), 'mailbox': mailbox(beta), 'admissions': admissions,
                    'replay': replay, 'beta_dm_turns': dm_turns,
                    'bot_chat_messages': query(beta_db, 'SELECT session_id, role, substr(content,1,200) AS content '
                                                        'FROM messages WHERE content LIKE ? ORDER BY id', ('%from hermes%',)),
                    'log_lines': log_lines(ctx, 'Bot Chat delivery', 'runtime_coordination_required',
                                           *([d for d in (delivery1, delivery2) if d]))}
                ids = [a.get('request_id') for a in admissions]
                L.check(ctx, 'resend: admitted exactly once (one bot:<id> row, one beta turn for it)',
                        ids.count(f'bot:{delivery2}') == 1 and len([t for t in dm_turns if 'ping 2' in t['last_user']]) == 1,
                        admissions=ids, beta_turns=[t['last_user'][:100] for t in dm_turns], first_turn=bool(beta_turn))
                L.check(ctx, 'resend: the refused id was never admitted (same-id attempt / replay included)',
                        f'bot:{delivery1}' not in ids and not [t for t in dm_turns if 'ping 1' in t['last_user']],
                        admissions=ids, replay=replay)
    except Exception as exc:  # noqa: BLE001 - recorded
        import traceback
        L.check(ctx, 'run', False, error=traceback.format_exc()[-3000:])
    finally:
        if window is not None:
            try:
                window.close()
            except Exception:  # noqa: BLE001
                pass
        L.stop_all(ctx)
        (ctx.base / 'evidence.json').write_text(json.dumps(evidence, indent=1, default=str, ensure_ascii=False))
        try:
            (ctx.base / 'requests.json').write_text(json.dumps(model.requests, indent=1, default=str, ensure_ascii=False))
        except NameError:
            pass
    data = L.save(ctx)
    print(json.dumps(data, indent=1, default=str, ensure_ascii=False)[:12000])


if __name__ == '__main__':
    main()
