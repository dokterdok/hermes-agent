#Requires -Version 5.1
<#
  Native A5 Beelink launch. Mechanical. No decisions.

  Save this file as:
    C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1

  Run with Windows PowerShell, not inside the daily Hermes window:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1 -Phase Launch
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1 -Phase AssertDestEmpty
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\launch-a5.ps1 -Phase Hash

  Exit 0 from Launch means WINDOW_STABLE and the UAT process is still running.
  Exit 0 from Hash means the destination file was hashed. It is not a Files UAT PASS.
  Any other exit is a STOP. Do not improvise a next step.
  PSEXEC_PID_UNPARSED is not a stop. The script keeps going until LAUNCH_IDENTITY=.
  If the output contains PROTOCOL_RESTORE_FAILED, import the PREIMAGE_REG file
  printed on the next line exactly once with reg.exe import. Do not reg delete.
  Do not start CUA after that line. Do not kill any process by image name.
  Do not delete, rename, or launch C:\Users\ddewit\hermes-uat-a5-ns-20260926.
  The only new folder is C:\Users\ddewit\hermes-uat-a5-ns2-20260926.
  PsExec is the existing PsExec64.exe under AppData\Local\Temp\hermes-uat-live.
  Do not download PsExec. Do not add it to PATH.

  This script never stops a process by image name. It never restarts daily Hermes.
  It never sets machine environment variables. It never grants ACLs. It never reboots.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('Launch', 'AssertDestEmpty', 'Hash')]
  [string]$Phase
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$script:ExitCode = 99
$script:NeedRestore = $false
$script:CreatedAttempt = $false
$script:Launched = $false

$UatExe = 'C:\Users\ddewit\hermes-uat-desktop-renderer-reuse-20260923\source\apps\desktop\release\win-unpacked\Hermes.exe'
$DailyExe = 'C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe'
$Attempt = 'C:\Users\ddewit\hermes-uat-a5-ns2-20260926'
$FrozenAttempt = 'C:\Users\ddewit\hermes-uat-a5-ns-20260926'
$Evidence = 'C:\Users\ddewit\hermes-uat-a5-live-20260926'
$KnownPsExec = 'C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec64.exe'
$WindowsPort = 54573
$RequiredFreeBytes = [int64]26439023616
$DestFile = Join-Path $Attempt 'dest\uat-download.bin'
$UserData = Join-Path $Attempt 'user-data'
$PreimageReg = Join-Path $Attempt 'logs\hermes-protocol-preimage.reg'
$PreimageAbsent = Join-Path $Attempt 'logs\protocol-preimage-absent.txt'
$DailyMarker = 'C:\Users\ddewit\AppData\Roaming\Hermes\windows-sandbox-fallback.json'
$DailyConnection = 'C:\Users\ddewit\AppData\Roaming\Hermes\connection.json'

function Write-Stop([string]$Reason) {
  Write-Output ('STOP ' + $Reason)
  Write-Output 'DO_NOT_KILL_DAILY=1'
  Write-Output 'DO_NOT_REBOOT=1'
  Write-Output 'DO_NOT_CLAIM_FILES_PASS=1'
}

function Stop-A5([int]$Code, [string]$Reason) {
  $script:ExitCode = $Code
  Write-Stop $Reason
  throw (New-Object System.InvalidOperationException($Reason))
}

function Undo-UnlaunchedAttempt {
  if ($script:Launched -or -not $script:CreatedAttempt) {
    return
  }

  $allowed = 'C:\Users\ddewit\hermes-uat-a5-ns2-20260926'

  if (-not [string]::Equals($Attempt, $allowed, [System.StringComparison]::OrdinalIgnoreCase)) {
    Write-Output 'ROLLBACK_REFUSED'
    return
  }

  foreach ($forbidden in @(
      $FrozenAttempt,
      $Evidence,
      'C:\Users\ddewit',
      'C:\Users\ddewit\AppData',
      'C:\Users\ddewit\AppData\Roaming',
      'C:\Users\ddewit\AppData\Roaming\Hermes',
      'C:\Users\ddewit\AppData\Local',
      'C:\Users\ddewit\AppData\Local\hermes'
    )) {
    if ([string]::Equals($Attempt, $forbidden, [System.StringComparison]::OrdinalIgnoreCase)) {
      Write-Output 'ROLLBACK_REFUSED'
      return
    }
  }

  Remove-Item -LiteralPath $allowed -Recurse -Force
  $script:CreatedAttempt = $false
  Write-Output 'UNLAUNCHED_ATTEMPT_REMOVED'
}

