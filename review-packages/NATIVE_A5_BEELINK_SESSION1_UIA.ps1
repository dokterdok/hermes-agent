#Requires -Version 5.1
<#
  Session-1 UI Automation drive for the live ns3 Files UAT.

  Copy this file, unmodified, to:
    C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\a5-session1-uia.ps1

  Run it only through the existing PsExec64, on session 1, after the SSH
  CIM attestation in NATIVE_A5_BEELINK_EARLY_EXIT_DIAGNOSIS.md is CLEAN
  for this action's pid. Discover is the read-only exception: it does not
  click, and its output is not that attestation.

  This file is not a Launch. It does not start Hermes. It does not kill a
  process. It does not read a password onto the command line or the output.
  Do not pass -Phase Launch. Do not add -d or -s to PsExec.
#>
[CmdletBinding()]
param(
  [string]$Action = '',
  [string]$AttestedPid = '',
  [string]$Nonce = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$script:UatExe = 'C:\Users\ddewit\hermes-uat-desktop-renderer-reuse-20260923\source\apps\desktop\release\win-unpacked\Hermes.exe'
$script:DailyExe = 'C:\Users\ddewit\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe'
$script:DailyRoot = 'C:\Users\ddewit\AppData\Local\hermes\hermes-agent'
$script:UserData = 'C:\Users\ddewit\hermes-uat-a5-ns3-20260926\user-data'
$script:DestFile = 'C:\Users\ddewit\hermes-uat-a5-ns3-20260926\dest\uat-download.bin'
$script:UsernameFile = 'C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\uat-username.txt'
$script:PasswordFile = 'C:\Users\ddewit\AppData\Local\Temp\hermes-uat-live\uat-password.txt'
$script:A5Stopped = $false
$script:A5UiaReady = $false

$script:A5FieldNames = @(
  'UIA_HELPER', 'UIA_PSEXEC_IS_NOT_A_PID', 'UIA_NONCE', 'UIA_ACTION',
  'UIA_HOST_SESSION', 'UIA_HOST_USER', 'UIA_DESKTOP', 'UIA_PATH_OK', 'UIA_PID',
  'UIA_STOP', 'UIA_DONE', 'UIA_ENTRY_RUNG', 'ENTRY_INVOKED', 'SIGNIN_INVOKED', 'FOCUS_OK',
  'TYPED_USERNAME', 'TYPED_PASSWORD', 'FILES_INVOKED', 'DOWNLOAD_INVOKED',
  'DIALOG_PRESENT', 'DIALOG_BUTTONS', 'CANCEL_INVOKED', 'SAVE_INVOKED',
  'DEST_SET', 'REPLACE_WINDOW', 'REPLACE_NO_INVOKED', 'LOGIN_WINDOW',
  'USERNAME_VISIBLE', 'LOGIN_ERROR', 'FILE_ROW_COUNT', 'DISCOVER_NOT_A_GRANT',
  'DISCOVER_DAILY_SEEN', 'DISCOVER_UAT_PID', 'DISCOVER_ENTRY_PID',
  'DISCOVER_ENTRY_RUNG', 'DISCOVER_WINDOW', 'DISCOVER_ROW_COUNT'
)

$script:A5Actions = @(
  'Discover', 'Entry', 'FocusUsername', 'TypeUsername', 'FocusPassword',
  'TypePassword', 'ClickSignIn', 'ReadLogin', 'ClickFiles', 'ClickDownload',
  'ReadSaveDialog', 'ClickCancel', 'TypeDest', 'ClickSave', 'ReadReplace',
  'ClickReplaceNo'
)

$script:A5Chrome = @(
  'Files', 'File system', 'Download', 'All', 'Images', 'Links', 'Cancel', 'Save',
  'Sign in', 'Username', 'Password', 'Sign in to remote gateway', 'Sign out & sign in',
  'File name:', 'Yes', 'No', 'Retry', 'Repair', 'Gateway settings', 'Use local gateway',
  'Open logs', 'Terminal', 'Preview', 'Refresh tree', 'Collapse all folders',
  'Sign in to Hermes gateway', 'Remote gateway sign-in required'
)

$script:A5NativeSource = @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class A5UiaNative {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint dwThreadId);
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr hObj, int nIndex, IntPtr pvInfo, uint nLength, out uint lpnLengthNeeded);
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
'@

function Test-A5Exact([string]$Left, [string]$Right) {
  return [string]::Equals([string]$Left, [string]$Right, [System.StringComparison]::Ordinal)
}

function Test-A5FieldName([string]$Name) {
  foreach ($item in $script:A5FieldNames) {
    if (Test-A5Exact $item $Name) {
      return $true
    }
  }

  return $false
}

function Test-A5ActionName([string]$Name) {
  foreach ($item in $script:A5Actions) {
    if (Test-A5Exact $item $Name) {
      return $true
    }
  }

  return $false
}

$script:A5StopCodes = @(
  'RUNBOOK_DRIFT',
  'ATTEST_POWERSHELL', 'ATTEST_WOW64', 'ATTEST_WRONG_OWNER', 'ATTEST_PID_MISMATCH',
  'ATTEST_WRONG_SESSION', 'ATTEST_PATH_MISMATCH', 'ATTEST_CMDLINE_REJECTED',
  'CUA_CANNOT_SEE_PROCESS_PATH', 'FOCUS_IS_DAILY',
  'UIA_NOT_SESSION1', 'UIA_DESKTOP', 'UIA_ADDTYPE', 'UIA_FAULT',
  'UIA_FOREGROUND_FAILED', 'UIA_FOCUS_NOT_TARGET', 'UIA_TYPE_FAILED',
  'UIA_INVOKE_UNAVAILABLE', 'UIA_TARGET_CHANGED', 'UIA_PID_MISMATCH',
  'UIA_CONTROL_ABSENT', 'UIA_CONTROL_AMBIGUOUS',
  'SIGNIN_CONTROL_AMBIGUOUS', 'SIGNIN_CONTROL_ABSENT',
  'FILES_CONTROL_ABSENT', 'FILES_CONTROL_AMBIGUOUS', 'FILES_ROW_AMBIGUOUS',
  'DOWNLOAD_CONTROL_ABSENT', 'DOWNLOAD_CONTROL_AMBIGUOUS',
  'SAVE_DIALOG_ABSENT', 'DIALOG_LOCALE_UNEXPECTED',
  'PASSWORD_UNAVAILABLE', 'USERNAME_UNAVAILABLE',
  'REPLACE_PROMPT', 'LOGIN_REJECTED'
)

function Test-A5StopCode([string]$Code) {
  foreach ($item in $script:A5StopCodes) {
    if (Test-A5Exact $item $Code) {
      return $true
    }
  }

  return $false
}

function Out-A5Obj([hashtable]$Map) {
  # Bind the object, then return that variable. A cast written on the return
  # line emits no object, and a later edit would drop every decision result.
  $obj = New-Object System.Management.Automation.PSObject -Property $Map
  return $obj
}

function Test-A5Nonce([string]$Text) {
  if ([string]::IsNullOrEmpty($Text)) {
    return $false
  }

  return [regex]::IsMatch($Text, '^[1-9][0-9]{8,18}$')
}

function Test-A5PidText([string]$Text) {
  if ([string]::IsNullOrEmpty($Text)) {
    return $false
  }

  if (-not [regex]::IsMatch($Text, '^[1-9][0-9]{0,9}$')) {
    return $false
  }

  if ([uint64]$Text -gt 4294967295) {
    return $false
  }

  $parsed = [uint32]$Text

  return [string]$parsed -eq $Text
}

