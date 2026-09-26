# Native A5 Beelink NO_WINDOW — diagnosis and mechanical runbook

**Status:** diagnosis and runbook only. Native Files UAT is not PASS. This document does not change Desktop product code. Do not run the previous launch script. Its LF SHA-256 is `56edee4a6212b52ad0dfd8c9db85a37289aedd127e6327b9732841dbc85ab5f6`. That is the script that killed pid 38980.

**Launch script (do not retype):** `review-packages/NATIVE_A5_BEELINK_EARLY_EXIT_LAUNCH.ps1`

**SHA-256, LF bytes (git blob and GitHub raw):** `9f0277fe04704d2677191112dcba1dd9b4df1c7bf588752afc581a2ebf98fbf3`

**SHA-256, CRLF bytes (a Windows checkout; `*.ps1` is `text eol=crlf`):** `a5d39b39e9c21cafafe224c00820625428e87a5271f90e1fc72c7f07eabbb2e0`

Either hash is this script. If the copy matches neither, stop with `RUNBOOK_DRIFT`. Do not edit the script to fix a mismatch, a port, a path, a username, or the line endings.

**Session-1 helper (do not retype):** `review-packages/NATIVE_A5_BEELINK_SESSION1_UIA.ps1`

**SHA-256, LF bytes (git blob and GitHub raw):** `f23cc7b4051f4d41fdf8fbba94fd3d4548ed1502c28d64e98f6bb75cfa85a042`

**SHA-256, CRLF bytes (a Windows checkout; `*.ps1` is `text eol=crlf`):** `dcfaabb32d9bc613179ae30f4ac735b72aa8d546762da4f110999196859326eb`

Either hash is this helper. The previous LF hash `6e133bd5638dbbe28ea4d3b44abb5aa2afb1feabca1feade45ef1fdcbfe7292f` is not this helper. Copy the new file only to `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\a5-session1-uia.ps1`. If the copy matches neither new hash, stop `RUNBOOK_DRIFT`. Do not edit it on Beelink. Do not copy `NATIVE_A5_BEELINK_SESSION1_UIA.Tests.ps1`. Do not copy the helper into the attempt folder or over `launch-a5.ps1`.

### Why Entry stopped on 2026-09-26

Discover for pid 42312 completed (`DISCOVER_ENTRY_RUNG=SIGNOUT`, window title `Hermes`, daily seen, not a grant). Entry then printed `UIA_ENTRY_RUNG=SIGNOUT` and a line whose only character was `2`. It did not print `UIA_DONE`, `UIA_STOP`, `UIA_PATH_OK`, or `ENTRY_INVOKED`. FocusUsername then printed `UIA_STOP=UIA_CONTROL_ABSENT`. No credential was typed. Cancel, Save, and Hash did not run. `DEST` is absent. UAT pid 42312 and daily pid 33048 stayed up. That attempt is not a click and not Files PASS.

Two defects in helper LF `6e133bd5…` produced that transcript on Windows PowerShell 5.1. Success-stream text from `Publish-A5Grant` and `Stop-A5Uia` was consumed by `if (function)`, so `UIA_PATH_OK` and `UIA_STOP` never reached stdout. A stop string is truthy, `ENTRY_INVOKED` was then suppressed, and `return 2` became the bare line `2`. The same host writes the old value of `++` to the success stream, so the hwnd walk could return `0` mixed with the handle and fail the click before `InvokePattern`. The replacement writes fields with `[Console]::Out`, returns only a Boolean into `if`, stores the exit code in `$script:A5ExitCode`, and does not use `++`. A failed Entry now prints `UIA_STOP=` and does not print `UIA_DONE`.

The SSH script in the fence below is unchanged. Its stdout is CRLF because it is Windows PowerShell. BarrX deletes CR characters before the CLEAN rules. `ATTEST_OUTPUT_CR_NORMALIZATION_REQUIRED` is not a stop. The 20:50 transcript's field values were already the staged UAT exe, session 1, and `ATTEST_MATCH=UAT`.

Next action on the live ns3 pid is `Entry` again, not `FocusUsername`. The sign-out control was not invoked. The login form was not shown. Do not relaunch.

**Phase 2 input** does not require a CUA tool, and it does not require the CUA tool to show `ExecutablePath`. BarrX has no CUA actuator. Each click, type, or foreground change is gated by the SSH attestation below, then sent by the helper on session 1. That attestation is not a Launch. The helper is not a Launch. The launch script bytes stay the two hashes above.

## What the ns2 launch showed

On 2026-09-26 the one Launch against `C:\Users\ddewit\hermes-uat-a5-ns2-20260926` did start Hermes. The transcript records:

- `HEALTH=200 port=54573`
- `DAILY_MAIN_PIDS=33048` left running
- `PSEXEC_PATH=C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec64.exe`
- `PROTOCOL_PREIMAGE=EXPORTED` then later `PROTOCOL_RESTORED`
- `PSEXEC_EXIT=37912` and `PSEXEC_PID=37912` (PsExec `-d` returns the started pid as its exit code; that number is not a Hermes failure)
- `LAUNCH_IDENTITY=OWNER_OK`
- `UAT_MAIN_PID=38980`
- `UAT_SESSION=1`
- `UAT_OWNER=ddewit`
- `CMDLINE_OK`
- no `UAT_WINDOW` line
- no `UAT_EXIT` line
- `KILLED_UAT_TREE pid=38980`
- `STOP NO_WINDOW`
- PowerShell exit 15

Exit 15 is the script's own stop. It is not an Electron exit code. The process was still alive when the script killed it. Exit 13 `PROFILE_NOT_ADOPTED` did not fire, so `Local State` or `windows-sandbox-fallback.json` had appeared under that attempt's `user-data`. The marker text was not printed. Daily Hermes pid 33048 was not killed.

## Why the window check killed a live process

The script that produced that transcript waits in the SSH PowerShell. Hermes is started separately with PsExec `-i 1`, which is the interactive desktop of session 1. The transcript shows that split: the launcher is the SSH command (`LAUNCH_SSH_EXIT=15`) and the Hermes process is `UAT_SESSION=1`.

The old check called `Get-Process` and read `MainWindowHandle` in the SSH process. That property enumerates top-level windows on the calling thread's desktop and keeps only a visible window with no owner. Windows on the interactive desktop are not in that enumeration. A zero handle there does not mean the UAT window was absent. The script treated 90 seconds of zeros as `NO_WINDOW` and ran `taskkill /F` on pid 38980.

A second, separate fact: every Electron window in this tree is created with `show: false`. `MainWindowHandle` stays zero until the window is actually visible, even from the right desktop. The main window is revealed on `ready-to-show`, or about four seconds after `did-finish-load` if that event never arrives. The login window is 520 by 720. The main window defaults to 1220 by 800. Profile files are written earlier than that reveal. `windows-sandbox-fallback.json` is written at startup, before `app.ready`. An adopted profile does not prove a visible window.

This transcript cannot separate those two facts. It can show that the check which fired was blind to the interactive desktop, and that the script then killed the process. Do not launch ns2 again to find out which fact it was.

## Why ns and ns2 stay frozen

`decideWindowsSandboxLaunch` writes `{ state: booting }` at startup when `--no-sandbox` is already on the command line. `shouldAttemptAclRepair` returns true for `state: booting` and for `state: fallback`. The next start of that user-data directory runs `icacls /grant` for `S-1-15-2-2` on the directory that contains the staged exe. A visible window rewrites the marker to `{ state: ok }` only after reveal, and only when the sticky fallback flag is false. `before-quit` can also write `ok`. `taskkill /F` does not run `before-quit`.

ns2 was started with `--no-sandbox`, then killed with `taskkill /F`, and the marker text was never read. Treat it as `booting`. The same holds for `C:\Users\ddewit\hermes-uat-a5-ns-20260926`, the partial from the earlier stderr `UNCAUGHT`, and for `C:\Users\ddewit\hermes-uat-a5-live-20260926`.

The new script prints `FROZEN_NS_MARKER=` and `FROZEN_NS2_MARKER=` as a recording. `state: ok` is not permission to launch. `state: booting` means a second start would grant the ACE. Leave the folder frozen either way. Do not copy a marker into the new attempt or into `AppData\Roaming\Hermes`.

