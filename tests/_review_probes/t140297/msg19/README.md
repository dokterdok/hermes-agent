# MSG-19 live check (#140297 §1, #140319) — evidence

Run 2026-10-19 on David's Mac (macOS 27.2, M5) over Tailscale, under `/tmp/msg19` only (no `~/.hermes`,
no installed app data, no running gateway touched). Harness: `scripts/check_msg19.py` (uses
`../lib140297.py`), launched by `run-msg19.sh <tree> <tag>`.

## Trees

| role | tree | sha |
|---|---|---|
| upgraded gateway (after) | `/tmp/msg19/main` | `ddabd38378` (task's main) |
| upgraded gateway (fail-first) | `/tmp/msg19/prefix` | `f17021fbec` = `be842aa2fd^`, main's parent of #140319 |
| pre-upgrade Desktop backend (holder) | `/tmp/msg19/pre106742` | `d04056d609` = first parent of #106742's merge `eb75627faf` |

The brief named `7f367feaf2` (= `e78bcbd5e0^1`) as the fail-first tree. #140319 landed as five
rebased commits `be842aa2fd..e78bcbd5e0` on main's first-parent line, so `7f367feaf2` already has
`bot_relay.settle` (`git merge-base --is-ancestor be842aa2fd 7f367feaf2` holds). The real pre-fix
parent is `f17021fbec`, which has no `settle`.

## What holds the chat (the holder)

The real pre-upgrade Desktop backend for the secondary profile, spawned the way Desktop's
`apps/desktop/electron/main.ts` pool spawns it (pre tree):
`python -m hermes_cli.main --profile beta serve --host 127.0.0.1 --port 0` with `HERMES_DESKTOP=1`,
`HERMES_DASHBOARD_SESSION_TOKEN`, `HERMES_PARENT_PID` (the harness), `HERMES_DESKTOP_READY_FILE`.
A scripted window on `/api/ws?token=…` sends the old Desktop Bot Mode plugin's own sequence
(`plugins/hermes-bots/canonical-chat.ts`): `session.create {profile: beta, title: "Bot Chat", hidden: true,
follow_profile_config: true}`, `session.title`, then the kickoff `prompt.submit`. Result: a legacy
row (`source=desktop`, `title=Bot Chat`, `hidden=1`), and the classic prompt path's lease
(`surface=desktop`, `track_liveness=true`, `bot_live_delivery_consumer=true`, `pid` = that backend). The
`/Applications/Hermes*.app` builds were not used: their Electron shells keep data in
`~/Library/Application Support` and their backend in `~/.hermes`, both off-limits.

Then the upgraded gateway starts on the same home: `python -m hermes_cli.main gateway run` (the
launchd entry point), with `gateway.multiplex_profiles: true`. Both profiles are Bot-Mode-managed
(`profile.yaml` `ui_meta['hermes-bots']`). The sender is a default-profile Bot Chat turn
(`hermes chat -c "Bot Chat" --create-if-missing -Q -q "MSG19-SEND-n …"`). A loopback stub model emits
`message_agent(target='beta', message='ping n from hermes')` and echoes the tool's answer back. So the
tool runs for real inside the gateway (`tools/bot_mode_dm.py` → `bot_relay.deliver` → `bot_relay.settle`).
`TMPDIR` is per run, so the DM folder is `<run>/tmp/hermes-dm-501`.

## Verdicts (main `ddabd38378`, run `runs/msg19-held-ddabd38378-cliv`; same in `-cli` and `-try1`)

| Expectation | main | pre-fix `f17021fbec` (`runs/msg19-held-f17021fbec-cliv`) |
|---|---|---|
| answer "held by another session writer … NOT sent" | PASS: `Bot Chat is held by another session writer — the message was NOT sent; it is safe to resend after that session closes.` (`reason: runtime_coordination_required`) | `status: ambiguous`, `Live delivery admission could not be confirmed: runtime_coordination_required. Do not resend.` + `evidence_file` |
| no `dm-*.txt` / `*.live.json` left | PASS: DM folder empty | `dm-9zzcas6g.txt`, `dm-9zzcas6g.txt.live.json` kept |
| refused `<id>.json` receipt | PASS: `status: failed, refused: true, reason: runtime_coordination_required, error: "Settled as not sent after a session-writer refusal"`, no `admission_id` | none (mailbox holds only `.lock`) |
| gateway.log: refusal line | **FAIL (file)**: the line `WARNING gateway.session_controls: bot_relay.deliver refused for profile=beta: runtime_coordination_required field=session_writer detail=another session writer holds the Bot Chat; nothing was admitted yet` is in the gateway's own stderr only; `home/logs/gateway.log` has 1 line, `profiles/beta/logs/gateway.log` 0 (see ROOT-CAUSE) | no such line (only `agent.tool_executor`'s tool-error WARNING); gateway.log itself works on this tree (37 + 6 lines) |
| gateway.log: `settled as not sent` | **FAIL (file)**, same cause. With `gateway run -v` it is on stderr: `INFO gateway.session_bot: Bot Chat delivery 9b02…cb09 settled as not sent (runtime_coordination_required)` | n/a |
| state.db admissions while held | PASS: none | none |
| window closes, resend admitted once | PASS: lease gone 20 s after the ws closed (the old backend detaches, then its ws-orphan reap ends it); resend `queued`, exactly one `bot:<id2>` row (`terminal/completed`), one beta turn | resend admitted once, **and** replaying the kept intent (its runner) admits `bot:<id1>` too: `ping 1` is delivered as well, so a user who resent gets it twice |
| same-id attempt after "NOT sent" | PASS: `bot_relay.deliver` with id1 answers the refused receipt; nothing admitted | — |

## Other observations

- The pre-upgrade backend's mailbox scan logs one `WARNING tools.bot_live_delivery: skipping unreadable ticket <id>.json (id does not match filename)` for the new not-sent receipt (in beta's `errors.log`, from the old build). It skips the file and claims nothing. Harmless and cross-version only; it is absent on the pre-fix tree (no receipt there).
- `python -m gateway.run -v` parses `--verbose` but never passes it to `start_gateway` (`gateway/run.py` main), so it is a no-op; `hermes gateway run -v` works.

## Files

- `runs/msg19-held-*/`: `result.json` (checks), `evidence.json` (tool answers, DM listings, mailbox, admissions, leases, log lines), `requests.json` (stub model calls), `desktop-window-ws.jsonl` (the window's RPC frames), `desktop-backend-old.log`, `gateway-new.log`, `send-*.out`, `home/` (state.db files, logs, mailbox).
- `runs/mux-log-probe-*`, `runs/router-close-probe-*`: gateway.log probes (ROOT-CAUSE).
- `scripts/`: `check_msg19.py`, `probe_mux_log.py`, `probe_router_close.py`, the failing test.
- `logs/`: run logs; `setup.sh`, `run-msg19.sh`: Mac drivers.
