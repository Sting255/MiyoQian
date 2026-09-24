# MiyoQian Web UI auto-start script (watchdog).
# Invoked by the MiyoQianWebUI scheduled task through start-miyoqian-hidden.vbs.
# Source is intentionally pure ASCII to avoid PowerShell 5.1 encoding issues
# when the project directory contains non-ASCII characters (e.g. the Chinese
# characters in the project path).
# The project directory is derived from $MyInvocation.MyCommand.Path,
# which the PowerShell host passes as a correctly-encoded Unicode string.

$ErrorActionPreference = 'Stop'

# Derive project directory from this script's own location
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$uvBin = Join-Path $env:USERPROFILE '.local\bin\uv.exe'

# Decode/encode console text as UTF-8 so the child's output is not mangled by the
# console code page (a GBK console turns UTF-8 log lines into mojibake).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

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

# Force the child Python to write UTF-8. When stdout is a redirected file (not a
# console) Python falls back to the system locale encoding (GBK on Chinese
# Windows), which would put non-UTF-8 bytes into autostart.out.log and make the
# log unreadable by UTF-8 tooling.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# 4) Switch to project dir so uv can find main.py
Set-Location $projectDir
Write-Host "[start-miyoqian] Working dir: $(Get-Location)"

# 5/6) Run main.py, keeping its stdout/stderr in the two log files.
# Start-Process redirects at the OS level, so the child's bytes are written
# verbatim (UTF-8); PowerShell's ">" would first decode them using the console
# code page and store the result as UTF-16, which mangles non-ASCII output.
$stdoutLog = Join-Path $logsDir 'autostart.out.log'
$stderrLog = Join-Path $logsDir 'autostart.err.log'

function Invoke-MainPy {
    $process = Start-Process -FilePath $uvBin -ArgumentList @('run', 'python', 'main.py') `
        -WorkingDirectory $projectDir -NoNewWindow -PassThru -Wait `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    return $process.ExitCode
}

Write-Host "[start-miyoqian] Launching uv run python main.py"
$exitCode = Invoke-MainPy
Write-Host "[start-miyoqian] main.py exited with code $exitCode"

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
        $code = Invoke-MainPy
        Write-Host "[start-miyoqian] watchdog: main.py exited code $code, will re-check in 60s"
    }
}