## What the new script changes

The SSH process no longer reads `MainWindowHandle`. After `CMDLINE_OK`, the same PsExec `-i 1` starts one read-only Windows PowerShell 5.1 probe on the interactive desktop. That probe is part of the single Launch. It is not a second Launch. It enumerates top-level windows and keeps only those whose executable path is the staged UAT exe. A window is large when its rectangle is at least 400 by 500. Smaller UAT windows are printed as `UAT_WINDOW_SMALL`. They do not satisfy `WINDOW_STABLE`, and they also do not select `NO_WINDOW`. The probe reads its own desktop name and writes `PROBE_OK=1` only when that name is `Default`. The SSH process trusts a sample only when the probe's own session is 1, the owner is `ddewit`, the desktop is `Default`, the sample is complete, and the file's last write is at most 5 seconds old. Re-reading a frozen file does not refresh that age. A sample that fails any of those checks is `probe-bad` and does not kill.

Once `CMDLINE_OK` has been printed, an unexpected fault leaves the UAT pid running and prints `LEAVE_UAT_RUNNING`. The script kills that pid only for a deliberate stop: daily Hermes died, the daily profile stamp changed, the UAT process exited, the profile was not adopted, a large window stayed hidden through the hard stop, or the healthy probe saw no UAT window at all.

`WINDOW_STABLE` requires a large visible UAT window for 15 continuous seconds, a profile file in the new attempt, the main pid still alive, and a fresh session-1 `Default`-desktop sample. A large hidden window waits until the 180 second hard stop and then stops as `WINDOW_NOT_VISIBLE` (exit 18), which kills the new tree and must not be launched again. Zero UAT windows after 90 seconds, with that healthy probe and the profile adopted, is still `NO_WINDOW` (exit 15). Only-small windows wait, and at the hard stop they are `WINDOW_UNSTABLE` (exit 20), which leaves the pid. A probe that is missing, stale, not session 1, or not on desktop `Default` stops as `WINDOW_PROBE_FAILED` (exit 19) or `WINDOW_UNSTABLE` (exit 20) and does **not** kill the UAT pid.

## Holds

- Do not claim native Files UAT PASS. A destination SHA-256 is a recording, not a verdict.
- Do not reboot Beelink. Do not change the memory reserve. Do not lower `26439023616`.
- Do not stop, restart, or focus daily Hermes. Do not `taskkill /IM Hermes.exe`. Do not `Stop-Process -Name Hermes`.
- Do not launch `C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`.
- Do not delete, rename, move, or launch these folders. Leave every file in them:
  - `C:\Users\ddewit\hermes-uat-a5-ns-20260926`
  - `C:\Users\ddewit\hermes-uat-a5-ns2-20260926`
  - `C:\Users\ddewit\hermes-uat-a5-live-20260926`
- Do not invent `ns4` or any other attempt folder. The only new folder is `C:\Users\ddewit\hermes-uat-a5-ns3-20260926`, and only the script may create it.
- Do not run Launch twice. If this Launch stops, do not run it again against ns3, ns2, or ns. A killed or still-running ns3 tree can be `booting`.
- If the transcript contains `LEAVE_UAT_RUNNING`, leave that pid. Do not kill it. Do not start CUA. Do not launch again.
- Do not run the launcher that printed `attempt exists`, and do not run the script whose hash is `56edee4a6212b52ad0dfd8c9db85a37289aedd127e6327b9732841dbc85ab5f6`.
- Do not `setx` anything. Do not grant ACLs. `icacls` in the script is read-only and has no `/grant` and no `/T`. A `no` ACE line does not authorize `icacls /grant`.
- Do not edit `HKCU\Software\Classes\hermes` by hand. Do not delete files under `C:\Users\ddewit\AppData\Roaming\Hermes`.
- Do not set `HERMES_DESKTOP_BOOT_FAKE` or `HERMES_DESKTOP_BOOT_FAKE_ERROR`.
- Do not click `Retry`, `Repair`, `Gateway settings`, `Use local gateway`, `Open logs`, or `File system`.
- Do not print, log, or screenshot the fixture password or username. Do not record helper stderr. Do not print `$Error`.
- If the script prints `KILL_REFUSED` or `KILL_ERROR`, stop. Do not escalate to an image-name kill.
- If the script prints `UAT_EXE_ALREADY_RUNNING`, stop. Do not kill that pid. Do not launch the daily exe instead.
- If daily Hermes is not seen, do not start it. If the script prints `DAILY_HERMES_DIED`, do not restart it.
- Do not click a window because its title is Hermes, or because its image name is `Hermes.exe`.
- Do not enable `SeDebugPrivilege`. Do not run the Phase 2 attestation as any user other than `ddewit`.
- Do not use daily Hermes `computer_use`, or any daily Hermes tool, to click the UAT window.
- Do not start a Cursor private worker from this procedure. None is connected on Beelink. The worker is an alternate unlock only after the SSH attestation cannot be made CLEAN, or after the session-1 helper cannot start (`PSEXEC_MISSING`, `UIA_NOT_SESSION1`, `UIA_DESKTOP`, `UIA_ADDTYPE`). Do not ask for one before that SSH attestation and that helper have been tried. A worker is not permission to click by title.
- Do not run Launch a second time against ns3. The Phase 2 attestation is not a Launch. The helper is not a Launch.
- Do not put PsExec on `PATH`. Do not download PsExec. The launch script and the helper drive both open `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec64.exe` when that file exists.
- A helper `UIA_STOP=` does not kill. Do not apply the launch exit table to the helper. `UIA_PSEXEC_IS_NOT_A_PID=1` means the PsExec exit code is the helper's exit, not a pid.

## What remains unproven

A stable window does not prove login, Cancel, Save, or the fixture bytes. `HASH_RECORDED_NOT_A_PASS` does not prove them either. Nothing in this procedure compares the hash to the fixture payload. Whether pid 38980 had a visible window is unproven. The next launch's `WINDOW_PROBE_SESSION`, `UAT_WINDOW`, `UAT_WINDOW_HIDDEN`, and `UAT_WINDOW_SMALL` lines are what separate a blind detector from a window that never became visible. This procedure does not launch ns2 to settle that.

This checkout did not re-query pid 42312. A UIA report of that pid on session 1 is not a path and is not a click grant.

## Why the ns3 CUA resume stopped

The one ns3 Launch had already reached a live UAT process. CUA login then stopped `CUA_CANNOT_SEE_PROCESS_PATH`. BarrX saw a UIA window for Hermes, pid 42312, session 1. On the CUA side, `Get-Process`, `MainModule`, CIM, and WMI all returned a blank path, so the old gate forbade the click. Separately, an SSH logon as `ddewit` can often resolve `Win32_Process.ExecutablePath` for that pid. No Cursor private worker is connected on Beelink.

That split is real, and it is the same split this runbook already uses at launch. `Get-Process.MainModule` opens the process with `PROCESS_QUERY_INFORMATION` and `PROCESS_VM_READ` and then reads the module list. A caller that cannot read the target address space gets an empty path or an access error that a tool turns into a blank. `Win32_Process.ExecutablePath` is a different property. Microsoft qualifies it with `SeDebugPrivilege` and maps it to the module path. A caller who cannot inspect the process gets null rather than a throw. `scripts/install.ps1` records that shape: CIM returns a null `ExecutablePath` for a process it cannot inspect. The CUA tool is that kind of caller. Its blank path does not mean pid 42312 has no image path.

The caller that can see the path is the process owner. The launch script, running in the SSH PowerShell as `ddewit`, already selects Hermes with `Get-CimInstance Win32_Process` and `.ExecutablePath`. The ns2 transcript got as far as `UAT_MAIN_PID`, `UAT_SESSION=1`, `UAT_OWNER=ddewit`, and `CMDLINE_OK`, which requires that property to be non-blank. SSH as `ddewit` is that same query. It does not need a private worker, and it does not need the CUA tool to expose `ExecutablePath`.

