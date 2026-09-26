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
  Do not delete, rename, or launch C:\Users\ddewit\hermes-uat-a5-ns2-20260926.
  The only new folder is C:\Users\ddewit\hermes-uat-a5-ns3-20260926.
  This Launch starts Hermes once. It also starts a read-only window probe
  on session 1. That probe is part of this Launch. It is not a second Launch.
  Do not start the probe yourself.
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
$script:LeaveUatRunning = $false
$script:LeaveAnnounced = $false
$script:UatPid = [uint32]0
$script:ProbePid = [uint32]0
$script:ProbeStopFile = $null

$UatExe = 'C:\Users\ddewit\hermes-uat-desktop-renderer-reuse-20260923\source\apps\desktop\release\win-unpacked\Hermes.exe'
$DailyExe = 'C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe'
$Attempt = 'C:\Users\ddewit\hermes-uat-a5-ns3-20260926'
$FrozenAttempt = 'C:\Users\ddewit\hermes-uat-a5-ns-20260926'
$FrozenNs2 = 'C:\Users\ddewit\hermes-uat-a5-ns2-20260926'
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

function Publish-LeaveRunning([uint32]$RootPid) {
  $script:LeaveUatRunning = $true
  $script:UatPid = $RootPid

  if (-not $script:LeaveAnnounced) {
    $script:LeaveAnnounced = $true
    Write-Output ('LEAVE_UAT_RUNNING pid=' + $RootPid)
  }
}

function Use-IntentionalUatStop {
  $script:LeaveUatRunning = $false
}

function Undo-UnlaunchedAttempt {
  if ($script:Launched -or -not $script:CreatedAttempt) {
    return
  }

  $allowed = 'C:\Users\ddewit\hermes-uat-a5-ns3-20260926'

  if (-not [string]::Equals($Attempt, $allowed, [System.StringComparison]::OrdinalIgnoreCase)) {
    Write-Output 'ROLLBACK_REFUSED'
    return
  }

  foreach ($forbidden in @(
      $FrozenAttempt,
      $FrozenNs2,
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
      Use-IntentionalUatStop
      Stop-A5 16 'DAILY_HERMES_DIED'
    }
  }
}

function Get-A5WindowDecision {
  param(
    [int]$ProbeSession,
    [string]$ProbeOwner,
    [bool]$ProbeFresh,
    [int]$Visible,
    [int]$Hidden,
    [double]$VisibleSeconds,
    [bool]$ProfileOk,
    [bool]$MainAlive,
    [bool]$PastNoWindow,
    [bool]$PastHardStop,
    [int]$Small = 0,
    [string]$ProbeDesktop = ''
  )

  $ownerOk = [string]::Equals([string]$ProbeOwner, 'ddewit', [System.StringComparison]::OrdinalIgnoreCase)
  $desktopOk = [string]::Equals([string]$ProbeDesktop, 'Default', [System.StringComparison]::OrdinalIgnoreCase)

  if (-not $ProbeFresh -or $ProbeSession -ne 1 -or -not $ownerOk -or -not $desktopOk) {
    return 'probe-bad'
  }

  if ($Visible -ge 1 -and $VisibleSeconds -ge 15 -and $ProfileOk -and $MainAlive) {
    return 'stable'
  }

  if ($PastHardStop -and $Visible -ge 1) {
    return 'unstable'
  }

  $noLarge = ($Visible -eq 0 -and $Hidden -eq 0)
  $noWindows = ($noLarge -and $Small -le 0)

  if (-not $ProfileOk -and ($PastHardStop -or ($PastNoWindow -and $noWindows))) {
    return 'profile'
  }

  if ($PastHardStop -and $Hidden -ge 1) {
    return 'hidden'
  }

  if ($PastHardStop -and $noLarge -and $Small -ge 1) {
    return 'unstable'
  }

  if (($PastHardStop -or $PastNoWindow) -and $noWindows -and $ProfileOk) {
    return 'no-window'
  }

  return 'wait'
}

