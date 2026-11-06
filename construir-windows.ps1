param([string]$Python)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:TEMP = Join-Path $PSScriptRoot '.build-tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null

if (-not $Python) {
    python -m venv .build-env
    if ($LASTEXITCODE) { throw 'No se pudo crear el entorno Python.' }
    $Python = Join-Path $PSScriptRoot '.build-env\Scripts\python.exe'
    & $Python -m pip install -r requirements.txt
    if ($LASTEXITCODE) { throw 'No se pudieron instalar las dependencias.' }
}

& $Python -m PyInstaller --noconfirm --noupx --windowed --onedir --name BrisaDock --version-file (Join-Path $PSScriptRoot 'version_info.txt') --distpath dist --workpath .build --specpath .build dock.py
if ($LASTEXITCODE) { throw 'Falló la compilación de BrisaDock.' }

# PyInstaller descubre este complemento opcional de Qt. BrisaDock usa la entrada nativa.
@(
    'dist\BrisaDock\_internal\PySide6\plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll',
    'dist\BrisaDock\_internal\Qt6VirtualKeyboard.dll'
) | ForEach-Object {
    if (Test-Path -LiteralPath $_ -PathType Leaf) { Remove-Item -LiteralPath $_ }
}
Copy-Item LICENSE,THIRD_PARTY 'dist\BrisaDock' -Recurse -Force

$binary = Get-Item 'dist\BrisaDock\BrisaDock.exe'
if ($binary.VersionInfo.ProductName -ne 'BrisaDock' -or $binary.VersionInfo.ProductVersion -ne '0.38.0.0') {
    throw 'Los metadatos PE no corresponden a BrisaDock 0.38.'
}