"Often" is the limit. This checkout did not query pid 42312, and a blank SSH result is still a stop. Do not enable `SeDebugPrivilege` to fill a blank. Do not translate a `\Device\` path into a drive letter. Do not case-fold a path into a match.

These substitutes are discarded:

- A click by title. Daily Hermes and the UAT build use the same titles.
- A match on image name `Hermes.exe`. Both installs use that name.
- `MainModule`, including `MainModule` run over SSH. That is the API that was blank.
- One attestation for the whole session. Focus can move to daily Hermes between two clicks.
- PsExec for the path query. Image path is not a desktop property. The SSH CIM script is the only path query. PsExec `-i 1` is a later step: it is the session-1 UI actuator, and only after that SSH result is CLEAN. It is not a second Launch.
- A second Launch of ns3, ns2, or ns, to "refresh" the path.
- Requiring David to start a Cursor private worker before this SSH query exists. None is connected. The worker remains the alternate unlock when this SSH attestation cannot be made CLEAN, or when the session-1 helper cannot start. It is not the first step.
- Daily Hermes `computer_use` as the hand that clicks UAT. The daily process stays untouched.

A CLEAN SSH path still cannot click by itself. BarrX has no CUA tool and no UIAutomation actuator. No Cursor private worker is connected on Beelink. The earlier resume saw a session-1 UIA window for the UAT pid and then stopped when the CUA path was blank. That blank path is now a reason to run the SSH script, not a reason to start a worker. The unlock is the PsExec64 already at `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec64.exe`, started with `-i 1` and without `-d`, so the helper runs on session 1. This checkout did not click and did not relaunch.

The hypothesis holds inside those limits. Immediately before each helper action except Discover, a read-only SSH PowerShell 5.1 process as `ddewit` prints `EXACT_EXECUTABLE_PATH` for that pid from CIM `Win32_Process`. The helper may send one input only when that path is the Phase-1 `UAT_EXE` line, byte for byte, and the rest of the CLEAN rule below is true. The helper's own `UIA_PATH_OK=1` does not replace that transcript.

## Mechanical procedure

Run the phases in order. After any `STOP` line, or any PowerShell exit code other than 0, do not start the next phase. Do not invent a replacement action. The stop line is the result.

### Phase 0 — fixture, before Hermes

1. Do not reboot. Do not kill processes by image name. Do not change memory policy. Do not delete, rename, or launch `C:\Users\ddewit\hermes-uat-a5-ns-20260926` or `C:\Users\ddewit\hermes-uat-a5-ns2-20260926`. Do not put PsExec on `PATH`. Do not download PsExec.
2. From Beelink, request `http://127.0.0.1:54573/api/health` with a 5 second timeout. Do not scan other ports.
3. If the status is not 200, bring the existing owned fixture and reverse tunnel back with the Windows listen port fixed at **54573**. That is the port the script writes into `connection.json`. Do not choose another port. Do not edit the script. If health is still not 200, stop `FIXTURE_DEAD`.
4. Confirm `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\uat-password.txt` exists. Do not print its contents. If the username is not already in orchestrator memory from that bring-up and `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\uat-username.txt` does not exist, stop `USERNAME_UNAVAILABLE`. Do not read a Linux process environment to discover it.
5. Copy `review-packages/NATIVE_A5_BEELINK_EARLY_EXIT_LAUNCH.ps1` from this PR, unmodified, to `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1`. Compute SHA-256 of those bytes. It must equal `9f0277fe04704d2677191112dcba1dd9b4df1c7bf588752afc581a2ebf98fbf3` (LF) or `a5d39b39e9c21cafafe224c00820625428e87a5271f90e1fc72c7f07eabbb2e0` (CRLF). Otherwise stop `RUNBOOK_DRIFT`. Do not keep the previous `launch-a5.ps1`. Do not copy the `.Tests.ps1` file to Beelink.

### Phase 1 — launch