function Test-A5WindowKill([string]$Decision) {
  return @('hidden', 'no-window', 'profile') -contains $Decision
}

function Read-A5ProbeText {
  param([string]$Text)

  $seen = @{}
  $complete = $false

  foreach ($line in ($Text -split "`r?`n")) {
    if ($line -eq 'END') {
      $complete = $true
      break
    }

    if ($line -notmatch '^([A-Z0-9_]+)=(.*)$') {
      continue
    }

    $key = [string]$Matches[1]
    $value = [string]$Matches[2]

    if (-not $seen.ContainsKey($key)) {
      $seen[$key] = $value
    }
  }

  $result = [pscustomobject]@{
    Complete = $false
    Ok = $false
    Session = -1
    Owner = ''
    ProbePid = [uint32]0
    Visible = 0
    Hidden = 0
    VisiblePid = ''
    HiddenPid = ''
    Title = ''
    Rect = ''
    Small = 0
    Desktop = ''
  }

  if (-not $complete) {
    return $result
  }

  $takeInt = {
    param([hashtable]$Map, [string]$Name)

    if (-not $Map.ContainsKey($Name)) {
      return $null
    }

    $raw = [string]$Map[$Name]

    if ($raw -notmatch '^\d+$') {
      return $null
    }

    return [int]$raw
  }

  $ok = & $takeInt $seen 'PROBE_OK'
  $session = & $takeInt $seen 'PROBE_SESSION'
  $visible = & $takeInt $seen 'VISIBLE'
  $hidden = & $takeInt $seen 'HIDDEN'
  $probePid = & $takeInt $seen 'PROBE_PID'

  if ($null -eq $ok -or $null -eq $session -or $null -eq $visible -or $null -eq $hidden) {
    return $result
  }

  $rect = ''

  if ($seen.ContainsKey('VISIBLE_RECT') -and ([string]$seen['VISIBLE_RECT'] -match '^-?\d+,-?\d+,\d+,\d+$')) {
    $rect = [string]$seen['VISIBLE_RECT']
  }

  $small = 0
  $smallRaw = & $takeInt $seen 'SMALL'

  if ($null -ne $smallRaw) {
    $small = [int]$smallRaw
  }

  $pidValue = [uint32]0

  if ($null -ne $probePid) {
    $pidValue = [uint32]$probePid
  }

  return [pscustomobject]@{
    Complete = $true
    Ok = ($ok -eq 1)
    Session = $session
    Owner = [string]$seen['PROBE_OWNER']
    ProbePid = $pidValue
    Visible = $visible
    Hidden = $hidden
    VisiblePid = [string]$seen['VISIBLE_PID']
    HiddenPid = [string]$seen['HIDDEN_PID']
    Title = [string]$seen['VISIBLE_TITLE']
    Rect = $rect
    Small = $small
    Desktop = [string]$seen['DESKTOP']
  }
}

function Format-A5MarkerText([string]$Raw) {
  if ($null -eq $Raw) {
    return 'unread'
  }

  $text = $Raw.Trim()

  if ($text.Length -gt 500) {
    $text = $text.Substring(0, 500)
  }

  $builder = New-Object System.Text.StringBuilder

  foreach ($ch in $text.ToCharArray()) {
    if ([int]$ch -lt 32) {
      [void]$builder.Append(' ')
    } else {
      [void]$builder.Append($ch)
    }
  }

  return $builder.ToString()
}

function Get-A5SampleAge($LastWriteUtc) {
  if ($null -eq $LastWriteUtc) {
    return 999.0
  }

  $stamp = [datetime]$LastWriteUtc

  if ($stamp.Kind -eq [DateTimeKind]::Unspecified) {
    $stamp = [datetime]::SpecifyKind($stamp, [DateTimeKind]::Utc)
  }

  return ((Get-Date).ToUniversalTime() - $stamp.ToUniversalTime()).TotalSeconds
}

