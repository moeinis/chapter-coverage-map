$ErrorActionPreference = 'Continue'
$uri = 'http://127.0.0.1:8501/_stcore/health'

$status = 'not_ready'
$code = 0

try {
    $resp = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 5
    $status = ($resp.Content | Out-String).Trim()
    $code = [int]$resp.StatusCode
} catch {
    $status = 'unreachable'
    $code = 0
}

$boundPids = Get-NetTCPConnection -LocalPort 8501 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    Where-Object { $_ -gt 0 }

$result = [PSCustomObject]@{
    timestamp = (Get-Date).ToString('s')
    url = $uri
    http_status = $code
    health = $status
    pids_on_port_8501 = @($boundPids)
}

$result | ConvertTo-Json -Depth 4

if ($status -eq 'ok' -and $code -eq 200) {
    exit 0
}

exit 1
