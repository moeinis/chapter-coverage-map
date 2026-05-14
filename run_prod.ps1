$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$build = 'local-dev'
try {
    $build = (git rev-parse --short HEAD).Trim()
} catch {}
$env:APP_BUILD_VERSION = "git-$build"
Write-Host "[$(Get-Date -Format s)] Build: $($env:APP_BUILD_VERSION)"

$pythonExe = 'C:\Python314\python.exe'
$logDir = Join-Path $projectRoot 'logs'
if (-not (Test-Path $logDir)) {
    New-Item -Path $logDir -ItemType Directory | Out-Null
}

$runtimeDir = Join-Path $projectRoot '.runtime'
if (-not (Test-Path $runtimeDir)) {
    New-Item -Path $runtimeDir -ItemType Directory | Out-Null
}
$streamlitPidFile = Join-Path $runtimeDir 'streamlit.pid'

while ($true) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $outLog = Join-Path $logDir "streamlit_$stamp.out.log"
    $errLog = Join-Path $logDir "streamlit_$stamp.err.log"

    Write-Host "[$(Get-Date -Format s)] Starting Streamlit..."
    $proc = Start-Process -FilePath $pythonExe `
        -ArgumentList "-m streamlit run app.py --server.port 8501 --server.headless true" `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru

    Set-Content -Path $streamlitPidFile -Value $proc.Id -Encoding ascii
    Write-Host "[$(Get-Date -Format s)] Streamlit PID: $($proc.Id)"

    Wait-Process -Id $proc.Id
    if (Test-Path $streamlitPidFile) {
        Remove-Item $streamlitPidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[$(Get-Date -Format s)] Streamlit exited with code $($proc.ExitCode). Restarting in 2 seconds..."
    Start-Sleep -Seconds 2
}
