# MiyoQian Web UI auto-start script.
# Invoked by shell:startup shortcut on user login.
# Source is intentionally pure ASCII to avoid PowerShell 5.1 encoding issues
# when the project directory contains non-ASCII characters (e.g. D:\签到).
# The project directory is derived from $MyInvocation.MyCommand.Path,
# which the PowerShell host passes as a correctly-encoded Unicode string.

$ErrorActionPreference = 'Stop'

# Derive project directory from this script's own location
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$uvBin = Join-Path $env:USERPROFILE '.local\bin\uv.exe'

# 1) Kill any stale instance still holding port 5890
$existing = Get-NetTCPConnection -LocalPort 5890 -ErrorAction SilentlyContinue |
    Where-Object State -eq 'Listen' |
    Select-Object -ExpandProperty OwningProcess -First 1
if ($existing) {
    Write-Host "[start-miyoqian] Killing existing PID=$existing"
    try {
        taskkill /F /T /PID $existing | Out-Null
    } catch {
        Write-Host "[start-miyoqian] Kill failed (may be gone): $_"
    }
    Start-Sleep -Seconds 2
}

# 2) Ensure logs/ directory exists
$logsDir = Join-Path $projectDir 'logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

# 3) Make sure uv is on PATH for the child process
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

# 4) Switch to project dir so uv can find main.py
Set-Location $projectDir
Write-Host "[start-miyoqian] Working dir: $(Get-Location)"

# 5) Launch main.py in the foreground. This call blocks until main.py exits.
#    Task Scheduler's RestartCount will restart the script if main.py dies.
$stdoutLog = Join-Path $logsDir 'autostart.out.log'
$stderrLog = Join-Path $logsDir 'autostart.err.log'
Write-Host "[start-miyoqian] Launching uv run python main.py"
& $uvBin run python main.py > $stdoutLog 2> $stderrLog

Write-Host "[start-miyoqian] main.py exited with code $LASTEXITCODE"

# Watchdog loop: keep this script alive forever, and relaunch main.py
# if/when it dies. The loop sleeps 60s between checks to avoid wasting CPU.
# Without this loop, the script would just exit after main.py dies and the
# Web UI would stay down until the next reboot/login.
while ($true) {
    Start-Sleep -Seconds 60
    $listen = Get-NetTCPConnection -LocalPort 5890 -ErrorAction SilentlyContinue |
        Where-Object State -eq 'Listen'
    if (-not $listen) {
        Write-Host "[start-miyoqian] watchdog: port 5890 down, relaunching main.py"
        Set-Location $projectDir
        $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
        & $uvBin run python main.py > $stdoutLog 2> $stderrLog
        Write-Host "[start-miyoqian] watchdog: main.py exited code $LASTEXITCODE, will re-check in 60s"
    }
}