# beelink-scalar-preflight.ps1
#
# Read-only memory and tool-presence preflight for the Beelink Win11 host.
# Emits bounded KEY=value scalars on stdout. Does not write files, does not
# delete files, does not start or stop processes, and does not change
# pagefile, reserve, or power state.
#
# This is not a reconstruction of the 2026-09-26 BarrX command. It was not
# executed in the receipt slice that added it. Compare a later run with
# review-packages/NATIVE_A5_FILES_UAT_BEELINK.md; do not treat a difference
# as permission to kill user apps or shrink reserves.
#
# The in-script stopwatch does not interrupt a stuck CIM call. The caller
# must impose an external deadline and, on expiry, stop only this powershell
# process. Do not pass ConvertTo-Json, Get-Content, or a process list.
#
# Invocation (on BEELINK-SER9-PR, not from a remote Linux VM):
#   powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass ^
#     -File beelink-scalar-preflight.ps1 -DeadlineSeconds 15

param(
    [int]$DeadlineSeconds = 15
)

# Do not dot-source. `exit` would then close the caller.
if ($MyInvocation.InvocationName -eq '.') {
    [Console]::Out.WriteLine('PREFLIGHT_STATUS=DOT_SOURCED')
    return
}

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$VerbosePreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
Set-StrictMode -Version 2

function Emit([string]$Line) {
    [Console]::Out.WriteLine($Line)
}

function Fail([string]$Status, [int]$Code) {
    if ($Status -notmatch '^[A-Z0-9_]{1,40}$') {
        Emit 'PREFLIGHT_STATUS=BAD_STATUS'
        exit 6
    }
    Emit ("PREFLIGHT_STATUS=" + $Status)
    exit $Code
}

if ($args.Count -gt 0) {
    Fail 'UNEXPECTED_ARGS' 4
}

if ($DeadlineSeconds -lt 1 -or $DeadlineSeconds -gt 30) {
    Fail 'BAD_DEADLINE' 4
}

$sw = [Diagnostics.Stopwatch]::StartNew()

function Assert-Deadline {
    if ($sw.Elapsed.TotalSeconds -ge $DeadlineSeconds) {
        Fail 'DEADLINE' 2
    }
}

function Format-UInt([uint64]$Number) {
    return $Number.ToString('D', [Globalization.CultureInfo]::InvariantCulture)
}

# Kilobytes from Win32_OperatingSystem, multiplied only after a range check
# so a uint64 multiply cannot wrap into a plausible-looking byte count.
function Convert-KbToBytes([object]$Kilobytes) {
    if ($null -eq $Kilobytes) {
        Fail 'MISSING_SCALAR' 5
    }
    $kb = [uint64]0
    try {
        $kb = [uint64]$Kilobytes
    } catch {
        Fail 'SCALAR_RANGE' 5
    }
    # Integer result of (2^64-1)/1024. The PowerShell `/` operator is not
    # used here: it converts uint64 operands to double and can round.
    $maxKb = [uint64]18014398509481983
    if ($kb -gt $maxKb) {
        Fail 'SCALAR_RANGE' 5
    }
    return [uint64]([decimal]$kb * [decimal]1024)
}

Assert-Deadline

$timeoutSec = $DeadlineSeconds
if ($timeoutSec -gt 5) {
    $timeoutSec = 5
}

# One operating-system instance. Three numeric properties. The CimInstance
# is never formatted, never piped, and never serialized.
$os = $null
try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -Property FreePhysicalMemory, FreeVirtualMemory, TotalVirtualMemorySize -OperationTimeoutSec $timeoutSec -ErrorAction Stop
} catch {
    $typeName = ''
    try {
        $typeName = [string]$_.Exception.GetType().FullName
    } catch {
        $typeName = ''
    }
    if ($typeName -match '^[A-Za-z0-9_.]+$') {
        Emit ("CIM_ERROR_TYPE=" + $typeName)
    } else {
        Emit 'CIM_ERROR_TYPE=REDACTED'
    }
    Fail 'CIM_FAILED' 5
}

Assert-Deadline

if ($null -eq $os) {
    Fail 'CIM_FAILED' 5
}

if ($os -is [System.Array]) {
    if (@($os).Count -ne 1) {
        Fail 'CIM_FAILED' 5
    }
    $os = $os[0]
}

try {
    $freePhys = Convert-KbToBytes $os.FreePhysicalMemory
    $commitFree = Convert-KbToBytes $os.FreeVirtualMemory
    $commitLimit = Convert-KbToBytes $os.TotalVirtualMemorySize
} catch {
    Fail 'SCALAR_RANGE' 5
}
$os = $null

if ($commitFree -gt $commitLimit) {
    Fail 'COMMIT_INCONSISTENT' 3
}

# Decimal subtraction stays exact for every uint64. The `-` operator on
# uint64 may coerce through double.
$commitUsed = [uint64]([decimal]$commitLimit - [decimal]$commitFree)

$hostname = [string]$env:COMPUTERNAME
if ($hostname -notmatch '^[A-Za-z0-9-]{1,15}$') {
    Fail 'HOSTNAME_REJECTED' 6
}

function Test-ToolPresent([string]$Name) {
    $cmd = Get-Command -Name $Name -CommandType Application, ExternalScript -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        return '0'
    }
    return '1'
}

Assert-Deadline

$toolUv = Test-ToolPresent 'uv'
$toolGit = Test-ToolPresent 'git'
$toolNode = Test-ToolPresent 'node'

Assert-Deadline

$elapsed = [uint64]$sw.ElapsedMilliseconds

Emit 'PREFLIGHT_STATUS=OK'
Emit ("HOSTNAME=" + $hostname)
Emit ("FREE_PHYS_BYTES=" + (Format-UInt $freePhys))
Emit ("COMMIT_LIMIT_BYTES=" + (Format-UInt $commitLimit))
Emit ("COMMIT_FREE_BYTES=" + (Format-UInt $commitFree))
Emit ("COMMIT_USED_BYTES=" + (Format-UInt $commitUsed))
Emit ("TOOL_UV=" + $toolUv)
Emit ("TOOL_GIT=" + $toolGit)
Emit ("TOOL_NODE=" + $toolNode)
Emit ("ELAPSED_MS=" + (Format-UInt $elapsed))
exit 0
