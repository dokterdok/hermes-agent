# Native A5 Beelink NO_WINDOW — diagnosis and mechanical runbook

**Status:** diagnosis and runbook only. Native Files UAT is not PASS. This document does not change Desktop product code. Do not run the previous launch script. Its LF SHA-256 is `56edee4a6212b52ad0dfd8c9db85a37289aedd127e6327b9732841dbc85ab5f6`. That is the script that killed pid 38980.

**Launch script (do not retype):** `review-packages/NATIVE_A5_BEELINK_EARLY_EXIT_LAUNCH.ps1`

**SHA-256, LF bytes (git blob and GitHub raw):** `9f0277fe04704d2677191112dcba1dd9b4df1c7bf588752afc581a2ebf98fbf3`

**SHA-256, CRLF bytes (a Windows checkout; `*.ps1` is `text eol=crlf`):** `a5d39b39e9c21cafafe224c00820625428e87a5271f90e1fc72c7f07eabbb2e0`

Either hash is this script. If the copy matches neither, stop with `RUNBOOK_DRIFT`. Do not edit the script to fix a mismatch, a port, a path, a username, or the line endings.

**Phase 2 clicks** do not require the CUA tool to show `ExecutablePath`. Each click or type is gated by the SSH attestation in Phase 2. That attestation is not a Launch. The launch script bytes stay the two hashes above.

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
- Do not click a window because its title is Hermes, or because its image name is `Hermes.exe`.
- Do not enable `SeDebugPrivilege`. Do not run the Phase 2 attestation as any user other than `ddewit`.
- Do not start a Cursor private worker from this procedure. None is connected on Beelink. The worker is an alternate unlock only when the Phase 2 SSH attestation cannot be made CLEAN. Do not ask for one before that attestation has been tried.
- Do not run Launch a second time against ns3. The Phase 2 attestation is not a Launch.
- Do not put PsExec on `PATH`. Do not download PsExec. The script opens `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec64.exe` itself when that file exists.

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
- PsExec for this query. Image path is not a desktop property. PsExec here would be another interactive start.
- A second Launch of ns3, ns2, or ns, to "refresh" the path.
- Requiring David to start a Cursor private worker before this SSH query exists. None is connected. The worker remains the alternate unlock when this SSH attestation cannot be made CLEAN.

The hypothesis holds inside those limits. Immediately before each click or type, a read-only SSH PowerShell 5.1 process as `ddewit` prints `EXACT_EXECUTABLE_PATH` for that HWND's pid from CIM `Win32_Process`. The click is legal only when that path is the Phase-1 `UAT_EXE` line, byte for byte, and the rest of the CLEAN rule below is true.

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

This phase does not launch Hermes. It does not kill a process by image name. It does not take a second Launch. It does not start a Cursor private worker. A blank path from the CUA tool is expected and is not, by itself, the stop. The stop is an SSH attestation that is not CLEAN. Do not click by title. Do not match image name `Hermes.exe`. Pid 42312 is the pid that stopped the last resume. It is not a hardcoded target. Attest the pid of the control you are about to use.

Do not take a screenshot while the password field is focused or contains text. Do not echo the username or the password. The attestation does not read `uat-password.txt` or `uat-username.txt`.

#### Path attestation, before every click and every type

Run this gate immediately before each click, each type, and each foreground change in Phase 2, Phase 3, and Phase 5. A CLEAN result covers that one action and then expires. Login does not cover Cancel. Cancel does not cover Save. Do not retry a blank query inside the same action. One CIM query per action.

1. From UIA, take the element that will receive the input. Its candidate pid is `CurrentProcessId`. Its candidate HWND is `CurrentNativeWindowHandle` when that handle is not zero. When the handle is zero, use the top-level window HWND and still use the element's pid. If the pid is missing, or both handles are missing, stop `CUA_CANNOT_SEE_PROCESS_PATH`. If the top-level window's process id is present and differs from the element's pid, stop `ATTEST_PID_MISMATCH`. If UIA shows a session and it is not 1, stop `ATTEST_WRONG_SESSION`. Do not search processes by title or by image name.
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

4. Parse stdout as text lines. Ignore blank lines. Do not read stderr. Split `EXACT_EXECUTABLE_PATH=` on the first `=` only. The value is the exact remainder, with no trim, no quote stripping, no slash change, and no case fold.
   - More than one `EXACT_EXECUTABLE_PATH=` line, or more than one `ATTEST_STOP=` line: `RUNBOOK_DRIFT`.
   - Exactly one `ATTEST_STOP=` line: that line is the stop. It wins over exit code 0 and over any missing success line. Do not relabel it. Do not continue to step 5.
   - Zero `ATTEST_STOP=` lines: require exactly one of each of `ATTEST_USER=ddewit`, `ATTEST_SOURCE=CIM`, `ATTEST_PID=` plus the candidate digits, `ATTEST_SESSION=1`, `ATTEST_CMDLINE_UDD=` whose value is `0` or `1`, `ATTEST_CMDLINE_SANDBOX=` whose value is `0` or `1`, `ATTEST_CMDLINE_FROZEN=` whose value is `0` or `1`, `ATTEST_MATCH=UAT`, and `EXACT_EXECUTABLE_PATH=`. A miss or a repeat is `CUA_CANNOT_SEE_PROCESS_PATH`.
