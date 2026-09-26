# Native A5 Beelink early exit — diagnosis and mechanical runbook

**Status:** diagnosis and runbook only. Native Files UAT is not PASS. This document does not change Desktop product code.

**Source pin:** `dokterdok/hermes-agent` `main` `057dcdf236f8a6a26721c10fcc6ccb72726e272a`. The staged Beelink package `hermes-uat-desktop-renderer-reuse-20260923` may be older than that pin. The launch flags below are Chromium process arguments plus environment variables the current `main` reads. They do not depend on BarrX deciding which binary revision is newer.

**Launch script (do not retype):** `review-packages/NATIVE_A5_BEELINK_EARLY_EXIT_LAUNCH.ps1`

**SHA-256, LF bytes (git blob and GitHub raw):** `3e164392f68210dbf380f6ab28f960cc0c8b216f87f2ebf9b546fab543441cfe`

**SHA-256, CRLF bytes (a Windows checkout; `*.ps1` is `text eol=crlf`):** `504683729b1ff05efe6c4799bb8078123a3bd64c77e0422a106c27a0466b7875`

Either hash is this script. If the copy matches neither, stop with `RUNBOOK_DRIFT`. Do not edit the script to "fix" a mismatch, a port, a path, a username, or the line endings.

## What happened

On 2026-09-26 the isolated UAT Desktop process started in session 1 as pid 37292 and exited within about 10 seconds with **-2147483645**. That signed value is **0x80000003**, Windows `STATUS_BREAKPOINT`.

A second start against `C:\Users\ddewit\hermes-uat-a5-live-20260926` failed with `attempt exists` before a new process was created. That string is the launcher guard, not an Electron exit.

The attempt tree that remained contained launcher-written `user-data\connection.json` and `user-data\active-profile.json` only. It also contained empty redirected roots `appdata`, `home`, `localappdata`, and `localappdata\Microsoft\Windows`. No `Local State`, no `windows-sandbox-fallback.json`, and no `desktop.log`. After that exit, daily Hermes was still running from:

`C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`

with `--user-data-dir=C:\Users\ddewit\AppData\Roaming\Hermes`. Its main pid was 33048. A child pid 12856 was `--type=gpu-process` with that same user-data directory. The captured command line is truncated, so a later `--no-sandbox` on that child is not ruled out. What is ruled out is a host-wide GPU death at that moment: that gpu-process was still alive. Do not grant an AppContainer ACE on either install because of this crash. `UAT_ACE_S-1-15-2-2=no` and `DAILY_ACE_S-1-15-2-2=no` are prints, not repair steps.

The owned Files fixture on `127.0.0.1:54573` had returned HTTP 200. `HERMES_DESKTOP_BOOT_FAKE` was cleared. CUA doctor was already clean in session 1. None of that is re-opened here.

## Verified in this tree

`apps/desktop/electron/windows-sandbox-fallback.ts` defines `WINDOWS_SANDBOX_BREAKPOINT_EXIT = -2147483645` and documents the matching Chromium failure: a sandboxed GPU or renderer dies with `STATUS_BREAKPOINT`, then the browser process aborts with "GPU process isn't usable. Goodbye." The in-app recovery is not immediate. `decideWindowsSandboxLaunch` tolerates one mid-boot abort, writes `windows-sandbox-fallback.json` with `state: booting`, and enables `--no-sandbox` only on the next consecutive abort (or immediately when argv already contains `--no-sandbox`, or when `ELECTRON_DISABLE_SANDBOX` is `1`). `writeSandboxMarker` runs at startup, before `app.ready`, only if that JavaScript runs.

`apps/desktop/electron/main.ts` applies `HERMES_DESKTOP_USER_DATA_DIR` with `app.setPath('userData', ...)` only when the variable is set in the process that actually executes the script. Chromium itself honors `--user-data-dir` from argv before that script runs. Both are required. A profile directory that never receives `Local State` or `windows-sandbox-fallback.json` was not the profile of the process that died.

`desktop.log` is `HERMES_HOME\logs\desktop.log`. `rememberLog` flushes asynchronously after 120ms. `installCrashForensics` flushes on a JavaScript exception. A native `STATUS_BREAKPOINT` abort does not take that path, so a missing `desktop.log` does not by itself prove the script never started.

