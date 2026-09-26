# Native A5 Files UAT — Beelink Win11 (receipt and plan)

This slice is the access recipe, the BarrX preflight restatement, and a bounded scalar harness. It does not execute native login, Save, Cancel, or a destination-byte check.

GUI Save, GUI Cancel, and destination bytes are **HELD**. Launcher admission is **HELD**. Native login is **HELD**.

Outcome of this document: the receipt/plan slice is complete on a Cursor Linux VM that cannot reach Beelink. That is not an A5 PASS.

## Environment of this slice

| Fact | Value |
| --- | --- |
| Host | `cursor`, Linux 6.12.94+ x86_64 |
| Cursor run | `bc-6f156573-91bf-5eff-b239-54ce5ece39ea` |
| `privateWorkerId` | null |
| `usePrivateWorker` | false |
| Run source | `sand` |
| Tower SSH key | path from the kickoff is not on this VM; the file was not opened |
| `~/.ssh/config` | absent |
| TCP/22 `192.168.10.21` | timed out at 3s |
| TCP/22 `100.70.200.79` | timed out at 3s |
| `pwsh` / `powershell` | not installed; the preflight script was not executed |

Run identity was read from this agent's cloud run record at 2026-09-26T15:25:54Z. Nothing in that record places the process on `BEELINK-SER9-PR`.

## Pins (live at write time)

Confirmed with the GitHub API on 2026-09-26. Not inferred from a stale local ref.

| Pin | Live state |
| --- | --- |
| Desktop #97846 | Draft, open, not merged. Head `5211890fb87b626c9ba569910375707530c3a2cc` on `feat/bot-mode-desktop-continuity-20260829`. Title: integrate hosted Group Chats into Desktop. `updated_at` `2026-09-24T19:23:02Z`. Matches the kickoff tip. |
| Files tip | `b353ab32c527bd02e2f606d567da4ca324c3aaff` exists on `dokterdok/hermes-agent`. Message: stage imported history files in the Files owner. Author date `2026-09-24T18:45:38Z`. Parent `804ce9124f6669568585a331aeb4ef1b5cce5e29`. |
| Compose stack | Fork draft #13 open. Head `9a1540b0ccb3180c2de25871260784cabb0383e7` on `cursor/files-attachment-catalog-compose-acf4`. Base `cursor/route-security-digest-compose-e266`. `updated_at` `2026-09-26T14:42:45Z`. |
| Fork `main` | `057dcdf236f8a6a26721c10fcc6ccb72726e272a`. This branch starts there. Do not merge this PR onto it. |
| Prior Windows endpoint receipt | `E/ASTRA_NATIVE_FILES_WINDOWS_ENDPOINT_20260923.md` — named by the kickoff. Not in this checkout. Not opened. Not a Save/Cancel result. |
| Prior native cookie refuse | `E/ASTRA_NATIVE_FILES_COOKIE_NATIVE_20260923.md` — named by the kickoff. Not in this checkout. Not opened. The kickoff states that journey was refused on COMMIT headroom and that login, Save, Cancel, and destination bytes remained untested. This document does not add detail that file might contain. |

`ACCEPTANCE_CHECKLIST.md` is not at the repo root of fork `main` or of NousResearch/hermes-agent `main` (contents API 404 on both). The A5 mapping below uses the kickoff's statement of A5. It does not invent extra checklist rows.

The Desktop pin is the code reference for the Save/Cancel contract below. It is not evidence that Beelink is running that commit. The installed desktop build on `BEELINK-SER9-PR` was not identified.

## Access recipe

David unlocked native Win11 UAT on Beelink on 2026-09-26. Restated from that unlock:

| Item | Value |
| --- | --- |
| LAN | `192.168.10.21` |
| Tailscale | `100.70.200.79` |
| Host | `BEELINK-SER9-PR` |
| User | `ddewit` |
| Tower SSH identity | `/mnt/cache/appdata/hermes-agent/.credentials/hermes-win11-hil/id_ed25519` (comment `hermes-win11-hil`) |
| Tower SSH config | Host `win11-hil` → `192.168.10.21` |

Host-key checking stays on. The kickoff notes that strict `known_hosts` may need a refresh on Tower. Refresh the pinned key there. Do not disable host-key checking. Do not copy the identity file into this repo, into a PR, or into a transcript. This slice did not read it.

