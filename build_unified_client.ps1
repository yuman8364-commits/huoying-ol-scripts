param(
    [switch]$SkipBuild,
    [switch]$WithVision,
    [switch]$InstallDesktop,
    [string]$PythonPath,
    [string]$OutputRoot
)
$ErrorActionPreference = 'Stop'
if (-not $PythonPath) { $PythonPath = Join-Path $PSScriptRoot '.build-env\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Build Python not found; use -PythonPath or create .build-env' }
if (-not $OutputRoot) {
    $profile = if ($WithVision) { 'vision' } else { 'standard' }
    $OutputRoot = Join-Path $PSScriptRoot ('build-output\' + $profile + '_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
}
$buildArguments = @((Join-Path $PSScriptRoot 'tools\build_unified_package.py'), '--output', $OutputRoot)
if ($SkipBuild) { $buildArguments += '--skip-build' }
if ($WithVision) { $buildArguments += '--with-vision' }
if ($InstallDesktop) { $buildArguments += '--install-desktop' }
& $PythonPath @buildArguments
if ($LASTEXITCODE -ne 0) { throw 'Unified package build or verification failed' }