function Write-CappedFile([string]$Label, [string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    Write-Output ($Label + '=absent')
    return
  }

  try {
    $raw = [System.IO.File]::ReadAllText($Path)
    Write-Output ($Label + '=' + (Format-A5MarkerText $raw))
  } catch {
    Write-Output ($Label + '=unread')
  }
}

function Stop-WindowProbe {
  if ($script:ProbeStopFile) {
    try {
      $stopDir = Split-Path -Parent $script:ProbeStopFile

      if ($stopDir -and (Test-Path -LiteralPath $stopDir)) {
        [System.IO.File]::WriteAllText($script:ProbeStopFile, 'stop')
      }
    } catch {
      Write-Output 'PROBE_STOP_WRITE_ERROR'
    }
  }

  $rootPid = [uint32]$script:ProbePid

  if ($rootPid -eq 0) {
    return
  }

  $proc = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $rootPid)

  if (-not $proc) {
    return
  }

  $path = [string]$proc.ExecutablePath
  $cmd = [string]$proc.CommandLine
  $expected = Join-Path $Attempt 'logs\window-probe.ps1'
  $powershell = $path -and ($path -like '*\powershell.exe')
  $mentionsProbe = $cmd -like ('*' + $expected + '*')
  $isHermes = $path -and (($path -ieq $UatExe) -or ($path -ieq $DailyExe))

  if (-not $powershell -or -not $mentionsProbe -or $isHermes) {
    Write-Output ('PROBE_KILL_REFUSED pid=' + $rootPid)
    return
  }

  $kill = Invoke-Native -File 'taskkill.exe' -ArgumentList @('/PID', ([string]$rootPid), '/F')

  if ($kill.ExitCode -ne 0) {
    Write-Output ('PROBE_KILL_ERROR pid=' + $rootPid + ' taskkill=' + $kill.ExitCode)
    return
  }

  Write-Output ('PROBE_STOPPED pid=' + $rootPid)
}