function Format-NativeArgument([string]$Value) {
  if ($Value -notmatch '[\s"]') {
    return $Value
  }

  return '"' + ($Value -replace '"', '\"') + '"'
}

function Invoke-Native {
  param(
    [Parameter(Mandatory = $true)]
    [string]$File,
    [string[]]$ArgumentList = @()
  )

  $quoted = New-Object System.Collections.Generic.List[string]

  foreach ($arg in $ArgumentList) {
    $quoted.Add((Format-NativeArgument ([string]$arg)))
  }

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $File
  $psi.Arguments = ($quoted -join ' ')
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $false

  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi

  try {
    [void]$proc.Start()
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    [void]$proc.WaitForExit()
    $stdout = [string]$outTask.Result
    $stderr = [string]$errTask.Result
    return [pscustomobject]@{ Output = ($stdout + $stderr); ExitCode = [int]$proc.ExitCode }
  } catch {
    return [pscustomobject]@{ Output = [string]$_.Exception.Message; ExitCode = 9009 }
  }
}

function Test-LogPhrase([string]$Path, [string]$Phrase) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return 'no'
  }

  try {
    $hit = Select-String -LiteralPath $Path -SimpleMatch -Pattern $Phrase -Quiet
    if ($hit) {
      return 'yes'
    }

    return 'no'
  } catch {
    return 'unread'
  }
}

function Restore-HermesProtocol {
  if (-not $script:NeedRestore) {
    return
  }

  $script:NeedRestore = $false
  $log = Join-Path $Attempt 'logs\protocol-restore.log'
  $deleteResult = Invoke-Native -File 'reg.exe' -ArgumentList @('delete', 'HKCU\Software\Classes\hermes', '/f')
  Add-Content -LiteralPath $log -Value $deleteResult.Output -Encoding ascii

  if (Test-Path -LiteralPath $PreimageReg) {
    $imported = $false

    foreach ($attemptIndex in 1, 2) {
      $importResult = Invoke-Native -File 'reg.exe' -ArgumentList @('import', $PreimageReg)
      Add-Content -LiteralPath $log -Value ("IMPORT_TRY_" + $attemptIndex + " exit=" + $importResult.ExitCode + " " + $importResult.Output) -Encoding ascii

      if ($importResult.ExitCode -eq 0) {
        $imported = $true
        break
      }
    }

    if (-not $imported) {
      Write-Output 'PROTOCOL_RESTORE_FAILED'
      Write-Output ('PREIMAGE_REG=' + $PreimageReg)
      if ($script:ExitCode -eq 0) {
        $script:ExitCode = 17
      }

      return
    }
  }

  Write-Output 'PROTOCOL_RESTORED'
}

function Get-Stamp([string]$Path) {
  if (Test-Path -LiteralPath $Path) {
    return (Get-Item -LiteralPath $Path).LastWriteTimeUtc.Ticks.ToString()
  }

  return 'ABSENT'
}

function Get-HermesProcesses {
  @(Get-CimInstance Win32_Process -Filter "Name = 'Hermes.exe'")
}

function Test-DailyTree([uint32]$RootPid) {
  $all = @(Get-CimInstance Win32_Process)
  $ids = New-Object 'System.Collections.Generic.List[uint32]'
  $ids.Add($RootPid)
  $grew = $true

  while ($grew) {
    $grew = $false

    foreach ($proc in $all) {
      $pidValue = [uint32]$proc.ProcessId
      $parent = [uint32]$proc.ParentProcessId

      if ($ids.Contains($parent) -and -not $ids.Contains($pidValue)) {
        $ids.Add($pidValue)
        $grew = $true
      }
    }
  }

  foreach ($proc in $all) {
    if (-not $ids.Contains([uint32]$proc.ProcessId)) {
      continue
    }

    if ($proc.ExecutablePath -and ($proc.ExecutablePath -ieq $DailyExe)) {
      return $false
    }
  }

  return $true
}

