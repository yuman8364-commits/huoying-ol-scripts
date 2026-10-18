param(
    [ValidateSet('mail','welfare','ranking','survival','quiz','arena','weekend','treasure')]
    [string[]]$Tasks = @('mail','welfare','ranking','survival','quiz','arena','weekend'),
    [ValidateRange(1,10)]
    [int]$ArenaWins = 5,
    [ValidateRange(1,30)]
    [int]$AccountCount = 6,
    [ValidateSet('keep','main','fast')]
    [string]$Formation = 'keep'
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.build-env\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw '缺少项目 Python 环境：.build-env\Scripts\python.exe'
}
& $python (Join-Path $projectRoot 'hyol_multi_solo.py') --tasks @Tasks --arena-wins $ArenaWins --account-count $AccountCount --formation $Formation
exit $LASTEXITCODE
