$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonExe = 'C:\Python314\python.exe'
$logDir = Join-Path $projectRoot 'logs'
if (-not (Test-Path $logDir)) {
    New-Item -Path $logDir -ItemType Directory | Out-Null
}

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

    Wait-Process -Id $proc.Id
    Write-Host "[$(Get-Date -Format s)] Streamlit exited with code $($proc.ExitCode). Restarting in 2 seconds..."
    Start-Sleep -Seconds 2
}
