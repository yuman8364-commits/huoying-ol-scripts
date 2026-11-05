param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$buildPython = Join-Path $projectRoot '.build-env\Scripts\python.exe'
$packageName = '火影OL组队任务通用版'
$distRoot = Join-Path $projectRoot 'dist_team_client'
$buildRoot = Join-Path $projectRoot 'build_team_client'
$packageRoot = Join-Path $distRoot $packageName
$runtimeBackup = Join-Path $projectRoot ('logs\release_preserved\team_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8))
if (Test-Path -LiteralPath $packageRoot) {
    foreach ($runtimeItem in Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -in @('state','logs') -or $_.Name -match '_(state|result|failure)\.json$' }) {
        New-Item -ItemType Directory -Path $runtimeBackup -Force | Out-Null
        Copy-Item -LiteralPath $runtimeItem.FullName -Destination $runtimeBackup -Recurse -Force
    }
}
if (-not $SkipBuild) {
    & $buildPython -m PyInstaller --noconfirm --clean --onedir --windowed --name $packageName --distpath $distRoot --workpath $buildRoot --specpath $buildRoot (Join-Path $projectRoot 'hyol_team_tasks.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
}
if (Test-Path -LiteralPath $runtimeBackup) {
    Get-ChildItem -LiteralPath $runtimeBackup | Copy-Item -Destination $packageRoot -Recurse -Force
}
$assetRoot = Join-Path $packageRoot 'assets'
New-Item -ItemType Directory -Path $assetRoot -Force | Out-Null
& $buildPython (Join-Path $projectRoot 'tools\prepare_generic_release.py') (Join-Path $projectRoot 'assets') $assetRoot --assets-only --backup (Join-Path $runtimeBackup 'personal_assets_dist')
if ($LASTEXITCODE -ne 0) { throw 'Team generic asset copy failed' }
$global:LASTEXITCODE = 0
Copy-Item -LiteralPath (Join-Path $projectRoot 'TEAM_TASKS_README.md') -Destination $packageRoot -Force
foreach ($configName in @('TEAM_Client.ini','DAILY_Client.ini','HYOL_Daily.ini','ARENA_Client.ini')) {
    $configLines = @(Get-Content -LiteralPath (Join-Path $projectRoot $configName) -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*(SharedStateDirectory|AllowMarketPurchase|MarketDirectBuyVerified)\s*=' })
    $configLines += @('SharedStateDirectory=%LOCALAPPDATA%\HYOLClient\state', 'AllowMarketPurchase=0', 'MarketDirectBuyVerified=0')
    Set-Content -LiteralPath (Join-Path $packageRoot $configName) -Value $configLines -Encoding UTF8
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'YOLO_WORKFLOW.md') -Destination $packageRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'models') -Destination $packageRoot -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'state') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'logs') -Force | Out-Null
$usableRoot = Join-Path ([Environment]::GetFolderPath('Desktop')) '可用脚本'
$desktopRoot = Join-Path $usableRoot '火影OL组队任务'
New-Item -ItemType Directory -Path $desktopRoot -Force | Out-Null
$resolvedDesktopRoot = [IO.Path]::GetFullPath($desktopRoot)
$resolvedUsableRoot = [IO.Path]::GetFullPath($usableRoot)
if (-not $resolvedDesktopRoot.StartsWith($resolvedUsableRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Team client publish path escaped the usable-script folder'
}
Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -notin @('state', 'logs') -and $_.Name -notmatch '_(state|result|failure)\.json$' } | Copy-Item -Destination $desktopRoot -Recurse -Force
foreach ($name in @('DAILY_Client.ini','TEAM_Client.ini','HYOL_Daily.ini','ARENA_Client.ini')) {
    $lines = Get-Content -LiteralPath (Join-Path $desktopRoot $name) -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*SharedStateDirectory\s*=' }
    $lines += ('SharedStateDirectory=' + (Join-Path $projectRoot 'state'))
    Set-Content -LiteralPath (Join-Path $desktopRoot $name) -Value $lines -Encoding UTF8
}
& $buildPython (Join-Path $projectRoot 'tools\prepare_generic_release.py') (Join-Path $projectRoot 'assets') (Join-Path $desktopRoot 'assets') --assets-only --backup (Join-Path $runtimeBackup 'personal_assets_desktop')
if ($LASTEXITCODE -ne 0) { throw 'Team desktop asset cleanup failed' }
$linkShell = New-Object -ComObject WScript.Shell
$link = $linkShell.CreateShortcut((Join-Path $usableRoot '火影OL组队任务.lnk'))
$link.TargetPath = Join-Path $desktopRoot ($packageName+'.exe')
$link.WorkingDirectory = $desktopRoot
$link.Save()
& $buildPython (Join-Path $projectRoot 'tools\prepare_generic_release.py') $packageRoot (Join-Path $projectRoot ('releases\team_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))) --root $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Team clean release preparation failed' }
Get-FileHash -LiteralPath (Join-Path $desktopRoot ($packageName+'.exe')) -Algorithm SHA256
Write-Output $desktopRoot