function Write-A5Field([string]$Name, [string]$Value) {
  if ($script:A5Stopped) {
    return
  }

  if (-not (Test-A5FieldName $Name)) {
    $script:A5Stopped = $true
    Write-Output 'UIA_STOP=RUNBOOK_DRIFT'
    return
  }

  if ($null -eq $Value) {
    $Value = ''
  }

  if ($Value.IndexOfAny(@([char]13, [char]10)) -ge 0) {
    $script:A5Stopped = $true
    Write-Output 'UIA_STOP=RUNBOOK_DRIFT'
    return
  }

  if ($Value.Length -gt 240) {
    $Value = $Value.Substring(0, 240)
  }

  Write-Output ($Name + '=' + $Value)
}

function Stop-A5Uia([string]$Code) {
  if ($script:A5Stopped) {
    return
  }

  $script:A5Stopped = $true

  if (-not (Test-A5StopCode $Code)) {
    $Code = 'RUNBOOK_DRIFT'
  }

  Write-Output ('UIA_STOP=' + $Code)
}

function ConvertTo-A5Hwnd([int]$Raw) {
  $bytes = [System.BitConverter]::GetBytes($Raw)
  return [string]([System.BitConverter]::ToUInt32($bytes, 0))
}

function ConvertTo-A5HwndPtr([int]$Raw) {
  $bytes = [System.BitConverter]::GetBytes($Raw)
  $unsigned = [int64]([System.BitConverter]::ToUInt32($bytes, 0))
  return (New-Object System.IntPtr $unsigned)
}

function ConvertTo-A5Title([string]$Raw) {
  if ($null -eq $Raw) {
    return ''
  }

  $sb = New-Object System.Text.StringBuilder

  foreach ($ch in $Raw.ToCharArray()) {
    $code = [int]$ch

    if ($code -lt 32 -or $code -eq 127) {
      [void]$sb.Append(' ')
    } else {
      [void]$sb.Append($ch)
    }

    if ($sb.Length -ge 80) {
      break
    }
  }

  return $sb.ToString()
}

function Get-A5SecretText([string]$Raw) {
  if ($null -eq $Raw) {
    return ''
  }

  if ($Raw.EndsWith("`r`n")) {
    return $Raw.Substring(0, $Raw.Length - 2)
  }

  if ($Raw.Length -gt 0 -and $Raw.EndsWith("`n")) {
    return $Raw.Substring(0, $Raw.Length - 1)
  }

  if ($Raw.Length -gt 0 -and $Raw.EndsWith("`r")) {
    return $Raw.Substring(0, $Raw.Length - 1)
  }

  return $Raw
}

function ConvertTo-A5SendKeys([string]$Text) {
  if ($null -eq $Text) {
    return ''
  }

  $sb = New-Object System.Text.StringBuilder

  foreach ($ch in $Text.ToCharArray()) {
    switch ($ch) {
      '+' { [void]$sb.Append('{+}') }
      '^' { [void]$sb.Append('{^}') }
      '%' { [void]$sb.Append('{%}') }
      '~' { [void]$sb.Append('{~}') }
      '(' { [void]$sb.Append('{(}') }
      ')' { [void]$sb.Append('{)}') }
      '{' { [void]$sb.Append('{{}') }
      '}' { [void]$sb.Append('{}}') }
      '[' { [void]$sb.Append('{[}') }
      ']' { [void]$sb.Append('{]}') }
      default { [void]$sb.Append($ch) }
    }
  }

  return $sb.ToString()
}

function Format-A5Fields($Map) {
  $lines = New-Object System.Collections.Generic.List[string]

  foreach ($key in @($Map.Keys)) {
    $name = [string]$key

    if (-not (Test-A5FieldName $name)) {
      continue
    }

    $value = [string]$Map[$key]

    if ($value.IndexOfAny(@([char]13, [char]10)) -ge 0) {
      continue
    }

    $lines.Add($name + '=' + $value)
  }

  return @($lines)
}

function Test-A5DailyPath([string]$Path) {
  if ([string]::IsNullOrEmpty($Path)) {
    return $false
  }

  $ordinal = [System.StringComparison]::Ordinal
  $ignore = [System.StringComparison]::OrdinalIgnoreCase
  $rootSlash = $script:DailyRoot + '\'
  $hit = $false

  foreach ($prefix in @('', '\\?\', '\??\')) {
    $item = $Path

    if ($prefix -ne '') {
      if ($Path.Length -lt $prefix.Length) {
        continue
      }

      if (-not [string]::Equals($Path.Substring(0, $prefix.Length), $prefix, $ordinal)) {
        continue
      }

      $item = $Path.Substring($prefix.Length)
    }

    $slash = $item.Replace('/', '\')
    $under = $false

    if ($slash.Length -gt $rootSlash.Length) {
      $under = [string]::Equals($slash.Substring(0, $rootSlash.Length), $rootSlash, $ignore)
    }

    if ([string]::Equals($slash, $script:DailyExe, $ignore) -or [string]::Equals($slash, $script:DailyRoot, $ignore) -or $under) {
      $hit = $true
    }
  }

  return $hit
}

function Get-A5CmdPins([string]$Cmd) {
  $udd = $script:UserData
  $uddOk = ($Cmd -like ('*--user-data-dir=' + $udd)) -or ($Cmd -like ('*--user-data-dir=' + $udd + ' *')) -or ($Cmd -like ('*--user-data-dir=' + $udd + '"*')) -or ($Cmd -like ('*--user-data-dir="' + $udd + '"*'))
  $sandboxOk = ($Cmd -like '*--no-sandbox') -or ($Cmd -like '*--no-sandbox *') -or ($Cmd -like '*--no-sandbox=*')
  $frozen = ($Cmd -like '*hermes-uat-a5-ns-20260926*') -or ($Cmd -like '*hermes-uat-a5-ns2-20260926*') -or ($Cmd -like '*hermes-uat-a5-live-20260926*')

  return (Out-A5Obj @{ Udd = [bool]$uddOk; Sandbox = [bool]$sandboxOk; Frozen = [bool]$frozen })
}

function Get-A5GrantDecision($Row) {
  $printed = [string]$Row.PrintedPid
  $wanted = [string]$Row.PidText

  if ($printed -ne $wanted) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'ATTEST_PID_MISMATCH' })
  }

  if ((-not $Row.OwnerRead) -or [string]::IsNullOrEmpty([string]$Row.Owner)) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'CUA_CANNOT_SEE_PROCESS_PATH' })
  }

  if ([string]$Row.Owner -ine 'ddewit') {
    return (Out-A5Obj @{ Ok = $false; Stop = 'ATTEST_WRONG_OWNER' })
  }

  $path = [string]$Row.Path

  if ([string]::IsNullOrEmpty($path)) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'CUA_CANNOT_SEE_PROCESS_PATH' })
  }

  if (Test-A5DailyPath $path) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'FOCUS_IS_DAILY' })
  }

  if ([int]$Row.SessionId -ne 1) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'ATTEST_WRONG_SESSION' })
  }

  if (-not (Test-A5Exact $path $script:UatExe)) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'ATTEST_PATH_MISMATCH' })
  }

  $pins = Get-A5CmdPins ([string]$Row.CommandLine)

  if ((-not $pins.Udd) -or (-not $pins.Sandbox) -or $pins.Frozen) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'ATTEST_CMDLINE_REJECTED' })
  }

  return (Out-A5Obj @{ Ok = $true; Stop = '' })
}

function Get-A5HostDecision([int]$Major, [string]$InstallHome, [string]$User, [int]$Session) {
  if ($Major -ne 5) {
    return 'ATTEST_POWERSHELL'
  }

  if ($InstallHome -like '*\SysWOW64\WindowsPowerShell\*') {
    return 'ATTEST_WOW64'
  }

  if ($User -ine 'ddewit') {
    return 'ATTEST_WRONG_OWNER'
  }

  if ($Session -ne 1) {
    return 'UIA_NOT_SESSION1'
  }

  return ''
}