BarrX reached the host from Tower with that identity on both IPs and observed shell `SHELL_OK` plus hostname `BEELINK-SER9-PR`. This VM did not repeat that session.

The next execution slice runs on a Cursor worker that is already on Beelink (`agent worker start` there). It does not need this identity if the worker is local. It must not ferry the private key out to a cloud VM.

## BarrX preflight (2026-09-26 ~17:25 Europe/Zurich)

Restated. Not re-measured here.

| Scalar | Reported value |
| --- | --- |
| Shell | `SHELL_OK` |
| Hostname | `BEELINK-SER9-PR` |
| Tools | `uv`, `git`, `node` present |
| `FREE_PHYS_BYTES` | `33966333952` (kickoff: ~31.6 GiB) |
| `COMMIT_FREE_BYTES` | `34995945472` (kickoff: ~32.6 GiB) |
| `COMMIT_USED_BYTES` | `33044967424` |
| Commit limit | kickoff prose: ≈68 GiB. No separate limit scalar was provided. |

Exact GiB of the three byte scalars, using 1024^3 = 1073741824. This is arithmetic on the reported integers, not a new sample:

| Scalar | Bytes | ÷ 1024^3 |
| --- | --- | --- |
| `FREE_PHYS_BYTES` | 33966333952 | 31.63361358642578125 GiB |
| `COMMIT_FREE_BYTES` | 34995945472 | 32.5925140380859375 GiB |
| `COMMIT_USED_BYTES` | 33044967424 | 30.775524139404296875 GiB |

`COMMIT_USED_BYTES + COMMIT_FREE_BYTES = 68040912896` bytes = 63.368038177490234375 GiB. Integer division, not a binary float rounded in prose. 1024^3 = 1073741824. Remainders are 680337408, 636207104, and 832712704.

That sum is the only limit implied by the two commit scalars. The kickoff's "≈68 GiB" does not equal that sum. This receipt does not pick a winner and does not invent a fourth measurement. Launcher admission stays unproven until a fresh on-host run prints `COMMIT_LIMIT_BYTES`, `COMMIT_FREE_BYTES`, and `COMMIT_USED_BYTES` from one snapshot, with `used + free = limit`.

These numbers are not a clearance to start the desktop app. They are the headroom BarrX saw when David unlocked the attempt. Memory moves. The next slice re-runs the scalar script immediately before any launch decision.

If that fresh `COMMIT_FREE_BYTES` is below `34995945472`, do not launch. Report the new scalars and stay HELD. Do not free memory by killing user applications, shrinking reserves, or rebooting.

## Memory-safety constraints

From the kickoff's ASTRA_BIOSKED_RESUME gate, applied to this lane:

- Helpers use bounded scalars.
- Callers impose an external deadline. An in-process stopwatch is not that deadline.
- Any tree the UAT creates is owned by the UAT and is the only tree it deletes.
- Do not send raw CIM instances or process graphs through `ConvertTo-Json`.
- Do not use unbounded `Get-Content`.

`review-packages/scripts/beelink-scalar-preflight.ps1` follows that shape: one `Win32_OperatingSystem` instance, three numeric properties, values copied to `uint64` and the instance dropped, stdout limited to `KEY=value` lines, no file I/O, no process enumeration, no `ConvertTo-Json`. `Get-CimInstance -OperationTimeoutSec` is capped at 5 seconds and does not abort a hang inside the cmdlet. The caller still needs an external timeout and, on expiry, stops only that powershell process.

The script asks `Win32_OperatingSystem` for `FreePhysicalMemory`, `FreeVirtualMemory`, and `TotalVirtualMemorySize` (kilobytes) and scales by 1024 into `FREE_PHYS_BYTES`, `COMMIT_FREE_BYTES`, and `COMMIT_LIMIT_BYTES`. `COMMIT_USED_BYTES` is limit minus free from that same snapshot. A non-numeric property, a value that would wrap the `uint64` multiply, or more than one CIM instance fails closed with a status token. Invoke the file with `-File`. Do not dot-source it (`exit` would then close the caller; the script returns `PREFLIGHT_STATUS=DOT_SOURCED` instead). This is a proposed comparable measurement. It is not claimed to be the query BarrX ran.

