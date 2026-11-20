$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv/Scripts/python.exe'
$env:PYTHONPATH = Join-Path $projectRoot 'backend'
& $pythonExe -m alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }
$runtimePath = Join-Path $projectRoot 'data/runtime'
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
foreach ($serviceName in @('api', 'worker', 'bot')) {
    $pidFile = Join-Path $runtimePath "$serviceName.pid"
    if (Test-Path -LiteralPath $pidFile) {
        $record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
        $running = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        if ($running -and $running.StartTime.ToUniversalTime().ToString('o') -eq $record.started) {
            Write-Output "$serviceName already running (PID $($record.pid))"
            continue
        }
    }
    $processArgs = switch ($serviceName) {
        'api' { @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--no-access-log') }
        'worker' { @('-m', 'app.worker') }
        'bot' { @('-m', 'app.bot') }
    }
    $process = Start-Process -FilePath $pythonExe -ArgumentList $processArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath "$serviceName.out.log") -RedirectStandardError (Join-Path $runtimePath "$serviceName.err.log")
    @{ pid = $process.Id; started = $process.StartTime.ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $pidFile
    Write-Output "Started $serviceName (PID $($process.Id))"
}
Write-Output 'Mini App local preview: http://127.0.0.1:8000'
