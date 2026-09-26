# Decision tests for the session-1 UIA helper. Not a Beelink phase.
# Do not copy this file to the UAT machine. The only process spawn is a
# rejected nonce, which returns before UI Automation.
$ErrorActionPreference = 'Stop'

$helper = Join-Path $PSScriptRoot 'NATIVE_A5_BEELINK_SESSION1_UIA.ps1'
. $helper

function Assert-Equal([string]$Name, $Actual, $Expected) {
  if ($Actual -ne $Expected) {
    throw ($Name + ' expected [' + $Expected + '] actual [' + $Actual + ']')
  }
}

function Assert-True([string]$Name, $Value) {
  if (-not $Value) {
    throw ($Name + ' was not true')
  }
}

function Assert-False([string]$Name, $Value) {
  if ($Value) {
    throw ($Name + ' was true')
  }
}

function New-A5Item([string]$Kind, [string]$Name, [bool]$Enabled = $true, [bool]$Offscreen = $false, $Siblings = @(), [string]$WindowTitle = '', [bool]$Folder = $false) {
  return @{
    Kind = $Kind
    Name = $Name
    Enabled = $Enabled
    Offscreen = $Offscreen
    Siblings = $Siblings
    WindowTitle = $WindowTitle
    Folder = $Folder
  }
}

function New-A5Cmd([string]$Extra) {
  return ('--user-data-dir=' + $script:UserData + ' --no-sandbox' + $Extra)
}

function New-A5Grant([string]$Path, [string]$Cmd, [string]$Owner = 'ddewit', [bool]$OwnerRead = $true, [int]$Session = 1, [string]$Printed = '42312', [string]$Wanted = '42312') {
  return Get-A5GrantDecision @{
    PidText = $Wanted
    PrintedPid = $Printed
    SessionId = $Session
    Path = $Path
    CommandLine = $Cmd
    Owner = $Owner
    OwnerRead = $OwnerRead
  }
}

