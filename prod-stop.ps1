$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$runtimeDir = Join-Path $projectRoot '.runtime'
$supervisorPidFile = Join-Path $runtimeDir 'prod-supervisor.pid'
$streamlitPidFile = Join-Path $runtimeDir 'streamlit.pid'

if (Test-Path $supervisorPidFile) {
    try {
        $supervisorPid = [int](Get-Content $supervisorPidFile -ErrorAction Stop)
        Stop-Process -Id $supervisorPid -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped production supervisor PID: $supervisorPid" -ForegroundColor Yellow
    } catch {
        Write-Host "Could not stop supervisor from PID file." -ForegroundColor DarkYellow
    }
    Remove-Item $supervisorPidFile -Force -ErrorAction SilentlyContinue
}

if (Test-Path $streamlitPidFile) {
    try {
        $streamlitPid = [int](Get-Content $streamlitPidFile -ErrorAction Stop)
        Stop-Process -Id $streamlitPid -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped streamlit PID: $streamlitPid" -ForegroundColor Yellow
    } catch {
        Write-Host "Could not stop streamlit from PID file." -ForegroundColor DarkYellow
    }
    Remove-Item $streamlitPidFile -Force -ErrorAction SilentlyContinue
}

$boundPids = Get-NetTCPConnection -LocalPort 8501 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    Where-Object { $_ -gt 0 }
if ($boundPids) {
    $boundPids | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped PID on port 8501: $_" -ForegroundColor Yellow
    }
}

Write-Host "Production stop complete." -ForegroundColor Green