function Test-A5DesktopName([string]$Name) {
  return [string]::Equals([string]$Name, 'Default', [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-A5EntryDecision($Items) {
  $windowIndexes = @()
  $remoteIndexes = @()
  $signoutIndexes = @()

  for ($i = 0; $i -lt @($Items).Count; $i++) {
    $item = @($Items)[$i]
    $enabled = [bool]$item.Enabled
    $offscreen = [bool]$item.Offscreen

    if ((-not $enabled) -or $offscreen) {
      continue
    }

    $kind = [string]$item.Kind
    $name = [string]$item.Name

    if ((Test-A5Exact $kind 'Window') -and (Test-A5Exact $name 'Sign in to Hermes gateway')) {
      $windowIndexes += $i
    } elseif ((Test-A5Exact $kind 'Button') -and (Test-A5Exact $name 'Sign in to remote gateway')) {
      $remoteIndexes += $i
    } elseif ((Test-A5Exact $kind 'Button') -and (Test-A5Exact $name 'Sign out & sign in')) {
      $signoutIndexes += $i
    }
  }

  if ($windowIndexes.Count -gt 1) {
    return (Out-A5Obj @{ Rung = 'AMBIGUOUS'; Stop = 'SIGNIN_CONTROL_AMBIGUOUS'; Index = -1 })
  }

  if ($windowIndexes.Count -eq 1) {
    return (Out-A5Obj @{ Rung = 'WINDOW'; Stop = ''; Index = [int]$windowIndexes[0] })
  }

  if ($remoteIndexes.Count -gt 1) {
    return (Out-A5Obj @{ Rung = 'AMBIGUOUS'; Stop = 'SIGNIN_CONTROL_AMBIGUOUS'; Index = -1 })
  }

  if ($remoteIndexes.Count -eq 1) {
    return (Out-A5Obj @{ Rung = 'REMOTE'; Stop = ''; Index = [int]$remoteIndexes[0] })
  }

  if ($signoutIndexes.Count -gt 1) {
    return (Out-A5Obj @{ Rung = 'AMBIGUOUS'; Stop = 'SIGNIN_CONTROL_AMBIGUOUS'; Index = -1 })
  }

  if ($signoutIndexes.Count -eq 1) {
    return (Out-A5Obj @{ Rung = 'SIGNOUT'; Stop = ''; Index = [int]$signoutIndexes[0] })
  }

  return (Out-A5Obj @{ Rung = 'ABSENT'; Stop = 'SIGNIN_CONTROL_ABSENT'; Index = -1 })
}

function Test-A5ArtifactSiblings($Names) {
  foreach ($need in @('All', 'Images', 'Files', 'Links')) {
    $found = $false

    foreach ($name in @($Names)) {
      if (Test-A5Exact ([string]$name) $need) {
        $found = $true
      }
    }

    if (-not $found) {
      return $false
    }
  }

  return $true
}

function Select-A5Files($Items) {
  $indexes = @()

  for ($i = 0; $i -lt @($Items).Count; $i++) {
    $item = @($Items)[$i]
    $kind = [string]$item.Kind

    if (-not (Test-A5Exact $kind 'Button') -and -not (Test-A5Exact $kind 'TabItem') -and -not (Test-A5Exact $kind 'SplitButton')) {
      continue
    }

    if ($null -eq $item.Siblings) {
      continue
    }

    if ((-not [bool]$item.Enabled) -or [bool]$item.Offscreen) {
      continue
    }

    if (-not (Test-A5Exact ([string]$item.Name) 'Files')) {
      continue
    }

    if (Test-A5ArtifactSiblings $item.Siblings) {
      continue
    }

    $indexes += $i
  }

  if ($indexes.Count -eq 0) {
    return (Out-A5Obj @{ Stop = 'FILES_CONTROL_ABSENT'; Index = -1 })
  }

  if ($indexes.Count -gt 1) {
    return (Out-A5Obj @{ Stop = 'FILES_CONTROL_AMBIGUOUS'; Index = -1 })
  }

  return (Out-A5Obj @{ Stop = ''; Index = [int]$indexes[0] })
}

function Test-A5ChromeName([string]$Name) {
  foreach ($item in $script:A5Chrome) {
    if (Test-A5Exact $item $Name) {
      return $true
    }
  }

  return $false
}

function Select-A5FileRows($Rows) {
  $indexes = @()

  for ($i = 0; $i -lt @($Rows).Count; $i++) {
    $row = @($Rows)[$i]
    $kind = [string]$row.Kind

    if (-not (Test-A5Exact $kind 'TreeItem') -and -not (Test-A5Exact $kind 'ListItem') -and -not (Test-A5Exact $kind 'DataItem')) {
      continue
    }

    if ((-not [bool]$row.Enabled) -or [bool]$row.Offscreen -or [bool]$row.Folder) {
      continue
    }

    $name = [string]$row.Name

    if ([string]::IsNullOrEmpty($name) -or (Test-A5ChromeName $name)) {
      continue
    }

    $indexes += $i
  }

  if ($indexes.Count -eq 0) {
    return (Out-A5Obj @{ Stop = 'FILES_ROW_ABSENT'; Index = -1; Count = 0 })
  }

  if ($indexes.Count -ne 1) {
    return (Out-A5Obj @{ Stop = 'FILES_ROW_AMBIGUOUS'; Index = -1; Count = $indexes.Count })
  }

  return (Out-A5Obj @{ Stop = ''; Index = [int]$indexes[0]; Count = 1 })
}

function Test-A5SaveButtons($Names) {
  $cancel = $false
  $save = $false
  $count = 0

  foreach ($name in @($Names)) {
    $count++

    if (Test-A5Exact ([string]$name) 'Cancel') {
      $cancel = $true
    } elseif (Test-A5Exact ([string]$name) 'Save') {
      $save = $true
    } else {
      return $false
    }
  }

  return ($count -eq 2 -and $cancel -and $save)
}

function Get-A5ReplaceDecision($Names) {
  $yes = $false
  $no = $false
  $save = $false

  foreach ($name in @($Names)) {
    if (Test-A5Exact ([string]$name) 'Yes') {
      $yes = $true
    }

    if (Test-A5Exact ([string]$name) 'No') {
      $no = $true
    }

    if (Test-A5Exact ([string]$name) 'Save') {
      $save = $true
    }
  }

  if ($yes -and -not $no) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'REPLACE_PROMPT' })
  }

  if ($save) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'REPLACE_ABSENT' })
  }

  if ($yes -and $no) {
    return (Out-A5Obj @{ Ok = $true; Stop = '' })
  }

  return (Out-A5Obj @{ Ok = $false; Stop = 'REPLACE_ABSENT' })
}

function Select-A5SignIn($Items) {
  $indexes = @()

  for ($i = 0; $i -lt @($Items).Count; $i++) {
    $item = @($Items)[$i]

    if (-not (Test-A5Exact ([string]$item.Kind) 'Button')) {
      continue
    }

    if ((-not [bool]$item.Enabled) -or [bool]$item.Offscreen) {
      continue
    }

    if (-not (Test-A5Exact ([string]$item.Name) 'Sign in')) {
      continue
    }

    if (-not (Test-A5Exact ([string]$item.WindowTitle) 'Sign in to Hermes gateway')) {
      continue
    }

    $indexes += $i
  }

  if ($indexes.Count -eq 0) {
    return (Out-A5Obj @{ Stop = 'SIGNIN_CONTROL_ABSENT'; Index = -1 })
  }

  if ($indexes.Count -gt 1) {
    return (Out-A5Obj @{ Stop = 'SIGNIN_CONTROL_AMBIGUOUS'; Index = -1 })
  }

  return (Out-A5Obj @{ Stop = ''; Index = [int]$indexes[0] })
}

