$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

Write-Host "Starting FastAPI + MapLibre app on http://localhost:8601 ..." -ForegroundColor Cyan

Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

& "c:\python314\python.exe" -m uvicorn map_api:app --host 0.0.0.0 --port 8601