Run exactly once:

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1 -Phase Launch
```

Use `powershell.exe` (Windows PowerShell 5.1). If that executable is missing, stop `POWERSHELL_MISSING`. Do not substitute `pwsh.exe`. Do not pipe the password into this command.

Continue only when the process exit code is 0 **and** the output contains a line `WINDOW_STABLE`. The same output must contain all of these lines:

- `NEW_ATTEMPT=C:\Users\ddewit\hermes-uat-a5-ns3-20260926`
- `DO_NOT_LAUNCH_FROZEN=1`
- `DO_NOT_LAUNCH_NS2=1`
- `ONE_LAUNCH_ONLY=1`
- `WINDOW_PROBE_SESSION=1`
- `WINDOW_PROBE_DESKTOP=Default`
- `FROZEN_MARKER_IS_NOT_A_LAUNCH_GRANT=1`

If `NEW_ATTEMPT=` is any other path, including ns2, stop `RUNBOOK_DRIFT`. Do not delete any attempt folder. Record `UAT_MAIN_PID`, `UAT_EXE`, `ATTEMPT`, and `DEST_FILE` from that output. Leave that pid running. Leave every daily Hermes pid running.

`PSEXEC_EXIT=` and `PROBE_PSEXEC_EXIT=` are not stops. `Connecting to local system...` is not a stop. The probe PsExec is not a second Launch.

Any other exit code is a stop, including a transcript that contains `WINDOW_STABLE` but whose PowerShell exit code is not 0.

`PSEXEC_PID_UNPARSED` is not a stop. Do not kill anything because of that line.

If the transcript contains `LEAVE_UAT_RUNNING`, stop. Leave that pid. Do not start CUA. Do not launch again. Do not kill by image name.

If the transcript contains `PROTOCOL_RESTORE_FAILED`, the script has already deleted `HKCU\Software\Classes\hermes` and both of its `reg.exe import` tries failed. Run `reg.exe import` once on the path in the `PREIMAGE_REG=` line printed with `PROTOCOL_RESTORE_FAILED`. Do not `reg delete`. Do not start CUA. Do not relaunch. Do not kill the UAT pid or daily Hermes. If that import fails, stop and leave the preimage file in place.

The script's own stops, and the only meaning of each:

| Exit | Line | Meaning |
| --- | --- | --- |
| 2 | `FIXTURE_DEAD` | `127.0.0.1:54573/api/health` was not 200. |
| 3 | `RESOURCE_HOLD` | Free physical memory is below 26439023616 bytes. Do not lower the bar. |
| 4 | `UAT_EXE_MISSING`, `UAT_ASAR_MISSING`, `DAILY_EXE_MISSING`, `UAT_EXE_IS_DAILY` | Staged package or daily exe path check failed. Do not fall back to the other exe. |
| 5 | `ATTEMPT_EXISTS`, `FROZEN_ATTEMPT_SELECTED` | The new folder already exists, or the script was pointed at a frozen folder. Do not delete it. Do not switch back to ns or ns2. |
| 6 | `PSEXEC_MISSING` | Neither the known PsExec file nor a PsExec already on `PATH` was usable. Do not download PsExec. Do not add a PATH wrapper. |
| 7 | `PREIMAGE_EXPORT_FAILED` | The `hermes` protocol key could not be exported. Do not delete it. |
| 8 | `UAT_EXE_ALREADY_RUNNING`, `LAUNCH_IDENTITY_MISSING`, `UAT_MAIN_MISSING`, `WRAPPER_MISSING_NO_SANDBOX`, `WRAPPER_HOME_UNPINNED`, `WRAPPER_REDIRECTS_PROFILE`, `WRAPPER_UNSAFE`, `CONNECTIONS_JSON_PRESENT`, `ACTIVE_PROFILE_BYTES` | Process did not come up as specified. `UAT_EXE_ALREADY_RUNNING` prints a pid. Do not kill that pid. |
| 9 | `EARLY_EXIT` | UAT Hermes exited. `UAT_EXIT=` is the code. Do not relaunch. |
| 10 | `WRONG_OWNER` | Process identity was not `ddewit`. |
| 11 | `WRONG_SESSION` | Process was not in session 1. Do not try another session. |
| 12 | `WRONG_CMDLINE` | Argv lacked the attempt `--user-data-dir` or `--no-sandbox`. |
| 13 | `PROFILE_NOT_ADOPTED` | No `Local State` or sandbox marker appeared under the attempt `user-data`. |
| 14 | `DAILY_PROFILE_TOUCHED` | A watched file under `Roaming\Hermes` changed timestamp. Do not delete it. |
| 15 | `NO_WINDOW` | The session-1 probe on desktop `Default` saw no UAT top-level window, large or small, for the wait. Do not relaunch this attempt. |
| 16 | `DAILY_HERMES_DIED` | A recorded daily main pid is gone. Do not restart it. |
| 17 | `PROTOCOL_RESTORE_FAILED` | Import of the preimage failed. Do the one `reg.exe import` above. Do not start CUA. |
| 18 | `WINDOW_NOT_VISIBLE` | The session-1 probe saw a large UAT window that stayed hidden. Do not relaunch this attempt. |
| 19 | `WINDOW_PROBE_FAILED` | The session-1 probe did not report a usable sample. `LEAVE_UAT_RUNNING` is set. Do not kill that pid. Do not relaunch. |
| 20 | `WINDOW_UNSTABLE` | A large window was seen and did not hold 15 seconds, only small UAT windows remained at the hard stop, or the wait ended without a kill decision. `LEAVE_UAT_RUNNING` is set. Do not kill that pid. Do not relaunch. |
| 21 | `CANCEL_WROTE_OR_DIRTY` | `dest` was not empty after Cancel. Do not delete the file and continue. |
| 22 | `DEST_FILE_MISSING` | The Save path was not the exact destination file. Do not hash a neighbor. |
| 23 | `ATTEMPT_MISSING` | The attempt `dest` directory is gone. Do not recreate it by hand. |
| 99 | `UNCAUGHT` | Script fault. Do not continue. Do not delete the attempt folder the script created. Do not launch a frozen folder to finish it. |

`DAILY_NOT_SEEN` is a warning inside a launch that may still reach `WINDOW_STABLE`. Do not start daily Hermes because of it.

`FROZEN_NS_MARKER=` and `FROZEN_NS2_MARKER=` are recordings. They do not authorize a launch.

### Phase 2 — CUA login

The live ns3 resume already has its one Launch. Do not run Launch again. Do not create ns4. Do not delete ns3. Use `UAT_EXE`, `UAT_MAIN_PID`, `ATTEMPT`, and `DEST_FILE` from that transcript. If this resume has no `WINDOW_STABLE` line from that one Launch, stop. Do not launch to obtain one.

This phase does not launch Hermes. It does not kill a process by image name. It does not take a second Launch. It does not start a Cursor private worker. A blank path from a CUA tool is expected and is not, by itself, the stop. The stop is an SSH attestation that is not CLEAN, or a helper transcript that is not success. Do not click by title. Do not match image name `Hermes.exe`. Pid 42312 is the pid that stopped the last resume. It is not a hardcoded target. Attest `DISCOVER_ENTRY_PID` from the helper, and keep that same pid for every later action.

Do not take a screenshot while the password field is focused or contains text. Do not echo the username or the password. The attestation does not read `uat-password.txt` or `uat-username.txt`.

#### Path attestation, before every click and every type

Run this gate immediately before each helper action in Phase 2, Phase 3, and Phase 5 except Discover. A CLEAN result covers that one action and then expires. Login does not cover Cancel. Cancel does not cover Save. Do not retry a blank query inside the same action. The script below is one CIM query. Do not run it twice for the same action. The helper's own re-check is a different process and does not authorize skipping this script.

1. The candidate pid is the single `DISCOVER_ENTRY_PID` from a successful Discover. Use that same pid for every later action. Do not switch to another `DISCOVER_UAT_PID`, a child pid, or a pid chosen by title. If Discover has no successful `DISCOVER_ENTRY_PID`, stop. Do not search processes by title or by image name. Discover itself has no candidate pid and does not run this script.
2. The Phase-1 `UAT_EXE` value is the exact characters after `UAT_EXE=` on that one transcript line. It must be byte-for-byte `C:\Users\ddewit\hermes-uat-desktop-renderer-reuse-20260923\source\apps\desktop\release\win-unpacked\Hermes.exe`. If it is anything else, stop `RUNBOOK_DRIFT`. Do not attest against a different path.
3. On the existing SSH logon as `ddewit` to Beelink — the same logon that runs `powershell.exe` for Launch — start exactly:

```text
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command -
```

Send the script below on that process's stdin, as ASCII or UTF-8 with no BOM, then close stdin so the process can exit. Do not use `pwsh.exe`. Do not use `SysWOW64\WindowsPowerShell`. Do not use PsExec. Do not use `Enter-PSSession`. Do not pass `-Phase Launch`. Do not write this script into the attempt folder, into `launch-a5.ps1`, or anywhere else on Beelink. Do not run it through a local shell that expands `$` or backticks. If the SSH logon as `ddewit` is not available, stop `CUA_CANNOT_SEE_PROCESS_PATH`. If the process does not exit, do not click, do not start a second query for the same action, and do not kill any process to clear it. Leave the UAT pid and daily Hermes running. Stop `CUA_CANNOT_SEE_PROCESS_PATH`. Stderr is not a path. Read `EXACT_EXECUTABLE_PATH` from stdout only.

Replace the single token `PID_DECIMAL` with the candidate pid in decimal digits, no sign and no leading zero. No other character of the script may change. If the token is missing, repeated, or the pid is not `^[1-9][0-9]{0,9}$`, stop `RUNBOOK_DRIFT`.

```powershell
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ([int]$PSVersionTable.PSVersion.Major -ne 5) {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=ATTEST_POWERSHELL'
  exit 2
}
if ($PSHOME -like '*\SysWOW64\WindowsPowerShell\*') {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=ATTEST_WOW64'
  exit 2
}
if ($env:USERNAME -ine 'ddewit') {
  Write-Output ('ATTEST_USER=' + [string]$env:USERNAME)
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=ATTEST_WRONG_OWNER'
  exit 2
}
Write-Output 'ATTEST_USER=ddewit'
$candidateText = 'PID_DECIMAL'
if ($candidateText -notmatch '^[1-9][0-9]{0,9}$') {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=CUA_CANNOT_SEE_PROCESS_PATH'
  exit 2
}
if ([uint64]$candidateText -gt 4294967295) {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=CUA_CANNOT_SEE_PROCESS_PATH'
  exit 2
}
$candidatePid = [uint32]$candidateText
if ([string]$candidatePid -ne $candidateText) {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=CUA_CANNOT_SEE_PROCESS_PATH'
  exit 2
}
$listed = @(Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = ' + $candidatePid))
if ($listed.Count -ne 1 -or $null -eq $listed[0]) {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=CUA_CANNOT_SEE_PROCESS_PATH'
  exit 2
}
$row = $listed[0]
$printedPid = [string]([uint32]$row.ProcessId)
if ($printedPid -ne $candidateText) {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=ATTEST_PID_MISMATCH'
  exit 2
}
$ownerUser = ''
$ownerRead = $false
try {
  $owner = Invoke-CimMethod -InputObject $row -MethodName GetOwner
  $ownerUser = [string]$owner.User
  $ownerRead = $true
} catch {
  $ownerRead = $false
}
if ((-not $ownerRead) -or [string]::IsNullOrEmpty($ownerUser)) {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=CUA_CANNOT_SEE_PROCESS_PATH'
  exit 2
}
if ($ownerUser -ine 'ddewit') {
  Write-Output 'EXACT_EXECUTABLE_PATH='
  Write-Output 'ATTEST_STOP=ATTEST_WRONG_OWNER'
  exit 2
}
$ordinal = [System.StringComparison]::Ordinal
$ignore = [System.StringComparison]::OrdinalIgnoreCase
$uat = 'C:\Users\ddewit\hermes-uat-desktop-renderer-reuse-20260923\source\apps\desktop\release\win-unpacked\Hermes.exe'
$dailyExe = 'C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe'
$dailyRoot = 'C:\Users\ddewit\AppData\Local\hermes\hermes-agent'
$path = [string]$row.ExecutablePath
Write-Output 'ATTEST_SOURCE=CIM'
Write-Output ('ATTEST_PID=' + $printedPid)
Write-Output ('ATTEST_SESSION=' + [string]([int]$row.SessionId))
Write-Output ('EXACT_EXECUTABLE_PATH=' + $path)
$cmd = [string]$row.CommandLine
$udd = 'C:\Users\ddewit\hermes-uat-a5-ns3-20260926\user-data'
$uddOk = ($cmd -like ('*--user-data-dir=' + $udd)) -or ($cmd -like ('*--user-data-dir=' + $udd + ' *')) -or ($cmd -like ('*--user-data-dir=' + $udd + '"*')) -or ($cmd -like ('*--user-data-dir="' + $udd + '"*'))
$sandboxOk = ($cmd -like '*--no-sandbox') -or ($cmd -like '*--no-sandbox *') -or ($cmd -like '*--no-sandbox=*')
$frozen = ($cmd -like '*hermes-uat-a5-ns-20260926*') -or ($cmd -like '*hermes-uat-a5-ns2-20260926*') -or ($cmd -like '*hermes-uat-a5-live-20260926*')
Write-Output ('ATTEST_CMDLINE_UDD=' + $(if ($uddOk) { '1' } else { '0' }))
Write-Output ('ATTEST_CMDLINE_SANDBOX=' + $(if ($sandboxOk) { '1' } else { '0' }))
Write-Output ('ATTEST_CMDLINE_FROZEN=' + $(if ($frozen) { '1' } else { '0' }))
if ([string]::IsNullOrEmpty($path)) {
  Write-Output 'ATTEST_STOP=CUA_CANNOT_SEE_PROCESS_PATH'
  exit 2
}
$dailyHit = $false
$rootSlash = $dailyRoot + '\'
foreach ($prefix in @('', '\\?\', '\??\')) {
  $item = $path
  if ($prefix -ne '') {
    if ($path.Length -lt $prefix.Length) { continue }
    if (-not [string]::Equals($path.Substring(0, $prefix.Length), $prefix, $ordinal)) { continue }
    $item = $path.Substring($prefix.Length)
  }
  $slash = $item.Replace('/', '\')
  $under = $false
  if ($slash.Length -gt $rootSlash.Length) {
    $under = [string]::Equals($slash.Substring(0, $rootSlash.Length), $rootSlash, $ignore)
  }
  if ([string]::Equals($slash, $dailyExe, $ignore) -or [string]::Equals($slash, $dailyRoot, $ignore) -or $under) {
    $dailyHit = $true
  }
}
if ($dailyHit) {
  Write-Output 'ATTEST_STOP=FOCUS_IS_DAILY'
  exit 2
}
if ([int]$row.SessionId -ne 1) {
  Write-Output 'ATTEST_STOP=ATTEST_WRONG_SESSION'
  exit 2
}
if (-not [string]::Equals($path, $uat, $ordinal)) {
  Write-Output 'ATTEST_STOP=ATTEST_PATH_MISMATCH'
  exit 2
}
if ((-not $uddOk) -or (-not $sandboxOk) -or $frozen) {
  Write-Output 'ATTEST_STOP=ATTEST_CMDLINE_REJECTED'
  exit 2
}
Write-Output 'ATTEST_MATCH=UAT'
exit 0
```

`Get-CimInstance Win32_Process` filtered by `ProcessId` is the query. It is the equivalent that still returns `ExecutablePath` when `MainModule` is blank for the CUA caller. Do not also call `Get-WmiObject` in the same action. A second query is a second pid. Do not call `Get-Process`. Do not call `Invoke-CimMethod` except `GetOwner`. `GetOwner` does not create a process. Do not call `Create` or `Delete` on `Win32_Process`.

The attestation process exit code is not a `launch-a5.ps1` exit and it is not a click grant. Exit 0 is a click grant only together with a CLEAN parse. Do not apply the launch exit table to an `ATTEST_STOP=` line. `ATTEST_WRONG_OWNER` is not launch exit 10, and it does not kill. These stop lines do not kill a pid and do not authorize a Launch:

| Line | When |
| --- | --- |
| `CUA_CANNOT_SEE_PROCESS_PATH` | The SSH logon is missing, the pid is not one row, the path line is missing or blank, the stdout cannot be parsed, or the HWND pid changed after the query. |
| `FOCUS_IS_DAILY` | The path is the daily exe, the daily install root, or a file under that root. Prefixes `\\?\` and `\??\` and either slash still count. |
| `ATTEST_PATH_MISMATCH` | The path is non-blank, not daily, and not byte-for-byte the staged UAT exe. A `\Device\` path is this stop. Do not translate it. |
| `ATTEST_WRONG_SESSION` | `SessionId` is not 1. |
| `ATTEST_PID_MISMATCH` | The printed pid is not the candidate pid, or the window pid and the element pid disagree. |
| `ATTEST_POWERSHELL` | The attestation host's major version is not 5. Do not substitute `pwsh.exe`. |
| `ATTEST_WOW64` | The host is `SysWOW64` PowerShell. A 32-bit host is a known way to blank a 64-bit image path. |
| `ATTEST_CMDLINE_REJECTED` | The command line does not pin `--user-data-dir` to the ns3 `user-data` directory, lacks a bounded `--no-sandbox`, mentions a frozen attempt, or is blank. |
| `ATTEST_WRONG_OWNER` | The SSH user is not `ddewit`, or the process owner was read and is not `ddewit`. A failed owner read is `CUA_CANNOT_SEE_PROCESS_PATH`. Owner `ddewit` is required and is not sufficient. This line does not kill. |
| `RUNBOOK_DRIFT` | The script text changed, or `UAT_EXE` / `ATTEST_MATCH=UAT` disagrees with the path bytes. |

4. Delete every CR (`U+000D`) from the SSH stdout before any other parse. Do the same for helper stdout before reading `UIA_` lines. Windows PowerShell ends lines with CRLF. A CR is not `ATTEST_NOT_CLEAN` and it is not a stop. Do not stop `ATTEST_OUTPUT_CR_NORMALIZATION_REQUIRED`. After those bytes are gone, parse text lines. Ignore blank lines. Do not read stderr. Split `EXACT_EXECUTABLE_PATH=` on the first `=` only. The value is the exact remainder, with no trim, no quote stripping, no slash change, and no case fold. A transcript that is CLEAN after this deletion is CLEAN. Run the helper. A transcript that is still not CLEAN does not get a helper run.
   - More than one `EXACT_EXECUTABLE_PATH=` line, or more than one `ATTEST_STOP=` line: `RUNBOOK_DRIFT`.
   - Exactly one `ATTEST_STOP=` line: that line is the stop. It wins over exit code 0 and over any missing success line. Do not relabel it. Do not continue to step 5.
   - Zero `ATTEST_STOP=` lines: require exactly one of each of `ATTEST_USER=ddewit`, `ATTEST_SOURCE=CIM`, `ATTEST_PID=` plus the candidate digits, `ATTEST_SESSION=1`, `ATTEST_CMDLINE_UDD=` whose value is `0` or `1`, `ATTEST_CMDLINE_SANDBOX=` whose value is `0` or `1`, `ATTEST_CMDLINE_FROZEN=` whose value is `0` or `1`, `ATTEST_MATCH=UAT`, and `EXACT_EXECUTABLE_PATH=`. A miss or a repeat is `CUA_CANNOT_SEE_PROCESS_PATH`.
5. Step 5 is the independent check of a transcript that printed no stop line. An edited script that prints `ATTEST_MATCH=UAT` for a daily path still dies here. Stop on the first hit. Do not click.
   1. `path` empty: `CUA_CANNOT_SEE_PROCESS_PATH`.
   2. `path` equals `C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`, equals `C:\Users\ddewit\AppData\Local\hermes\hermes-agent`, or has that root plus `\` as a prefix, compared ordinal-ignore-case. Also try the same test after removing one leading `\\?\` or `\??\`, and after replacing `/` with `\`. Any hit is `FOCUS_IS_DAILY`.
   3. `path` is not ordinal-equal to the staged UAT exe: `ATTEST_PATH_MISMATCH`. Do not case-fold this comparison. `ATTEST_MATCH=UAT` does not repair it. The staged path is ASCII. Ordinal equality of those characters is the byte-for-byte check.
   4. Command-line flags are not `1`, `1`, and `0`: `ATTEST_CMDLINE_REJECTED`. A blank command line is this stop. Do not enable `SeDebugPrivilege` to fill it. Path equality alone does not cover a process whose command line is not the ns3 wrapper.
6. Do not send the input yourself, and do not re-read a HWND from a CUA tool. BarrX has no UIA handle. Immediately after a CLEAN parse, run the helper once for this action, on this pid. The helper waits for the control, re-queries CIM once by this pid, re-finds the element, and sends one input only when the element pid and the top-level HWND pid both equal this pid. A zero HWND fails closed. If the helper prints `UIA_PID_MISMATCH` or `UIA_TARGET_CHANGED`, stop. Do not attest a replacement pid inside this action. Do not sleep, do not switch windows, and do not start another helper action between this SSH transcript and that one helper run.
7. A result is CLEAN only when step 5 did not stop, the attestation exit code is 0, and `ATTEST_MATCH=UAT` was printed. CLEAN is not the click. The helper sends the one click, type, or foreground change. Do not also click. Do not type the password unless this action's own SSH result is CLEAN and the helper action is `TypePassword`. The helper reads the secret file itself. Do not put the secret on the command line.
8. The next click, type, or foreground change starts again at step 1.

Cursor private worker: if this SSH attestation cannot be made CLEAN, stop with the line above. Leave the UAT pid and daily Hermes running. Do not run the helper for that action. Do not click. Do not launch. A private worker on Beelink is the alternate unlock for a later decision. It is not connected. This procedure does not start one, and it does not wait for David to start one before the SSH query. A worker is not permission to click by title. The same holds when the helper cannot start. Try the SSH script and the helper first.

#### Session-1 helper drive

BarrX does not click. The helper clicks, and only through PsExec `-i 1` after the SSH gate. Do not use daily Hermes `computer_use`. Do not drive UIAutomation from the SSH PowerShell. That process is not on the interactive desktop.

Copy the helper, unmodified, to `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\a5-session1-uia.ps1` and require one of the two helper hashes above. Confirm `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec64.exe` exists. If it does not, stop `PSEXEC_MISSING`. Do not download PsExec. Do not put it on `PATH`. Do not start a worker to replace it.

The PowerShell host is `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`. Do not use `pwsh.exe`. Do not use `SysWOW64\WindowsPowerShell`. The helper's only `Get-Process` is `Get-Process -Id $PID`, to read its own session. It does not call `Get-Process` on the UAT pid. The SSH script above still does not call `Get-Process`.

Generate a new nonce for every invocation, including Discover. It must match `^[1-9][0-9]{8,18}$`. Do not reuse a nonce. Discover omits `-AttestedPid`. Every other action passes `-AttestedPid` as the SSH candidate pid.

```text
C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec64.exe -accepteula -nobanner -i 1 -w C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\a5-session1-uia.ps1 -Action ACTION -AttestedPid PID -Nonce NONCE
```

Do not add `-d`, `-s`, or `-u`. Do not pass a password, a username, or a destination path. If the SSH channel is still open after 45 seconds, stop `UIA_HELPER_HUNG`. Do not kill Hermes. Do not `taskkill /IM`. You may stop only a `powershell.exe` whose command line contains `a5-session1-uia.ps1` when exactly one such process exists. If that identification is not exact, leave the process.

Helper success, all required:

- PsExec's exit code is 0. The transcript contains `UIA_PSEXEC_IS_NOT_A_PID=1`. That exit code is not a pid and it is not a launch exit.
- `UIA_HELPER=1` is present. If stdout does not begin with that field, stop `RUNBOOK_DRIFT`.
- Exactly one `UIA_NONCE=` equal to the nonce just sent. Any other value is stale. Stop `RUNBOOK_DRIFT`.
- Exactly one `UIA_ACTION=` equal to the action just sent.
- No `UIA_STOP=` line. One `UIA_STOP=` line is the stop. It wins over exit code 0 and over `UIA_PATH_OK=1`. Do not relabel it. Do not kill.
- Exactly one `UIA_DONE=1`. Exit 0 without `UIA_DONE=1` is not success.
- No stdout line whose entire text is a digit. `2` and `0` are not fields. That line is `RUNBOOK_DRIFT`. The process exit code is an integer from PsExec, not a line of helper stdout.
- Helper stdout was CR-stripped before these checks, same as the SSH transcript.
- `UIA_HOST_SESSION=1`, `UIA_HOST_USER=ddewit`, and `UIA_DESKTOP=Default`.
- Except on Discover: exactly one `UIA_PATH_OK=1` and exactly one `UIA_PID=` equal to the SSH pid. If the SSH transcript immediately before this run was not CLEAN, stop `RUNBOOK_DRIFT` even when the helper exits 0.

Do not read stderr. Do not copy stderr into the receipt.

Discover is read-only. It prints `DISCOVER_NOT_A_GRANT=1`. It is not a click and it is not CLEAN. Require exactly one `DISCOVER_ENTRY_PID=` and exactly one `DISCOVER_ENTRY_RUNG=` of `WINDOW`, `REMOTE`, or `SIGNOUT`. `DISCOVER_UAT_PID`, `DISCOVER_WINDOW`, and `DISCOVER_ROW_COUNT` may repeat. A `DISCOVER_WINDOW` title is not a grant. Do not act on the first `DISCOVER_UAT_PID` when it is not `DISCOVER_ENTRY_PID`. `DISCOVER_DAILY_SEEN` does not authorize touching daily Hermes. If a granted main process and a granted child both show an entry control, the helper keeps the main. If no `DISCOVER_ENTRY_PID` is printed, stop with the helper's `UIA_STOP`.

Every later action uses that same pid. Focus and type are separate actions. Each one gets a new SSH attestation, a new nonce, and one helper run.

#### Login steps

A heading, a description, or a hint is not a control. `Remote gateway sign-in required` is a heading. Do not accept `Sign out and sign in`. Do not click `Gateway settings`, `Use local gateway`, `Open logs`, `Retry`, or `Repair`. A daily window with the same title is not an entry control. `FOCUS_IS_DAILY` is that refusal. The helper chooses one entry control, in this order, and uses it once: the window titled exactly `Sign in to Hermes gateway`, else the button named exactly `Sign in to remote gateway`, else the button named exactly `Sign out & sign in`. Two matches on the rung that is reached stop `SIGNIN_CONTROL_AMBIGUOUS`. Absent controls wait up to 20 seconds and then stop `SIGNIN_CONTROL_ABSENT`. The helper does not fall through to another rung after a foreground failure.

1. Run Discover once, with no SSH script and no `-AttestedPid`.
2. `Entry`. Require `ENTRY_INVOKED=1`, `UIA_DONE=1`, no `UIA_STOP=`, and `UIA_ENTRY_RUNG` of `WINDOW`, `REMOTE`, or `SIGNOUT`. A rung without `UIA_DONE=1` did not click. Do not run `FocusUsername`. The 2026-09-26 Entry is that case. Re-run `Entry` on pid 42312 after a CR-normalized CLEAN attestation. Do not start at `FocusUsername`. Do not run `Entry` again after one that printed `ENTRY_INVOKED=1` and `UIA_DONE=1`.
3. `FocusUsername`. Require `FOCUS_OK=1`.
4. `TypeUsername`. Require `TYPED_USERNAME=1`. The helper reads `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\uat-username.txt` and strips one trailing newline. It does not print the text. An empty file stops `USERNAME_UNAVAILABLE`.
5. `FocusPassword`. Require `FOCUS_OK=1`.
6. `TypePassword`. Require `TYPED_PASSWORD=1`. The helper reads `uat-password.txt` the same way. It does not print the text. An empty file stops `PASSWORD_UNAVAILABLE`. Do not screenshot.
7. `ClickSignIn`. Require `SIGNIN_INVOKED=1`. That button's whole name is `Sign in`, and its window title is `Sign in to Hermes gateway`. It is not `Sign in to remote gateway`.
8. Poll `ReadLogin` at most six times. Run the first immediately. Separate the reads by at least four seconds. Do not start a read after 30 seconds from `ClickSignIn`. Each read is a new SSH attestation and a new nonce. Continue only when one read has `LOGIN_WINDOW=0`, `USERNAME_VISIBLE=0`, `LOGIN_ERROR=none`, and `UIA_DONE=1`. If the helper prints `UIA_STOP=LOGIN_REJECTED`, or `LOGIN_ERROR` is `invalid` or `throttle`, stop `LOGIN_REJECTED`. Do not retry. If the sixth read still has `LOGIN_WINDOW=1`, or 30 seconds have passed, stop `LOGIN_NOT_FINISHED`. The UAT main window staying open is not this stop. Do not relaunch. The only login strings that count are `Invalid username or password.` and `Too many attempts. Please wait and try again.` The helper does not print any other error text.

### Phase 3 — Cancel

Every helper action runs the SSH attestation again on `DISCOVER_ENTRY_PID`. A CLEAN result from login has expired. The eligible `Files` control is a button, tab, or split button named exactly `Files` whose siblings are not the Artifacts set `All`, `Images`, `Files`, and `Links`. Do not click `File system`. Do not click a button named `Download`. The file row is one enabled, visible, non-folder `TreeItem`, `ListItem`, or `DataItem` whose name is not chrome. The helper opens that row's menu and invokes one `MenuItem` named `Download`.

1. `ClickFiles`. Require `FILES_INVOKED=1`. Absent waits up to 20 seconds (`FILES_CONTROL_ABSENT`). Two eligible controls stop `FILES_CONTROL_AMBIGUOUS` without waiting.
2. `ClickDownload`. Require `DOWNLOAD_INVOKED=1` and `FILE_ROW_COUNT=1`. `UIA_STOP=FILES_ROW_AMBIGUOUS` means the eligible row count is not 1. Read `FILE_ROW_COUNT`. Zero is none. Greater than one is several. Do not pick a row. A missing menu item stops `DOWNLOAD_CONTROL_ABSENT`. Two menu items stop `DOWNLOAD_CONTROL_AMBIGUOUS`.
3. `ReadSaveDialog`. Require `DIALOG_PRESENT=1` and `DIALOG_BUTTONS=Cancel,Save`. The dialog title is exactly `Save File` and it must belong to the attested pid. Two such windows stop `DIALOG_LOCALE_UNEXPECTED`. Absent waits up to 20 seconds and then stops `SAVE_DIALOG_ABSENT`. Do not switch pids to find a dialog.
4. `ClickCancel`. Require `CANCEL_INVOKED=1`. Do not run `TypeDest`. Do not run `ClickSave`.

### Phase 4 — assert Cancel wrote nothing

Run this from the SSH PowerShell, not through PsExec and not through the helper:

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1 -Phase AssertDestEmpty
```

Continue only when the exit code is 0 and the output contains `DEST_EMPTY`. `CANCEL_WROTE_OR_DIRTY` (exit 21) or `ATTEMPT_MISSING` (exit 23) stops the procedure. Do not delete the unexpected file and continue.

### Phase 5 — Save

Every helper action runs the SSH attestation again. Repeat the download and the save-dialog read. If Phase 1 `DEST_FILE` is not `C:\Users\ddewit\hermes-uat-a5-ns3-20260926\dest\uat-download.bin`, stop `RUNBOOK_DRIFT` and do not type. The helper types that path itself. Do not pass it as an argument.

1. `ClickDownload`. Same success lines as Phase 3.
2. `ReadSaveDialog`. Same success lines as Phase 3.
3. `TypeDest`. Require `DEST_SET=1`. The file-name control is the one `Edit` or `ComboBox` named exactly `File name:`. Any other count stops `DIALOG_LOCALE_UNEXPECTED` before the grant, or `UIA_TARGET_CHANGED` after it. A read-only value does not fall through to keystrokes.
4. `ClickSave`. Require `SAVE_INVOKED=1`.
5. `ReadReplace`. Require `REPLACE_WINDOW=0` or `REPLACE_WINDOW=1`, with `UIA_DONE=1`. A window whose buttons are `Yes` and `No`, and not `Save`, is the prompt. The Save File dialog is not a prompt. `Yes` without `No` stops `REPLACE_PROMPT`. Do not click `Yes`.
6. If `REPLACE_WINDOW=1`, run `ClickReplaceNo` and require `REPLACE_NO_INVOKED=1`. If the prompt is gone or malformed, the helper stops `REPLACE_PROMPT` and does not click. If `REPLACE_WINDOW=0`, do not run `ClickReplaceNo`.

### Phase 6 — record the hash

Run this from the SSH PowerShell, not through PsExec and not through the helper:

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1 -Phase Hash
```

Exit 0 prints `DEST_BYTES=`, `DEST_SHA256=`, and `HASH_RECORDED_NOT_A_PASS`. Keep those three lines. Do not compare the hash. Do not call the result PASS. Exit 22 `DEST_FILE_MISSING` means the dialog did not write that exact path. Do not hash a neighboring file.

Leave the UAT pid and daily Hermes running. This procedure has no cleanup kill.

## Adversarial review

Reviewer question: would this send BarrX back into ns2, grant an AppContainer ACE, kill daily Hermes, or treat another blind `MainWindowHandle` zero as `NO_WINDOW`?

First pass found three kill holes. A UAT window smaller than 400 by 500 was ignored, so the 90 second path still selected `NO_WINDOW`. The parent trusted `PROBE_OK=1` without requiring desktop `Default`, so a session-1 probe on another desktop could still select `NO_WINDOW`. After `CMDLINE_OK`, an unexpected exception still fell through to the tree kill. Those three now fail closed: only-small windows become `WINDOW_UNSTABLE` and leave the pid; a desktop other than `Default` is `probe-bad` and leaves the pid; after `CMDLINE_OK` the pid stays unless the stop is one of the deliberate kills listed above.

Re-review of that delta:

- The kill decisions are `hidden`, `no-window`, and `profile`, plus the earlier deliberate stops for daily death, daily profile stamp, and UAT early exit. `probe-bad`, `unstable`, `wait`, and `stable` do not kill. An unexpected decision leaves the pid running. A probe whose session is not 1, whose owner is not `ddewit`, or whose desktop is not `Default` cannot select `no-window`. Tests call `Get-A5WindowDecision` and `Test-A5WindowKill` directly, including the small-window and wrong-desktop cases.
- Freshness follows the probe file's last new write time, with a 5 second bound. Reading the same bytes again does not make a dead probe look fresh. Status updates use `File.Replace` so a reader does not observe a deleted file.
- The probe process is stopped only when its executable is `powershell.exe` and its command line contains this attempt's `window-probe.ps1`. The kill has no `/T`. The staged exe and the daily exe are refused.
- Rollback removes only an ns3 folder this invocation created before PsExec started. It refuses ns, ns2, the evidence attempt, the user profile, and both Hermes install roots.
- ns, ns2, and the evidence attempt are never deleted, renamed, or launched. The wrapper and the probe text are refused if they contain those paths. A printed marker state is not a launch grant.
- `--no-sandbox` stays inside the UAT wrapper. There is no `setx` and no `/grant`.
- The protocol key is still exported before launch and restored by the script. A failed restore prints `PROTOCOL_RESTORE_FAILED` and does not start CUA.
- `WINDOW_STABLE` without exit code 0 does not start CUA. Exit 0 without `WINDOW_STABLE` does not start CUA. One Launch only. Do not invent ns4.
- The probe script parses, and its window-scan type compiles. That compile does not execute on Beelink. Beelink was not run.

That pass was re-review 2 of the kill path. Re-review 3 is the Phase 2 entry-control delta.

Reviewer question for this delta: would BarrX click hint text, sign into daily Hermes, click `Use local gateway`, click `Sign out & sign in` twice, treat the UAT main window as the login window that must close, or take a screenshot of the password?

The live overlay heading is `Remote gateway sign-in required`. The button on that overlay is `Sign out & sign in`. Hint text can contain the words `Sign in to remote gateway` without that string being a button. The old step stopped `SIGNIN_CONTROL_ABSENT` because it allowed only the login-window title and that remote-gateway button. The procedure now accepts entry controls only in this order, and the refusal list is in the same step as the click: the exact window title `Sign in to Hermes gateway` if that window is open, else the exact button `Sign in to remote gateway`, else the exact button `Sign out & sign in`. Headings and hints are not clicks. `Gateway settings`, `Use local gateway`, and `Open logs` are not entry controls. Two matches on the rung that is reached stop `SIGNIN_CONTROL_AMBIGUOUS` with no click. The chosen entry control is used once. On the form, `Sign in` is the whole button name, and neither entry button is pressed again. The login window that must close is the one titled `Sign in to Hermes gateway`. The UAT main window stays, the `Username` field must be gone, and `UAT_MAIN_PID` must still be running. The launch script does not embed these CUA steps. Its bytes and both hashes above are unchanged. This edit does not launch, does not kill by image name, does not echo a password, and does not authorize a password screenshot.

That pass was re-review 3 of the entry controls. Re-review 4 is the SSH path gate.

Reviewer question for this delta: would BarrX click daily Hermes because the CUA path was blank, click by title, match `Hermes.exe`, reuse one attestation, treat attestation exit 0 as a click, launch ns3 again, enable `SeDebugPrivilege`, grant an ACE, kill a pid, or wait for David to start a private worker before trying SSH?

The CUA blank path is expected and is not a query. The only query is one SSH `Get-CimInstance Win32_Process` by `ProcessId`, as `ddewit`, immediately before the action. Continue requires ordinal equality with the staged exe and with the Phase-1 `UAT_EXE` line, plus `ATTEST_MATCH=UAT`, session 1, owner `ddewit`, and the ns3 command-line pins. Daily classification uses ordinal-ignore-case, runs before a match, and includes the install root with a `\` boundary plus `\\?\`, `\??\`, and either slash. Owner `ddewit` is not sufficient. Exit 0 is not sufficient. `ATTEST_STOP=` wins over exit 0 and is not relabeled when success lines are missing. A `\Device\` path is `ATTEST_PATH_MISMATCH` and is not translated. Image name is not a filter. Title chooses the control and does not authorize input. One result covers one click, one type, or one foreground change. PsExec, `Get-WmiObject` in the same action, `Get-Process`, `SeDebugPrivilege`, and a second Launch are refused. A hung attestation kills nothing. The command line pin is an additional stop: the same UAT exe opened on a frozen profile must not be clicked, and a blank command line does not fall through to a path-only click. `NATIVE_A5_BEELINK_EARLY_EXIT_LAUNCH.Tests.ps1` does not cover this decision text. It was not changed. The launch script was not changed.

Re-review 4 found one kill-shaped name collision. The attestation stop was `WRONG_OWNER`, which is also launch exit 10, and that launch stop kills the UAT tree. A failed `GetOwner` used the same line, so a dead pid could be read as a reason to change user or to kill. The attestation stop is now `ATTEST_WRONG_OWNER`, and only when the owner was actually read and is not `ddewit`. A failed owner read is `CUA_CANNOT_SEE_PROCESS_PATH`. The launch exit table does not apply to `ATTEST_STOP=`. `ATTEST_WRONG_OWNER` does not kill.

No Cursor private worker is connected. The procedure tries the SSH attestation first. If that attestation is not CLEAN, it stops. The worker is the alternate unlock after that stop. It is not a step, and it is not permission to click by title.

Re-review 5 checked that rename against the gate. No attestation stop line is a launch exit line. `ATTEST_WRONG_OWNER` does not select `Stop-UatTree`. Daily still stops as `FOCUS_IS_DAILY` before `ATTEST_MATCH=UAT`. A match flag does not override a daily path in step 5. The launch script bytes are unchanged.

Re-review 5 also required the daily prefix test to use `Substring` and `String.Equals`. `String.StartsWith` with a `StringComparison` argument is off this path, so a binder miss cannot throw after a real UAT path has been printed and turn that match into `CUA_CANNOT_SEE_PROCESS_PATH`. The prefix rule is unchanged: ordinal-ignore-case equality with the daily exe or the daily root, or a longer path whose first characters are that root plus `\`.

That pass was re-review 6 of the SSH gate. Re-review 7 is the session-1 helper that sends the input.

Reviewer question for this delta: would BarrX start `agent worker start` before trying the helper, use daily Hermes `computer_use`, click a Discover title or the first `DISCOVER_UAT_PID`, skip the SSH script because `UIA_PATH_OK=1`, treat the PsExec exit as a pid or a launch exit, kill on `UIA_STOP`, relaunch ns3 or start ns4, click the Artifacts `Files` tab or a `Download` button, type the destination before `AssertDestEmpty`, or call the hash PASS?

The actuator is PsExec `-i 1` without `-d`, `-s`, or `-u`, running the helper. That is not the path query. Re-review 4 refused PsExec inside the SSH attestation, and that refusal still stands: the script in the fence above is unchanged and still says not to use PsExec. The helper runs only after a CLEAN parse of that script, except Discover, which does not click and prints `DISCOVER_NOT_A_GRANT=1`. `UIA_PATH_OK=1` does not replace `ATTEST_MATCH=UAT`. `UIA_STOP=` wins over helper exit 0. The launch exit table does not apply. `UIA_PSEXEC_IS_NOT_A_PID=1` is that distinction. A hung helper stops `UIA_HELPER_HUNG` and does not kill Hermes.

Discover may list more than one granted pid. The entry pid prefers `role=main` when both a main and a child show an entry control. Later actions do not switch pids. A save dialog on another pid stops `SAVE_DIALOG_ABSENT`. Titles are not grants. Entry order is still the login window, then `Sign in to remote gateway`, then `Sign out & sign in`. Headings are not clicks. `Sign in` is a separate action from those entry buttons. Files rejects the Artifacts sibling set and `File system`. Download is a `MenuItem`. Cancel finishes, then Phase 4 runs `launch-a5.ps1 -Phase AssertDestEmpty` from the SSH PowerShell, and only then does Phase 5 type and save. Phase 6 prints `HASH_RECORDED_NOT_A_PASS` and is not PASS. The helper has no password parameter, does not print the secret, and does not take a screenshot. Unknown stop text and unknown field names become `RUNBOOK_DRIFT` instead of echoing the rejected text.

The worker is still not a step. It is an alternate only after SSH cannot be made CLEAN or the helper cannot start. This procedure does not start one. It does not relaunch. ns, ns2, and the evidence attempt stay frozen. The launch script bytes and both launch hashes above are unchanged. Desktop product strings are unchanged. Beelink was not executed from this checkout. The decision tests call the helper's functions and one rejected-nonce process. They do not click. They are not copied to Beelink.

That pass was re-review 7. Re-review 8 is the live Entry transcript from helper LF `6e133bd5…`.

Reviewer question for this delta: would BarrX treat the bare `2` as a finished Entry, run `FocusUsername` without `UIA_DONE`, call a CRLF attestation unclean, hide `UIA_STOP` inside `if (function)`, or pass a leaked `0` into `GetWindowThreadProcessId`?

The 20:50 Entry transcript is not success. `UIA_ENTRY_RUNG=SIGNOUT` without `ENTRY_INVOKED=1` and `UIA_DONE=1` did not invoke `Sign out & sign in`. `FocusUsername` is not the next action. `UIA_CONTROL_ABSENT` on that later action is the missing login form, and it does not authorize another username search until Entry succeeds. Fields and stops go to `[Console]::Out`, which `if` and assignment cannot swallow. The exit code stays in `$script:A5ExitCode` and is not written as a digit line. `++` is not used, so a 5.1 success-stream integer cannot ride along with the hwnd. A hwnd result that is not an `IntPtr`, or that is zero, fails closed as `UIA_PID_MISMATCH` and does not click. SSH script bytes are unchanged. BarrX deletes CR from SSH stdout and from helper stdout, then applies the existing CLEAN rules. `ATTEST_OUTPUT_CR_NORMALIZATION_REQUIRED` is not a stop. A normalized CLEAN transcript runs the helper. A helper `UIA_STOP=` wins over exit 0. Launch hashes are unchanged. No worker. No relaunch. No daily `computer_use`. Hash remains `HASH_RECORDED_NOT_A_PASS`.

**Adversarial review: CLEAN.** Re-review count: 8. No open finding inside this procedure. Native Files PASS is not claimed. This procedure does not reboot, change the reserve, grant an ACE, or take a second launch of a user-data directory that may be `booting`. `LEAVE_UAT_RUNNING` is a finished stop: report the pid and do not clear it. A helper stop leaves both trees running.
