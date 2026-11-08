param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host 'Richte die lokale Python-Umgebung ein ...'
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
    else { throw 'Python 3.11 oder neuer fehlt. Bitte von python.org installieren (Add Python to PATH).' }
    if ($LASTEXITCODE -ne 0) { throw 'Python-Umgebung konnte nicht erstellt werden.' }
}
& $pythonExe -c "import fastapi, uvicorn, aiohttp; import dealchecker"
if ($LASTEXITCODE -ne 0) {
    & $pythonExe -m pip install -r requirements.lock -e .
    if ($LASTEXITCODE -ne 0) { throw 'Installation fehlgeschlagen. Internetverbindung prüfen.' }
}
Write-Host "Oeffne im Browser: http://127.0.0.1:$Port"
& $pythonExe -m dealchecker --port $Port
exit $LASTEXITCODE