5. Step 5 is the independent check of a transcript that printed no stop line. An edited script that prints `ATTEST_MATCH=UAT` for a daily path still dies here. Stop on the first hit. Do not click.
   1. `path` empty: `CUA_CANNOT_SEE_PROCESS_PATH`.
   2. `path` equals `C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`, equals `C:\Users\ddewit\AppData\Local\hermes\hermes-agent`, or has that root plus `\` as a prefix, compared ordinal-ignore-case. Also try the same test after removing one leading `\\?\` or `\??\`, and after replacing `/` with `\`. Any hit is `FOCUS_IS_DAILY`.
   3. `path` is not ordinal-equal to the staged UAT exe: `ATTEST_PATH_MISMATCH`. Do not case-fold this comparison. `ATTEST_MATCH=UAT` does not repair it. The staged path is ASCII. Ordinal equality of those characters is the byte-for-byte check.
   4. Command-line flags are not `1`, `1`, and `0`: `ATTEST_CMDLINE_REJECTED`. A blank command line is this stop. Do not enable `SeDebugPrivilege` to fill it. Path equality alone does not cover a process whose command line is not the ns3 wrapper.
6. Re-read the same HWND's process id from UIA. If the HWND is gone or the pid differs, discard the stdout. Stop `CUA_CANNOT_SEE_PROCESS_PATH`. Do not attest a replacement pid inside this action. Do not sleep, do not switch windows, and do not start another UIA action between the pid read, this re-read, and the input.
7. A result is CLEAN only when step 5 did not stop, step 6 matched, the attestation exit code is 0, and `ATTEST_MATCH=UAT` was printed. Send exactly one click, one type, or one foreground change to that HWND. Do not send it to whatever window is foreground unless that foreground HWND is the attested HWND. Do not type the password unless this action's own result is CLEAN.
8. The next click, type, or foreground change starts again at step 1.

Cursor private worker: if this SSH attestation cannot be made CLEAN, stop with the line above. Leave the UAT pid and daily Hermes running. Do not click. Do not launch. A private worker on Beelink is the alternate unlock for a later decision. It is not connected. This procedure does not start one, and it does not wait for David to start one before the SSH query. A worker is not permission to click by title.

#### Login steps

Every click, every type, and every foreground change below includes a new run of the path attestation. The click on a field and the type into that field are two actions. Title chooses which control to attest. Title does not authorize the input. The control named in the step is the UIA element the gate attests. A heading, a description, or a hint is not a control. `Remote gateway sign-in required` is a heading. Do not click it. Words inside hint text are not a button. Do not accept `Sign out and sign in`. Do not click `Gateway settings`, `Use local gateway`, `Open logs`, `Retry`, or `Repair`. A daily window with the same title or the same button is not an entry control. The gate's `FOCUS_IS_DAILY` stop is that refusal.

1. Choose one entry control on the UAT executable, in this order, and use only that one. A heading, a description, or a hint is not a control. `Remote gateway sign-in required` is a heading. Do not click it. Words inside hint text are not a button. Do not accept `Sign out and sign in`. Do not click `Gateway settings`, `Use local gateway`, `Open logs`, `Retry`, or `Repair`. If the rung you reach matches more than one control, stop `SIGNIN_CONTROL_AMBIGUOUS` and do not click.
   1. If a UAT window titled exactly `Sign in to Hermes gateway` is open, bring that window to the foreground. Do not click `Sign in to remote gateway` or `Sign out & sign in` while it is open.
   2. Else if a button named exactly `Sign in to remote gateway` is in a UAT window, click that button once.
   3. Else if a button named exactly `Sign out & sign in` is in a UAT window, click that button once. That button is the boot overlay when a remote URL is set and the session cookie is missing.
2. If none of those three controls is present within 20 seconds, stop `SIGNIN_CONTROL_ABSENT`. After one of them has been used, do not stop `SIGNIN_CONTROL_ABSENT` and do not click a second entry control.
3. Click the field labeled `Username`. Type the fixture username from orchestrator memory or from `uat-username.txt`. Do not echo it. That field is on the login form. The form may already be in the window from step 1.1, or in the UAT window titled `Sign in to Hermes gateway` that opens after the button click. If that field is not present within 20 seconds after the entry control is used, stop `LOGIN_NOT_FINISHED`.
4. Click the field labeled `Password`. Type the contents of `uat-password.txt`. Do not echo them.
5. Click the button whose whole name is exactly `Sign in`. Do not press a button named `Sign in to remote gateway` or `Sign out & sign in` on this form. Do not click `Retry` or `Repair`.
6. If the page shows `Invalid username or password.` or `Too many attempts. Please wait and try again.`, stop `LOGIN_REJECTED`. Do not retry.
7. The login window is the UAT window titled exactly `Sign in to Hermes gateway`. If that window is still open 30 seconds after the `Sign in` click, stop `LOGIN_NOT_FINISHED`. The UAT main window staying open is not this stop.
8. Continue only when that login window is gone, the `Username` field is not visible, and the UAT main process from `UAT_MAIN_PID` is still running. Do not continue while the login form is still showing.

### Phase 3 — CUA Cancel

Every click in this phase runs the Phase 2 path attestation again, on that click's HWND and pid. A CLEAN result from login has expired.

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

Every click and the type into `File name:` run the Phase 2 path attestation again. Repeat Phase 3 steps 4 through 6 on the same row. In the box labeled `File name:`, replace the contents with the exact `DEST_FILE` path from Phase 1. Click `Save`.

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

**Adversarial review: CLEAN.** Re-review count: 6. No open finding inside this procedure. Native Files PASS is not claimed. ns, ns2, and the evidence attempt stay frozen. This procedure does not reboot, change the reserve, grant an ACE, or take a second launch of a user-data directory that may be `booting`. Beelink was not executed from this checkout. `LEAVE_UAT_RUNNING` is a finished stop: report the pid and do not clear it.
