"""One line per live/signal-restart-*/result.json: rows at gen1's exit, what ran after the relaunch, what
the Signal user saw. Usage: python signal_restart_summary.py [glob-suffix ...]"""
import json
import sqlite3
import sys
import time
from pathlib import Path
import os
ROOT = Path(os.environ.get('PKG10_ROOT') or Path(__file__).resolve().parent.parent)  # checkouts live under ROOT/<tree>, runs under ROOT/live

LIVE = (ROOT / 'live')


def short(msg):
    msg = (msg or '').replace('\n', ' ')
    for prefix, label in (('\u267b Restarting gateway', 'RESTARTING'), ('\u267b Gateway restarted', 'RESTARTED'),
                          ('Sorry, I encountered an error (RuntimeStoreError)', 'ERR runtime_draining'),
                          ('\u23f3 Gateway is restarting', 'NOT-ACCEPTING')):
        if msg.startswith(prefix):
            return label
    return msg[:48]


MARKERS = {'A': 'VIDEO_A_SLOW', 'B_text': 'TEXT_B_AFTER_VIDEO', 'B_video': 'VIDEO_B_SMALL'}


def label(text):
    return '+'.join(k for k, m in MARKERS.items() if m in (text or '')) or None


def transcript(run_dir):
    """(role, label, first chars) of every stored message, read-only from the run's state.db."""
    db = run_dir / 'home' / 'state.db'
    con = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True, timeout=2)
    try:
        rows = con.execute('SELECT id, session_id, role, content FROM messages ORDER BY id').fetchall()
    finally:
        con.close()
    return [{'id': i, 'session': sid, 'role': role, 'label': label(c), 'content': (c or '')[:160]}
            for i, sid, role, c in rows]


def log_dt(run_dir, needle, t0, gen_log=False):
    """Seconds after the injection of the first agent.log line containing *needle* (local timestamps)."""
    path = run_dir / 'home' / 'logs' / 'agent.log'
    try:
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            if needle in line and line[:4].isdigit():
                stamp, ms = line[:19], int(line[20:23])
                return round(time.mktime(time.strptime(stamp, '%Y-%m-%d %H:%M:%S')) + ms / 1000 - t0, 3)
    except OSError:
        pass
    return None


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    pats = sys.argv[1:] or ['*']
    for pat in pats:
        for path in sorted(LIVE.glob(f'signal-restart-{pat}/result.json')):
            r = json.loads(path.read_text(encoding='utf-8'))
            b = 'B_video' if r.get('b_kind') == 'video' else 'B_text'
            before = [(x['seq'], x['label'], x['status']) for x in (r.get('rows_before_restart_exit') or {}).get('rows', [])]
            final = [(x['seq'], x['label'], x['status'], x['outcome']) for x in (r.get('rows_final') or {}).get('rows', [])]
            sends = [(s['gen'], short(s['message'])) for s in r.get('user_sees') or []]
            reqs = r.get('model_requests') or []
            ran = {lab: [q['gen'] for q in reqs if q['label'] and lab in q['label']] for lab in ('A', b)}
            cw = r.get('capture_watch') or {}
            staging = [(k[:16], v['first_dt'], v['last_dt'], v['size']) for k, v in cw.items() if k.startswith('.capture')]
            print(f"{path.parent.name}: head={r.get('head')} mib={r.get('mib')} busy={r.get('busy')} b={r.get('b_kind')} "
                  f"trigger={r.get('trigger')} gen1_rc={(r.get('gen1_exit') or {}).get('rc') if r.get('gen1_exit') else [e for e in r.get('supervisor') or [] if e['event']=='exit'][:1]}")
            print(f"   B durable before restart: {any(x[1] == b for x in before)}  rows@exit1={before}")
            print(f"   rows final={final}")
            print(f"   model runs: A gens={ran['A']} {b} gens={ran[b]} order={r.get('model_order')}")
            tr = r.get('transcript') if isinstance(r.get('transcript'), list) else transcript(path.parent)
            users = [m['label'] for m in tr if m['role'] == 'user']
            print(f"   transcript user turns (in order)={users} assistant={[m['content'][:40] for m in tr if m['role'] == 'assistant']}")
            print(f"   user saw={sends}")
            t0 = (r.get('t') or {}).get('inject') or 0
            marks = {k: log_dt(path.parent, n, t0) for k, n in (
                ('restart_cmd', "Command '/restart'"), ('restart_requested', 'Restart requested'),
                ('busy_failed', 'Queued busy follow-up failed'), ('burst_not_queued', 'was not queued'),
                ('msg_error', 'Error handling message'), ('stopping', 'Stopping gateway for restart'))}
            print(f"   staging={staging} log marks(dt)={marks} error={r.get('error')}")
            rq = marks.get('restart_requested')
            at_rq = None
            for snap in r.get('row_timeline') or []:
                if rq is not None and snap['dt'] <= rq:
                    at_rq = snap['rows']
            reloc = [(x['seq'], x['label'], x['status']) for x in (r.get('rows_at_relaunch') or {}).get('rows', [])]
            print(f"   rows at 'Restart requested' (dt {rq})={at_rq}  rows at relaunch+0.5s={reloc}")


if __name__ == '__main__':
    main()