function Select-A5Edit($Items, [string]$Label, [string]$WindowTitle) {
  $indexes = @()

  for ($i = 0; $i -lt @($Items).Count; $i++) {
    $item = @($Items)[$i]

    if (-not (Test-A5Exact ([string]$item.Kind) 'Edit')) {
      continue
    }

    if ((-not [bool]$item.Enabled) -or [bool]$item.Offscreen) {
      continue
    }

    if (-not (Test-A5Exact ([string]$item.Name) $Label)) {
      continue
    }

    if ($WindowTitle -and -not (Test-A5Exact ([string]$item.WindowTitle) $WindowTitle)) {
      continue
    }

    $indexes += $i
  }

  if ($indexes.Count -eq 0) {
    return (Out-A5Obj @{ Stop = 'UIA_CONTROL_ABSENT'; Index = -1 })
  }

  if ($indexes.Count -gt 1) {
    return (Out-A5Obj @{ Stop = 'UIA_CONTROL_AMBIGUOUS'; Index = -1 })
  }

  return (Out-A5Obj @{ Stop = ''; Index = [int]$indexes[0] })
}

function Get-A5LoginError($Names) {
  foreach ($name in @($Names)) {
    if (Test-A5Exact ([string]$name) 'Invalid username or password.') {
      return 'invalid'
    }
  }

  foreach ($name in @($Names)) {
    if (Test-A5Exact ([string]$name) 'Too many attempts. Please wait and try again.') {
      return 'throttle'
    }
  }

  return 'none'
}

function Wait-A5Until([scriptblock]$Probe, [int]$Seconds, [string[]]$WaitableStops) {
  $deadline = (Get-Date).AddSeconds($Seconds)

  while ($true) {
    $found = & $Probe
    $stop = [string]$found.Decision.Stop

    if ([string]::IsNullOrEmpty($stop)) {
      return $found
    }

    $wait = $false

    foreach ($item in @($WaitableStops)) {
      if (Test-A5Exact $stop $item) {
        $wait = $true
      }
    }

    if (-not $wait -or ((Get-Date) -ge $deadline)) {
      return $found
    }

    Start-Sleep -Seconds 1
  }
}

function Initialize-A5Uia {
  if ($script:A5UiaReady) {
    return $true
  }

  try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Windows.Forms
  } catch {
    Stop-A5Uia 'UIA_ADDTYPE'
    return $false
  }

  if (-not ('A5UiaNative' -as [type])) {
    try {
      Add-Type -TypeDefinition $script:A5NativeSource
    } catch {
      if (-not ('A5UiaNative' -as [type])) {
        Stop-A5Uia 'UIA_ADDTYPE'
        return $false
      }
    }
  }

  $script:A5UiaReady = $true
  return $true
}

function Get-A5TopWindows([uint32]$ProcessId) {
  $cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
    [int]$ProcessId
  )

  return @([System.Windows.Automation.AutomationElement]::RootElement.FindAll(
      [System.Windows.Automation.TreeScope]::Children,
      $cond
    ))
}

function Find-A5Named($Root, [string]$Name, $ControlType) {
  $nameCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty,
    $Name
  )
  $typeCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    $ControlType
  )
  $both = New-Object System.Windows.Automation.AndCondition($nameCond, $typeCond)

  return @($Root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $both))
}

function Find-A5ByType($Root, $ControlType) {
  $typeCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    $ControlType
  )

  return @($Root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $typeCond))
}

function Get-A5SiblingNames($Element) {
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $parent = $walker.GetParent($Element)

  if (-not $parent) {
    return $null
  }

  $names = @()
  $child = $walker.GetFirstChild($parent)
  $guard = 0

  while ($child -and $guard -lt 80) {
    $names += [string]$child.Current.Name
    $child = $walker.GetNextSibling($child)
    $guard++
  }

  return $names
}

function Get-A5BoundHwnd($Element) {
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  $current = $Element
  $hwnd = [IntPtr]::Zero
  $guard = 0

  while ($current -and $guard -lt 30) {
    $raw = 0

    try {
      $raw = [int]$current.Current.NativeWindowHandle
    } catch {
      $raw = 0
    }

    if ($raw -ne 0) {
      $hwnd = ConvertTo-A5HwndPtr $raw
    }

    $parent = $null

    try {
      $parent = $walker.GetParent($current)
    } catch {
      $parent = $null
    }

    if (-not $parent) {
      break
    }

    $current = $parent
    $guard++
  }

  return $hwnd
}

function Test-A5ElementBound($Element, [uint32]$ProcessId) {
  try {
    if ([uint32]$Element.Current.ProcessId -ne $ProcessId) {
      return $false
    }
  } catch {
    return $false
  }

  $hwnd = Get-A5BoundHwnd $Element

  if ($hwnd -eq [IntPtr]::Zero) {
    return $false
  }

  $owner = [uint32]0
  [void][A5UiaNative]::GetWindowThreadProcessId($hwnd, [ref]$owner)

  return $owner -eq $ProcessId
}

function Test-A5Focused($Element, [uint32]$ProcessId) {
  $focused = $null

  try {
    $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
  } catch {
    return $false
  }

  if (-not $focused) {
    return $false
  }

  try {
    if ([uint32]$focused.Current.ProcessId -ne $ProcessId) {
      return $false
    }
  } catch {
    return $false
  }

  $left = @($Element.GetRuntimeId())
  $right = @($focused.GetRuntimeId())

  if ($left.Count -eq 0 -or $left.Count -ne $right.Count) {
    return $false
  }

  for ($i = 0; $i -lt $left.Count; $i++) {
    if ([int64]$left[$i] -ne [int64]$right[$i]) {
      return $false
    }
  }

  return $true
}

function Get-A5LiveGrant([uint32]$ProcessId) {
  $text = [string]$ProcessId

  if (-not (Test-A5PidText $text)) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'CUA_CANNOT_SEE_PROCESS_PATH' })
  }

  $listed = @()

  try {
    $listed = @(Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = ' + $text))
  } catch {
    return (Out-A5Obj @{ Ok = $false; Stop = 'CUA_CANNOT_SEE_PROCESS_PATH' })
  }

  if ($listed.Count -ne 1 -or $null -eq $listed[0]) {
    return (Out-A5Obj @{ Ok = $false; Stop = 'CUA_CANNOT_SEE_PROCESS_PATH' })
  }

  $row = $listed[0]
  $ownerUser = ''
  $ownerRead = $false

  try {
    $owner = Invoke-CimMethod -InputObject $row -MethodName GetOwner
    $ownerUser = [string]$owner.User
    $ownerRead = $true
  } catch {
    $ownerRead = $false
  }

  return Get-A5GrantDecision @{
    PidText = $text
    PrintedPid = [string]([uint32]$row.ProcessId)
    SessionId = [int]$row.SessionId
    Path = [string]$row.ExecutablePath
    CommandLine = [string]$row.CommandLine
    Owner = $ownerUser
    OwnerRead = $ownerRead
  }
}

function Publish-A5Grant([uint32]$ProcessId) {
  $grant = Get-A5LiveGrant $ProcessId

  if (-not $grant.Ok) {
    Stop-A5Uia ([string]$grant.Stop)
    return $false
  }

  Write-A5Field 'UIA_PATH_OK' '1'
  Write-A5Field 'UIA_PID' ([string]$ProcessId)
  return -not $script:A5Stopped
}

function Invoke-A5Pattern($Element, [uint32]$ProcessId) {
  if (-not (Test-A5ElementBound $Element $ProcessId)) {
    Stop-A5Uia 'UIA_PID_MISMATCH'
    return $false
  }

  try {
    $pattern = $Element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $pattern.Invoke()
  } catch {
    Stop-A5Uia 'UIA_INVOKE_UNAVAILABLE'
    return $false
  }

  return -not $script:A5Stopped
}

