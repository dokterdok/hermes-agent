# Native A5 Beelink NO_WINDOW — diagnosis and mechanical runbook

**Status:** diagnosis and runbook only. Native Files UAT is not PASS. This document does not change Desktop product code. Do not run the previous launch script. Its LF SHA-256 is `56edee4a6212b52ad0dfd8c9db85a37289aedd127e6327b9732841dbc85ab5f6`. That is the script that killed pid 38980.

**Launch script (do not retype):** `review-packages/NATIVE_A5_BEELINK_EARLY_EXIT_LAUNCH.ps1`

**SHA-256, LF bytes (git blob and GitHub raw):** `9f0277fe04704d2677191112dcba1dd9b4df1c7bf588752afc581a2ebf98fbf3`

**SHA-256, CRLF bytes (a Windows checkout; `*.ps1` is `text eol=crlf`):** `a5d39b39e9c21cafafe224c00820625428e87a5271f90e1fc72c7f07eabbb2e0`

Either hash is this script. If the copy matches neither, stop with `RUNBOOK_DRIFT`. Do not edit the script to fix a mismatch, a port, a path, a username, or the line endings.

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
- Do not click `Retry`, `Repair`, `Gateway settings`, or `Open logs`.
- Do not print, log, or screenshot the fixture password or username.
- If the script prints `KILL_REFUSED` or `KILL_ERROR`, stop. Do not escalate to an image-name kill.
- If the script prints `UAT_EXE_ALREADY_RUNNING`, stop. Do not kill that pid. Do not launch the daily exe instead.
- If daily Hermes is not seen, do not start it. If the script prints `DAILY_HERMES_DIED`, do not restart it.
- Do not put PsExec on `PATH`. Do not download PsExec. The script opens `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec64.exe` itself when that file exists.

## What remains unproven

A stable window does not prove login, Cancel, Save, or the fixture bytes. `HASH_RECORDED_NOT_A_PASS` does not prove them either. Nothing in this procedure compares the hash to the fixture payload. Whether pid 38980 had a visible window is unproven. The next launch's `WINDOW_PROBE_SESSION`, `UAT_WINDOW`, `UAT_WINDOW_HIDDEN`, and `UAT_WINDOW_SMALL` lines are what separate a blind detector from a window that never became visible. This procedure does not launch ns2 to settle that.

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

Use only windows whose process executable path equals the `UAT_EXE` line. If the CUA tool cannot show that path for the window it is about to click, stop `CUA_CANNOT_SEE_PROCESS_PATH`. Do not click by title alone. If the foreground executable is the daily exe, stop `FOCUS_IS_DAILY` without clicking.

Do not take a screenshot while the password field is focused or contains text.

1. If a UAT window titled exactly `Sign in to Hermes gateway` is open, use it. Otherwise click the button named exactly `Sign in to remote gateway` in a UAT window.
2. If that window or button is not present within 20 seconds, stop `SIGNIN_CONTROL_ABSENT`.
3. Click the field labeled `Username`. Type the fixture username from orchestrator memory or from `uat-username.txt`. Do not echo it.
4. Click the field labeled `Password`. Type the contents of `uat-password.txt`. Do not echo them.
5. Click the button named exactly `Sign in`. Do not press a button named `Sign in to remote gateway` on this form. Do not click `Retry` or `Repair`.
6. If the page shows `Invalid username or password.` or `Too many attempts. Please wait and try again.`, stop `LOGIN_REJECTED`. Do not retry.
7. If the login window is still open after 30 seconds, stop `LOGIN_NOT_FINISHED`.
8. Continue only when that login window is gone and the UAT main process from `UAT_MAIN_PID` is still running.

### Phase 3 — CUA Cancel

Still only on the UAT executable.

1. Find a control named exactly `Files` that does **not** sit with sibling tabs named `All`, `Images`, `Files`, and `Links`. That sibling set is the Artifacts tab. Do not use it.
2. Do not click `File system`. Do not click `Download` until step 4.
3. If no eligible `Files` control appears within 20 seconds, stop `FILES_CONTROL_ABSENT`. If more than one eligible `Files` control exists, stop `FILES_CONTROL_AMBIGUOUS`.
4. Activate that `Files` control. If there is not exactly one file row, stop `FILES_ROW_AMBIGUOUS`.
5. Activate the control named exactly `Download` on that row.
6. Wait for a dialog titled exactly `Save File` owned by the UAT executable. If the buttons are not exactly `Cancel` and `Save`, stop `DIALOG_LOCALE_UNEXPECTED`.
7. Click `Cancel`. Do not type a path. Do not click `Save`.

### Phase 4 — assert Cancel wrote nothing

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1 -Phase AssertDestEmpty
```

Continue only when the exit code is 0 and the output contains `DEST_EMPTY`. `CANCEL_WROTE_OR_DIRTY` (exit 21) or `ATTEMPT_MISSING` (exit 23) stops the procedure. Do not delete the unexpected file and continue.

### Phase 5 — CUA Save

Repeat Phase 3 steps 4 through 6 on the same row. In the box labeled `File name:`, replace the contents with the exact `DEST_FILE` path from Phase 1. Click `Save`.

If a replace confirmation appears, click `No` when that exact button exists. Otherwise stop `REPLACE_PROMPT`. Do not click `Yes`.

### Phase 6 — record the hash

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

**Adversarial review: CLEAN.** Re-review count: 2. No open finding inside this procedure. Native Files PASS is not claimed. ns and ns2 stay frozen. This procedure does not reboot, change the reserve, grant an ACE, or take a second launch of a user-data directory that may be `booting`. Beelink was not executed from this checkout. `LEAVE_UAT_RUNNING` is a finished stop: report the pid and do not clear it.