function Stop-UatTree([uint32]$RootPid) {
  if ($RootPid -eq 0) {
    return
  }

  $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $RootPid)

  if (-not $proc) {
    return
  }

  $pathOk = $proc.ExecutablePath -and ($proc.ExecutablePath -ieq $UatExe)
  $cmdOk = $proc.ExecutablePath -and ($proc.ExecutablePath -like '*\cmd.exe') -and ($proc.CommandLine -like '*launch-uat.cmd*')

  if (-not $pathOk -and -not $cmdOk) {
    Write-Output ('KILL_REFUSED pid=' + $RootPid)
    return
  }

  if (-not (Test-DailyTree $RootPid)) {
    Write-Output 'KILL_REFUSED daily exe is in the tree'
    return
  }

  $kill = Invoke-Native -File 'taskkill.exe' -ArgumentList @('/PID', ([string]$RootPid), '/T', '/F')

  if ($kill.ExitCode -ne 0) {
    Write-Output ('KILL_ERROR pid=' + $RootPid + ' taskkill=' + $kill.ExitCode)
    return
  }

  Write-Output ('KILLED_UAT_TREE pid=' + $RootPid)
}

function Stop-MatchingUat {
  foreach ($proc in @(Get-CimInstance Win32_Process)) {
    $cmd = [string]$proc.CommandLine
    $path = [string]$proc.ExecutablePath
    $matchHermes = $path -and ($path -ieq $UatExe) -and ($cmd -like ('*' + $UserData + '*'))
    $matchCmd = ($path -like '*\cmd.exe') -and ($cmd -like '*launch-uat.cmd*')

    if ($matchHermes -or $matchCmd) {
      Stop-UatTree ([uint32]$proc.ProcessId)
    }
  }
}

function Assert-DailyStillAlive([uint32[]]$DailyPids) {
  $live = Get-HermesProcesses

  foreach ($dailyPid in $DailyPids) {
    $still = $false

    foreach ($proc in $live) {
      if ([uint32]$proc.ProcessId -eq $dailyPid -and $proc.ExecutablePath -and ($proc.ExecutablePath -ieq $DailyExe)) {
        $still = $true
      }
    }

    if (-not $still) {
      Stop-A5 16 'DAILY_HERMES_DIED'
    }
  }
}