`app.requestSingleInstanceLock()` with no extra key is per user-data directory. The loser calls `app.exit(0)`. Exit 0 is not -2147483645. Current source therefore does not explain this exit as the second-instance path. The runbook still forces a distinct `--user-data-dir` so that path cannot be taken.

`HERMES_DESKTOP_BOOT_FAKE_ERROR` throws a boot error overlay. It does not raise `STATUS_BREAKPOINT`. Leave those variables unset. The wrapper clears them.

`registerDeepLinkProtocol` calls `app.setAsDefaultProtocolClient('hermes')` from `app.whenReady`. Failure is caught and logged. Success rewrites `HKCU\Software\Classes\hermes` and can point `hermes://` at the UAT exe. The script exports that key before launch and restores it before it exits. Do not run `reg delete` yourself.

For an all-password gateway, `oauthGuardMayHardFail` does not hard-fail before the WebSocket ticket mint. The mint fails with no cookie, and the boot overlay button for a password gateway is exactly `Sign in to remote gateway` (`apps/desktop/src/i18n/en.ts`). The login window title is `Sign in to Hermes gateway`. It loads `/login`. The password form fields are `Username` and `Password` and the submit button is `Sign in` (`hermes_cli/dashboard_auth/login_page.py`).

The save dialog implemented in this tree is titled `Save File`. Cancel returns without writing. This checkout has no `CanonicalGroupFiles` / `saveCanonicalFile` surface. The right-sidebar label is `File system`, and its Download action uses `/api/fs/download`. The owned fixture described for this UAT does not mount that route. Do not substitute it.

## Ranked hypotheses

**H1 — Chromium sandbox breakpoint, and the attempt directory was not the profile that crashed.** The exit code matches the in-tree constant. About 10 seconds matches the GPU retry before the browser abort. The attempt `user-data` directory had no Chromium profile files, so `HERMES_DESKTOP_USER_DATA_DIR` was not effective for the process that exited, or the process died before that script wrote its marker. The previous launcher then refused a second start, so the two-strike `--no-sandbox` recovery never ran. Daily Hermes's still-running gpu-process means this is not evidence that every sandboxed Hermes on the machine is dead. Do not repair the daily install.

Falsify on the next launch, which passes `--no-sandbox` and `--user-data-dir` on argv and pins the real user `APPDATA` / `LOCALAPPDATA` / `USERPROFILE`:

- `EARLY_EXIT` plus `CHROME_GPU_GOODBYE=yes` means the breakpoint still happened with `--no-sandbox`. Stop. H1's switch was not sufficient.
- `EARLY_EXIT` plus `PROFILE_ADOPTED=no` means this process still did not use the attempt directory. Stop.
- `WINDOW_STABLE` means a window stayed up 15 seconds with a profile file in the attempt directory. That confirms the combined launch, not which single change was necessary.

**H2 — Single-instance collision with daily Hermes.** Current source would exit 0, not -2147483645. Daily Hermes keeps its own user-data directory. The new command line must contain the attempt `--user-data-dir`. If `UAT_EXIT=0` and there is no `WINDOW_STABLE` line, stop. Do not type into the daily window. Do not treat exit 0 as success.

**H3 — Redirected `LOCALAPPDATA` / `USERPROFILE` / `APPDATA`.** The failed attempt contains `appdata`, `home`, `localappdata`, and `localappdata\Microsoft\Windows`, and it does not contain a Chromium profile. That is the previous launcher's redirected profile roots, not `Local State`. AppContainer sandbox setup against a fake local app-data root is a known way to die with `STATUS_BREAKPOINT`, and it fits the surviving daily gpu-process better than a broken host. The wrapper pins the real `C:\Users\ddewit` profile paths and does not create `attempt\appdata`, `attempt\home`, or `attempt\localappdata`. This launch does not also perform a crashing A/B against H1. Do not add that launch.

**H4 — PsExec token is not `ddewit`.** The wrapper exits 77 before starting Hermes when `%USERNAME%` is not `ddewit`. The script then prints `WRONG_OWNER` and does not leave a Hermes process running as `SYSTEM`. That result confirms H4. Stop. Do not retry with a different account, and do not put a password on a `PsExec -u` command line.

