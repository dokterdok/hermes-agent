# Decision tests for the Beelink launch script. Not a Beelink phase.
# Do not copy this file to the UAT machine. Do not pass -Phase Launch here
# except as the dot-source parameter that the launch script ignores.
$ErrorActionPreference = 'Stop'

if (-not (Get-PSDrive -Name C -ErrorAction SilentlyContinue)) {
  New-PSDrive -Name C -PSProvider FileSystem -Root $PSScriptRoot | Out-Null
}

$launch = Join-Path $PSScriptRoot 'NATIVE_A5_BEELINK_EARLY_EXIT_LAUNCH.ps1'
. $launch -Phase Launch

if ($MyInvocation.InvocationName -eq $null) {
  # keep the test host alive
}

function Assert-Equal([string]$Name, $Actual, $Expected) {
  if ($Actual -ne $Expected) {
    throw ($Name + ' expected [' + $Expected + '] actual [' + $Actual + ']')
  }
}

function Decide {
  param(
    [int]$ProbeSession = 1,
    [string]$ProbeOwner = 'ddewit',
    [bool]$ProbeFresh = $true,
    [int]$Visible = 0,
    [int]$Hidden = 0,
    [double]$VisibleSeconds = 0,
    [bool]$ProfileOk = $true,
    [bool]$MainAlive = $true,
    [bool]$PastNoWindow = $false,
    [bool]$PastHardStop = $false,
    [int]$Small = 0,
    [string]$ProbeDesktop = 'Default'
  )

  return Get-A5WindowDecision -ProbeSession $ProbeSession -ProbeOwner $ProbeOwner -ProbeDesktop $ProbeDesktop -ProbeFresh:$ProbeFresh -Visible $Visible -Hidden $Hidden -Small $Small -VisibleSeconds $VisibleSeconds -ProfileOk:$ProfileOk -MainAlive:$MainAlive -PastNoWindow:$PastNoWindow -PastHardStop:$PastHardStop
}

Assert-Equal 'ssh session is not no-window' (Decide -ProbeSession 0 -PastNoWindow $true -PastHardStop $true) 'probe-bad'
Assert-Equal 'stale probe is not stable' (Decide -ProbeFresh $false -Visible 1 -VisibleSeconds 20 -PastHardStop $true) 'probe-bad'
Assert-Equal 'wrong owner is not no-window' (Decide -ProbeOwner 'SYSTEM' -PastNoWindow $true -PastHardStop $true) 'probe-bad'
Assert-Equal 'wrong desktop is not no-window' (Decide -ProbeDesktop 'Winlogon' -PastNoWindow $true -PastHardStop $true) 'probe-bad'
Assert-Equal 'blank desktop is not no-window' (Decide -ProbeDesktop '' -PastNoWindow $true -PastHardStop $true) 'probe-bad'
Assert-Equal 'owner case still counts' (Decide -ProbeOwner 'DDEWIT' -Visible 1 -VisibleSeconds 15) 'stable'
Assert-Equal 'fifteen visible seconds is stable' (Decide -Visible 1 -VisibleSeconds 15) 'stable'
Assert-Equal 'fourteen visible seconds waits' (Decide -Visible 1 -VisibleSeconds 14) 'wait'
Assert-Equal 'stable requires profile' (Decide -Visible 1 -VisibleSeconds 15 -ProfileOk $false) 'wait'
Assert-Equal 'stable requires main' (Decide -Visible 1 -VisibleSeconds 15 -MainAlive $false) 'wait'
Assert-Equal 'visible at hard stop without 15s is unstable' (Decide -Visible 1 -VisibleSeconds 14 -PastHardStop $true) 'unstable'
Assert-Equal 'visible and dead main at hard stop is unstable' (Decide -Visible 1 -VisibleSeconds 20 -MainAlive $false -PastHardStop $true) 'unstable'
Assert-Equal 'zero windows after 90s is no-window' (Decide -PastNoWindow $true) 'no-window'
Assert-Equal 'small windows do not early-kill' (Decide -Small 1 -PastNoWindow $true) 'wait'
Assert-Equal 'small windows at hard stop stay up' (Decide -Small 1 -PastHardStop $true) 'unstable'
Assert-Equal 'no profile and only small waits through 90s' (Decide -ProfileOk $false -Small 1 -PastNoWindow $true) 'wait'
Assert-Equal 'no profile at hard stop still profile when only small' (Decide -ProfileOk $false -Small 1 -PastHardStop $true) 'profile'
Assert-Equal 'hidden windows do not early-exit' (Decide -Hidden 2 -PastNoWindow $true) 'wait'
Assert-Equal 'hidden at hard stop' (Decide -Hidden 2 -PastHardStop $true) 'hidden'
Assert-Equal 'no profile and no window' (Decide -ProfileOk $false -PastNoWindow $true) 'profile'
Assert-Equal 'no profile with hidden window waits' (Decide -ProfileOk $false -Hidden 1 -PastNoWindow $true) 'wait'
Assert-Equal 'no profile at hard stop beats hidden' (Decide -ProfileOk $false -Hidden 1 -PastHardStop $true) 'profile'
Assert-Equal 'fresh zero before deadline waits' (Decide) 'wait'

