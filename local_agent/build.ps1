$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Spec = Join-Path $Root "SysvarLocalAgent.spec"
$Dist = Join-Path $Root "dist\SysvarLocalAgent"
$Build = Join-Path $Root "build\SysvarLocalAgent"

$PythonCommand = $null
if (Test-Path $VenvPython) {
    & $VenvPython -c "pass" *> $null
    if ($LASTEXITCODE -eq 0) {
        $PythonCommand = @($VenvPython)
    }
}
if (-not $PythonCommand) {
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        $PythonCommand = @($PyLauncher.Source, "-3.12")
    } else {
        $SystemPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($SystemPython) {
            $PythonCommand = @($SystemPython.Source)
        } else {
            $DefaultPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
            if (Test-Path $DefaultPython) {
                $PythonCommand = @($DefaultPython)
            }
        }
    }
}
if (-not $PythonCommand) {
    throw "Python de build não encontrado. Instale Python no ambiente de desenvolvimento ou recrie local_agent\.venv."
}

Remove-Item -LiteralPath $Dist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $Build -Recurse -Force -ErrorAction SilentlyContinue

$env:PYTHONPATH = @(
    $Root
    (Join-Path $Root ".venv\Lib\site-packages")
    (Join-Path $Root ".venv\Lib\site-packages\win32")
    (Join-Path $Root ".venv\Lib\site-packages\win32\lib")
    (Join-Path $Root ".venv\Lib\site-packages\pythonwin")
) -join [IO.Path]::PathSeparator

Push-Location $Root
$PythonExe = $PythonCommand[0]
$PythonArgs = @()
if ($PythonCommand.Count -gt 1) {
    $PythonArgs = $PythonCommand[1..($PythonCommand.Count - 1)]
}
& $PythonExe @PythonArgs "-m" "PyInstaller" "--clean" "--noconfirm" $Spec
$ExitCode = $LASTEXITCODE
Pop-Location
if ($ExitCode -ne 0) {
    exit $ExitCode
}

$Exe = Join-Path $Dist "SysvarLocalAgent.exe"
if (-not (Test-Path $Exe)) {
    throw "Build concluída sem gerar $Exe"
}

Write-Host "Executável gerado em: $Exe"
