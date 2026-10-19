"""Probe (not a check): does a multiplexed gateway keep writing gateway.log after boot?

Starts ``python -m gateway.run`` on a fresh home with one named profile, ``--mux true|false``, runs one
``hermes -z`` turn, stops it, and counts gateway.log lines per home plus the WARNING lines the process
printed that never reached any gateway.log.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib140297 as L  # noqa: E402


def main():
    ctx = L.setup('mux-log-probe', {'--mux': {'choices': ['true', 'false'], 'default': 'true'},
                                    '--no-profile': {'action': 'store_true'}, '--dump': {'action': 'store_true'}})
    mux = ctx.args.mux == 'true'
    beta = ctx.home / 'profiles' / 'beta'
    if not ctx.args.no_profile:
        beta.mkdir(parents=True, mode=0o700)
    try:
        with L.fake_model() as model:
            cfg = {'gateway': {'multiplex_profiles': mux},
                   'model': {'provider': 'custom', 'default': 'fake-model', 'base_url': model.url},
                   'auxiliary': {'title_generation': {'enabled': False}}, 'terminal': {'cwd': str(ctx.work)}}
            L.write_home(ctx, cfg)
            if beta.is_dir():
                (beta / 'config.yaml').write_text(json.dumps(cfg))
            for home in (ctx.home, beta):
                if not home.is_dir():
                    continue
                (home / '.env').write_text('OPENAI_API_KEY=loopback-only\n')
            env = L.base_env(ctx, OPENAI_API_KEY='loopback-only')
            with L.gateway(ctx, env, name='gateway') as (gw, _desc):
                z = L.subprocess.run([sys.executable, '-m', 'hermes_cli.main', '-z', 'probe turn'], cwd=ctx.work,
                                     env=env, stdin=L.subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
                L.log('z rc', z.returncode, z.stdout[-100:])
                if ctx.args.dump:
                    import os, signal, time
                    os.kill(gw.pid, signal.SIGUSR2)
                    time.sleep(2)
    finally:
        L.stop_all(ctx)
    files = {str(p.relative_to(ctx.base)): len(p.read_text(errors='replace').splitlines())
             for p in sorted(ctx.home.rglob('gateway.log'))}
    printed = [line for line in (ctx.base / 'gateway.log').read_text(errors='replace').splitlines() if ' WARNING ' in line]
    logged = '\n'.join(p.read_text(errors='replace') for p in ctx.home.rglob('gateway.log'))
    missing = [line for line in printed if line.split(': ', 1)[-1] not in logged]
    L.check(ctx, f'mux={mux}: every printed WARNING also reached a gateway.log', not missing,
            gateway_log_lines=files, printed_warnings=len(printed), missing=missing[:10])
    L.save(ctx)


if __name__ == '__main__':
    main()
