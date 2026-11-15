$port = 8080
$envFile = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match '^HTTP_PORT=\d+' } | Select-Object -First 1
    if ($line -match '^HTTP_PORT=(\d+)') { $port = [int]$Matches[1] }
}
$url = 'http://localhost:' + $port + '/api/health'
$deadline = (Get-Date).AddSeconds(240)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { exit 0 }
    } catch { }
    Start-Sleep -Seconds 3
}
exit 1