Exit codes when invoked with `-File`: `0` OK, `2` in-script deadline, `3` commit free greater than limit, `4` bad invocation, `5` CIM, missing scalar, or range failure, `6` hostname rejected or an internal status token rejected. `DOT_SOURCED` is a stdout line only. That branch does not call `exit`, because `exit` would close the caller; the supported `-File` invocation never takes it. Failure lines are status tokens, not exception text. The script does not use PowerShell's `/` or `-` operators on the `uint64` counters. Those operators coerce through `double`. Scaling uses `decimal`, and the kilobyte ceiling is the integer `(2^64-1)/1024`.

Supported invocation, on the Beelink worker only:

```text
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File review-packages\scripts\beelink-scalar-preflight.ps1 -DeadlineSeconds 15
```

Wrap that process in an external deadline of 20 seconds. Do not run it from this Linux VM. It was not run here (`pwsh` is absent, and both Beelink addresses timed out).

## A5 mapping

The kickoff states that A5 requires authenticated Files interaction, native login, a mounted Save/Cancel, and destination bytes on the agreed Windows target. Status against that statement:

| Requirement | Status | What would count |
| --- | --- | --- |
| Authenticated Files interaction | HELD | After native login on this Windows session, Files is reachable as that signed-in user. A unit test, a cookie copied from another host, or a Linux checkout does not count. |
| Native login | HELD | `runNativeLogin` in `apps/desktop/electron/native-oauth-login.ts` on the Desktop pin (RFC 8252 loopback, system browser, one-time code). Evidence is completion of that flow in the Beelink session. `oauthSessionIsLive` is true for a native bearer **or** a cookie, so it is not the oracle. A cookie-only session is the prior refused journey, not this one. |
| Mounted Save | HELD | The OS save dialog from `saveGatewayFile` → `finalizeGatewayDownload` or `saveGatewayFileViaDataUrl` (`title: 'Save File'`) actually appears on the Beelink desktop. IPC `hermes:saveGatewayFile` (`main.ts` on the Desktop pin, handler at line 16128; dialogs at lines 7887 and 8031). |
| Mounted Cancel | HELD | The same dialog is dismissed with Cancel. Both functions return `{ canceled: true, saved: false }` before any destination write (`finalizeGatewayDownload` may abort the download request; that is not a file write). `hermes:selectSavePath` (line 16108) only returns a path and does not write bytes. A Cancel there is not A5 Cancel. |
| Destination bytes | HELD | After Save of synthetic content, the chosen path exists, its length and SHA-256 match the synthetic object, and no `.hermes-download-*.part` sibling remains. `downloadTempPath` names that sibling `.hermes-download-<8 hex>.part` beside the destination (`gateway-file-download.ts` line 139 on the Desktop pin). |
| Launcher admission under current COMMIT headroom | HELD | A recorded start of the desktop build on `BEELINK-SER9-PR` after a fresh scalar preflight. The BarrX snapshot is not that record. The installed build id was not read. |

Code locations above were read from commit `5211890fb87b626c9ba569910375707530c3a2cc` with `git show` / `git grep`. They describe the contract a future on-machine run must exercise. They are not a report that the dialog was shown.

Cancel on `finalizeGatewayDownload` happens before `pumpStreamToFile`. A correct Cancel creates neither the destination nor a `.part` temp. Save writes the temp, then renames it onto the destination. Destination proof is the file after that rename.

Synthetic payload reserved for the on-machine slice, and only if that slice creates it inside an owned directory:

```text
HERMES-A5-SYNTHETIC-20260926\n
```

29 bytes. SHA-256 `518c0e2d90669aac9318f14ddf62fa7103af31665522144009e2110de7064ef1`.

A hash mismatch, a missing dialog, or a different byte length is a HELD result. It is not permission to edit Files, Desktop, Output, F1, or A7.

Do not open, save, or hash a pre-existing user file. If the only way to place those bytes into Files is a write path owned by Output, F1, or A7, stop and hold. This lane does not touch those product paths.

JEV is not used. No timing comparison exists. Do not add it unless a later slice measures that it is faster and the evidence (dialog result, path, length, hash) stays the same.

## Owned directory for the later slice

When a Beelink worker runs the GUI journey:

1. Create one new directory whose name includes `hermes-a5-uat-` under that user's temp directory.
2. Save and Cancel only inside that directory.
3. Record path, length, and SHA-256 before deleting anything.
4. Delete that directory only. Do not delete its parent. Do not run a broad temp cleanup.

