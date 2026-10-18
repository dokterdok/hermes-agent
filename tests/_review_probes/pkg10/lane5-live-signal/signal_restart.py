"""Live retest of dokterdok's #3 on #140973 (package 10, lane 5): a slow video, then a text, then /restart,
on a Signal bot.

Real processes: ``python -m gateway.run`` (the tree's own test-env interpreter, its own process) with the
real Signal adapter (``plugins/platforms/signal/adapter.py``), under a supervisor loop that relaunches it
on exit 75 the way launchd/systemd would. Only the Signal network is replaced: ``fake_signal_cli.py`` is
a separate process speaking signal-cli's HTTP-daemon API. The model is livelib's loopback fake.

Flow: the fake phone injects, back to back on the SSE stream, video A (``video/mp4`` attachment of
--mib MiB, served by ``getAttachment`` as base64), then B (text, or a small video with --b-kind video),
then ``/restart``. A poller reads ``state.db`` read-only every 20 ms. After the relaunch the run waits
for the queue to settle, then records rows, model prompts, the fake daemon's ``send`` RPCs, logs and
the native-inputs store. Big media files are deleted at the end of the run (listings kept).

Usage: run.sh <lane5|lane5-r2|main> signal_restart.py --tag 1 --mib 100 [--busy queue|default]
       [--b-kind text|video]
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import livelib  # noqa: E402
from livelib import LIVE, log, record_pid, kill_tree, fake_model, write_home, env_for, grep_log  # noqa: E402
ROOT = Path(os.environ.get('PKG10_ROOT') or Path(__file__).resolve().parent.parent)  # checkouts live under ROOT/<tree>, runs under ROOT/live

TREES = {'lane5': (ROOT / 'lane5'),
         'lane5-r2': (ROOT / 'lane5-r2'),
         'main': (ROOT / 'main')}
SCRIPTS = Path(__file__).resolve().parent
ACCOUNT, PEER, PEER_UUID = '+15559999', '+15550001', 'aaaaaaaa-0000-4000-8000-000000000001'
MP4_HEAD = b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom'
MARKERS = {'A': 'VIDEO_A_SLOW', 'B_text': 'TEXT_B_AFTER_VIDEO', 'B_video': 'VIDEO_B_SMALL'}


def parse():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', choices=sorted(TREES), required=True)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--mib', type=float, required=True)
    ap.add_argument('--busy', choices=('queue', 'default'), default='queue')
    ap.add_argument('--b-kind', choices=('text', 'video'), default='text')
    ap.add_argument('--b-mib', type=float, default=0.25)
    ap.add_argument('--keep-media', action='store_true')
    ap.add_argument('--gap', default='capture',
                    help="'capture': send A, then B + /restart back to back as soon as A's capture staging file "
                         "(.capture-*) exists; or seconds between A and B (0 = all three in one injection)")
    return ap.parse_args()


def setup(args):
    ctx = livelib.Ctx()
    ctx.args, ctx.tree, ctx.scenario = args, args.tree, f'signal-restart-{args.tag}'
    ctx.root = TREES[args.tree]
    ctx.base = LIVE / f'signal-restart-{args.tree}-{args.tag}'
    if ctx.base.exists():
        shutil.rmtree(ctx.base)
    ctx.home, ctx.user, ctx.work = ctx.base / 'home', ctx.base / 'user', ctx.base / 'work'
    ctx.home.mkdir(parents=True, mode=0o700)
    for d in (ctx.user, ctx.work, ctx.base / 'localappdata', ctx.base / 'appdata', ctx.base / 'phone',
              ctx.base / 'locks'):
        d.mkdir(parents=True, exist_ok=True)
    os.environ.update(HERMES_HOME=str(ctx.home), HOME=str(ctx.user), USERPROFILE=str(ctx.user),
                      LOCALAPPDATA=str(ctx.base / 'localappdata'), APPDATA=str(ctx.base / 'appdata'))
    sys.path.insert(0, str(ctx.root))
    os.chdir(ctx.root)
    ctx.results = {'scenario': 'signal-restart', 'tree': args.tree, 'tag': args.tag, 'head': livelib.git_head(ctx.root),
                   'mib': args.mib, 'busy': args.busy, 'b_kind': args.b_kind}
    log(f'[signal-restart] tree={args.tree} head={ctx.results["head"]} base={ctx.base}')
    return ctx


def make_video(path, mib):
    size = int(mib * 1024 * 1024)
    with open(path, 'wb') as fh:
        fh.write(MP4_HEAD)
        left = size - len(MP4_HEAD)
        while left > 0:
            n = min(left, 4 * 1024 * 1024)
            fh.write(os.urandom(n))
            left -= n
    return size


# ---------------------------------------------------------------- fake signal-cli (separate process)
class FakeSignal:
    def __init__(self, ctx, attachments):
        self.record = ctx.base / 'signal-rpc.jsonl'
        port_file = ctx.base / 'signal.port'
        cmd = [sys.executable, '-I', str(SCRIPTS / 'fake_signal_cli.py'), '--account', ACCOUNT,
               '--record', str(self.record), '--port-file', str(port_file)]
        for att_id, path in attachments.items():
            cmd += ['--attachment', f'{att_id}={path}']
        self.out = (ctx.base / 'fake-signal.log').open('w')
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=self.out, stderr=subprocess.STDOUT)
        record_pid(ctx, self.proc.pid, 'fake-signal-cli')
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('fake signal-cli did not start')
            time.sleep(.05)
        self.url = f'http://127.0.0.1:{port_file.read_text().strip()}'
        log('fake signal-cli at', self.url, 'pid', self.proc.pid)

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method='GET' if body is None else 'POST',
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read() or b'{}')

    def entries(self):
        try:
            return [json.loads(line) for line in self.record.read_text(encoding='utf-8').splitlines() if line]
        except OSError:
            return []

    def stop(self):
        try:
            self.call('/control/stop', {})
        except Exception:  # noqa: BLE001 - killed below either way
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        kill_tree(self.proc.pid)
        self.out.close()


# ---------------------------------------------------------------- supervisor (launchd/systemd stand-in)
class Supervisor(threading.Thread):
    """Relaunch ``python -m gateway.run`` on exit 75, as KeepAlive / RestartForceExitStatus=75 would."""

    def __init__(self, ctx, env, max_gens=3):
        super().__init__(daemon=True)
        self.ctx, self.env, self.max_gens = ctx, env, max_gens
        self.events, self.procs, self.stopping = [], {}, False
        self.lock = threading.Lock()

    def run(self):
        gen = 0
        while gen < self.max_gens and not self.stopping:
            gen += 1
            logpath = self.ctx.base / f'gateway-gen{gen}.log'
            with logpath.open('w', encoding='utf-8', errors='replace') as fh:
                proc = subprocess.Popen([sys.executable, '-m', 'gateway.run'], cwd=self.ctx.root, env=self.env,
                                        stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT)
                record_pid(self.ctx, proc.pid, f'gateway-gen{gen}')
                with self.lock:
                    self.procs[gen] = proc
                    self.events.append({'t': time.time(), 'event': 'launch', 'gen': gen, 'pid': proc.pid})
                rc = proc.wait()
            with self.lock:
                self.events.append({'t': time.time(), 'event': 'exit', 'gen': gen, 'rc': rc})
            log(f'supervisor: gen{gen} pid={proc.pid} exited rc={rc}')
            if rc != 75 or self.stopping:
                break
            log('supervisor: rc 75 -> relaunch')

    def event(self, kind, gen):
        with self.lock:
            return next((e for e in self.events if e['event'] == kind and e['gen'] == gen), None)

    def stop(self):
        self.stopping = True
        with self.lock:
            procs = list(self.procs.values())
        for proc in procs:
            if proc.poll() is None:
                kill_tree(proc.pid)
        self.join(timeout=30)


# ---------------------------------------------------------------- state.db poller
def summarize_payload(raw):
    try:
        p = json.loads(raw)
    except ValueError:
        return {'text': '?'}
    env = p.get('native_text_v1') or {}
    text = p.get('text') or ''
    label = next((k for k, m in MARKERS.items() if m in text), None)
    if text.strip() == '' and env.get('message_type') == 'video':
        label = 'video?'
    return {'label': label, 'text': text[:60], 'message_type': env.get('message_type', 'text'),
            'media': len(env.get('media') or [])}


class Poller(threading.Thread):
    def __init__(self, db):
        super().__init__(daemon=True)
        self.db, self.timeline, self.stopping = db, [], False
        self.errors = 0

    def read(self):
        if not self.db.exists():
            return None
        con = sqlite3.connect(f'file:{self.db.as_posix()}?mode=ro', uri=True, timeout=0.5)
        try:
            rows = con.execute('SELECT seq, request_id, status, outcome, intent, target_session_id, payload_json '
                               'FROM session_admissions ORDER BY seq').fetchall()
        finally:
            con.close()
        return tuple((seq, rid, status, outcome, intent, sid, json.dumps(summarize_payload(payload)))
                     for seq, rid, status, outcome, intent, sid, payload in rows)

    def run(self):
        last = object()
        while not self.stopping:
            try:
                rows = self.read()
            except sqlite3.Error:
                self.errors += 1
                rows = last
            if rows != last and rows is not None:
                self.timeline.append({'t': time.time(), 'rows': [
                    {'seq': r[0], 'request_id': r[1], 'status': r[2], 'outcome': r[3], 'intent': r[4],
                     'session': r[5], **json.loads(r[6])} for r in rows]})
                last = rows
            time.sleep(0.02)

    def at(self, t):
        """The last snapshot taken at or before *t*."""
        snap = None
        for entry in self.timeline:
            if entry['t'] <= t:
                snap = entry
        return snap


class CaptureWatch(threading.Thread):
    """Watches the native-inputs store: when a ``.capture-*`` staging file (an admission's media copy in
    flight) appears, how it grows, and when the content-addressed ``<sha256>/<name>`` files appear."""

    def __init__(self, root):
        super().__init__(daemon=True)
        self.root, self.stopping, self.first_staging = root, False, None
        self.seen = {}  # name -> {'first': t, 'last': t, 'size': n}

    def run(self):
        while not self.stopping:
            now = time.time()
            try:
                entries = list(os.scandir(self.root))
            except OSError:
                entries = []
            for entry in entries:
                try:
                    if entry.is_dir():
                        for sub in os.scandir(entry.path):
                            key = entry.name[:12] + '/' + sub.name
                            info = self.seen.setdefault(key, {'first': now, 'size': None})
                            info['last'], info['size'] = now, sub.stat().st_size
                    elif entry.name.startswith('.capture-'):
                        if self.first_staging is None:
                            self.first_staging = now
                        info = self.seen.setdefault(entry.name, {'first': now, 'size': None})
                        info['last'], info['size'] = now, entry.stat().st_size
                except OSError:
                    pass
            time.sleep(0.002)


# ---------------------------------------------------------------- helpers
def wait_ready(ctx, sup, sig, gen, connects_before, timeout=150):
    """Gen *gen* answers ``identify`` with state ready and opened a new SSE stream on the fake daemon."""
    from tests.gateway.test_normal_runtime_boot import control
    deadline = time.monotonic() + timeout
    desc = {}
    while time.monotonic() < deadline:
        if sup.event('exit', gen) is not None:
            raise RuntimeError(f'gen{gen} exited before ready: {sup.event("exit", gen)}')
        if sup.event('launch', gen) is not None:
            try:
                desc = control(ctx.home, 'identify')
            except (OSError, ValueError, AssertionError):
                desc = {}
            if desc.get('state') == 'ready':
                state = sig.call('/control/state')
                if state['connects'] > connects_before:
                    return time.time(), desc, state
        time.sleep(0.1)
    raise RuntimeError(f'gen{gen} not ready / SSE not connected within {timeout}s (desc={desc})')


def envelope(ts, text, attachments=None):
    data = {'timestamp': ts, 'message': text, 'expiresInSeconds': 0, 'viewOnce': False}
    if attachments:
        data['attachments'] = attachments
    return {'source': PEER, 'sourceNumber': PEER, 'sourceUuid': PEER_UUID, 'sourceName': 'Test User',
            'sourceDevice': 1, 'timestamp': ts, 'serverReceivedTimestamp': ts + 40,
            'serverDeliveredTimestamp': ts + 60, 'dataMessage': data}


def attachment(att_id, filename, size, ts):
    return {'contentType': 'video/mp4', 'filename': filename, 'id': att_id, 'size': size, 'width': 1920,
            'height': 1080, 'caption': None, 'uploadTimestamp': ts}


def classify(text):
    labels = [k for k, m in MARKERS.items() if m in (text or '')]
    return '+'.join(labels) or None


def transcript(db):
    """Every stored message (session, role, marker label, first 160 chars): what each turn really ran."""
    try:
        con = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True, timeout=2)
        try:
            rows = con.execute('SELECT id, session_id, role, content FROM messages ORDER BY id').fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        return repr(exc)
    return [{'id': i, 'session': sid, 'role': role, 'label': classify(content), 'content': (content or '')[:160]}
            for i, sid, role, content in rows]


def list_tree(root):
    out = []
    if root.exists():
        for p in sorted(root.rglob('*')):
            out.append({'path': str(p.relative_to(root)), 'dir': p.is_dir(),
                        'size': None if p.is_dir() else p.stat().st_size})
    return out


def main():
    args = parse()
    ctx = setup(args)
    res = ctx.results
    phone = ctx.base / 'phone'
    a_id, b_id = 'Xa1Vid3oAttachmentA0.mp4', 'Yb2Vid3oAttachmentB0.mp4'
    a_size = make_video(phone / 'clip_A.mp4', args.mib)
    attachments = {a_id: str(phone / 'clip_A.mp4')}
    b_size = None
    if args.b_kind == 'video':
        b_size = make_video(phone / 'clip_B.mp4', args.b_mib)
        attachments[b_id] = str(phone / 'clip_B.mp4')
    res.update(a_size=a_size, b_size=b_size)
    sig = FakeSignal(ctx, attachments)
    sup = poller = capw = None
    t_ready1 = t_inject = t_ready2 = t_end = None
    model_requests = []
    ts = 0
    try:
        with fake_model() as model:
            cfg_extra = {'platforms': {'signal': {'enabled': True, 'extra': {'http_url': sig.url, 'account': ACCOUNT}}},
                         'logging': {'level': 'DEBUG', 'max_size_mb': 500, 'backup_count': 5}}
            if args.busy == 'queue':
                cfg_extra['display'] = {'busy_input_mode': 'queue'}  # the documented key; busy_text_mode follows
            write_home(ctx, model.url, config_extra=cfg_extra)
            (ctx.home / '.env').write_text(f'SIGNAL_ALLOWED_USERS={PEER}\n')
            res['config'] = ctx.config
            env = env_for(ctx, model.url, SIGNAL_ALLOWED_USERS=PEER, HERMES_GATEWAY_EXTERNAL_SUPERVISOR='1',
                          HERMES_GATEWAY_LOCK_DIR=str(ctx.base / 'locks'))
            poller = Poller(ctx.home / 'state.db')
            poller.start()
            capw = CaptureWatch(ctx.home / 'cache' / 'documents' / 'native-inputs')
            capw.start()
            sup = Supervisor(ctx, env)
            sup.start()
            try:
                t_ready1, desc1, st1 = wait_ready(ctx, sup, sig, 1, 0)
                log(f'gen1 ready, SSE connected ({st1})')
                time.sleep(1.0)  # let startup housekeeping settle before the burst
                ts = int(time.time() * 1000)
                env_a = envelope(ts, MARKERS['A'] + ' caption', [attachment(a_id, 'clip_A.mp4', a_size, ts)])
                if args.b_kind == 'video':
                    env_b = envelope(ts + 1, MARKERS['B_video'] + ' caption',
                                     [attachment(b_id, 'clip_B.mp4', b_size, ts + 1)])
                else:
                    env_b = envelope(ts + 1, MARKERS['B_text'])
                env_r = envelope(ts + 2, '/restart')
                t_inject = time.time()
                if args.gap == '0':
                    res['inject'] = [sig.call('/control/inject', {'envelopes': [env_a, env_b, env_r]})]
                    log('injected A, B, /restart in one go', res['inject'])
                else:
                    res['inject'] = [sig.call('/control/inject', {'envelopes': [env_a]})]
                    if args.gap == 'capture':
                        deadline = time.monotonic() + 60
                        while not capw.first_staging and time.monotonic() < deadline:
                            time.sleep(0.002)
                        res['trigger'] = {'kind': 'capture', 'staging_seen_dt': capw.first_staging and round(
                            capw.first_staging - t_inject, 3)}
                    else:
                        time.sleep(float(args.gap))
                        res['trigger'] = {'kind': 'sleep', 'seconds': float(args.gap)}
                    t_br = time.time()
                    res['inject'].append(sig.call('/control/inject', {'envelopes': [env_b, env_r]}))
                    res['trigger']['b_injected_dt'] = round(t_br - t_inject, 3)
                    log('injected A, then B + /restart', res['inject'], res['trigger'])
                deadline = time.monotonic() + 240  # gen1 should exit 75 after the restart drain
                while sup.event('exit', 1) is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                exit1 = sup.event('exit', 1)
                if exit1 is None:
                    raise RuntimeError('gen1 did not exit within 240 s of /restart')
                if exit1['rc'] != 75:
                    raise RuntimeError(f'gen1 exited rc={exit1["rc"]}, not 75')
                t_ready2, desc2, st2 = wait_ready(ctx, sup, sig, 2, sig.call('/control/state')['connects'])
                log(f'gen2 ready ({st2})')
                # settle: every admission terminal, and model + fake phone + rows unchanged for 8 s
                deadline = time.monotonic() + 120
                quiet_since, last_sig = time.monotonic(), None
                while time.monotonic() < deadline:
                    rows = poller.timeline[-1]['rows'] if poller.timeline else []
                    pending = [r for r in rows if r['status'] != 'terminal']
                    marker = (len(model.requests), len(sig.entries()), json.dumps(rows))
                    if marker != last_sig:
                        last_sig, quiet_since = marker, time.monotonic()
                    if not pending and time.monotonic() - quiet_since > 8:
                        break
                    time.sleep(0.25)
                res['settled'] = time.monotonic() < deadline
            except Exception as exc:  # noqa: BLE001 - recorded; partial results are still written
                res['error'] = repr(exc)
                log('ERROR', repr(exc))
            t_end = time.time()
            model_requests = [dict(r, full_last_user=livelib.last_user_text(b))
                              for r, b in zip(list(model.requests), list(model.bodies))]
    finally:
        if sup is not None:
            sup.stop()
        if poller is not None:
            poller.stopping = True
            poller.join(timeout=5)
        if capw is not None:
            capw.stopping = True
            capw.join(timeout=5)
        sig.stop()
    # ---------------------------------------------------------------- results
    exit1 = sup.event('exit', 1) if sup else None
    t_exit1 = exit1['t'] if exit1 else time.time()
    res['supervisor'] = sup.events if sup else None
    t_inject = t_inject or time.time()
    res['t'] = {'ready1': t_ready1, 'inject': t_inject, 'exit1': t_exit1, 'ready2': t_ready2, 'end': t_end}
    res['rows_before_restart_exit'] = poller.at(t_exit1)
    launch2 = sup.event('launch', 2) if sup else None
    res['rows_at_relaunch'] = poller.at(launch2['t'] + 0.5) if launch2 else None
    res['rows_final'] = poller.timeline[-1] if poller.timeline else None
    res['row_timeline'] = [{'dt': round(e['t'] - t_inject, 3), 'rows': [
        (r['seq'], r['label'], r['status'], r['outcome']) for r in e['rows']]} for e in poller.timeline]
    res['poller_errors'] = poller.errors
    # model prompts
    reqs = []
    for r in model_requests:
        gen = 2 if launch2 and r['t'] >= launch2['t'] else 1
        full = r.get('full_last_user') or r['last_user']
        reqs.append({'dt': round(r['t'] - t_inject, 3), 'gen': gen, 'label': classify(full),
                     'last_user': full[:400], 'n_messages': r['n_messages'], 'summary': r['summary']})
    res['model_requests'] = reqs
    res['model_order'] = [r['label'] for r in reqs if r['label']]
    # fake phone: what the user sees
    entries = sig.entries()
    sends, reactions, other = [], [], {}
    for e in entries:
        if e.get('kind') != 'rpc':
            continue
        p = e.get('params') or {}
        gen = 2 if launch2 and e['t'] >= launch2['t'] else 1
        if e['method'] == 'send':
            sends.append({'dt': round(e['t'] - t_inject, 3), 'gen': gen, 'message': p.get('message'),
                          'attachments': p.get('attachments'), 'recipient': p.get('recipient')})
        elif e['method'] == 'sendReaction':
            reactions.append({'dt': round(e['t'] - t_inject, 3), 'gen': gen, 'emoji': p.get('emoji'),
                              'remove': p.get('remove', False), 'targetTimestamp': p.get('targetTimestamp')})
        else:
            other[e['method']] = other.get(e['method'], 0) + 1
    res['user_sees'] = sends
    res['reactions'] = reactions
    res['other_rpcs'] = other
    res['getAttachment'] = [{'dt': round(e['t'] - t_inject, 3), 'id': (e.get('params') or {}).get('id'),
                             'b64_chars': e.get('data_b64_chars'), 'duration_s': e.get('duration_s')}
                            for e in entries if e.get('method') == 'getAttachment']
    res['sse'] = [{k: v for k, v in e.items() if k != 't'} | {'dt': round(e['t'] - t_inject, 3)}
                  for e in entries if e.get('kind') in ('sse', 'inject') and e.get('event') != 'written']
    res['target_ts'] = {'A': ts, 'B': ts + 1, 'restart': ts + 2}
    res['capture_watch'] = {k: {'first_dt': round(v['first'] - t_inject, 3), 'last_dt': round(v['last'] - t_inject, 3),
                                'size': v['size']} for k, v in (capw.seen.items() if capw else ())}
    res['transcript'] = transcript(ctx.home / 'state.db')
    # logs + capture leftovers
    logs = ctx.home / 'logs'
    needles = ('restart', 'Restart', 'draining', 'runtime_draining', 'burst', 'Burst', 'debounc', 'admission',
               'refus', 'Refus', 'queued', 'capture', 'Signal: message', 'Signal SSE', 'not queued', 'drain',
               'recover', 'Recover', 'ERROR', 'Traceback', 'lost', 'busy')
    res['agent_log'] = grep_log(logs / 'agent.log', *needles, limit=200)
    res['errors_log'] = grep_log(logs / 'errors.log', '', limit=80)
    res['native_inputs'] = list_tree(ctx.home / 'cache' / 'documents' / 'native-inputs')
    res['document_cache'] = [e for e in list_tree(ctx.home / 'cache' / 'documents')
                             if not e['path'].startswith('native-inputs')]
    out = ctx.base / 'result.json'
    out.write_text(json.dumps(res, indent=1, default=str))
    log('result written', out)
    # summary line
    before = res['rows_before_restart_exit']
    b_label = 'B_video' if args.b_kind == 'video' else 'B_text'
    b_before = [r for r in (before or {}).get('rows', []) if r['label'] == b_label]
    print(json.dumps({'tree': args.tree, 'tag': args.tag, 'mib': args.mib, 'busy': args.busy, 'b': args.b_kind,
                      'gen1_rc': exit1 and exit1['rc'],
                      'rows_before_exit1': [(r['seq'], r['label'], r['status'], r['outcome'])
                                            for r in (before or {}).get('rows', [])],
                      'b_durable_before_restart': bool(b_before),
                      'rows_final': [(r['seq'], r['label'], r['status'], r['outcome'])
                                     for r in (res['rows_final'] or {}).get('rows', [])],
                      'model_order': res['model_order'], 'sends': [(s['gen'], (s['message'] or '')[:70]) for s in sends],
                      'settled': res.get('settled'), 'trigger': res.get('trigger'), 'error': res.get('error'),
                      'capture_watch': res['capture_watch']}, indent=1))
    if not args.keep_media:
        for p in list(phone.glob('*.mp4')) + list((ctx.home / 'cache' / 'documents').rglob('*')):
            if p.is_file() and p.stat().st_size > 64 * 1024:
                p.unlink()
        log('big media deleted (listings kept in result.json)')


if __name__ == '__main__':
    main()