function Install-WindowProbe {
  $probeScript = Join-Path $Attempt 'logs\window-probe.ps1'
  $statusFile = Join-Path $Attempt 'logs\window-probe.txt'
  $stopFile = Join-Path $Attempt 'logs\window-probe.stop'
  $script:ProbeStopFile = $stopFile

  if (Test-Path -LiteralPath $stopFile) {
    Remove-Item -LiteralPath $stopFile -Force
  }

  if (Test-Path -LiteralPath $statusFile) {
    Remove-Item -LiteralPath $statusFile -Force
  }

  $probeBody = @'
$ErrorActionPreference = 'Stop'
$uatExe = '__UAT_EXE__'
$statusFile = '__STATUS_FILE__'
$stopFile = '__STOP_FILE__'

function Write-ProbeStatus([string]$Body) {
  $dir = Split-Path -Parent $statusFile
  $tmp = Join-Path $dir ('window-probe-' + [guid]::NewGuid().ToString('n') + '.tmp')
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($tmp, $Body, $utf8)

  if (Test-Path -LiteralPath $statusFile) {
    [System.IO.File]::Replace($tmp, $statusFile, [NullString]::Value)
  } else {
    [System.IO.File]::Move($tmp, $statusFile)
  }
}

try {
  $session = [int](Get-Process -Id $PID).SessionId
  $owner = [string]$env:USERNAME
  $selfPid = [string]$PID

  if ($session -ne 1 -or ($owner -ine 'ddewit')) {
    Write-ProbeStatus ("PROBE_OK=0`r`nPROBE_SESSION=" + $session + "`r`nPROBE_OWNER=" + $owner + "`r`nPROBE_PID=" + $selfPid + "`r`nPROBE_ERROR=identity`r`nEND`r`n")
    exit 1
  }

  Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class A5WindowScan {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
  [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint dwThreadId);
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr hObj, int nIndex, IntPtr pvInfo, uint nLength, out uint lpnLengthNeeded);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  public class Row { public uint Pid; public bool Visible; public int Left; public int Top; public int Width; public int Height; public string Title; }
  static List<Row> pending;
  static EnumProc callback;
  static bool Callback(IntPtr h, IntPtr l) {
    uint pid;
    GetWindowThreadProcessId(h, out pid);
    RECT rect;
    int width = 0;
    int height = 0;
    int left = 0;
    int top = 0;
    if (GetWindowRect(h, out rect)) {
      left = rect.Left;
      top = rect.Top;
      width = rect.Right - rect.Left;
      height = rect.Bottom - rect.Top;
    }
    var sb = new StringBuilder(180);
    GetWindowText(h, sb, 180);
    var clean = new StringBuilder();
    string raw = sb.ToString();
    for (int i = 0; i < raw.Length && clean.Length < 120; i++) {
      char c = raw[i];
      if (c < 32 || c == 127) clean.Append(' ');
      else clean.Append(c);
    }
    var row = new Row();
    row.Pid = pid;
    row.Visible = IsWindowVisible(h) && !IsIconic(h);
    row.Left = left;
    row.Top = top;
    row.Width = width;
    row.Height = height;
    row.Title = clean.ToString();
    pending.Add(row);
    return true;
  }
  public static List<Row> Scan() {
    pending = new List<Row>();
    if (callback == null) callback = Callback;
    EnumWindows(callback, IntPtr.Zero);
    return pending;
  }
  public static string Desktop() {
    IntPtr desk = GetThreadDesktop(GetCurrentThreadId());
    if (desk == IntPtr.Zero) return "";
    uint needed;
    GetUserObjectInformation(desk, 2, IntPtr.Zero, 0, out needed);
    if (needed < 2 || needed > 512) return "";
    IntPtr buf = Marshal.AllocHGlobal((int)needed);
    try {
      uint got;
      if (!GetUserObjectInformation(desk, 2, buf, needed, out got)) return "";
      string raw = Marshal.PtrToStringUni(buf);
      if (raw == null) return "";
      var clean = new StringBuilder();
      for (int i = 0; i < raw.Length && clean.Length < 80; i++) {
        char c = raw[i];
        if (c >= 32 && c != 127) clean.Append(c);
      }
      return clean.ToString();
    } finally {
      Marshal.FreeHGlobal(buf);
    }
  }
}
"@

  $desktopName = [string]([A5WindowScan]::Desktop())

  if ($desktopName -ne 'Default') {
    Write-ProbeStatus ("PROBE_OK=0`r`nPROBE_SESSION=" + $session + "`r`nPROBE_OWNER=" + $owner + "`r`nPROBE_PID=" + $selfPid + "`r`nPROBE_ERROR=desktop`r`nDESKTOP=" + $desktopName + "`r`nEND`r`n")
    exit 1
  }

  $deadline = (Get-Date).AddSeconds(200)

  while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $stopFile) {
      break
    }

    try {
    $visible = 0
    $hidden = 0
    $small = 0
    $visiblePid = ''
    $hiddenPid = ''
    $visibleTitle = ''
    $visibleRect = ''
    $uatPids = @{}

    foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name = 'Hermes.exe'" -ErrorAction SilentlyContinue)) {
      if ($proc.ExecutablePath -and ($proc.ExecutablePath -ieq $uatExe)) {
        $uatPids[[string]([uint32]$proc.ProcessId)] = $true
      }
    }

    foreach ($row in @([A5WindowScan]::Scan())) {
      $pidKey = [string]([uint32]$row.Pid)

      if (-not $uatPids.ContainsKey($pidKey)) {
        continue
      }

      if ([int]$row.Width -lt 400 -or [int]$row.Height -lt 500) {
        $small++
        continue
      }

      if ($row.Visible) {
        $visible++

        if (-not $visiblePid) {
          $visiblePid = $pidKey
          $visibleTitle = [string]$row.Title
          $visibleRect = ([string]([int]$row.Left) + ',' + [string]([int]$row.Top) + ',' + [string]([int]$row.Width) + ',' + [string]([int]$row.Height))
        }
      } else {
        $hidden++

        if (-not $hiddenPid) {
          $hiddenPid = $pidKey
        }
      }
    }

    $lines = @(
      'PROBE_OK=1',
      ('PROBE_SESSION=' + $session),
      ('PROBE_OWNER=' + $owner),
      ('PROBE_PID=' + $selfPid),
      ('DESKTOP=' + $desktopName),
      ('VISIBLE=' + $visible),
      ('HIDDEN=' + $hidden),
      ('SMALL=' + $small),
      ('VISIBLE_PID=' + $visiblePid),
      ('HIDDEN_PID=' + $hiddenPid),
      ('VISIBLE_RECT=' + $visibleRect),
      ('VISIBLE_TITLE=' + $visibleTitle),
      'END'
    )
    Write-ProbeStatus (($lines -join "`r`n") + "`r`n")
    } catch {
      Start-Sleep -Seconds 1
      continue
    }

    Start-Sleep -Seconds 1
  }
} catch {
  try {
    $sessionText = '-1'
    $ownerText = ''

    try {
      $sessionText = [string](Get-Process -Id $PID).SessionId
      $ownerText = [string]$env:USERNAME
    } catch {
      $sessionText = '-1'
    }

    $err = 'scan'
    $message = [string]$_.Exception.Message

    if ($message -like '*Add-Type*' -or $message -like '*Compilation*') {
      $err = 'addtype'
    }

    Write-ProbeStatus ("PROBE_OK=0`r`nPROBE_SESSION=" + $sessionText + "`r`nPROBE_OWNER=" + $ownerText + "`r`nPROBE_PID=" + [string]$PID + "`r`nPROBE_ERROR=" + $err + "`r`nEND`r`n")
  } catch {
    exit 1
  }

  exit 1
}