**H5 — Missing package payload, wrong working directory, or an AeDebug debugger.** The script refuses to start unless `resources\app.asar` sits beside the staged exe, and it starts that exe with the working directory set to the exe directory. It prints `AEDEBUG_DEBUGGER=` and does not change that registry value. A missing asar stops with `UAT_ASAR_MISSING` before any process starts.

`BOOT_FAKE` is not a cause of this exit code. Protocol registration is not a cause of this exit code. `attempt exists` is not an Electron failure.

## Holds

- Do not claim native Files UAT PASS. A destination SHA-256 is a recording, not a verdict.
- Do not reboot Beelink. Do not change the memory reserve. Do not lower `26439023616`.
- Do not stop, restart, or focus daily Hermes. Do not `taskkill /IM Hermes.exe`. Do not `Stop-Process -Name Hermes`.
- Do not launch `C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`.
- Do not delete or reuse `C:\Users\ddewit\hermes-uat-a5-live-20260926`. Do not invent another attempt folder name. The only new folder is `C:\Users\ddewit\hermes-uat-a5-ns-20260926`, and only the script may create it.
- Do not run the launcher that printed `attempt exists`.
- Do not `setx` anything. Do not grant ACLs. `icacls` in the script is read-only and has no `/grant` and no `/T`. `UAT_ACE_S-1-15-2-2=no` or `DAILY_ACE_S-1-15-2-2=no` does not authorize `icacls /grant`. Daily Hermes already had a live gpu-process.
- Do not edit `HKCU\Software\Classes\hermes` by hand. Do not delete files under `C:\Users\ddewit\AppData\Roaming\Hermes`.
- Do not set `HERMES_DESKTOP_BOOT_FAKE` or `HERMES_DESKTOP_BOOT_FAKE_ERROR`.
- Do not click `Retry`, `Repair`, `Gateway settings`, or `Open logs`.
- Do not print, log, or screenshot the fixture password or username.
- If the script prints `KILL_REFUSED` or `KILL_ERROR`, stop. Do not escalate to an image-name kill.
- If the script prints `UAT_EXE_ALREADY_RUNNING`, stop. Do not kill that pid. Do not launch the daily exe instead.
- If daily Hermes is not seen, do not start it. If the script prints `DAILY_HERMES_DIED`, do not restart it.
- One `Launch` only. Do not run `Launch` again in this procedure.

## What remains unproven

A stable window does not prove login, Cancel, Save, or the fixture bytes. `HASH_RECORDED_NOT_A_PASS` does not prove them either. Nothing in this procedure compares the hash to the fixture payload. The split between H1 and H3 stays unproven on purpose: separating them requires another crashing launch, and this runbook does not do that.

## Mechanical procedure

Run the phases in order. After any `STOP` line, or any PowerShell exit code other than 0, do not start the next phase. Do not invent a replacement action. The stop line is the result.

### Phase 0 — fixture, before Hermes

1. Do not reboot. Do not kill processes by image name. Do not change memory policy.
2. From Beelink, request `http://127.0.0.1:54573/api/health` with a 5 second timeout. Do not scan other ports.
3. If the status is not 200, bring the existing owned fixture and reverse tunnel back with the Windows listen port fixed at **54573**. That is the port the script writes into `connection.json`. Do not choose another port. Do not edit the script. If health is still not 200, stop `FIXTURE_DEAD`.
4. Confirm `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\uat-password.txt` exists. Do not print its contents. If the username is not already in orchestrator memory from that bring-up and `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\uat-username.txt` does not exist, stop `USERNAME_UNAVAILABLE`. Do not read a Linux process environment to discover it.
5. Copy `review-packages/NATIVE_A5_BEELINK_EARLY_EXIT_LAUNCH.ps1` from this PR, unmodified, to `C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1`. Compute SHA-256 of those bytes. It must equal `3e164392f68210dbf380f6ab28f960cc0c8b216f87f2ebf9b546fab543441cfe` (LF) or `504683729b1ff05efe6c4799bb8078123a3bd64c77e0422a106c27a0466b7875` (CRLF). Otherwise stop `RUNBOOK_DRIFT`.

### Phase 1 — launch