Assert-True 'uat path is not daily' (-not (Test-A5DailyPath $script:UatExe))
Assert-True 'daily exe is daily' (Test-A5DailyPath $script:DailyExe)
Assert-True 'daily root is daily' (Test-A5DailyPath $script:DailyRoot)
Assert-True 'file under daily root is daily' (Test-A5DailyPath ($script:DailyRoot + '\side\Hermes.exe'))
Assert-False 'daily root without slash boundary' (Test-A5DailyPath ($script:DailyRoot + '-extra\Hermes.exe'))
Assert-False 'daily root trailing slash is not the root' (Test-A5DailyPath ($script:DailyRoot + '\'))
Assert-True 'device prefix daily exe' (Test-A5DailyPath ('\\?\' + $script:DailyExe))
Assert-True 'nt prefix daily exe' (Test-A5DailyPath ('\??\' + $script:DailyExe))
Assert-True 'slash flip daily exe' (Test-A5DailyPath ($script:DailyExe.Replace('\', '/')))
Assert-True 'daily exe case fold' (Test-A5DailyPath $script:DailyExe.ToUpperInvariant())
Assert-False 'device path is not daily' (Test-A5DailyPath '\Device\HarddiskVolume2\Hermes.exe')
Assert-False 'blank path is not daily' (Test-A5DailyPath '')
Assert-False 'uat case fold is not daily' (Test-A5DailyPath $script:UatExe.Replace('Hermes.exe', 'hermes.exe'))

$goodCmd = New-A5Cmd ''
$good = New-A5Grant $script:UatExe $goodCmd
Assert-True 'clean grant' $good.Ok
Assert-Equal 'clean grant stop' $good.Stop ''

$pins = Get-A5CmdPins $goodCmd
Assert-True 'ns3 udd pin' $pins.Udd
Assert-True 'sandbox pin' $pins.Sandbox
Assert-False 'ns3 is not a frozen substring' $pins.Frozen

Assert-Equal 'printed pid wins first' (New-A5Grant $script:DailyExe $goodCmd -Printed '1' -Wanted '2').Stop 'ATTEST_PID_MISMATCH'
Assert-Equal 'owner read failure' (New-A5Grant $script:UatExe $goodCmd -Owner '' -OwnerRead $false).Stop 'CUA_CANNOT_SEE_PROCESS_PATH'
Assert-Equal 'empty owner' (New-A5Grant $script:UatExe $goodCmd -Owner '' -OwnerRead $true).Stop 'CUA_CANNOT_SEE_PROCESS_PATH'
Assert-Equal 'wrong owner before daily' (New-A5Grant $script:DailyExe $goodCmd -Owner 'SYSTEM').Stop 'ATTEST_WRONG_OWNER'
Assert-True 'owner case' (New-A5Grant $script:UatExe $goodCmd -Owner 'DDEWIT').Ok
Assert-Equal 'blank path' (New-A5Grant '' $goodCmd).Stop 'CUA_CANNOT_SEE_PROCESS_PATH'
Assert-Equal 'daily before session' (New-A5Grant $script:DailyExe $goodCmd -Session 0).Stop 'FOCUS_IS_DAILY'
Assert-Equal 'daily on session 1' (New-A5Grant ($script:DailyRoot + '\other.exe') $goodCmd).Stop 'FOCUS_IS_DAILY'
Assert-Equal 'device path wrong session' (New-A5Grant '\Device\HarddiskVolume2\Hermes.exe' $goodCmd -Session 0).Stop 'ATTEST_WRONG_SESSION'
Assert-Equal 'device path is mismatch' (New-A5Grant '\Device\HarddiskVolume2\Hermes.exe' $goodCmd).Stop 'ATTEST_PATH_MISMATCH'
Assert-Equal 'uat case is mismatch' (New-A5Grant $script:UatExe.Replace('Hermes.exe', 'hermes.exe') $goodCmd).Stop 'ATTEST_PATH_MISMATCH'
Assert-Equal 'prefixed uat is mismatch' (New-A5Grant ('\\?\' + $script:UatExe) $goodCmd).Stop 'ATTEST_PATH_MISMATCH'
Assert-Equal 'wrong session after path class' (New-A5Grant $script:UatExe $goodCmd -Session 2).Stop 'ATTEST_WRONG_SESSION'
Assert-Equal 'blank cmdline' (New-A5Grant $script:UatExe '').Stop 'ATTEST_CMDLINE_REJECTED'
Assert-Equal 'frozen ns' (New-A5Grant $script:UatExe (New-A5Cmd ' hermes-uat-a5-ns-20260926')).Stop 'ATTEST_CMDLINE_REJECTED'
Assert-Equal 'frozen ns2' (New-A5Grant $script:UatExe (New-A5Cmd ' hermes-uat-a5-ns2-20260926')).Stop 'ATTEST_CMDLINE_REJECTED'
Assert-Equal 'frozen live' (New-A5Grant $script:UatExe (New-A5Cmd ' hermes-uat-a5-live-20260926')).Stop 'ATTEST_CMDLINE_REJECTED'
Assert-Equal 'missing sandbox' (New-A5Grant $script:UatExe ('--user-data-dir=' + $script:UserData)).Stop 'ATTEST_CMDLINE_REJECTED'
Assert-Equal 'unbounded sandbox' (New-A5Grant $script:UatExe ('--user-data-dir=' + $script:UserData + ' --no-sandboxed')).Stop 'ATTEST_CMDLINE_REJECTED'
Assert-True 'sandbox equals form' (New-A5Grant $script:UatExe ('--user-data-dir=' + $script:UserData + ' --no-sandbox=1')).Ok
Assert-True 'quoted udd' (New-A5Grant $script:UatExe ('--user-data-dir="' + $script:UserData + '" --no-sandbox')).Ok

Assert-Equal 'host ok' (Get-A5HostDecision 5 'C:\Windows\System32\WindowsPowerShell\v1.0' 'ddewit' 1) ''
Assert-Equal 'host user case' (Get-A5HostDecision 5 'C:\Windows\System32\WindowsPowerShell\v1.0' 'DDEWIT' 1) ''
Assert-Equal 'host major before wow64' (Get-A5HostDecision 7 'C:\Windows\SysWOW64\WindowsPowerShell\v1.0' 'ddewit' 1) 'ATTEST_POWERSHELL'
Assert-Equal 'host wow64' (Get-A5HostDecision 5 'C:\Windows\SysWOW64\WindowsPowerShell\v1.0' 'ddewit' 1) 'ATTEST_WOW64'
Assert-Equal 'host user' (Get-A5HostDecision 5 'C:\Windows\System32\WindowsPowerShell\v1.0' 'SYSTEM' 1) 'ATTEST_WRONG_OWNER'
Assert-Equal 'host session' (Get-A5HostDecision 5 'C:\Windows\System32\WindowsPowerShell\v1.0' 'ddewit' 0) 'UIA_NOT_SESSION1'
Assert-True 'desktop default' (Test-A5DesktopName 'Default')
Assert-True 'desktop case' (Test-A5DesktopName 'default')
Assert-False 'desktop other' (Test-A5DesktopName 'Winlogon')
Assert-False 'desktop blank' (Test-A5DesktopName '')
Assert-False 'desktop padded' (Test-A5DesktopName 'Default ')

$window = New-A5Item 'Window' 'Sign in to Hermes gateway'
$remote = New-A5Item 'Button' 'Sign in to remote gateway'
$signout = New-A5Item 'Button' 'Sign out & sign in'
$heading = New-A5Item 'Text' 'Remote gateway sign-in required'
$hint = New-A5Item 'Text' 'Sign in to remote gateway'
$andWord = New-A5Item 'Button' 'Sign out and sign in'
Assert-Equal 'window beats buttons' (Get-A5EntryDecision @($remote, $window, $signout)).Rung 'WINDOW'
Assert-Equal 'window index' (Get-A5EntryDecision @($remote, $window)).Index 1
Assert-Equal 'two windows' (Get-A5EntryDecision @($window, $window)).Stop 'SIGNIN_CONTROL_AMBIGUOUS'
Assert-Equal 'remote when no window' (Get-A5EntryDecision @($heading, $hint, $remote, $signout)).Rung 'REMOTE'
Assert-Equal 'two remotes' (Get-A5EntryDecision @($remote, $remote)).Stop 'SIGNIN_CONTROL_AMBIGUOUS'
Assert-Equal 'signout last' (Get-A5EntryDecision @($heading, $andWord, $signout)).Rung 'SIGNOUT'
Assert-Equal 'and-word is not the button' (Get-A5EntryDecision @($andWord)).Stop 'SIGNIN_CONTROL_ABSENT'
Assert-Equal 'disabled window falls through' (Get-A5EntryDecision @((New-A5Item 'Window' 'Sign in to Hermes gateway' $false), $remote)).Rung 'REMOTE'
Assert-Equal 'offscreen remote falls through' (Get-A5EntryDecision @((New-A5Item 'Button' 'Sign in to remote gateway' $true $true), $signout)).Rung 'SIGNOUT'
Assert-Equal 'kind case is ordinal' (Get-A5EntryDecision @((New-A5Item 'window' 'Sign in to Hermes gateway'))).Stop 'SIGNIN_CONTROL_ABSENT'

$artifact = @('All', 'Images', 'Files', 'Links', 'Extra')
$files = New-A5Item 'Button' 'Files' $true $false $artifact
$plain = New-A5Item 'Button' 'Files' $true $false @('Files')
Assert-Equal 'artifact siblings rejected' (Select-A5Files @($files, $plain)).Index 1
Assert-Equal 'file system is not files' (Select-A5Files @((New-A5Item 'Button' 'File system' $true $false @('File system')))).Stop 'FILES_CONTROL_ABSENT'
Assert-Equal 'null siblings ineligible' (Select-A5Files @((New-A5Item 'Button' 'Files' $true $false $null))).Stop 'FILES_CONTROL_ABSENT'
Assert-Equal 'text named files' (Select-A5Files @((New-A5Item 'Text' 'Files' $true $false @('Files')))).Stop 'FILES_CONTROL_ABSENT'
Assert-Equal 'heading named files' (Select-A5Files @((New-A5Item 'Heading' 'Files' $true $false @('Files')))).Stop 'FILES_CONTROL_ABSENT'
Assert-Equal 'hyperlink named files' (Select-A5Files @((New-A5Item 'Hyperlink' 'Files' $true $false @('Files')))).Stop 'FILES_CONTROL_ABSENT'
Assert-Equal 'list item named files' (Select-A5Files @((New-A5Item 'ListItem' 'Files' $true $false @('Files')))).Stop 'FILES_CONTROL_ABSENT'
Assert-Equal 'two files buttons' (Select-A5Files @($plain, $plain)).Stop 'FILES_CONTROL_AMBIGUOUS'
Assert-Equal 'files name case' (Select-A5Files @((New-A5Item 'Button' 'files' $true $false @('files')))).Stop 'FILES_CONTROL_ABSENT'
Assert-Equal 'tab item files' (Select-A5Files @((New-A5Item 'TabItem' 'Files' $true $false @('Files')))).Stop ''
Assert-True 'artifact set with extra still matches' (Test-A5ArtifactSiblings $artifact)
Assert-False 'incomplete artifact set' (Test-A5ArtifactSiblings @('All', 'Images', 'Links'))

$row = New-A5Item 'DataItem' 'fixture.bin'
Assert-Equal 'no rows' (Select-A5FileRows @()).Stop 'FILES_ROW_ABSENT'
Assert-Equal 'no rows count' (Select-A5FileRows @()).Count 0
Assert-Equal 'one row' (Select-A5FileRows @($row)).Stop ''
Assert-Equal 'two rows' (Select-A5FileRows @($row, (New-A5Item 'ListItem' 'other.bin'))).Stop 'FILES_ROW_AMBIGUOUS'
Assert-Equal 'two rows count' (Select-A5FileRows @($row, (New-A5Item 'TreeItem' 'other.bin'))).Count 2
Assert-Equal 'folder excluded' (Select-A5FileRows @((New-A5Item 'TreeItem' 'dir' $true $false @() '' $true), $row)).Index 1
Assert-Equal 'chrome name excluded' (Select-A5FileRows @((New-A5Item 'ListItem' 'Download'), $row)).Index 1
Assert-Equal 'empty name excluded' (Select-A5FileRows @((New-A5Item 'DataItem' ''))).Stop 'FILES_ROW_ABSENT'
Assert-Equal 'disabled row excluded' (Select-A5FileRows @((New-A5Item 'DataItem' 'fixture.bin' $false))).Stop 'FILES_ROW_ABSENT'

Assert-True 'save buttons' (Test-A5SaveButtons @('Save', 'Cancel'))
Assert-False 'save button extra' (Test-A5SaveButtons @('Cancel', 'Save', 'Help'))
Assert-False 'save button duplicate' (Test-A5SaveButtons @('Cancel', 'Cancel'))
Assert-False 'save buttons yes no' (Test-A5SaveButtons @('Yes', 'No'))

Assert-True 'replace yes no' (Get-A5ReplaceDecision @('Yes', 'No')).Ok
Assert-Equal 'yes without no' (Get-A5ReplaceDecision @('Yes')).Stop 'REPLACE_PROMPT'
Assert-Equal 'save dialog is not a prompt' (Get-A5ReplaceDecision @('Cancel', 'Save')).Stop 'REPLACE_ABSENT'
Assert-Equal 'yes no and save is absent' (Get-A5ReplaceDecision @('Yes', 'No', 'Save')).Stop 'REPLACE_ABSENT'
Assert-Equal 'yes and save without no' (Get-A5ReplaceDecision @('Yes', 'Save')).Stop 'REPLACE_PROMPT'
Assert-Equal 'no alone' (Get-A5ReplaceDecision @('No')).Stop 'REPLACE_ABSENT'

$sign = New-A5Item 'Button' 'Sign in' $true $false @() 'Sign in to Hermes gateway'
Assert-Equal 'sign in button' (Select-A5SignIn @($sign)).Stop ''
Assert-Equal 'sign in needs the login title' (Select-A5SignIn @((New-A5Item 'Button' 'Sign in' $true $false @() 'Hermes'))).Stop 'SIGNIN_CONTROL_ABSENT'
Assert-Equal 'remote name is not sign in' (Select-A5SignIn @((New-A5Item 'Button' 'Sign in to remote gateway' $true $false @() 'Sign in to Hermes gateway'))).Stop 'SIGNIN_CONTROL_ABSENT'
Assert-Equal 'two sign in buttons' (Select-A5SignIn @($sign, $sign)).Stop 'SIGNIN_CONTROL_AMBIGUOUS'
Assert-Equal 'text sign in' (Select-A5SignIn @((New-A5Item 'Text' 'Sign in' $true $false @() 'Sign in to Hermes gateway'))).Stop 'SIGNIN_CONTROL_ABSENT'

$user = New-A5Item 'Edit' 'Username' $true $false @() 'Sign in to Hermes gateway'
Assert-Equal 'username edit' (Select-A5Edit @($user) 'Username' 'Sign in to Hermes gateway').Stop ''
Assert-Equal 'username wrong window' (Select-A5Edit @($user) 'Username' 'Hermes').Stop 'UIA_CONTROL_ABSENT'
Assert-Equal 'two username edits' (Select-A5Edit @($user, $user) 'Username' 'Sign in to Hermes gateway').Stop 'UIA_CONTROL_AMBIGUOUS'

Assert-Equal 'login invalid' (Get-A5LoginError @('Invalid username or password.')) 'invalid'
Assert-Equal 'login throttle' (Get-A5LoginError @('Too many attempts. Please wait and try again.')) 'throttle'
Assert-Equal 'login invalid wins' (Get-A5LoginError @('Too many attempts. Please wait and try again.', 'Invalid username or password.')) 'invalid'
$other = Get-A5LoginError @('typed-name-must-not-print', 'invalid username or password.')
if ($other -ne 'none') {
  throw 'login error class'
}

Assert-Equal 'secret crlf' (Get-A5SecretText "abc`r`n") 'abc'
Assert-Equal 'secret lf' (Get-A5SecretText "abc`n") 'abc'
Assert-Equal 'secret cr' (Get-A5SecretText "abc`r") 'abc'
Assert-Equal 'secret one newline' (Get-A5SecretText "ab`n`n") "ab`n"
Assert-Equal 'secret keeps spaces' (Get-A5SecretText '  ab  ') '  ab  '
Assert-Equal 'secret empty' (Get-A5SecretText '') ''
Assert-Equal 'secret null' (Get-A5SecretText $null) ''
Assert-Equal 'sendkeys' (ConvertTo-A5SendKeys 'a+b^c%d~(){}[]') 'a{+}b{^}c{%}d{~}{(}{)}{{}{}}{[}{]}'

Assert-True 'nonce 9' (Test-A5Nonce '123456789')
Assert-False 'nonce 8' (Test-A5Nonce '12345678')
Assert-True 'nonce 19' (Test-A5Nonce ('9' * 19))
Assert-False 'nonce 20' (Test-A5Nonce ('9' * 20))
Assert-False 'nonce leading zero' (Test-A5Nonce '012345678')
Assert-True 'pid' (Test-A5PidText '42312')
Assert-True 'pid max' (Test-A5PidText '4294967295')
Assert-False 'pid over' (Test-A5PidText '4294967296')
Assert-False 'pid zero' (Test-A5PidText '0')
Assert-False 'pid leading zero' (Test-A5PidText '042312')
Assert-Equal 'hwnd high bit' (ConvertTo-A5Hwnd -1) '4294967295'
Assert-Equal 'hwnd zero' (ConvertTo-A5Hwnd 0) '0'
Assert-Equal 'hwnd ptr' ([int64](ConvertTo-A5HwndPtr -1)) 4294967295

Assert-True 'stop login' (Test-A5StopCode 'LOGIN_REJECTED')
Assert-False 'stop case' (Test-A5StopCode 'login_rejected')
Assert-False 'stop secret name' (Test-A5StopCode 'Password')
Assert-False 'stop path field' (Test-A5StopCode 'EXACT_EXECUTABLE_PATH')
Assert-True 'action discover' (Test-A5ActionName 'Discover')
Assert-False 'action case' (Test-A5ActionName 'discover')
Assert-False 'action closed' (Test-A5ActionName 'ClickYes')
Assert-True 'field done' (Test-A5FieldName 'UIA_DONE')
Assert-False 'field password' (Test-A5FieldName 'Password')
Assert-False 'field username' (Test-A5FieldName 'Username')
Assert-False 'field path' (Test-A5FieldName 'EXACT_EXECUTABLE_PATH')
Assert-False 'field match' (Test-A5FieldName 'ATTEST_MATCH')

$kept = @(Format-A5Fields @{
    UIA_DONE = '1'
    UIA_HELPER = '1'
    Password = 'hunter2'
    Username = 'someone'
    EXACT_EXECUTABLE_PATH = 'C:\nope'
    ATTEST_MATCH = 'UAT'
    UIA_STOP = "bad`nline"
  })
$keptText = ($kept -join '|')
if ($keptText.Contains('hunter2') -or $keptText.Contains('someone') -or $keptText.Contains('EXACT_EXECUTABLE_PATH') -or $keptText.Contains('ATTEST_MATCH') -or $keptText.Contains('bad')) {
  throw 'field leak'
}
Assert-True 'kept helper' ($keptText.Contains('UIA_HELPER=1'))
Assert-True 'kept done' ($keptText.Contains('UIA_DONE=1'))

$onlyChild = Select-A5DiscoverEntry @(@{ Pid = 11; Rung = 'WINDOW'; Role = 'child' })
Assert-Equal 'child entry pid' $onlyChild.Pid '11'
Assert-Equal 'child entry rung' $onlyChild.Rung 'WINDOW'
$preferMain = Select-A5DiscoverEntry @(
  @{ Pid = 11; Rung = 'WINDOW'; Role = 'child' },
  @{ Pid = 22; Rung = 'REMOTE'; Role = 'main' }
)
Assert-Equal 'main preferred' $preferMain.Pid '22'
Assert-Equal 'two mains' (Select-A5DiscoverEntry @(
    @{ Pid = 22; Rung = 'WINDOW'; Role = 'main' },
    @{ Pid = 23; Rung = 'WINDOW'; Role = 'main' }
  )).Stop 'SIGNIN_CONTROL_AMBIGUOUS'
Assert-Equal 'no entry rung' (Select-A5DiscoverEntry @(@{ Pid = 22; Rung = 'ABSENT'; Role = 'main' })).Stop 'SIGNIN_CONTROL_ABSENT'
Assert-Equal 'empty discover' (Select-A5DiscoverEntry @()).Stop 'SIGNIN_CONTROL_ABSENT'
$caseRole = Select-A5DiscoverEntry @(
  @{ Pid = 22; Rung = 'WINDOW'; Role = 'MAIN' },
  @{ Pid = 11; Rung = 'WINDOW'; Role = 'child' }
)
Assert-Equal 'role case is not main' $caseRole.Stop 'SIGNIN_CONTROL_AMBIGUOUS'

$script:A5WaitCalls = 0
$ambiguousSample = {
  $script:A5WaitCalls = $script:A5WaitCalls + 1
  Out-A5Obj @{ Decision = (Out-A5Obj @{ Stop = 'FILES_ROW_AMBIGUOUS' }) }
}
$immediate = Wait-A5Until $ambiguousSample 20 @('FILES_ROW_ABSENT')
Assert-Equal 'ambiguous does not wait' $script:A5WaitCalls 1
Assert-Equal 'ambiguous stop preserved' $immediate.Decision.Stop 'FILES_ROW_AMBIGUOUS'

$script:A5WaitCalls = 0
$absentSample = {
  $script:A5WaitCalls = $script:A5WaitCalls + 1
  if ($script:A5WaitCalls -ge 2) {
    Out-A5Obj @{ Decision = (Out-A5Obj @{ Stop = '' }) }
  } else {
    Out-A5Obj @{ Decision = (Out-A5Obj @{ Stop = 'FILES_ROW_ABSENT' }) }
  }
}
$ready = Wait-A5Until $absentSample 3 @('FILES_ROW_ABSENT')
Assert-Equal 'absent waits once' $script:A5WaitCalls 2
Assert-Equal 'absent can clear' $ready.Decision.Stop ''

function Read-A5Console([scriptblock]$Body) {
  $writer = New-Object System.IO.StringWriter
  $previous = [Console]::Out
  [Console]::SetOut($writer)
  try {
    & $Body | Out-Null
  } finally {
    [Console]::SetOut($previous)
  }

  return ($writer.ToString() -replace "`r", '')
}

Assert-Equal 'strict true rejects a stop string' (Test-A5IsTrue 'UIA_STOP=UIA_INVOKE_UNAVAILABLE') $false
Assert-Equal 'strict true rejects false' (Test-A5IsTrue $false) $false
Assert-Equal 'strict true accepts true' (Test-A5IsTrue $true) $true

$script:A5Stopped = $false
$stopRead = Read-A5Console {
  $pipeline = @(Stop-A5Uia 'hunter2')
  $second = @(Stop-A5Uia 'UIA_FAULT')
  if (($pipeline -join '|').Contains('hunter2') -or ($second -join '|').Contains('hunter2')) {
    throw 'stop leaked'
  }

  Assert-Equal 'stop stays off the success stream' @($pipeline).Count 0
  Assert-Equal 'second stop suppressed' @($second).Count 0
}
if ($stopRead.Contains('hunter2')) {
  throw 'stop leaked'
}
Assert-Equal 'unknown stop' $stopRead.Trim() 'UIA_STOP=RUNBOOK_DRIFT'

$script:A5Stopped = $false
$fieldRead = Read-A5Console {
  $pipeline = @(Write-A5Field 'Password' 'hunter2')
  if (($pipeline -join '|').Contains('hunter2')) {
    throw 'field write leaked'
  }

  Assert-Equal 'field stays off the success stream' @($pipeline).Count 0
}
if ($fieldRead.Contains('hunter2')) {
  throw 'field write leaked'
}
Assert-Equal 'rejected field' $fieldRead.Trim() 'UIA_STOP=RUNBOOK_DRIFT'

$script:A5Stopped = $false
$invokeRead = Read-A5Console {
  function Invoke-A5FakePattern {
    Stop-A5Uia 'UIA_INVOKE_UNAVAILABLE'
    return $false
  }

  if (Test-A5IsTrue (Invoke-A5FakePattern)) {
    Write-A5Field 'ENTRY_INVOKED' '1'
  }
}
if ($invokeRead.Contains('ENTRY_INVOKED')) {
  throw 'failed invoke counted as a click'
}
Assert-Equal 'failed invoke prints the stop' ($invokeRead.Trim()) 'UIA_STOP=UIA_INVOKE_UNAVAILABLE'

$helperPath = Join-Path $PSScriptRoot 'NATIVE_A5_BEELINK_SESSION1_UIA.ps1'
$pwshExe = Join-Path $PSHOME 'pwsh'
if (-not (Test-Path -LiteralPath $pwshExe)) {
  $pwshExe = Join-Path $PSHOME 'pwsh.exe'
}
$nonceProbe = & $pwshExe -NoProfile -File $helperPath -Action Entry -AttestedPid 42312 -Nonce 12
$nonceText = (($nonceProbe | Out-String) -replace "`r", '').Trim()
if ($nonceText -match '(?m)^2$') {
  throw 'exit code leaked'
}
if ($nonceText.Contains('UIA_DONE')) {
  throw 'bad nonce completed'
}
Assert-Equal 'bad nonce stops' ($nonceText.Contains('UIA_STOP=RUNBOOK_DRIFT')) $true
Assert-Equal 'bad nonce still identifies the helper' ($nonceText.Contains('UIA_HELPER=1')) $true
if ($LASTEXITCODE -ne 2) {
  throw 'bad nonce exit'
}

if ($MyInvocation.InvocationName -eq $null) {
  # keep the test host alive
}

Write-Output 'A5_UIA_DECISION_TESTS_OK'
