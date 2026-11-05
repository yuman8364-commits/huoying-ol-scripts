param(
    [ValidateRange(1,10)]
    [int]$Victories = 1
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimePython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    throw 'Bundled Python runtime is unavailable.'
}
# Unicode escapes avoid Windows PowerShell 5.1 UTF-8 decoding differences.
$moonAccount = '74' + [char]0x533A + [char]0x6708 + [char]0x5149
& $runtimePython (Join-Path $projectRoot 'run_panel_command_once.py') arena --current-account $moonAccount --current-tab-x 180 --arena-victories $Victories
exit $LASTEXITCODE
