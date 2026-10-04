param([switch]$UseDeepSeek, [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install backend[dev] first; see README.md.' }
if ($UseDeepSeek) {
    if (-not $env:DEEPSEEK_API_KEY) { throw 'DEEPSEEK_API_KEY is missing; do not paste it into this script.' }
    $env:AI_MODE = 'deepseek'
}
& $taskPython -m alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
$workerProcess = Start-Process -FilePath $taskPython -ArgumentList '-m','smartdiary.worker' -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru
try { & $taskPython -m uvicorn smartdiary.app:app --host 127.0.0.1 --port $Port --no-access-log }
finally { if (-not $workerProcess.HasExited) { Stop-Process -Id $workerProcess.Id } }