function Set-A5ElementText($Element, [uint32]$ProcessId, [string]$Text, [string]$DoneField) {
  if (-not (Test-A5ElementBound $Element $ProcessId)) {
    Stop-A5Uia 'UIA_PID_MISMATCH'
    return
  }

  $valuePattern = $null

  try {
    $valuePattern = $Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
  } catch {
    $valuePattern = $null
  }

  if ($valuePattern) {
    if ($valuePattern.Current.IsReadOnly) {
      Stop-A5Uia 'UIA_TYPE_FAILED'
      return
    }

    try {
      $valuePattern.SetValue($Text)
    } catch {
      Stop-A5Uia 'UIA_TYPE_FAILED'
      return
    }

    Write-A5Field $DoneField '1'
    return
  }

  try {
    $Element.SetFocus()
  } catch {
    Stop-A5Uia 'UIA_FOCUS_NOT_TARGET'
    return
  }

  if (-not (Test-A5Focused $Element $ProcessId)) {
    Stop-A5Uia 'UIA_FOCUS_NOT_TARGET'
    return
  }

  try {
    $encoded = ConvertTo-A5SendKeys $Text
    [System.Windows.Forms.SendKeys]::SendWait($encoded)
    $encoded = $null
  } catch {
    Stop-A5Uia 'UIA_TYPE_FAILED'
    return
  }

  Write-A5Field $DoneField '1'
}

function Get-A5EntryLive([uint32]$ProcessId) {
  $items = @()
  $elements = @()

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    $items += ,@{
      Kind = 'Window'
      Name = [string]$win.Current.Name
      Enabled = [bool]$win.Current.IsEnabled
      Offscreen = [bool]$win.Current.IsOffscreen
    }
    $elements += ,$win

    foreach ($btn in (Find-A5ByType $win ([System.Windows.Automation.ControlType]::Button))) {
      $items += ,@{
        Kind = 'Button'
        Name = [string]$btn.Current.Name
        Enabled = [bool]$btn.Current.IsEnabled
        Offscreen = [bool]$btn.Current.IsOffscreen
      }
      $elements += ,$btn
    }
  }

  $decision = Get-A5EntryDecision $items
  $element = $null

  if ([string]::IsNullOrEmpty([string]$decision.Stop) -and [int]$decision.Index -ge 0) {
    $element = $elements[[int]$decision.Index]
  }

  return (Out-A5Obj @{ Decision = $decision; Element = $element })
}

function Invoke-A5Entry([uint32]$ProcessId) {
  $sample = Wait-A5Until { Get-A5EntryLive $ProcessId } 20 @('SIGNIN_CONTROL_ABSENT')

  if ($sample.Decision.Stop) {
    Stop-A5Uia ([string]$sample.Decision.Stop)
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5EntryLive $ProcessId

  if ($again.Decision.Stop) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  if (-not (Test-A5Exact ([string]$again.Decision.Rung) ([string]$sample.Decision.Rung))) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  $rung = [string]$again.Decision.Rung
  Write-A5Field 'UIA_ENTRY_RUNG' $rung

  if ($rung -eq 'WINDOW') {
    $hwnd = Get-A5BoundHwnd $again.Element

    if ($hwnd -eq [IntPtr]::Zero) {
      Stop-A5Uia 'UIA_PID_MISMATCH'
      return
    }

    $owner = [uint32]0
    [void][A5UiaNative]::GetWindowThreadProcessId($hwnd, [ref]$owner)

    if ($owner -ne $ProcessId) {
      Stop-A5Uia 'UIA_PID_MISMATCH'
      return
    }

    [void][A5UiaNative]::SetForegroundWindow($hwnd)
    $foreground = [A5UiaNative]::GetForegroundWindow()

    if ($foreground.ToInt64() -ne $hwnd.ToInt64()) {
      Stop-A5Uia 'UIA_FOREGROUND_FAILED'
      return
    }

    Write-A5Field 'ENTRY_INVOKED' '1'
    return
  }

  if (Invoke-A5Pattern $again.Element $ProcessId) {
    Write-A5Field 'ENTRY_INVOKED' '1'
  }
}

function Get-A5EditLive([uint32]$ProcessId, [string]$Label, [string]$WindowTitle) {
  $items = @()
  $elements = @()

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    $title = [string]$win.Current.Name

    foreach ($edit in (Find-A5ByType $win ([System.Windows.Automation.ControlType]::Edit))) {
      $items += ,@{
        Kind = 'Edit'
        Name = [string]$edit.Current.Name
        WindowTitle = $title
        Enabled = [bool]$edit.Current.IsEnabled
        Offscreen = [bool]$edit.Current.IsOffscreen
      }
      $elements += ,$edit
    }
  }

  $decision = Select-A5Edit $items $Label $WindowTitle
  $element = $null

  if ([string]::IsNullOrEmpty([string]$decision.Stop) -and [int]$decision.Index -ge 0) {
    $element = $elements[[int]$decision.Index]
  }

  return (Out-A5Obj @{ Decision = $decision; Element = $element })
}

function Invoke-A5FocusEdit([uint32]$ProcessId, [string]$Label) {
  $sample = Wait-A5Until { Get-A5EditLive $ProcessId $Label 'Sign in to Hermes gateway' } 20 @('UIA_CONTROL_ABSENT')

  if ($sample.Decision.Stop) {
    Stop-A5Uia ([string]$sample.Decision.Stop)
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5EditLive $ProcessId $Label 'Sign in to Hermes gateway'

  if ($again.Decision.Stop) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  if (-not (Test-A5ElementBound $again.Element $ProcessId)) {
    Stop-A5Uia 'UIA_PID_MISMATCH'
    return
  }

  try {
    $again.Element.SetFocus()
  } catch {
    Stop-A5Uia 'UIA_FOCUS_NOT_TARGET'
    return
  }

  if (-not (Test-A5Focused $again.Element $ProcessId)) {
    Stop-A5Uia 'UIA_FOCUS_NOT_TARGET'
    return
  }

  Write-A5Field 'FOCUS_OK' '1'
}

function Read-A5SecretFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return ''
  }

  $raw = [System.IO.File]::ReadAllText($Path)
  return Get-A5SecretText $raw
}

function Invoke-A5TypeEdit([uint32]$ProcessId, [string]$Label, [string]$FilePath, [string]$MissingStop, [string]$DoneField) {
  $sample = Wait-A5Until { Get-A5EditLive $ProcessId $Label 'Sign in to Hermes gateway' } 20 @('UIA_CONTROL_ABSENT')

  if ($sample.Decision.Stop) {
    Stop-A5Uia ([string]$sample.Decision.Stop)
    return
  }

  $secret = ''

  try {
    $secret = Read-A5SecretFile $FilePath
  } catch {
    $secret = ''
  }

  if ([string]::IsNullOrEmpty($secret)) {
    Stop-A5Uia $MissingStop
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5EditLive $ProcessId $Label 'Sign in to Hermes gateway'

  if ($again.Decision.Stop) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  Set-A5ElementText $again.Element $ProcessId $secret $DoneField
  $secret = $null
}

function Get-A5SignInLive([uint32]$ProcessId) {
  $items = @()
  $elements = @()

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    $title = [string]$win.Current.Name

    foreach ($btn in (Find-A5ByType $win ([System.Windows.Automation.ControlType]::Button))) {
      $items += ,@{
        Kind = 'Button'
        Name = [string]$btn.Current.Name
        WindowTitle = $title
        Enabled = [bool]$btn.Current.IsEnabled
        Offscreen = [bool]$btn.Current.IsOffscreen
      }
      $elements += ,$btn
    }
  }

  $decision = Select-A5SignIn $items
  $element = $null

  if ([string]::IsNullOrEmpty([string]$decision.Stop) -and [int]$decision.Index -ge 0) {
    $element = $elements[[int]$decision.Index]
  }

  return (Out-A5Obj @{ Decision = $decision; Element = $element })
}