function Invoke-Launch {
  Write-Output 'PHASE_LAUNCH'
  Write-Output 'DO_NOT_KILL_DAILY=1'
  Write-Output ('FROZEN_ATTEMPT=' + $FrozenAttempt)
  Write-Output 'DO_NOT_DELETE_FROZEN=1'
  Write-Output 'DO_NOT_RENAME_FROZEN=1'
  Write-Output 'DO_NOT_LAUNCH_FROZEN=1'
  Write-Output ('NEW_ATTEMPT=' + $Attempt)

  if (Test-Path -LiteralPath $Attempt) {
    Stop-A5 5 'ATTEMPT_EXISTS'
  }

  if (Test-Path -LiteralPath $Evidence) {
    $script:EvidenceShown = 0

    Get-ChildItem -LiteralPath $Evidence -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
      $script:EvidenceShown += 1

      if ($script:EvidenceShown -le 200) {
        Write-Output ('EVIDENCE ' + $_.FullName + ' dir=' + $_.PSIsContainer + ' len=' + $_.Length)
      }
    }

    if ($script:EvidenceShown -gt 200) {
      Write-Output 'EVIDENCE_TRUNCATED'
    }
  } else {
    Write-Output 'EVIDENCE_ATTEMPT_ABSENT'
  }

  foreach ($pair in @(
      @('EVIDENCE_MARKER', (Join-Path $Evidence 'user-data\windows-sandbox-fallback.json')),
      @('EVIDENCE_LOCAL_STATE', (Join-Path $Evidence 'user-data\Local State')),
      @('EVIDENCE_DESKTOP_LOG', (Join-Path $Evidence 'hermes\logs\desktop.log'))
    )) {
    if (Test-Path -LiteralPath $pair[1]) {
      Write-Output ($pair[0] + '=present')
    } else {
      Write-Output ($pair[0] + '=absent')
    }
  }

  if (-not (Test-Path -LiteralPath $UatExe)) {
    Stop-A5 4 'UAT_EXE_MISSING'
  }

  $exeDir = Split-Path -Parent $UatExe

  if (-not (Test-Path -LiteralPath (Join-Path $exeDir 'resources\app.asar'))) {
    Stop-A5 4 'UAT_ASAR_MISSING'
  }

  if (-not (Test-Path -LiteralPath $DailyExe)) {
    Stop-A5 4 'DAILY_EXE_MISSING'
  }

  if ([string]::Equals($UatExe, $DailyExe, [System.StringComparison]::OrdinalIgnoreCase)) {
    Stop-A5 4 'UAT_EXE_IS_DAILY'
  }

  $ae = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AeDebug' -ErrorAction SilentlyContinue
  Write-Output ('AEDEBUG_DEBUGGER=' + [string]$ae.Debugger)

  $uatDir = $exeDir
  $dailyDir = Split-Path -Parent $DailyExe
  $uatAcl = (Invoke-Native -File 'icacls.exe' -ArgumentList @($uatDir)).Output
  $dailyAcl = (Invoke-Native -File 'icacls.exe' -ArgumentList @($dailyDir)).Output
  New-Item -ItemType Directory -Force -Path 'C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live' | Out-Null
  [System.IO.File]::WriteAllText('C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\icacls-uat.txt', $uatAcl)
  [System.IO.File]::WriteAllText('C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\icacls-daily.txt', $dailyAcl)
  Write-Output ('UAT_ACE_S-1-15-2-2=' + $(if ($uatAcl -like '*S-1-15-2-2*') { 'yes' } else { 'no' }))
  Write-Output ('DAILY_ACE_S-1-15-2-2=' + $(if ($dailyAcl -like '*S-1-15-2-2*') { 'yes' } else { 'no' }))

  try {
    $health = Invoke-WebRequest -Uri ('http://127.0.0.1:' + $WindowsPort + '/api/health') -UseBasicParsing -TimeoutSec 5
  } catch {
    Stop-A5 2 'FIXTURE_DEAD'
  }

  if ([int]$health.StatusCode -ne 200) {
    Stop-A5 2 'FIXTURE_DEAD'
  }

  $healthHost = ''

  try {
    $healthHost = [string]$health.BaseResponse.ResponseUri.Host
  } catch {
    $healthHost = ''
  }

  if ($healthHost -and $healthHost -ne '127.0.0.1') {
    Stop-A5 2 'FIXTURE_DEAD'
  }

  Write-Output ('HEALTH=200 port=' + $WindowsPort)

  $os = Get-CimInstance Win32_OperatingSystem
  $free = [int64]$os.FreePhysicalMemory * 1024
  Write-Output ('FREE_PHYSICAL_BYTES=' + $free)

  if ($free -lt $RequiredFreeBytes) {
    Stop-A5 3 'RESOURCE_HOLD'
  }

  $dailyPids = @()

  foreach ($proc in (Get-HermesProcesses)) {
    if ($proc.ExecutablePath -and ($proc.ExecutablePath -ieq $DailyExe) -and $proc.CommandLine -notlike '*--type=*') {
      $dailyPids += [uint32]$proc.ProcessId
    }
  }

  Write-Output ('DAILY_MAIN_PIDS=' + ($dailyPids -join ','))

  if ($dailyPids.Count -eq 0) {
    Write-Output 'DAILY_NOT_SEEN'
  }

  foreach ($proc in (Get-HermesProcesses)) {
    if ($proc.ExecutablePath -and ($proc.ExecutablePath -ieq $UatExe)) {
      Write-Output ('UAT_EXE_ALREADY_RUNNING pid=' + $proc.ProcessId)
      Stop-A5 8 'UAT_EXE_ALREADY_RUNNING'
    }
  }

  $markerBefore = Get-Stamp $DailyMarker
  $connectionBefore = Get-Stamp $DailyConnection
  Write-Output ('DAILY_MARKER_STAMP=' + $markerBefore)
  Write-Output ('DAILY_CONNECTION_STAMP=' + $connectionBefore)

  New-Item -ItemType Directory -Force -Path @(
    $UserData,
    (Join-Path $Attempt 'hermes'),
    (Join-Path $Attempt 'dest'),
    (Join-Path $Attempt 'temp'),
    (Join-Path $Attempt 'logs')
  ) | Out-Null
  $script:CreatedAttempt = $true

  $utf8 = New-Object System.Text.UTF8Encoding $false
  $connection = '{"mode":"remote","remote":{"url":"http://127.0.0.1:' + $WindowsPort + '","authMode":"oauth"},"profiles":{}}'
  [System.IO.File]::WriteAllText((Join-Path $UserData 'connection.json'), $connection, $utf8)
  [System.IO.File]::WriteAllText((Join-Path $UserData 'active-profile.json'), '{"profile":null}', $utf8)

  if (Test-Path -LiteralPath (Join-Path $UserData 'connections.json')) {
    Stop-A5 8 'CONNECTIONS_JSON_PRESENT'
  }

  $profileBytes = [System.IO.File]::ReadAllBytes((Join-Path $UserData 'active-profile.json'))

  if ($profileBytes.Length -ne 16) {
    Stop-A5 8 'ACTIVE_PROFILE_BYTES'
  }

  $psexecPath = $null

  foreach ($candidate in @(
      $KnownPsExec,
      'C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\PsExec.exe'
    )) {
    if (Test-Path -LiteralPath $candidate) {
      $info = Get-Item -LiteralPath $candidate

      if (-not $info.PSIsContainer -and $info.Length -gt 0) {
        $psexecPath = $candidate
        break
      }
    }
  }

  if (-not $psexecPath) {
    foreach ($name in @('PsExec64.exe', 'PsExec.exe', 'psexec.exe')) {
      $found = Get-Command $name -ErrorAction SilentlyContinue

      if ($found -and $found.Source -and (Test-Path -LiteralPath $found.Source)) {
        $psexecPath = $found.Source
        break
      }
    }
  }

  if (-not $psexecPath) {
    Stop-A5 6 'PSEXEC_MISSING'
  }

  Write-Output ('PSEXEC_PATH=' + $psexecPath)

  $wrapper = Join-Path $Attempt 'launch-uat.cmd'
  $idFile = Join-Path $Attempt 'logs\launch-identity.txt'
  $exitFile = Join-Path $Attempt 'logs\exit-code.txt'
  $chromeLog = Join-Path $Attempt 'logs\chrome.log'
  $electronLog = Join-Path $Attempt 'logs\electron.log'
  $cmdBody = @"