Run exactly:

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1 -Phase Launch
```

Use `powershell.exe` (Windows PowerShell 5.1). If that executable is missing, stop `POWERSHELL_MISSING`. Do not substitute `pwsh.exe`. Do not pipe the password into this command.

Continue only when the process exit code is 0 **and** the output contains a line `WINDOW_STABLE`. Record `UAT_MAIN_PID`, `UAT_EXE`, `ATTEMPT`, and `DEST_FILE` from that output. Leave that pid running. Leave every daily Hermes pid running.

Any other exit code is a stop, including a transcript that contains `WINDOW_STABLE` but whose PowerShell exit code is not 0.

`PSEXEC_PID_UNPARSED` is not a stop. Do not kill anything because of that line. The script continues and waits for `LAUNCH_IDENTITY=`.

If the transcript contains `PROTOCOL_RESTORE_FAILED`, the script has already deleted `HKCU\Software\Classes\hermes` and both of its `reg.exe import` tries failed. That can happen on exit 17 or on an earlier stop such as `EARLY_EXIT`. Run `reg.exe import` once on the path in the `PREIMAGE_REG=` line printed with `PROTOCOL_RESTORE_FAILED`. Do not `reg delete`. Do not start CUA. Do not relaunch. Do not kill the UAT pid or daily Hermes. If that import fails, stop and leave the preimage file in place.

The script's own stops, and the only meaning of each:

| Exit | Line | Meaning |
| --- | --- | --- |
| 2 | `FIXTURE_DEAD` | `127.0.0.1:54573/api/health` was not 200. |
| 3 | `RESOURCE_HOLD` | Free physical memory is below 26439023616 bytes. Do not lower the bar. |
| 4 | `UAT_EXE_MISSING`, `UAT_ASAR_MISSING`, `DAILY_EXE_MISSING`, `UAT_EXE_IS_DAILY` | Staged package or daily exe path check failed. Do not fall back to the other exe. |
| 5 | `ATTEMPT_EXISTS` | `C:\Users\ddewit\hermes-uat-a5-ns-20260926` already exists. Do not delete it. |
| 6 | `PSEXEC_MISSING` | `PsExec.exe` / `PsExec64.exe` is not on `PATH`. Do not download PsExec. |
| 7 | `PREIMAGE_EXPORT_FAILED` | The `hermes` protocol key could not be exported. Do not delete it. |
| 8 | `UAT_EXE_ALREADY_RUNNING`, `LAUNCH_IDENTITY_MISSING`, `UAT_MAIN_MISSING`, `WRAPPER_MISSING_NO_SANDBOX`, `WRAPPER_UNSAFE`, `CONNECTIONS_JSON_PRESENT`, `ACTIVE_PROFILE_BYTES` | Process did not come up as specified. `UAT_EXE_ALREADY_RUNNING` prints a pid. Do not kill that pid. Do not switch to the daily exe. |
| 9 | `EARLY_EXIT` | UAT Hermes exited. `UAT_EXIT=` is the code. Do not relaunch. |
| 10 | `WRONG_OWNER` | Process identity was not `ddewit`. |
| 11 | `WRONG_SESSION` | Process was not in session 1. Do not try another session. |
| 12 | `WRONG_CMDLINE` | Argv lacked the attempt `--user-data-dir` or `--no-sandbox`. |
| 13 | `PROFILE_NOT_ADOPTED` | No `Local State` or sandbox marker appeared under the attempt `user-data`. |
| 14 | `DAILY_PROFILE_TOUCHED` | A watched file under `Roaming\Hermes` changed timestamp. Do not delete it. |
| 15 | `NO_WINDOW` | No UAT window handle stayed non-zero for 15 seconds. |
| 16 | `DAILY_HERMES_DIED` | A recorded daily main pid is gone. Do not restart it. |
| 17 | `PROTOCOL_RESTORE_FAILED` | The window may have been stable, then import of the preimage failed. The UAT pid is still running. Do not kill it. Do the one `reg.exe import` above. Do not start CUA. |
| 21 | `CANCEL_WROTE_OR_DIRTY` | `dest` was not empty after Cancel. Do not delete the file and continue. |
| 22 | `DEST_FILE_MISSING` | The Save path was not the exact destination file. Do not hash a neighbor. |
| 23 | `ATTEMPT_MISSING` | The attempt `dest` directory is gone. Do not recreate it by hand. |
| 99 | `UNCAUGHT` | Script fault. Do not continue. |

`DAILY_NOT_SEEN` is a warning inside a launch that may still reach `WINDOW_STABLE`. Do not start daily Hermes because of it.

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

Reviewer question: would this document make BarrX do something unsafe, or miss the failure mode that actually produced -2147483645?

Checked and kept:

- Daily Hermes is identified by full exe path and recorded pid. Nothing in the script or the CUA steps stops a process by image name. A kill is `taskkill /PID` only after the executable is the staged UAT exe, or `cmd.exe` whose command line contains `launch-uat.cmd`, and only after a walk that refuses the tree if the daily exe appears in it. `KILL_REFUSED` does not escalate. Pid 0 is not passed to `taskkill`. A staged `Hermes.exe` that is already running before PsExec is not adopted and not killed (`UAT_EXE_ALREADY_RUNNING`).
- `PSEXEC_PID_UNPARSED` does not stop the launch and does not pick a process by title. The wrapper must write `OWNER_OK` before Hermes starts, and the script still requires the staged exe path, session 1, owner `ddewit`, `--user-data-dir` of the new attempt, and `--no-sandbox`.
- The staged exe and the daily exe are different constants. Equality stops the launch. A missing staged exe does not fall through to the daily exe.
- The evidence attempt is read-only. The new attempt name is one constant. Rollback `Remove-Item` runs only when this invocation created that exact path and PsExec was not started. It cannot target `C:\Users\ddewit` or the evidence folder.
- `LOCALAPPDATA` is pinned to the real profile inside the wrapper, and only after `%USERNAME%` is `ddewit`, so a `SYSTEM` PsExec exit does not retarget the user profile and does not start Hermes.
- `--no-sandbox` and `ELECTRON_DISABLE_SANDBOX=1` exist only in the UAT wrapper. There is no `setx` and no machine-wide sandbox disable.
- `icacls` is a directory read with no `/grant` and no `/T`. AeDebug is printed and not written. A `no` ACE line is not a grant. Daily's surviving gpu-process is why the runbook does not repair the host.
- The protocol key is exported before launch. It is deleted and re-imported only by the script, and only after a successful export or an English "unable to find" result. Any other export failure stops before launch and does not delete the key. The script tries `reg.exe import` twice. If both fail it prints `PROTOCOL_RESTORE_FAILED` without hiding an earlier stop code. BarrX then imports that same preimage once and does not `reg delete`. Hash and dest-assert phases do not touch the key, so a later daily registration is not removed. A restore failure after `WINDOW_STABLE` leaves the UAT process running and does not start CUA.
- Watched daily files are `windows-sandbox-fallback.json` and `connection.json` under `Roaming\Hermes`, by timestamp only. Their contents are not read and not deleted. A timestamp change stops the UAT tree and stops the procedure.
- The password file is never read by the script. CUA is forbidden from echoing it or screenshotting the filled field. Login is not retried, because the fixture returns 429 on repeated failure.
- CUA cannot proceed on title alone, cannot click `Retry` / `Repair`, and cannot substitute the Artifacts `Files` tab or the `File system` tree. Those substitutions would hash the wrong bytes or start a local backend.
- Cancel is asserted before Save. A dirty dest directory stops the run. A replace prompt is not accepted. The hash is taken only from the exact destination path and is labeled not-a-pass.
- `WINDOW_STABLE` without PowerShell exit code 0 does not start CUA. Exit 0 without `WINDOW_STABLE` does not start CUA. A second `Launch` is forbidden, including after `EARLY_EXIT`, so BarrX cannot loop the crash.
- Free-memory refusal uses the previously required 26439023616 bytes and does not shrink it. There is no reboot step.
- The script does not contain a fixture password, a `Co-authored-by` trailer, or a product-code edit.

Failure mode coverage: the observed code is the sandbox breakpoint constant; the observed empty attempt tree is the "profile not adopted" probe; the `attempt exists` guard is why the in-app two-strike fallback never ran; exit 0 remains classified as failure so a single-instance focus of daily Hermes cannot receive the password. The runbook does not pretend one successful window isolates H1 from H3.

**Adversarial review: CLEAN.** No open finding inside this procedure. Native Files PASS is not claimed. H1 is not split from H3. This procedure does not reboot, change the reserve, or rewrite history. `UAT_EXE_ALREADY_RUNNING` is a finished stop: report the pid and do not clear it.