function Invoke-A5ClickSignIn([uint32]$ProcessId) {
  $sample = Wait-A5Until { Get-A5SignInLive $ProcessId } 20 @('SIGNIN_CONTROL_ABSENT')

  if ($sample.Decision.Stop) {
    Stop-A5Uia ([string]$sample.Decision.Stop)
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5SignInLive $ProcessId

  if ($again.Decision.Stop) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  if (Invoke-A5Pattern $again.Element $ProcessId) {
    Write-A5Field 'SIGNIN_INVOKED' '1'
  }
}

function Get-A5Names([uint32]$ProcessId) {
  $names = @()

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    $names += [string]$win.Current.Name
    $cond = New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
      [int]$ProcessId
    )
    foreach ($node in @($win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond))) {
      $name = [string]$node.Current.Name

      if (-not [string]::IsNullOrEmpty($name)) {
        $names += $name
      }
    }
  }

  return $names
}

function Invoke-A5ReadLogin([uint32]$ProcessId) {
  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $login = $false
  $username = $false

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    if (Test-A5Exact ([string]$win.Current.Name) 'Sign in to Hermes gateway') {
      $login = $true
    }

    $edits = Find-A5Named $win 'Username' ([System.Windows.Automation.ControlType]::Edit)

    if ($edits.Count -ge 1) {
      $username = $true
    }
  }

  $errorCode = Get-A5LoginError (Get-A5Names $ProcessId)
  Write-A5Field 'LOGIN_WINDOW' $(if ($login) { '1' } else { '0' })
  Write-A5Field 'USERNAME_VISIBLE' $(if ($username) { '1' } else { '0' })
  Write-A5Field 'LOGIN_ERROR' $errorCode

  if ($errorCode -eq 'invalid' -or $errorCode -eq 'throttle') {
    Stop-A5Uia 'LOGIN_REJECTED'
  }
}

function Get-A5FilesLive([uint32]$ProcessId) {
  $items = @()
  $elements = @()

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    foreach ($typeName in @('Button', 'TabItem', 'SplitButton')) {
      $type = [System.Windows.Automation.ControlType]::$typeName

      foreach ($control in (Find-A5Named $win 'Files' $type)) {
        $siblings = Get-A5SiblingNames $control

        if ($null -eq $siblings) {
          continue
        }

        $items += ,@{
          Kind = $typeName
          Name = [string]$control.Current.Name
          Siblings = $siblings
          Enabled = [bool]$control.Current.IsEnabled
          Offscreen = [bool]$control.Current.IsOffscreen
        }
        $elements += ,$control
      }
    }
  }

  $decision = Select-A5Files $items
  $element = $null

  if ([string]::IsNullOrEmpty([string]$decision.Stop) -and [int]$decision.Index -ge 0) {
    $element = $elements[[int]$decision.Index]
  }

  return (Out-A5Obj @{ Decision = $decision; Element = $element })
}

function Invoke-A5ClickFiles([uint32]$ProcessId) {
  $sample = Wait-A5Until { Get-A5FilesLive $ProcessId } 20 @('FILES_CONTROL_ABSENT')

  if ($sample.Decision.Stop) {
    Stop-A5Uia ([string]$sample.Decision.Stop)
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5FilesLive $ProcessId

  if ($again.Decision.Stop) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  if (Invoke-A5Pattern $again.Element $ProcessId) {
    Write-A5Field 'FILES_INVOKED' '1'
  }
}

function Test-A5FolderElement($Element) {
  try {
    $pattern = $Element.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
  } catch {
    return $false
  }

  if (-not $pattern) {
    return $false
  }

  $state = [string]$pattern.Current.ExpandCollapseState
  return -not (Test-A5Exact $state 'Leaf')
}

function Get-A5RowsLive([uint32]$ProcessId) {
  $items = @()
  $elements = @()

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    foreach ($typeName in @('TreeItem', 'ListItem', 'DataItem')) {
      $type = [System.Windows.Automation.ControlType]::$typeName

      foreach ($row in (Find-A5ByType $win $type)) {
        $items += ,@{
          Kind = $typeName
          Name = [string]$row.Current.Name
          Enabled = [bool]$row.Current.IsEnabled
          Offscreen = [bool]$row.Current.IsOffscreen
          Folder = [bool](Test-A5FolderElement $row)
        }
        $elements += ,$row
      }
    }
  }

  $decision = Select-A5FileRows $items
  $element = $null

  if ([string]::IsNullOrEmpty([string]$decision.Stop) -and [int]$decision.Index -ge 0) {
    $element = $elements[[int]$decision.Index]
  }

  return (Out-A5Obj @{ Decision = $decision; Element = $element })
}

function Find-A5DownloadItems([uint32]$ProcessId) {
  $hits = @()

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    foreach ($item in (Find-A5Named $win 'Download' ([System.Windows.Automation.ControlType]::MenuItem))) {
      if ([bool]$item.Current.IsEnabled -and -not [bool]$item.Current.IsOffscreen) {
        $hits += ,$item
      }
    }
  }

  return $hits
}