@echo off
setlocal
if /I not "%USERNAME%"=="ddewit" (
  >"$idFile" echo WRONG_OWNER
  exit /b 77
)
>"$idFile" echo OWNER_OK
set "USERPROFILE=C:\Users\ddewit"
set "APPDATA=C:\Users\ddewit\AppData\Roaming"
set "LOCALAPPDATA=C:\Users\ddewit\AppData\Local"
set "HOME=C:\Users\ddewit"
set "HERMES_HOME=$Attempt\hermes"
set "HERMES_DESKTOP_USER_DATA_DIR=$UserData"
set "TMP=$Attempt\temp"
set "TEMP=$Attempt\temp"
set "ELECTRON_DISABLE_SANDBOX=1"
set "ELECTRON_ENABLE_LOGGING=1"
set "ELECTRON_LOG_FILE=$electronLog"
set "HERMES_DESKTOP_BOOT_FAKE="
set "HERMES_DESKTOP_BOOT_FAKE_ERROR="
set "HERMES_DESKTOP_BOOT_FAKE_STEP_MS="
cd /d "$exeDir"
"$UatExe" --user-data-dir="$UserData" --no-sandbox --enable-logging --log-file="$chromeLog"
set RC=%ERRORLEVEL%
>"$exitFile" echo %RC%
exit /b %RC%
"@
  $cmdBody = $cmdBody -replace "`r`n", "`n" -replace "`n", "`r`n"
  [System.IO.File]::WriteAllText($wrapper, $cmdBody, (New-Object System.Text.ASCIIEncoding))
  $wrapRaw = [System.IO.File]::ReadAllText($wrapper)

  if ($wrapRaw -notlike '*--no-sandbox*') {
    Stop-A5 8 'WRAPPER_MISSING_NO_SANDBOX'
  }

  if ($wrapRaw -notlike ('*HERMES_HOME=' + $Attempt + '\hermes*')) {
    Stop-A5 8 'WRAPPER_HOME_UNPINNED'
  }

  if ($wrapRaw -notlike '*LOCALAPPDATA=C:\Users\ddewit\AppData\Local*') {
    Stop-A5 8 'WRAPPER_REDIRECTS_PROFILE'
  }

  if ($wrapRaw -like ('*' + $Attempt + '\localappdata*') -or $wrapRaw -like ('*' + $Attempt + '\appdata*') -or $wrapRaw -like ('*' + $Attempt + '\home*')) {
    Stop-A5 8 'WRAPPER_REDIRECTS_PROFILE'
  }

  if ($wrapRaw -like ('*' + $FrozenAttempt + '*') -or $wrapRaw -like ('*' + $Evidence + '*')) {
    Stop-A5 8 'WRAPPER_REDIRECTS_PROFILE'
  }

  if ($wrapRaw -like '*uat-password*' -or $wrapRaw -like '*BOOT_FAKE=1*') {
    Stop-A5 8 'WRAPPER_UNSAFE'
  }

  $exportResult = Invoke-Native -File 'reg.exe' -ArgumentList @('export', 'HKCU\Software\Classes\hermes', $PreimageReg, '/y')
  $export = $exportResult.Output

  if ($exportResult.ExitCode -ne 0) {
    if ($export -match 'unable to find|cannot find') {
      Write-Output 'PROTOCOL_PREIMAGE=ABSENT'
      Remove-Item -LiteralPath $PreimageReg -ErrorAction SilentlyContinue
      [System.IO.File]::WriteAllText($PreimageAbsent, 'ABSENT')
    } else {
      Stop-A5 7 'PREIMAGE_EXPORT_FAILED'
    }
  } else {
    Write-Output 'PROTOCOL_PREIMAGE=EXPORTED'
    Write-Output ('PREIMAGE_REG=' + $PreimageReg)
  }

  $script:NeedRestore = $true
  $script:Launched = $true
  $psexecLog = Join-Path $Attempt 'logs\psexec.txt'
  $psexecResult = Invoke-Native -File $psexecPath -ArgumentList @(
    '-accepteula', '-nobanner', '-i', '1', '-d', '-w', $exeDir,
    "$env:SystemRoot\System32\cmd.exe", '/c', $wrapper
  )
  [System.IO.File]::WriteAllText($psexecLog, [string]$psexecResult.Output)
  Write-Output ('PSEXEC_EXIT=' + $psexecResult.ExitCode)
  $psexecText = [string]$psexecResult.Output

  Write-Output 'PSEXEC_LOG_WRITTEN'

  $cmdPid = [uint32]0

  if ($psexecText -match 'process ID (\d+)') {
    $cmdPid = [uint32]$Matches[1]
    Write-Output ('PSEXEC_PID=' + $cmdPid)
  } else {
    Write-Output 'PSEXEC_PID_UNPARSED'
  }

  $identityDeadline = (Get-Date).AddSeconds(20)

  while ((Get-Date) -lt $identityDeadline -and -not (Test-Path -LiteralPath $idFile)) {
    Start-Sleep -Seconds 1
  }

  if (-not (Test-Path -LiteralPath $idFile)) {
    Stop-UatTree $cmdPid
    Stop-A5 8 'LAUNCH_IDENTITY_MISSING'
  }

  $identity = ([System.IO.File]::ReadAllText($idFile)).Trim()
  Write-Output ('LAUNCH_IDENTITY=' + $identity)

  if ($identity -ne 'OWNER_OK') {
    Stop-UatTree $cmdPid
    Stop-A5 10 'WRONG_OWNER'
  }

  $main = $null
  $findDeadline = (Get-Date).AddSeconds(20)

  while ((Get-Date) -lt $findDeadline -and -not $main) {
    foreach ($proc in (Get-HermesProcesses)) {
      if ($proc.ExecutablePath -and ($proc.ExecutablePath -ieq $UatExe) -and $proc.CommandLine -notlike '*--type=*') {
        $main = $proc
      }
    }

    if (-not $main) {
      Start-Sleep -Seconds 1
    }
  }

  if (-not $main) {
    Stop-UatTree $cmdPid
    Stop-A5 8 'UAT_MAIN_MISSING'
  }

  $uatPid = [uint32]$main.ProcessId
  Write-Output ('UAT_MAIN_PID=' + $uatPid)
  Write-Output ('UAT_SESSION=' + $main.SessionId)

  if ([int]$main.SessionId -ne 1) {
    Stop-UatTree $uatPid
    Stop-UatTree $cmdPid
    Stop-A5 11 'WRONG_SESSION'
  }

  $owner = Invoke-CimMethod -InputObject (Get-CimInstance Win32_Process -Filter ("ProcessId = " + $uatPid)) -MethodName GetOwner

  if (-not $owner.User -or ($owner.User -ine 'ddewit')) {
    Stop-UatTree $uatPid
    Stop-UatTree $cmdPid
    Stop-A5 10 'WRONG_OWNER'
  }

  Write-Output ('UAT_OWNER=' + $owner.User)

  $cmdLine = [string]$main.CommandLine

  if ($cmdLine -notlike ('*--user-data-dir=' + $UserData + '*') -and $cmdLine -notlike ('*--user-data-dir="' + $UserData + '"*')) {
    Stop-UatTree $uatPid
    Stop-UatTree $cmdPid
    Stop-A5 12 'WRONG_CMDLINE'
  }

  if ($cmdLine -notlike '*--no-sandbox*') {
    Stop-UatTree $uatPid
    Stop-UatTree $cmdPid
    Stop-A5 12 'WRONG_CMDLINE'
  }

  Write-Output 'CMDLINE_OK'

  $hardStop = (Get-Date).AddSeconds(120)
  $noWindowAfter = (Get-Date).AddSeconds(90)
  $stableSince = $null
  $profileOk = $false
  $announcedWindow = $false

  while ((Get-Date) -lt $hardStop) {
    Assert-DailyStillAlive $dailyPids

    if ((Get-Stamp $DailyMarker) -ne $markerBefore -or (Get-Stamp $DailyConnection) -ne $connectionBefore) {
      Stop-UatTree $uatPid
      Stop-UatTree $cmdPid
      Stop-A5 14 'DAILY_PROFILE_TOUCHED'
    }

    $markerPath = Join-Path $UserData 'windows-sandbox-fallback.json'
    $localState = Join-Path $UserData 'Local State'

    if ((Test-Path -LiteralPath $markerPath) -or (Test-Path -LiteralPath $localState)) {
      $profileOk = $true
    }

    if (Test-Path -LiteralPath $exitFile) {
      $codeText = ([System.IO.File]::ReadAllText($exitFile)).Trim()
      Write-Output ('UAT_EXIT=' + $codeText)
      Write-Output ('CHROME_GPU_GOODBYE=' + (Test-LogPhrase $chromeLog "GPU process isn't usable"))
      Write-Output ('ELECTRON_GPU_GOODBYE=' + (Test-LogPhrase $electronLog "GPU process isn't usable"))
      Write-Output ('PROFILE_ADOPTED=' + $(if ($profileOk) { 'yes' } else { 'no' }))

      if (Test-Path -LiteralPath $markerPath) {
        try {
          $rawMarker = ([System.IO.File]::ReadAllText($markerPath)).Trim()

          if ($rawMarker.Length -gt 500) {
            $rawMarker = $rawMarker.Substring(0, 500)
          }

          Write-Output ('SANDBOX_MARKER=' + $rawMarker)
        } catch {
          Write-Output 'SANDBOX_MARKER=unread'
        }
      }

      Stop-A5 9 'EARLY_EXIT'
    }

    $window = $false

    foreach ($proc in (Get-HermesProcesses)) {
      if (-not $proc.ExecutablePath -or ($proc.ExecutablePath -ine $UatExe)) {
        continue
      }

      $gp = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue

      if ($gp -and $gp.MainWindowHandle -ne 0) {
        $window = $true

        if (-not $announcedWindow) {
          $announcedWindow = $true
          Write-Output ('UAT_WINDOW pid=' + $proc.ProcessId + ' title=' + $gp.MainWindowTitle)
        }
      }
    }

    if ($window) {
      if (-not $stableSince) {
        $stableSince = Get-Date
      }

      $mainStill = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $uatPid)

      if (((Get-Date) - $stableSince).TotalSeconds -ge 15 -and $profileOk -and $mainStill) {
        Write-Output 'WINDOW_STABLE'
        Write-Output ('UAT_MAIN_PID=' + $uatPid)
        Write-Output ('UAT_EXE=' + $UatExe)
        Write-Output ('ATTEMPT=' + $Attempt)
        Write-Output ('DEST_FILE=' + $DestFile)
        $script:ExitCode = 0
        return
      }
    } else {
      $stableSince = $null
    }

    if (-not $window -and (Get-Date) -gt $noWindowAfter) {
      break
    }

    Start-Sleep -Seconds 1
  }

  if (-not $profileOk) {
    Stop-UatTree $uatPid
    Stop-UatTree $cmdPid
    Stop-A5 13 'PROFILE_NOT_ADOPTED'
  }

  Stop-UatTree $uatPid
  Stop-UatTree $cmdPid
  Stop-A5 15 'NO_WINDOW'
}

