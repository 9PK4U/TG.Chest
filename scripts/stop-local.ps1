$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runtimePath = Join-Path $projectRoot 'data/runtime'
foreach ($serviceName in @('bot', 'worker', 'api')) {
    $pidFile = Join-Path $runtimePath "$serviceName.pid"
    if (!(Test-Path -LiteralPath $pidFile)) { continue }
    $record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().ToString('o') -eq $record.started) {
        # The Windows venv launcher can own a child Python process; stop the whole owned tree.
        & taskkill.exe /PID $record.pid /T /F | Out-Null
        Write-Output "Stopped $serviceName"
    }
    Remove-Item -LiteralPath $pidFile
}
