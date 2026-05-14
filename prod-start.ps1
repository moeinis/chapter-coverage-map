$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$runtimeDir = Join-Path $projectRoot '.runtime'
if (-not (Test-Path $runtimeDir)) {
    New-Item -Path $runtimeDir -ItemType Directory | Out-Null
}

$supervisorPidFile = Join-Path $runtimeDir 'prod-supervisor.pid'
$streamlitPidFile = Join-Path $runtimeDir 'streamlit.pid'

# Ensure a clean start.
if (Test-Path $supervisorPidFile) {
    try {
        $oldSupervisor = [int](Get-Content $supervisorPidFile -ErrorAction Stop)
        Stop-Process -Id $oldSupervisor -Force -ErrorAction SilentlyContinue
    } catch {}
    Remove-Item $supervisorPidFile -Force -ErrorAction SilentlyContinue
}

if (Test-Path $streamlitPidFile) {
    try {
        $oldStreamlit = [int](Get-Content $streamlitPidFile -ErrorAction Stop)
        Stop-Process -Id $oldStreamlit -Force -ErrorAction SilentlyContinue
    } catch {}
    Remove-Item $streamlitPidFile -Force -ErrorAction SilentlyContinue
}

# Also clear anything currently bound to 8501.
$boundPids = Get-NetTCPConnection -LocalPort 8501 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    Where-Object { $_ -gt 0 }
if ($boundPids) {
    $boundPids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

$supervisor = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File .\run_prod.ps1' `
    -WorkingDirectory $projectRoot `
    -PassThru

Set-Content -Path $supervisorPidFile -Value $supervisor.Id -Encoding ascii
Write-Host "Production supervisor started. PID: $($supervisor.Id)" -ForegroundColor Green
Write-Host "Health: http://localhost:8501/_stcore/health" -ForegroundColor Cyan