function Invoke-AssertDestEmpty {
  Write-Output 'PHASE_ASSERT_DEST'
  $destDir = Join-Path $Attempt 'dest'

  if (-not (Test-Path -LiteralPath $destDir)) {
    Stop-A5 23 'ATTEMPT_MISSING'
  }

  $items = @(Get-ChildItem -LiteralPath $destDir -Force -ErrorAction SilentlyContinue)

  if ($items.Count -ne 0) {
    Stop-A5 21 'CANCEL_WROTE_OR_DIRTY'
  }

  Write-Output 'DEST_EMPTY'
  $script:ExitCode = 0
}

function Invoke-Hash {
  Write-Output 'PHASE_HASH'

  if (-not (Test-Path -LiteralPath $DestFile)) {
    Stop-A5 22 'DEST_FILE_MISSING'
  }

  $item = Get-Item -LiteralPath $DestFile
  $hash = Get-FileHash -LiteralPath $DestFile -Algorithm SHA256
  Write-Output ('DEST_BYTES=' + $item.Length)
  Write-Output ('DEST_SHA256=' + $hash.Hash)
  Write-Output 'HASH_RECORDED_NOT_A_PASS'
  $script:ExitCode = 0
}

try {
  if ($Phase -eq 'Launch') {
    Invoke-Launch
  } elseif ($Phase -eq 'AssertDestEmpty') {
    Invoke-AssertDestEmpty
  } else {
    Invoke-Hash
  }
} catch {
  if ($script:ExitCode -eq 99) {
    Write-Stop 'UNCAUGHT'
    Write-Output $_.Exception.Message
  }
} finally {
  if ($Phase -eq 'Launch' -and $script:Launched -and $script:ExitCode -ne 0) {
    try {
      Stop-MatchingUat
    } catch {
      Write-Output 'KILL_ERROR'
    }
  }

  Restore-HermesProtocol
  Undo-UnlaunchedAttempt
}

exit $script:ExitCode
