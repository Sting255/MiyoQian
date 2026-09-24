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
# Guard 2: if something is already serving port 5890, monitor it instead of
# killing it. Restarting here would interrupt a check-in that is in progress;
# the loop at the bottom already relaunches main.py if the port goes down.
# ---------------------------------------------------------------------------
$logsDir = Join-Path $projectDir 'logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}
$stdoutLog = Join-Path $logsDir 'autostart.out.log'
$stderrLog = Join-Path $logsDir 'autostart.err.log'

function Invoke-MainPy {
    # Start-Process redirects at the OS level, so the child's bytes are written
    # verbatim (UTF-8). PowerShell's ">" would decode them using the console code
    # page first and store UTF-16, which mangles non-ASCII log output.
    $process = Start-Process -FilePath $uvBin -ArgumentList @('run', 'python', 'main.py') `
        -WorkingDirectory $projectDir -NoNewWindow -PassThru -Wait `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    return $process.ExitCode
}

$listening = @(Get-NetTCPConnection -LocalPort 5890 -State Listen -ErrorAction SilentlyContinue)
if ($listening.Count -gt 0) {
    Write-Host "[start-miyoqian] port 5890 is already served by PID $($listening[0].OwningProcess); monitoring it"
} else {
    Set-Location $projectDir
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    Write-Host "[start-miyoqian] launching uv run python main.py"
    $exitCode = Invoke-MainPy
    Write-Host "[start-miyoqian] main.py exited with code $exitCode"
}

# Watchdog loop: keep this script alive and relaunch main.py if the port goes
# down. Sleeps 60s between checks to avoid wasting CPU. Without this loop the
# script would exit after main.py dies and the Web UI would stay down until the
# next logon.
while ($true) {
    Start-Sleep -Seconds 60
    $listen = @(Get-NetTCPConnection -LocalPort 5890 -State Listen -ErrorAction SilentlyContinue)
    if ($listen.Count -eq 0) {
        Write-Host "[start-miyoqian] watchdog: port 5890 down, relaunching main.py"
        Set-Location $projectDir
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        $code = Invoke-MainPy
        Write-Host "[start-miyoqian] watchdog: main.py exited code $code, will re-check in 60s"
    }
}