This preflight script creates nothing, so it has nothing to tear down.

## Holds

1. **GUI UAT.** Unlock: start a Cursor agent worker on Beelink (`agent worker start`), confirm hostname `BEELINK-SER9-PR`, re-run the scalar script under an external deadline, and only then run login → Files → Save → Cancel → destination hash. Synthetic data only.
2. **Launcher admission.** Same unlock. Also record the desktop build or commit actually launched. Do not assume it is `5211890fb87b626c9ba569910375707530c3a2cc`.
3. **Fresh commit limit.** Unlock: one on-host snapshot where `COMMIT_USED_BYTES + COMMIT_FREE_BYTES = COMMIT_LIMIT_BYTES`. Do not launch if fresh `COMMIT_FREE_BYTES` is below `34995945472`. Do not manufacture headroom.
4. **Prior `E/` receipts.** Unlock: read them on the machine that has them, without pasting secrets, if the next slice needs more than the kickoff's one-line refuse. Absence from this checkout is not a claim that they are empty or wrong.
5. **`ACCEPTANCE_CHECKLIST.md`.** Unlock: point this mapping at the checklist file if it lives outside the fork. Do not invent rows to fill the gap.
6. **Draft publication boundary.** This PR stays draft. Do not merge it to fork `main`. Do not push, fast-forward, or open a PR on NousResearch/hermes-agent.

## Out of scope

Reboot, power cycle, killing user applications, changing reserves, deploying, restarting gateways, production takeover, Barry, Output / F1 / A7 product edits, and any edit outside `review-packages/NATIVE_A5_FILES_UAT_BEELINK.md` and `review-packages/scripts/beelink-scalar-preflight.ps1`.

## Adversarial review

Re-review count: **4**. Verdict: **CLEAN** for the receipt/plan slice. GUI, login, Save, Cancel, destination bytes, and launcher admission are **HELD** with the unlocks in the Holds section.

Pass 1 looked for a false Save/Cancel PASS, a live re-measure this VM cannot have made, a weakened SSH host-key check, CIM/`ConvertTo-Json` leakage, process kill or reserve changes, product-path edits, and a launch floor presented as a new measurement. Fixes still in this commit: both the "≈68 GiB" prose and the scalar sum are shown, and admission stays HELD until one fresh snapshot satisfies `used + free = limit`; the script is not labeled as BarrX's command; `hermes:selectSavePath` is not A5 destination evidence; the Desktop pin is not the Beelink install; exception text is not printed; host-key checking stays on; the identity file was not read; a non-numeric CIM value exits `SCALAR_RANGE`; more than one CIM instance exits `CIM_FAILED`; dot-sourcing does not call `exit`; a hash mismatch is HELD, not a product edit.

Pass 2, on that text, found three remaining script defects. Property reads sat outside the converter's catch, so a missing CIM property could surface as a raw error record. The kilobyte ceiling used PowerShell's `/` operator, which coerces `uint64` through `double`. Used-bytes used the `-` operator, same coercion. The exit-code sentence also called `6` hostname-only and did not say `DOT_SOURCED` is stdout-only.

Pass 3, after those three fixes, overclaimed two checks. The GiB column had been computed by hand and the fractional parts were wrong (for example free physical was written `31.633544921875` instead of `31.63361358642578125`). The same paragraph said a text search found no `ConvertTo-Json` and no counter `-` operator. The header comment names `ConvertTo-Json` and `Get-Content` as things the caller must not pass, and used-bytes is `[decimal]` subtraction.

Pass 4 recomputed the three quotients and the 68040912896-byte sum with integer division by 1073741824. The table now matches those quotients and remainders. Executable lines do not call `ConvertTo-Json`, `Get-Content`, `Get-Process`, `Win32_Process`, `Stop-Process`, `Restart-Computer`, or `taskkill`. Success-path stdout is the ten keys `PREFLIGHT_STATUS`, `HOSTNAME`, `FREE_PHYS_BYTES`, `COMMIT_LIMIT_BYTES`, `COMMIT_FREE_BYTES`, `COMMIT_USED_BYTES`, `TOOL_UV`, `TOOL_GIT`, `TOOL_NODE`, `ELAPSED_MS`. The script was not executed. No command in this receipt reboots, kills user apps, disables host-key checks, or calls Barry. PASS language is limited to the receipt/plan slice. Open findings in this slice: none.
