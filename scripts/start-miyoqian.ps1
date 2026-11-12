# MiyoQian Web UI auto-start script (watchdog).
#
# Invoked by the MiyoQianWebUI scheduled task through start-miyoqian-hidden.vbs.
# The task has two triggers: at logon, and repeating every 5 minutes. The repeat
# exists only to revive a watchdog that died; the guards below make sure the
# repeat never disturbs a healthy service.
#
# Source is intentionally pure ASCII: Windows PowerShell 5.1 decodes BOM-less
# .ps1 files using the ANSI code page, so non-ASCII text turns into mojibake and
# can even break parsing.
#
# This script lives in <project>\scripts, so the project root is one level up.

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $scriptDir
$uvBin = Join-Path $env:USERPROFILE '.local\bin\uv.exe'

# Decode/encode console text as UTF-8 so the child's output is not mangled by the
# console code page (a GBK console turns UTF-8 log lines into mojibake).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# Force the child Python to write UTF-8. When stdout is a redirected file (not a
# console) Python falls back to the system locale encoding (GBK on Chinese
# Windows), which would put non-UTF-8 bytes into autostart.out.log.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# ---------------------------------------------------------------------------
# Guard 1: only one watchdog at a time.
# The task repeats every 5 minutes, and its action (wscript) exits immediately,
# so Task Scheduler considers the task "finished" and will start it again. Without
# this guard every repetition would start another watchdog, and each new watchdog
# would restart main.py -- killing a check-in that is in progress.
# ---------------------------------------------------------------------------
$selfPid = $PID
$otherWatchdogs = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        # Match the real invocation ("... -File <path>\start-miyoqian.ps1") rather
        # than any command line that merely mentions the file name -- otherwise an
        # unrelated shell (or a diagnostic command) would look like a watchdog.
        $_.ProcessId -ne $selfPid -and
        $_.CommandLine -and
        $_.CommandLine -match '-File\s+.*start-miyoqian\.ps1'
    })
if ($otherWatchdogs.Count -gt 0) {
    Write-Host "[start-miyoqian] another watchdog is already running (PID $($otherWatchdogs[0].ProcessId)); nothing to do"
    exit 0
}

# ---------------------------------------------------------------------------
# Guard 2: if the console is already up, monitor it instead of killing it.
# Restarting here would interrupt a check-in that is in progress; the loop at the
# bottom already relaunches main.py if the service goes down.
# ---------------------------------------------------------------------------
$logsDir = Join-Path $projectDir 'logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}
$stdoutLog = Join-Path $logsDir 'autostart.out.log'
$stderrLog = Join-Path $logsDir 'autostart.err.log'

# The console writes the port it actually bound into data/runtime.json, because
# the configured port may be taken and the console then falls back to another one.
$runtimeFile = Join-Path $projectDir 'data\runtime.json'

function Get-RuntimeInfo {
    # Returns the parsed runtime file, or $null when it is missing or unreadable.
    if (-not (Test-Path -LiteralPath $runtimeFile)) { return $null }
    try {
        return (Get-Content -LiteralPath $runtimeFile -Raw -ErrorAction Stop) | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-PingOk([int]$port) {
    # Ask the port who it is, instead of only asking "is anything listening".
    # A hard-killed console leaves runtime.json behind; if another program then
    # grabs that port, a listen-only check would keep the watchdog asleep forever
    # and the console would stay offline.
    if (-not $port) { return $false }
    try {
        $ping = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/ping" -TimeoutSec 2 -ErrorAction Stop
        return ($ping.app -eq 'miyouqian')
    } catch {
        return $false
    }
}

function Test-ServiceUp {
    # Two misjudgements, both costly:
    #   "healthy" when it is not -> the console stays offline and nothing restarts it;
    #   "down" when it is up      -> a second instance binds another port and runs a
    #                                second scheduler, duplicating check-ins and pushes.
    $info = Get-RuntimeInfo
    if ($null -ne $info) {
        # 1. The console answers on its recorded port and says it is us.
        if (Test-PingOk ([int]$info.port)) { return $true }
        # 2. Startup window: the recorded pid is alive and is a main.py process, but
        #    the port is not bound yet. Also covers a console whose HTTP is wedged:
        #    we prefer "do not start a second one" over "restart".
        if ($info.pid) {
            $proc = @(Get-CimInstance Win32_Process -Filter "ProcessId=$($info.pid)" -ErrorAction SilentlyContinue)
            if ($proc.Count -gt 0 -and $proc[0].CommandLine -match 'main\.py') {
                return $true
            }
        }
    }
    # 3. No usable runtime file (deleted, data dir moved, the write failed): match a
    #    main.py process that belongs to this project. Windows does not put the
    #    working directory into the command line, so also accept a parent whose
    #    command line carries the project dir (uv.exe -> python.exe -> venv python).
    #    Without that check any foreign `uv run python main.py` would look healthy
    #    and keep our console offline.
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $byId = @{}
    foreach ($item in $all) { $byId[[int]$item.ProcessId] = $item }
    $procs = @($all | Where-Object {
        $_.Name -match '^(python|pythonw|uv)\.exe$' -and
        $_.CommandLine -and
        $_.CommandLine -match 'main\.py' -and
        ($_.CommandLine -match [regex]::Escape($projectDir) -or
            ($byId[[int]$_.ParentProcessId] -and
             $byId[[int]$_.ParentProcessId].CommandLine -match [regex]::Escape($projectDir)))
    })
    return ($procs.Count -gt 0)
}

function Invoke-MainPy {
    # Start-Process redirects at the OS level, so the child's bytes are written
    # verbatim (UTF-8). PowerShell's ">" would decode them using the console code
    # page first and store UTF-16, which mangles non-ASCII log output.
    $process = Start-Process -FilePath $uvBin -ArgumentList @('run', 'python', 'main.py') `
        -WorkingDirectory $projectDir -NoNewWindow -PassThru -Wait `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    return $process.ExitCode
}

if (Test-ServiceUp) {
    Write-Host "[start-miyoqian] console already running; monitoring it"
} else {
    Set-Location $projectDir
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    Write-Host "[start-miyoqian] launching uv run python main.py"
    $exitCode = Invoke-MainPy
    Write-Host "[start-miyoqian] main.py exited with code $exitCode"
}

# Watchdog loop: keep this script alive and relaunch main.py if the service goes
# down. Sleeps 60s between checks to avoid wasting CPU. Without this loop the
# script would exit after main.py dies and the Web UI would stay down until the
# next logon.
while ($true) {
    Start-Sleep -Seconds 60
    if (-not (Test-ServiceUp)) {
        Write-Host "[start-miyoqian] watchdog: service down, relaunching main.py"
        Set-Location $projectDir
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        $code = Invoke-MainPy
        Write-Host "[start-miyoqian] watchdog: main.py exited code $code, will re-check in 60s"
    }
}
