"""Probe (not a check): who closes the profile log router in a multi-profile gateway?

Runs the real ``gateway.run`` through a launcher that wraps ``_ProfileRoutingFileHandler.close`` and
``logging.shutdown`` to append their call stacks to ``<base>/router-close.txt``; then a ``hermes -z``
turn; then counts gateway.log lines.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib140297 as L  # noqa: E402

LAUNCHER = r'''
import logging, runpy, sys, threading, traceback
import hermes_logging
OUT = sys.argv.pop(1)
def note(tag):
    with open(OUT, "a") as fh:
        fh.write(f"=== {tag} thread={threading.current_thread().name}\n" + "".join(traceback.format_stack()[:-1]) + "\n")
orig_close = hermes_logging._ProfileRoutingFileHandler.close
def close(self):
    note("_ProfileRoutingFileHandler.close " + self._filename)
    return orig_close(self)
hermes_logging._ProfileRoutingFileHandler.close = close
orig_shutdown = logging.shutdown
def shutdown(*a, **k):
    note("logging.shutdown")
    return orig_shutdown(*a, **k)
logging.shutdown = shutdown
import logging.config
orig_dict = logging.config.dictConfig
def dictConfig(cfg):
    note("logging.config.dictConfig")
    return orig_dict(cfg)
logging.config.dictConfig = dictConfig
sys.argv = ["gateway.run"]
runpy.run_module("gateway.run", run_name="__main__", alter_sys=True)
'''


def main():
    ctx = L.setup('router-close-probe')
    beta = ctx.home / 'profiles' / 'beta'
    beta.mkdir(parents=True, mode=0o700)
    out = ctx.base / 'router-close.txt'
    launcher = ctx.base / 'launch_gateway.py'
    launcher.write_text(LAUNCHER)
    try:
        with L.fake_model() as model:
            cfg = {'gateway': {'multiplex_profiles': True},
                   'model': {'provider': 'custom', 'default': 'fake-model', 'base_url': model.url},
                   'auxiliary': {'title_generation': {'enabled': False}}, 'terminal': {'cwd': str(ctx.work)}}
            L.write_home(ctx, cfg)
            (beta / 'config.yaml').write_text(json.dumps(cfg))
            for home in (ctx.home, beta):
                (home / '.env').write_text('OPENAI_API_KEY=loopback-only\n')
            env = L.base_env(ctx, OPENAI_API_KEY='loopback-only')
            proc = L.popen(ctx, [sys.executable, str(launcher), str(out)], env=env, logname='gateway.log')
            desc = L.wait_ready(ctx.home, proc)
            L.log('ready', desc.get('state'))
            z = L.subprocess.run([sys.executable, '-m', 'hermes_cli.main', '-z', 'probe turn'], cwd=ctx.work,
                                 env=env, stdin=L.subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
            L.log('z rc', z.returncode)
            L.kill_tree(proc)
    finally:
        L.stop_all(ctx)
    lines = {str(p.relative_to(ctx.base)): len(p.read_text(errors='replace').splitlines())
             for p in sorted(ctx.home.rglob('gateway.log'))}
    L.log('gateway.log lines', lines)
    print(out.read_text() if out.exists() else '(no close recorded)')


if __name__ == '__main__':
    main()
