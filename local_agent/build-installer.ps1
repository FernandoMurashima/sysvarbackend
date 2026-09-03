$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistExe = Join-Path $Root "dist\SysvarLocalAgent\SysvarLocalAgent.exe"
$Iss = Join-Path $Root "installer\SysvarLocalAgent.iss"
$Output = Join-Path $Root "installer\output\SysvarLocalAgent-Setup-0.2.0.exe"

if (-not (Test-Path $DistExe)) {
    throw "Distribuição do Agent não encontrada: $DistExe. Execute .\build.ps1 antes."
}

$Candidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 5\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) }
$Candidates = @($Candidates)

$Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($Command) {
    $Candidates = @($Command.Source) + @($Candidates)
}
$Candidates = @($Candidates)

if (-not $Candidates) {
    throw "Inno Setup Compiler (ISCC.exe) não encontrado. Instale o Inno Setup para gerar o Setup.exe."
}

$Iscc = $Candidates[0]
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    throw "Caminho inválido para Inno Setup Compiler (ISCC.exe): $Iscc"
}
& $Iscc $Iss
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not (Test-Path $Output)) {
    throw "Build do instalador terminou sem gerar: $Output"
}

Write-Host "Instalador gerado em: $Output"
