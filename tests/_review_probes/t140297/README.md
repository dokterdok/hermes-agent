# Live checks for #140297 (not for merge)

Real processes against an isolated `HERMES_HOME` per run: `python -m gateway.run`, `python -m hermes_cli.main`,
the OpenAI-compatible API server, and (for LC-16) a real Open WebUI. The model is a loopback OpenAI-compatible
stub that records each request's path, `Authorization`/`x-api-key` and model. Nothing touches `~/.hermes` or
`%LOCALAPPDATA%\hermes`.

`api/run.sh <tree> <out dir> <script> [args]` runs a check with that tree's own test-env interpreter (found through
`scripts/run-in-hermes-env`; set `HERMES_DEV_HOME` to an isolated dev home used only for that lookup). Each run
writes `<out>/<check>-<head>[-tag]/result.json` plus the gateway logs and the run's home.

| Script | Item | What it checks |
| --- | --- | --- |
| `api/check_335_keys.py [--keys env\|dotenv\|both] [--aux pinned\|auto] [--transport declared\|omitted]` | #140335 recheck, and the `custom:anthropic` aux transport | `providers.deepseek` / `providers.anthropic` entries with their own `key_env`, the built-in key also set: the `-z` turn, an API server turn and the title call must carry the entry's key; with `--aux pinned` the title call must use the entry's wire |
| `api/check_cli33_z.py` | CLI-33 (#139666), footer recheck | `hermes -z` with streaming and the runtime footer on: plain, cron-shaped (detached, no terminal, minimal env), relay (`$(hermes -z ...)`), cold start with no gateway |
| `api/check_lc9_upgrade.py --old-root <tree> --old-python <python>` | LC-9 (#140314) | keyed Chat/Responses turns and a Bot delivery settled by the old build, then the same home upgraded in place: byte-identical replays with no model call, the same Bot receipt, result-row and transcript bytes |
| `api/serve_api.py --api-port 18642` + `api/owui_start.sh <rig dir> <tag>` | LC-16 / GW-12 with Open WebUI | a long-running rig; Open WebUI 0.12.3 (`WEBUI_AUTH=False`, bound to one address) pointed at it; `owui_sessions.py <rig dir>` prints the Hermes sessions it produced |
| `msg19/` | MSG-19 (#140319) on macOS | see `msg19/README.md`; `msg19/ROOT-CAUSE-gateway-log-router.md` and `test_multiplex_log_routing_survives_api_listener.py` cover the log-file regression found there |

Fail-first trees used: `59ee4989a6` (before `ed6414fd6d`, #140335's first landing), `17b598aecc` (before `033e39f023`,
CLI-33's first landing), the 2026-10-17 canary `8259f4ad80` (before #140232 and #140314), `f17021fbec` (before #140319).
