$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$usable = Join-Path $desktop '可用脚本'
$pythonw = Join-Path $projectRoot '.build-env\Scripts\pythonw.exe'
$soloGui = Join-Path $projectRoot 'hyol_multi_solo_gui.py'
$weekendGui = Join-Path $projectRoot 'hyol_weekend_gui.py'
$dailyExe = Join-Path $usable '火影OL日常任务\火影OL日常三合一.exe'
$teamExe = Join-Path $usable '火影OL组队任务\火影OL组队任务通用版.exe'
foreach ($path in @($pythonw, $soloGui, $weekendGui, $dailyExe, $teamExe)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "缺少入口文件：$path" }
}
$shell = New-Object -ComObject WScript.Shell
$entries = @(
    @{ Name='火影OL多人脚本'; Target=$teamExe; Args=''; Working=(Split-Path $teamExe); Hotkey='CTRL+ALT+1' },
    @{ Name='火影OL单人脚本'; Target=$dailyExe; Args='--task all'; Working=(Split-Path $dailyExe); Hotkey='CTRL+ALT+2' },
    @{ Name='火影OL多账号单人脚本'; Target=$pythonw; Args=('"' + $soloGui + '"'); Working=$projectRoot; Hotkey='CTRL+ALT+3' },
    @{ Name='火影OL周末任务'; Target=$pythonw; Args=('"' + $weekendGui + '"'); Working=$projectRoot; Hotkey='CTRL+ALT+4' }
)
foreach ($entry in $entries) {
    $link = $shell.CreateShortcut((Join-Path $desktop ($entry.Name + '.lnk')))
    $link.TargetPath = $entry.Target
    $link.Arguments = $entry.Args
    $link.WorkingDirectory = $entry.Working
    $link.Hotkey = $entry.Hotkey
    $link.Save()
    $copy = $shell.CreateShortcut((Join-Path $usable ($entry.Name + '.lnk')))
    $copy.TargetPath = $entry.Target
    $copy.Arguments = $entry.Args
    $copy.WorkingDirectory = $entry.Working
    $copy.Save()
}
# Keep the older multi-solo shortcut useful for people who already pinned it.
$old = $shell.CreateShortcut((Join-Path $usable '火影OL多账号单人日常.lnk'))
$old.TargetPath = $pythonw
$old.Arguments = '"' + $soloGui + '"'
$old.WorkingDirectory = $projectRoot
$old.Save()
Write-Output '四个桌面快捷方式及旧多账号入口已更新。'
