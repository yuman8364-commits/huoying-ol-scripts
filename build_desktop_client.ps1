param(
    [string]$PythonPath = '.\.build-env\Scripts\python.exe',
    [string]$OutputRoot = '',
    [switch]$WithVision
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $OutputRoot) { $OutputRoot = Join-Path $PSScriptRoot ('releases\desktop-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$desktopArgs = @('-B', (Join-Path $PSScriptRoot 'tools\build_desktop_package.py'), '--output', $OutputRoot)
if ($WithVision) { $desktopArgs += '--with-vision' }
& $PythonPath @desktopArgs
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
