param(
    [ValidateSet('recognize','independent','team','full','all_day','auto')]
    [string]$Stage = 'auto',
    [switch]$Enroll,
    [switch]$NoContentClicks
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$commandPython = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
$python = if (Test-Path -LiteralPath $bundledPython) { $bundledPython } elseif ($commandPython -and (Test-Path -LiteralPath $commandPython)) { $commandPython } else { $null }
if (-not $python) {
    throw '未找到可用的 Python。请安装 Python 3，或从 Codex 环境运行本脚本。'
}
$arguments = @((Join-Path $root 'hyol_scheduler.py'), '--stage', $Stage)
if ($Enroll) { $arguments += '--enroll' }
if ($NoContentClicks) { $arguments += '--no-content-clicks' }

Write-Host "HYOL scheduler stage=$Stage enroll=$($Enroll.IsPresent) no_content_clicks=$($NoContentClicks.IsPresent)"
Write-Host 'F12 is the global emergency stop.'
& $python @arguments
exit $LASTEXITCODE
