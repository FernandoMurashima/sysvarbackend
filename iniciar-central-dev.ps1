$ErrorActionPreference = "Stop"

$backend = $PSScriptRoot
$frontend = [System.IO.Path]::GetFullPath(
    (Join-Path $backend "..\Frontend\sysvar")
)

if (-not (Test-Path (Join-Path $backend "venv\Scripts\python.exe"))) {
    throw "Venv do Central não encontrado."
}

if (-not (Test-Path (Join-Path $frontend "package.json"))) {
    throw "Frontend Central não encontrado."
}

# Aplica migrations existentes antes de iniciar o Backend e o Frontend.
$python = Join-Path $backend "venv\Scripts\python.exe"
Write-Host "Aplicando migrations pendentes do Central..."
Push-Location -LiteralPath $backend
try {
    & $python manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao aplicar migrations do Central. Inicialização interrompida."
    }
}
finally {
    Pop-Location
}

$backendCommand = "Set-Location -LiteralPath '$backend'; & '.\venv\Scripts\python.exe' manage.py runserver 127.0.0.1:8001"
$frontendCommand = "Set-Location -LiteralPath '$frontend'; npm start"

Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $backendCommand
Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand