"""#140297 section 1: "CMD-1 and RT-7 (#140249) ... and `hermes -z` from cron and relay callers" (CLI-33,
#139666), plus section 2's "`hermes -z` prints the runtime footer" recheck.

Gateway streaming on and the messaging runtime footer on. The model is a loopback stub that streams
``REPLY <prompt>``. Each caller must get exactly that reply on stdout and exit 0:
- direct: a plain captured ``hermes -z`` call (control);
- cron: a detached caller (new session, no controlling terminal, stdin from /dev/null, stdout and
  stderr to files, cron's minimal environment, cwd = home), as a crontab line runs it;
- relay: a wrapper script that captures ``$(hermes -z "$PROMPT")`` the way the reporter's relay did;
- cold cron: the cron caller with no gateway running yet (the CLI must bring one up itself).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib140297 as L  # noqa: E402


def cron_env(ctx):
    if L.WINDOWS:  # a scheduled task without a console: system PATH only
        sysroot = os.environ.get('SYSTEMROOT', r'C:\Windows')
        env = {'SYSTEMROOT': sysroot, 'WINDIR': sysroot, 'COMSPEC': os.environ.get('COMSPEC', ''),
               'PATH': rf'{sysroot}\system32;{sysroot}', 'TEMP': str(ctx.base / 'tmp'), 'TMP': str(ctx.base / 'tmp'),
               'USERPROFILE': str(ctx.user), 'LOCALAPPDATA': str(ctx.base / 'localappdata'),
               'APPDATA': str(ctx.base / 'appdata')}
        (ctx.base / 'tmp').mkdir(exist_ok=True)
    else:  # what cron gives a job (no LANG, no TERM)
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(ctx.user), 'SHELL': '/bin/sh',
               'LOGNAME': os.environ.get('USER', 'user'), 'USER': os.environ.get('USER', 'user')}
    # The installed `hermes` shim finds its package by itself; this checkout needs PYTHONPATH.
    env.update(HERMES_HOME=str(ctx.home), PYTHONPATH=str(ctx.root))
    return env


def run_cron(ctx, tag, prompt, timeout=240):
    out, err = ctx.base / f'{tag}.stdout', ctx.base / f'{tag}.stderr'
    kw = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if L.WINDOWS \
        else {'start_new_session': True}
    with out.open('wb') as fo, err.open('wb') as fe:
        proc = subprocess.Popen([sys.executable, '-m', 'hermes_cli.main', '-z', prompt], cwd=ctx.user,
                                env=cron_env(ctx), stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, **kw)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            L.kill_tree(proc)
            rc = 'timeout'
    # A redirected Windows console writes CRLF; the text is what a caller compares.
    text = out.read_bytes().decode(errors='replace').replace(chr(13) + chr(10), chr(10))
    return rc, text, err.read_bytes().decode(errors='replace')


def run_relay(ctx, env, prompt, timeout=240):
    """The reporter's relay shape: the reply is captured by command substitution, then forwarded."""
    renv = dict(env, PY=sys.executable, PROMPT=prompt, ERRF=str(ctx.base / 'relay.stderr'))
    if L.WINDOWS:
        argv = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                '$r = & $env:PY -m hermes_cli.main -z $env:PROMPT 2>$env:ERRF; '
                '"rc=$LASTEXITCODE"; "reply=[" + ($r -join "`n") + "]"']
    else:
        argv = ['/bin/sh', '-c', 'REPLY=$("$PY" -m hermes_cli.main -z "$PROMPT" 2>"$ERRF"); rc=$?; '
                                 'printf "rc=%s\\nreply=[%s]\\n" "$rc" "$REPLY"']
    done = subprocess.run(argv, cwd=ctx.work, env=renv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=timeout)
    lines = done.stdout.splitlines()
    rc = next((line[3:] for line in lines if line.startswith('rc=')), None)
    reply = done.stdout.split('reply=[', 1)[1].rsplit(']', 1)[0] if 'reply=[' in done.stdout else None
    return rc, reply, done.stdout, (ctx.base / 'relay.stderr').read_text(errors='replace')


def main():
    ctx = L.setup('cli33-oneshot')
    try:
        with L.fake_model() as model:
            L.write_home(ctx, {'gateway': {'multiplex_profiles': False},
                               'streaming': {'enabled': True},
                               'display': {'runtime_footer': {'enabled': True}},
                               'model': {'provider': 'custom', 'default': 'fake-model', 'base_url': model.url},
                               'auxiliary': {'title_generation': {'enabled': False}},
                               'terminal': {'cwd': str(ctx.work)}})
            (ctx.home / '.env').write_text('OPENAI_API_KEY=loopback-only\n')
            env = L.base_env(ctx, OPENAI_API_KEY='loopback-only')
            expected = lambda p: f'REPLY {p}'  # noqa: E731

            # cold first: nothing is running for this home yet
            rc, out, err = run_cron(ctx, 'cold-cron', 'cold cron one shot')
            L.check(ctx, 'cold cron caller (no gateway yet) prints the reply', rc == 0 and out == expected('cold cron one shot') + '\n',
                    rc=rc, stdout=out[-300:], stderr=err[-600:])
            L.stop_all(ctx)
            ctx.procs.clear()
            time.sleep(2)

            with L.gateway(ctx, env):
                direct = subprocess.run([sys.executable, '-m', 'hermes_cli.main', '-z', 'direct one shot'],
                                        cwd=ctx.work, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, timeout=240)
                L.check(ctx, 'direct caller prints only the reply', direct.returncode == 0
                        and direct.stdout == expected('direct one shot') + '\n',
                        rc=direct.returncode, stdout=direct.stdout[-300:], stderr=direct.stderr[-600:])
                rc, out, err = run_cron(ctx, 'warm-cron', 'warm cron one shot')
                L.check(ctx, 'cron caller prints only the reply', rc == 0 and out == expected('warm cron one shot') + '\n',
                        rc=rc, stdout=out[-300:], stderr=err[-600:])
                rc, reply, raw, err = run_relay(ctx, env, 'relay one shot')
                L.check(ctx, 'relay wrapper captures the reply', rc == '0' and reply == expected('relay one shot'),
                        rc=rc, reply=reply, raw=raw[-300:], stderr=err[-600:])
            streamed = [r['stream'] for r in model.requests if r['path'].endswith('/chat/completions')]
            L.check(ctx, 'every turn streamed', bool(streamed) and all(streamed), streamed=streamed)
            footer = [line for line in L.grep(ctx.base / 'gateway.log', 'footer')]
            ctx.results['gateway_footer_lines'] = footer
    finally:
        L.stop_all(ctx)
    data = L.save(ctx)
    print(json.dumps(data, indent=1, default=str)[:6000])


if __name__ == '__main__':
    main()