exit 0
'@

  $probeBody = $probeBody.Replace('__UAT_EXE__', $UatExe)
  $probeBody = $probeBody.Replace('__STATUS_FILE__', $statusFile)
  $probeBody = $probeBody.Replace('__STOP_FILE__', $stopFile)
  $probeBody = $probeBody -replace "`r`n", "`n" -replace "`n", "`r`n"
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($probeScript, $probeBody, $utf8)

  if ($probeBody -like ('*' + $FrozenAttempt + '*') -or $probeBody -like ('*' + $FrozenNs2 + '*') -or $probeBody -like ('*' + $Evidence + '*')) {
    Stop-A5 8 'WRAPPER_UNSAFE'
  }

  if ($probeBody -notlike ('*' + $UatExe + '*')) {
    Stop-A5 8 'WRAPPER_UNSAFE'
  }

  return $probeScript
}

function Invoke-Launch {
  Write-Output 'PHASE_LAUNCH'
  Write-Output 'DO_NOT_KILL_DAILY=1'
  Write-Output ('FROZEN_ATTEMPT=' + $FrozenAttempt)
  Write-Output 'DO_NOT_DELETE_FROZEN=1'
  Write-Output 'DO_NOT_RENAME_FROZEN=1'
  Write-Output 'DO_NOT_LAUNCH_FROZEN=1'
  Write-Output ('FROZEN_NS2=' + $FrozenNs2)
  Write-Output 'DO_NOT_DELETE_NS2=1'
  Write-Output 'DO_NOT_RENAME_NS2=1'
  Write-Output 'DO_NOT_LAUNCH_NS2=1'
  Write-Output 'ONE_LAUNCH_ONLY=1'
  Write-Output ('NEW_ATTEMPT=' + $Attempt)

  foreach ($frozen in @($FrozenAttempt, $FrozenNs2, $Evidence)) {
    if ([string]::Equals($Attempt, $frozen, [System.StringComparison]::OrdinalIgnoreCase)) {
      Stop-A5 5 'FROZEN_ATTEMPT_SELECTED'
    }
  }

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

  Write-CappedFile 'FROZEN_NS_MARKER' (Join-Path $FrozenAttempt 'user-data\windows-sandbox-fallback.json')
  Write-CappedFile 'FROZEN_NS2_MARKER' (Join-Path $FrozenNs2 'user-data\windows-sandbox-fallback.json')
  Write-Output 'FROZEN_MARKER_IS_NOT_A_LAUNCH_GRANT=1'

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

  if ($wrapRaw -like ('*' + $FrozenAttempt + '*') -or $wrapRaw -like ('*' + $FrozenNs2 + '*') -or $wrapRaw -like ('*' + $Evidence + '*')) {
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
  $script:UatPid = $uatPid
  $script:LeaveUatRunning = $true

  # MainWindowHandle enumerates the calling desktop only. This process is the
  # SSH logon. Hermes was started with PsExec -i 1, on the interactive desktop.
  # A zero handle in this process is not evidence that the window is absent.
  $probeScript = Install-WindowProbe
  $detectorSession = -1

  try {
    $detectorSession = [int](Get-Process -Id $PID).SessionId
  } catch {
    $detectorSession = -1
  }

  Write-Output ('DETECTOR_SESSION=' + $detectorSession)
  $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

  if (-not (Test-Path -LiteralPath $powershellExe)) {
    Publish-LeaveRunning $uatPid
    Stop-A5 19 'WINDOW_PROBE_FAILED'
  }

  $probeLaunch = Invoke-Native -File $psexecPath -ArgumentList @(
    '-accepteula', '-nobanner', '-i', '1', '-d',
    $powershellExe, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $probeScript
  )
  Write-Output ('PROBE_PSEXEC_EXIT=' + $probeLaunch.ExitCode)
  $probeStarted = Get-Date
  $hardStop = (Get-Date).AddSeconds(180)
  $noWindowAfter = (Get-Date).AddSeconds(90)
  $stableSince = $null
  $profileOk = $false
  $announcedWindow = $false
  $announcedHidden = $false
  $announcedSmall = $false
  $announcedProbe = $false
  $lastGood = $null
  $lastProbeWrite = $null
  $lastParsed = $null
  $decision = 'wait'
  $statusFile = Join-Path $Attempt 'logs\window-probe.txt'

  while ((Get-Date) -lt $hardStop) {
    Assert-DailyStillAlive $dailyPids

    if ((Get-Stamp $DailyMarker) -ne $markerBefore -or (Get-Stamp $DailyConnection) -ne $connectionBefore) {
      Use-IntentionalUatStop
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
      Write-CappedFile 'SANDBOX_MARKER' $markerPath
      Use-IntentionalUatStop
      Stop-A5 9 'EARLY_EXIT'
    }

    $parsed = $null

    if (Test-Path -LiteralPath $statusFile) {
      try {
        $parsed = Read-A5ProbeText ([System.IO.File]::ReadAllText($statusFile))
      } catch {
        $parsed = $null
      }
    }

    if ($parsed -and $parsed.Complete) {
      $write = $null

      try {
        $write = (Get-Item -LiteralPath $statusFile).LastWriteTimeUtc
      } catch {
        $write = $null
      }

      $newWrite = $false

      if ($write -and (($null -eq $lastProbeWrite) -or ($write -ne $lastProbeWrite))) {
        $newWrite = $true
        $lastProbeWrite = $write
        $lastGood = $write
      }

      if ($newWrite) {
        $lastParsed = $parsed

        if ($parsed.ProbePid -gt 0) {
          $script:ProbePid = [uint32]$parsed.ProbePid
        }

        if (-not $announcedProbe) {
          $announcedProbe = $true
          Write-Output ('WINDOW_PROBE_SESSION=' + $parsed.Session)
          Write-Output ('WINDOW_PROBE_OWNER=' + $parsed.Owner)
          Write-Output ('WINDOW_PROBE_PID=' + $parsed.ProbePid)
          Write-Output ('WINDOW_PROBE_DESKTOP=' + (Format-A5MarkerText ([string]$parsed.Desktop)))

          if (-not $parsed.Ok) {
            Write-Output 'WINDOW_PROBE_REJECTED=1'
          }
        }

        if (-not $parsed.Ok) {
          $decision = 'probe-bad'
          break
        }
      }
    }

    $age = Get-A5SampleAge $lastGood

    $probeFresh = $false
    $visible = 0
    $hidden = 0
    $small = 0
    $probeSession = -1
    $probeOwner = ''
    $probeDesktop = ''

    if ($lastParsed -and $lastParsed.Complete -and $lastParsed.Ok -and $age -le 5) {
      $probeFresh = $true
      $visible = [int]$lastParsed.Visible
      $hidden = [int]$lastParsed.Hidden
      $small = [int]$lastParsed.Small
      $probeSession = [int]$lastParsed.Session
      $probeOwner = [string]$lastParsed.Owner
      $probeDesktop = [string]$lastParsed.Desktop
    }

    if ($probeFresh -and $visible -ge 1) {
      if (-not $stableSince) {
        $stableSince = Get-Date
      }

      if (-not $announcedWindow) {
        $announcedWindow = $true
        $title = Format-A5MarkerText ([string]$lastParsed.Title)
        Write-Output ('UAT_WINDOW pid=' + $lastParsed.VisiblePid + ' title=' + $title + ' rect=' + $lastParsed.Rect)
      }
    } else {
      $stableSince = $null
    }

    if ($probeFresh -and $visible -eq 0 -and $hidden -ge 1 -and -not $announcedHidden) {
      $announcedHidden = $true
      Write-Output ('UAT_WINDOW_HIDDEN pid=' + $lastParsed.HiddenPid + ' count=' + $hidden)
    }

    if ($probeFresh -and $visible -eq 0 -and [int]$lastParsed.Small -ge 1 -and -not $announcedSmall) {
      $announcedSmall = $true
      Write-Output ('UAT_WINDOW_SMALL count=' + $lastParsed.Small)
    }

    $visibleSeconds = 0.0

    if ($stableSince) {
      $visibleSeconds = ((Get-Date) - $stableSince).TotalSeconds
    }

    $mainStill = $null

    try {
      $mainStill = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $uatPid)
    } catch {
      $mainStill = $null
    }

    $pastNoWindow = (Get-Date) -gt $noWindowAfter
    $decision = Get-A5WindowDecision -ProbeSession $probeSession -ProbeOwner $probeOwner -ProbeDesktop $probeDesktop -ProbeFresh:$probeFresh -Visible $visible -Hidden $hidden -Small $small -VisibleSeconds $visibleSeconds -ProfileOk:$profileOk -MainAlive:([bool]$mainStill) -PastNoWindow:$pastNoWindow -PastHardStop:$false

    if ($decision -eq 'stable') {
      Write-Output 'WINDOW_STABLE'
      Write-Output ('UAT_MAIN_PID=' + $uatPid)
      Write-Output ('UAT_EXE=' + $UatExe)
      Write-Output ('ATTEMPT=' + $Attempt)
      Write-Output ('DEST_FILE=' + $DestFile)
      Write-Output 'ONE_LAUNCH_ONLY=1'
      $script:ExitCode = 0
      return
    }

    if ($decision -eq 'no-window' -or $decision -eq 'profile') {
      break
    }

    if ($decision -eq 'probe-bad') {
      $startedAgo = ((Get-Date) - $probeStarted).TotalSeconds
      $stale = $lastGood -and ((Get-A5SampleAge $lastGood) -gt 20)

      if ((-not $lastGood -and $startedAgo -gt 30) -or $stale) {
        break
      }
    }

    Start-Sleep -Seconds 1
  }

  if ($decision -eq 'wait') {
    $age = Get-A5SampleAge $lastGood

    $probeFresh = $false
    $visible = 0
    $hidden = 0
    $small = 0
    $probeSession = -1
    $probeOwner = ''
    $probeDesktop = ''

    if ($lastParsed -and $lastParsed.Complete -and $lastParsed.Ok -and $age -le 5) {
      $probeFresh = $true
      $visible = [int]$lastParsed.Visible
      $hidden = [int]$lastParsed.Hidden
      $small = [int]$lastParsed.Small
      $probeSession = [int]$lastParsed.Session
      $probeOwner = [string]$lastParsed.Owner
      $probeDesktop = [string]$lastParsed.Desktop
    }

    $visibleSeconds = 0.0

    if ($stableSince) {
      $visibleSeconds = ((Get-Date) - $stableSince).TotalSeconds
    }

    $mainStill = $null

    try {
      $mainStill = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $uatPid)
    } catch {
      $mainStill = $null
    }

    $decision = Get-A5WindowDecision -ProbeSession $probeSession -ProbeOwner $probeOwner -ProbeDesktop $probeDesktop -ProbeFresh:$probeFresh -Visible $visible -Hidden $hidden -Small $small -VisibleSeconds $visibleSeconds -ProfileOk:$profileOk -MainAlive:([bool]$mainStill) -PastNoWindow:$true -PastHardStop:$true
  }

  Write-CappedFile 'SANDBOX_MARKER' (Join-Path $UserData 'windows-sandbox-fallback.json')
  Write-Output ('PROFILE_ADOPTED=' + $(if ($profileOk) { 'yes' } else { 'no' }))
  Write-Output ('WINDOW_DECISION=' + $decision)

  if ($decision -eq 'stable') {
    Write-Output 'WINDOW_STABLE'
    Write-Output ('UAT_MAIN_PID=' + $uatPid)
    Write-Output ('UAT_EXE=' + $UatExe)
    Write-Output ('ATTEMPT=' + $Attempt)
    Write-Output ('DEST_FILE=' + $DestFile)
    Write-Output 'ONE_LAUNCH_ONLY=1'
    $script:ExitCode = 0
    return
  }

  if ($decision -eq 'probe-bad' -or $decision -eq 'unstable' -or $decision -eq 'wait') {
    Publish-LeaveRunning $uatPid

    if ($decision -eq 'unstable' -or $decision -eq 'wait') {
      Stop-A5 20 'WINDOW_UNSTABLE'
    }

    Stop-A5 19 'WINDOW_PROBE_FAILED'
  }

  if ($decision -eq 'profile') {
    Use-IntentionalUatStop
    Stop-UatTree $uatPid
    Stop-UatTree $cmdPid
    Stop-A5 13 'PROFILE_NOT_ADOPTED'
  }

  if ($decision -eq 'hidden') {
    Use-IntentionalUatStop
    Stop-UatTree $uatPid
    Stop-UatTree $cmdPid
    Stop-A5 18 'WINDOW_NOT_VISIBLE'
  }

  if ($decision -ne 'no-window') {
    Publish-LeaveRunning $uatPid
    Stop-A5 19 'WINDOW_PROBE_FAILED'
  }

  Use-IntentionalUatStop
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

if ($MyInvocation.InvocationName -ne '.') {
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
    if ($Phase -eq 'Launch') {
      try {
        Stop-WindowProbe
      } catch {
        Write-Output 'PROBE_STOP_ERROR'
      }
    }

    if ($Phase -eq 'Launch' -and $script:LeaveUatRunning -and $script:ExitCode -ne 0) {
      Publish-LeaveRunning $script:UatPid
    }

    if ($Phase -eq 'Launch' -and $script:Launched -and $script:ExitCode -ne 0 -and -not $script:LeaveUatRunning) {
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
}