function Invoke-A5ClickDownload([uint32]$ProcessId) {
  $sample = Wait-A5Until { Get-A5RowsLive $ProcessId } 20 @('FILES_ROW_ABSENT')

  if ($sample.Decision.Stop) {
    Write-A5Field 'FILE_ROW_COUNT' ([string][int]$sample.Decision.Count)
    Stop-A5Uia 'FILES_ROW_AMBIGUOUS'
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5RowsLive $ProcessId

  if ($again.Decision.Stop) {
    Write-A5Field 'FILE_ROW_COUNT' ([string][int]$again.Decision.Count)
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  $beforeName = ''
  $afterName = ''

  try {
    $beforeName = [string]$sample.Element.Current.Name
    $afterName = [string]$again.Element.Current.Name
  } catch {
    $beforeName = ''
    $afterName = ''
  }

  if ([string]::IsNullOrEmpty($beforeName) -or -not (Test-A5Exact $beforeName $afterName)) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  Write-A5Field 'FILE_ROW_COUNT' '1'

  if (-not (Test-A5ElementBound $again.Element $ProcessId)) {
    Stop-A5Uia 'UIA_PID_MISMATCH'
    return
  }

  try {
    $again.Element.SetFocus()
  } catch {
    Stop-A5Uia 'UIA_FOCUS_NOT_TARGET'
    return
  }

  if (-not (Test-A5Focused $again.Element $ProcessId)) {
    Stop-A5Uia 'UIA_FOCUS_NOT_TARGET'
    return
  }

  try {
    [System.Windows.Forms.SendKeys]::SendWait('+{F10}')
  } catch {
    Stop-A5Uia 'DOWNLOAD_CONTROL_ABSENT'
    return
  }

  $menu = @()
  $deadline = (Get-Date).AddSeconds(5)

  do {
    $menu = @(Find-A5DownloadItems $ProcessId)

    if ($menu.Count -ge 1) {
      break
    }

    if ((Get-Date) -ge $deadline) {
      break
    }

    Start-Sleep -Milliseconds 250
  } while ($true)

  if ($menu.Count -eq 0) {
    Stop-A5Uia 'DOWNLOAD_CONTROL_ABSENT'
    return
  }

  if ($menu.Count -ne 1) {
    Stop-A5Uia 'DOWNLOAD_CONTROL_AMBIGUOUS'
    return
  }

  if (Invoke-A5Pattern $menu[0] $ProcessId) {
    Write-A5Field 'DOWNLOAD_INVOKED' '1'
  }
}

function Get-A5SaveWindows([uint32]$ProcessId) {
  $hits = New-Object System.Collections.Generic.List[object]

  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    if (Test-A5Exact ([string]$win.Current.Name) 'Save File') {
      [void]$hits.Add($win)
    }
  }

  $first = $null

  if ($hits.Count -ge 1) {
    $first = $hits[0]
  }

  return (Out-A5Obj @{ Count = $hits.Count; First = $first })
}

function Get-A5ButtonNames($Window) {
  $names = @()

  foreach ($btn in (Find-A5ByType $Window ([System.Windows.Automation.ControlType]::Button))) {
    $name = [string]$btn.Current.Name

    if (-not [string]::IsNullOrEmpty($name)) {
      $names += $name
    }
  }

  return $names
}

function Get-A5SaveLive([uint32]$ProcessId) {
  $wins = Get-A5SaveWindows $ProcessId

  if ([int]$wins.Count -eq 0) {
    return (Out-A5Obj @{ Decision = @{ Stop = 'SAVE_DIALOG_ABSENT' }; Window = $null; Names = @() })
  }

  if ([int]$wins.Count -ne 1) {
    return (Out-A5Obj @{ Decision = @{ Stop = 'DIALOG_LOCALE_UNEXPECTED' }; Window = $null; Names = @() })
  }

  $win = $wins.First
  $names = @(Get-A5ButtonNames $win)

  if (-not (Test-A5SaveButtons $names)) {
    return (Out-A5Obj @{ Decision = @{ Stop = 'DIALOG_LOCALE_UNEXPECTED' }; Window = $win; Names = $names })
  }

  return (Out-A5Obj @{ Decision = @{ Stop = '' }; Window = $win; Names = $names })
}

function Invoke-A5ReadSave([uint32]$ProcessId) {
  $sample = Wait-A5Until { Get-A5SaveLive $ProcessId } 20 @('SAVE_DIALOG_ABSENT')

  if ($sample.Decision.Stop) {
    Stop-A5Uia ([string]$sample.Decision.Stop)
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5SaveLive $ProcessId

  if ($again.Decision.Stop) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  Write-A5Field 'DIALOG_PRESENT' '1'
  Write-A5Field 'DIALOG_BUTTONS' 'Cancel,Save'
}

function Get-A5DialogButton($Window, [string]$Name) {
  $hits = @(Find-A5Named $Window $Name ([System.Windows.Automation.ControlType]::Button))
  $visible = @()

  foreach ($hit in $hits) {
    if ([bool]$hit.Current.IsEnabled -and -not [bool]$hit.Current.IsOffscreen) {
      $visible += ,$hit
    }
  }

  return $visible
}

function Invoke-A5DialogButton([uint32]$ProcessId, [string]$Name, [string]$DoneField) {
  $sample = Wait-A5Until { Get-A5SaveLive $ProcessId } 20 @('SAVE_DIALOG_ABSENT')

  if ($sample.Decision.Stop) {
    Stop-A5Uia ([string]$sample.Decision.Stop)
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5SaveLive $ProcessId

  if ($again.Decision.Stop) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  $buttons = @(Get-A5DialogButton $again.Window $Name)

  if ($buttons.Count -ne 1) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  if (Invoke-A5Pattern $buttons[0] $ProcessId) {
    Write-A5Field $DoneField '1'
  }
}

function Get-A5FileNameEdit($Window) {
  $edits = @(Find-A5Named $Window 'File name:' ([System.Windows.Automation.ControlType]::Edit))
  $combos = @(Find-A5Named $Window 'File name:' ([System.Windows.Automation.ControlType]::ComboBox))
  return @($edits + $combos)
}

function Invoke-A5TypeDest([uint32]$ProcessId) {
  $sample = Wait-A5Until { Get-A5SaveLive $ProcessId } 20 @('SAVE_DIALOG_ABSENT')

  if ($sample.Decision.Stop) {
    Stop-A5Uia ([string]$sample.Decision.Stop)
    return
  }

  $edits = @(Get-A5FileNameEdit $sample.Window)

  if ($edits.Count -ne 1) {
    Stop-A5Uia 'DIALOG_LOCALE_UNEXPECTED'
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5SaveLive $ProcessId

  if ($again.Decision.Stop) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  $againEdits = @(Get-A5FileNameEdit $again.Window)

  if ($againEdits.Count -ne 1) {
    Stop-A5Uia 'UIA_TARGET_CHANGED'
    return
  }

  Set-A5ElementText $againEdits[0] $ProcessId $script:DestFile 'DEST_SET'
}

function Get-A5ReplaceLive([uint32]$ProcessId) {
  foreach ($win in (Get-A5TopWindows $ProcessId)) {
    $names = @(Get-A5ButtonNames $win)
    $decision = Get-A5ReplaceDecision $names

    if ($decision.Ok) {
      return (Out-A5Obj @{ Decision = $decision; Window = $win })
    }

    if ($decision.Stop -eq 'REPLACE_PROMPT') {
      return (Out-A5Obj @{ Decision = $decision; Window = $win })
    }
  }

  return (Out-A5Obj @{ Decision = @{ Ok = $false; Stop = 'REPLACE_ABSENT' }; Window = $null })
}

function Invoke-A5ReadReplace([uint32]$ProcessId) {
  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $sample = Get-A5ReplaceLive $ProcessId

  if ($sample.Decision.Stop -eq 'REPLACE_PROMPT') {
    Stop-A5Uia 'REPLACE_PROMPT'
    return
  }

  if ($sample.Decision.Ok) {
    Write-A5Field 'REPLACE_WINDOW' '1'
    return
  }

  Write-A5Field 'REPLACE_WINDOW' '0'
}

function Invoke-A5ClickReplaceNo([uint32]$ProcessId) {
  $sample = Get-A5ReplaceLive $ProcessId

  if (-not $sample.Decision.Ok) {
    Stop-A5Uia 'REPLACE_PROMPT'
    return
  }

  if (-not (Publish-A5Grant $ProcessId)) {
    return
  }

  $again = Get-A5ReplaceLive $ProcessId

  if (-not $again.Decision.Ok) {
    Stop-A5Uia 'REPLACE_PROMPT'
    return
  }

  $buttons = @(Get-A5DialogButton $again.Window 'No')

  if ($buttons.Count -ne 1) {
    Stop-A5Uia 'REPLACE_PROMPT'
    return
  }

  if (Invoke-A5Pattern $buttons[0] $ProcessId) {
    Write-A5Field 'REPLACE_NO_INVOKED' '1'
  }
}

function Get-A5DiscoverRow([object]$Row) {
  $path = [string]$Row.ExecutablePath
  $cmd = [string]$Row.CommandLine
  $role = 'main'

  if ($cmd -like '*--type=*') {
    $role = 'child'
  }

  $ownerUser = ''
  $ownerRead = $false

  try {
    $owner = Invoke-CimMethod -InputObject $Row -MethodName GetOwner
    $ownerUser = [string]$owner.User
    $ownerRead = $true
  } catch {
    $ownerRead = $false
  }

  $decision = Get-A5GrantDecision @{
    PidText = [string]([uint32]$Row.ProcessId)
    PrintedPid = [string]([uint32]$Row.ProcessId)
    SessionId = [int]$Row.SessionId
    Path = $path
    CommandLine = $cmd
    Owner = $ownerUser
    OwnerRead = $ownerRead
  }

  return (Out-A5Obj @{ Ok = [bool]$decision.Ok; Stop = [string]$decision.Stop; Role = $role; Pid = [uint32]$Row.ProcessId })
}

function Select-A5DiscoverEntry($Hits) {
  $chosen = New-Object System.Collections.Generic.List[object]

  foreach ($hit in @($Hits)) {
    $rung = [string]$hit.Rung

    if ((Test-A5Exact $rung 'WINDOW') -or (Test-A5Exact $rung 'REMOTE') -or (Test-A5Exact $rung 'SIGNOUT')) {
      [void]$chosen.Add($hit)
    }
  }

  if ($chosen.Count -eq 0) {
    return (Out-A5Obj @{ Stop = 'SIGNIN_CONTROL_ABSENT'; Pid = ''; Rung = '' })
  }

  $mains = New-Object System.Collections.Generic.List[object]

  foreach ($hit in $chosen) {
    if (Test-A5Exact ([string]$hit.Role) 'main') {
      [void]$mains.Add($hit)
    }
  }

  if ($mains.Count -ge 1) {
    $chosen = $mains
  }

  if ($chosen.Count -ne 1) {
    return (Out-A5Obj @{ Stop = 'SIGNIN_CONTROL_AMBIGUOUS'; Pid = ''; Rung = '' })
  }

  return (Out-A5Obj @{ Stop = ''; Pid = [string]$chosen[0].Pid; Rung = [string]$chosen[0].Rung })
}

function Invoke-A5Discover {
  if (-not (Initialize-A5Uia)) {
    return
  }

  Write-A5Field 'DISCOVER_NOT_A_GRANT' '1'
  $listed = @()

  try {
    $listed = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'Hermes.exe'")
  } catch {
    Stop-A5Uia 'CUA_CANNOT_SEE_PROCESS_PATH'
    return
  }
  $daily = $false
  $granted = @()

  foreach ($row in $listed) {
    $path = [string]$row.ExecutablePath

    if (Test-A5DailyPath $path) {
      $daily = $true
      continue
    }

    if (-not (Test-A5Exact $path $script:UatExe)) {
      continue
    }

    $info = Get-A5DiscoverRow $row

    if ($info.Ok) {
      $granted += ,$info
    }
  }

  Write-A5Field 'DISCOVER_DAILY_SEEN' $(if ($daily) { '1' } else { '0' })

  if ($granted.Count -eq 0) {
    Stop-A5Uia 'CUA_CANNOT_SEE_PROCESS_PATH'
    return
  }

  $entryHits = @()

  foreach ($info in $granted) {
    $role = [string]$info.Role
    Write-A5Field 'DISCOVER_UAT_PID' ([string]$info.Pid + ',' + $role)
    $live = Get-A5EntryLive ([uint32]$info.Pid)
    $rung = [string]$live.Decision.Rung

    if ($live.Decision.Stop -eq 'SIGNIN_CONTROL_AMBIGUOUS') {
      Stop-A5Uia 'SIGNIN_CONTROL_AMBIGUOUS'
      return
    }

    if ($rung -eq 'WINDOW' -or $rung -eq 'REMOTE' -or $rung -eq 'SIGNOUT') {
      $entryHits += ,@{ Pid = [uint32]$info.Pid; Rung = $rung; Role = [string]$info.Role }
    }

    foreach ($win in (Get-A5TopWindows ([uint32]$info.Pid))) {
      $hwndText = '0'

      try {
        $hwndText = ConvertTo-A5Hwnd ([int]$win.Current.NativeWindowHandle)
      } catch {
        $hwndText = '0'
      }

      $title = ConvertTo-A5Title ([string]$win.Current.Name)
      Write-A5Field 'DISCOVER_WINDOW' ('pid=' + [string]$info.Pid + ' hwnd=' + $hwndText + ' title=' + $title)
    }

    $rows = Get-A5RowsLive ([uint32]$info.Pid)
    Write-A5Field 'DISCOVER_ROW_COUNT' ([string]$info.Pid + ',' + [string][int]$rows.Decision.Count)
  }

  if ($script:A5Stopped) {
    return
  }

  $picked = Select-A5DiscoverEntry $entryHits

  if ($picked.Stop) {
    Stop-A5Uia ([string]$picked.Stop)
    return
  }

  Write-A5Field 'DISCOVER_ENTRY_PID' ([string]$picked.Pid)
  Write-A5Field 'DISCOVER_ENTRY_RUNG' ([string]$picked.Rung)
}

function Invoke-A5Action([string]$Name, [uint32]$ProcessId) {
  if (-not (Initialize-A5Uia)) {
    return
  }

  switch ($Name) {
    'Entry' { Invoke-A5Entry $ProcessId }
    'FocusUsername' { Invoke-A5FocusEdit $ProcessId 'Username' }
    'TypeUsername' { Invoke-A5TypeEdit $ProcessId 'Username' $script:UsernameFile 'USERNAME_UNAVAILABLE' 'TYPED_USERNAME' }
    'FocusPassword' { Invoke-A5FocusEdit $ProcessId 'Password' }
    'TypePassword' { Invoke-A5TypeEdit $ProcessId 'Password' $script:PasswordFile 'PASSWORD_UNAVAILABLE' 'TYPED_PASSWORD' }
    'ClickSignIn' { Invoke-A5ClickSignIn $ProcessId }
    'ReadLogin' { Invoke-A5ReadLogin $ProcessId }
    'ClickFiles' { Invoke-A5ClickFiles $ProcessId }
    'ClickDownload' { Invoke-A5ClickDownload $ProcessId }
    'ReadSaveDialog' { Invoke-A5ReadSave $ProcessId }
    'ClickCancel' { Invoke-A5DialogButton $ProcessId 'Cancel' 'CANCEL_INVOKED' }
    'TypeDest' { Invoke-A5TypeDest $ProcessId }
    'ClickSave' { Invoke-A5DialogButton $ProcessId 'Save' 'SAVE_INVOKED' }
    'ReadReplace' { Invoke-A5ReadReplace $ProcessId }
    'ClickReplaceNo' { Invoke-A5ClickReplaceNo $ProcessId }
    default { Stop-A5Uia 'RUNBOOK_DRIFT' }
  }
}

function Invoke-A5Session1Main {
  $script:A5Stopped = $false
  Write-A5Field 'UIA_HELPER' '1'
  Write-A5Field 'UIA_PSEXEC_IS_NOT_A_PID' '1'

  if (-not (Test-A5Nonce $Nonce)) {
    Stop-A5Uia 'RUNBOOK_DRIFT'
    return 2
  }

  Write-A5Field 'UIA_NONCE' $Nonce

  if (-not (Test-A5ActionName $Action)) {
    Stop-A5Uia 'RUNBOOK_DRIFT'
    return 2
  }

  Write-A5Field 'UIA_ACTION' $Action
  $hostStop = Get-A5HostDecision ([int]$PSVersionTable.PSVersion.Major) ([string]$PSHOME) ([string]$env:USERNAME) ([int](Get-Process -Id $PID).SessionId)

  if ($hostStop) {
    Stop-A5Uia $hostStop
    return 2
  }

  Write-A5Field 'UIA_HOST_SESSION' '1'
  Write-A5Field 'UIA_HOST_USER' 'ddewit'

  if (-not (Initialize-A5Uia)) {
    return 2
  }

  $desktop = ''

  try {
    $desktop = [string][A5UiaNative]::Desktop()
  } catch {
    $desktop = ''
  }

  if (-not (Test-A5DesktopName $desktop)) {
    Stop-A5Uia 'UIA_DESKTOP'
    return 2
  }

  Write-A5Field 'UIA_DESKTOP' 'Default'

  if ($Action -eq 'Discover') {
    Invoke-A5Discover
  } else {
    if (-not (Test-A5PidText $AttestedPid)) {
      Stop-A5Uia 'RUNBOOK_DRIFT'
      return 2
    }

    Invoke-A5Action $Action ([uint32]$AttestedPid)
  }

  if ($script:A5Stopped) {
    return 2
  }

  Write-A5Field 'UIA_DONE' '1'
  return 0
}

if ($MyInvocation.InvocationName -ne '.') {
  $code = 2

  try {
    $code = Invoke-A5Session1Main
  } catch {
    if (-not $script:A5Stopped) {
      Stop-A5Uia 'UIA_FAULT'
    }

    $code = 2
  }

  exit $code
}