foreach ($pair in @(
    @('probe-bad', $false),
    @('unstable', $false),
    @('stable', $false),
    @('wait', $false),
    @('hidden', $true),
    @('no-window', $true),
    @('profile', $true)
  )) {
  $kill = Test-A5WindowKill $pair[0]
  if ($kill -ne [bool]$pair[1]) {
    throw ('kill ' + $pair[0])
  }
}

$injected = @"
PROBE_OK=1
PROBE_SESSION=1
PROBE_OWNER=ddewit
PROBE_PID=10
VISIBLE=1
HIDDEN=0
VISIBLE_PID=9
HIDDEN_PID=
VISIBLE_RECT=10,20,1220,800
VISIBLE_TITLE=Sign in to Hermes gateway
VISIBLE=0
PROBE_SESSION=0
END
"@
$parsed = Read-A5ProbeText $injected
if (-not $parsed.Complete) { throw 'injected sample should be complete' }
Assert-Equal 'first visible wins' $parsed.Visible 1
Assert-Equal 'first session wins' $parsed.Session 1
Assert-Equal 'rect accepted' $parsed.Rect '10,20,1220,800'
Assert-Equal 'title kept' $parsed.Title 'Sign in to Hermes gateway'
Assert-Equal 'missing desktop is blank' $parsed.Desktop ''
Assert-Equal 'probe ok' $parsed.Ok $true

$torn = "PROBE_OK=1`nPROBE_SESSION=1`nVISIBLE=1`nHIDDEN=0`n"
$tornParsed = Read-A5ProbeText $torn
if ($tornParsed.Complete) { throw 'sample without END must not count' }

$badVisible = "PROBE_OK=1`nPROBE_SESSION=1`nVISIBLE=1`nHIDDEN=no`nEND`n"
$badParsed = Read-A5ProbeText $badVisible
if ($badParsed.Complete) { throw 'non-numeric HIDDEN must not count' }

$rejected = "PROBE_OK=0`nPROBE_SESSION=9`nPROBE_OWNER=SYSTEM`nVISIBLE=0`nHIDDEN=0`nEND`n"
$rejectedParsed = Read-A5ProbeText $rejected
if (-not $rejectedParsed.Complete) { throw 'rejected probe should still parse' }
if ($rejectedParsed.Ok) { throw 'PROBE_OK=0 must not be ok' }
Assert-Equal 'rejected session' $rejectedParsed.Session 9

$evilRect = "PROBE_OK=1`nPROBE_SESSION=1`nPROBE_OWNER=ddewit`nVISIBLE=1`nHIDDEN=0`nVISIBLE_RECT=1,2,3,4;VISIBLE=0`nEND`n"
$evilParsed = Read-A5ProbeText $evilRect
Assert-Equal 'evil rect dropped' $evilParsed.Rect ''
Assert-Equal 'evil rect keeps visible' $evilParsed.Visible 1

$marker = Format-A5MarkerText ("{`"state`":`"booting`"}`r`nWINDOW_STABLE`n")
if ($marker -match "`n" -or $marker -match "`r") { throw 'marker newline survived' }
if ($marker -notlike '*WINDOW_STABLE*') { throw 'marker text should stay on one line' }
Assert-Equal 'marker cap' (Format-A5MarkerText ('x' * 600)).Length 500

if (Test-A5WindowKill (Decide -ProbeSession 0 -PastHardStop $true -PastNoWindow $true)) {
  throw 'a non-interactive probe must not select a kill'
}

if ((Get-A5SampleAge $null) -lt 100) { throw 'missing sample must look stale' }
$recent = (Get-Date).ToUniversalTime().AddSeconds(-1)
if ((Get-A5SampleAge $recent) -gt 5) { throw 'a one-second-old sample must stay fresh' }
$old = (Get-Date).ToUniversalTime().AddSeconds(-30)
if ((Get-A5SampleAge $old) -lt 20) { throw 'a thirty-second-old sample must be stale' }

$withDesktop = "PROBE_OK=1`nPROBE_SESSION=1`nPROBE_OWNER=ddewit`nDESKTOP=Default`nVISIBLE=1`nHIDDEN=0`nSMALL=2`nEND`n"
$desktopParsed = Read-A5ProbeText $withDesktop
Assert-Equal 'desktop recorded' $desktopParsed.Desktop 'Default'
Assert-Equal 'small recorded' $desktopParsed.Small 2
if (Test-A5WindowKill (Decide -Small 1 -PastHardStop $true)) { throw 'small windows at the hard stop must not select a kill' }

Write-Output 'A5_DECISION_TESTS_OK'
