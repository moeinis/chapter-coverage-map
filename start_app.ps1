# BSF Chapter Coverage Map - Reliable Startup Script
# Usage: .\start_app.ps1
# Kills any existing Streamlit/Python on port 8501, then starts a fresh instance.

$PORT = 8501
$PYTHON = "C:\Python314\python.exe"
$SCRIPT = Join-Path $PSScriptRoot "app.py"

Write-Host "BSF Chapter Coverage Map" -ForegroundColor Cyan
Write-Host "========================" -ForegroundColor Cyan

# Build marker so UI shows exactly what revision is running.
$build = "local-dev"
try {
    $build = (git rev-parse --short HEAD).Trim()
} catch {}
$env:APP_BUILD_VERSION = "git-$build"
Write-Host "Build: $($env:APP_BUILD_VERSION)" -ForegroundColor DarkCyan

# Kill anything on port 8501
$procs = Get-NetTCPConnection -LocalPort $PORT -ErrorAction SilentlyContinue |
         Select-Object -ExpandProperty OwningProcess -Unique |
         Where-Object { $_ -gt 0 }
if ($procs) {
    Write-Host "Stopping existing processes on port $PORT..." -ForegroundColor Yellow
    $procs | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

Write-Host "Starting app on http://localhost:$PORT ..." -ForegroundColor Green
& $PYTHON -m streamlit run $SCRIPT `
    --server.port $PORT `
    --server.headless true `
    --server.runOnSave false `
    --server.fileWatcherType none
