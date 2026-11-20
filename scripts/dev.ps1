param([ValidateSet('api', 'bot', 'worker', 'web', 'migrate')][string]$Service = 'api')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$env:PYTHONPATH = Join-Path (Get-Location) 'backend'
$pythonExe = Join-Path (Get-Location) '.venv/Scripts/python.exe'
switch ($Service) {
    'api' { & $pythonExe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log }
    'bot' { & $pythonExe -m app.bot }
    'worker' { & $pythonExe -m app.worker }
    'migrate' { & $pythonExe -m alembic -c backend/alembic.ini upgrade head }
    'web' { Set-Location web; npm run dev }
}
