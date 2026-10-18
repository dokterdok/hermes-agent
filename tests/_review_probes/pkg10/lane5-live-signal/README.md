# Live retest of #140973's "#3" on a Signal gateway (not for merge)

The real gateway daemon (`python -m gateway.run`) and the real Signal adapter, against `fake_signal_cli.py`, a stand-in for signal-cli's `daemon --http` API (SSE events, JSON-RPC 2.0, `getAttachment`). Only the Signal network is replaced. `/restart` exits 75 and a supervisor thread relaunches it, as launchd/systemd would.

Setup: put checkouts of the trees under `$PKG10_ROOT/<tree>` (the script knows `lane5` = `655aef56d8`, `lane5-r2` = `ffbb90f2e79`, `main`). Set `HERMES_DEV_HOME` to an isolated dev home, used only to locate each tree's interpreter.

```bash
bash run.sh lane5-r2 signal_restart.py --tag 1 --mib 64                     # queue mode, text B behind a capturing video A
bash run.sh lane5-r2 signal_restart.py --tag default-1 --mib 64 --busy default
bash run.sh lane5-r2 signal_restart.py --tag video-1 --mib 64 --b-kind video
python signal_restart_summary.py
```

Each run writes `$PKG10_ROOT/live/signal-restart-<tree>-<tag>/`: `result.json` (admission rows over time, model requests, Signal sends), the gateway logs and `state.db`. B and `/restart` are sent once A's `native-inputs/.capture-*` staging file exists; `--gap 0` sends all three back to back instead.
