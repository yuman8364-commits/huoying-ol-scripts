param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$buildPython = Join-Path $projectRoot '.build-env\Scripts\python.exe'
$packageName = '火影OL日常三合一'
$distRoot = Join-Path $projectRoot 'dist_daily'
$buildRoot = Join-Path $projectRoot 'build_daily'
$packageRoot = Join-Path $distRoot $packageName
$runtimeBackup = Join-Path $projectRoot ('logs\release_preserved\daily_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8))
if (Test-Path -LiteralPath $packageRoot) {
    foreach ($runtimeItem in Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -in @('state','logs') -or $_.Name -match '_(state|result|failure)\.json$' }) {
        New-Item -ItemType Directory -Path $runtimeBackup -Force | Out-Null
        Copy-Item -LiteralPath $runtimeItem.FullName -Destination $runtimeBackup -Recurse -Force
    }
}
if (-not $SkipBuild) {
    & $buildPython -m PyInstaller --noconfirm --clean --onedir --windowed --name $packageName --distpath $distRoot --workpath $buildRoot --specpath $buildRoot (Join-Path $projectRoot 'hyol_daily_tasks.py')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
}
if (Test-Path -LiteralPath $runtimeBackup) {
    Get-ChildItem -LiteralPath $runtimeBackup | Copy-Item -Destination $packageRoot -Recurse -Force
}
$assetRoot = Join-Path $packageRoot 'assets'
New-Item -ItemType Directory -Path $assetRoot -Force | Out-Null
robocopy (Join-Path $projectRoot 'assets') $assetRoot /E | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Asset copy failed: $LASTEXITCODE" }
$global:LASTEXITCODE = 0
$configLines = @(Get-Content -LiteralPath (Join-Path $projectRoot 'DAILY_Client.ini') -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*(SharedStateDirectory|AllowMarketPurchase|MarketDirectBuyVerified)\s*=' })
$configLines += @(('SharedStateDirectory=' + (Join-Path $projectRoot 'state')), 'AllowMarketPurchase=0', 'MarketDirectBuyVerified=0')
Set-Content -LiteralPath (Join-Path $packageRoot 'DAILY_Client.ini') -Value $configLines -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $projectRoot 'DAILY_TASKS_README.md') -Destination $packageRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'YOLO_WORKFLOW.md') -Destination $packageRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'models') -Destination $packageRoot -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'state') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'logs') -Force | Out-Null
$usableRoot = Join-Path ([Environment]::GetFolderPath('Desktop')) '可用脚本'
$desktopRoot = Join-Path $usableRoot '火影OL日常任务'
New-Item -ItemType Directory -Path $desktopRoot -Force | Out-Null
$resolvedDesktopRoot = [IO.Path]::GetFullPath($desktopRoot)
$resolvedUsableRoot = [IO.Path]::GetFullPath($usableRoot)
if (-not $resolvedDesktopRoot.StartsWith($resolvedUsableRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Daily client publish path escaped the usable-script folder'
}
# Keep identity materials needed to reconnect the shared named checkpoints.
$staleConfig = Join-Path $resolvedDesktopRoot 'HYOL_Daily.ini'
if (Test-Path -LiteralPath $staleConfig -PathType Leaf) { Remove-Item -LiteralPath $staleConfig -Force }
Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -notin @('state', 'logs') -and $_.Name -notmatch '_(state|result|failure)\.json$' } | Copy-Item -Destination $desktopRoot -Recurse -Force
$linkShell = New-Object -ComObject WScript.Shell
$launchModes = @{'组队副本'='group';'生存演习'='survival';'通缉任务'='wanted';'日常三合一'='all';'单人日常'='all';'忍者测验'='quiz';'排位赛'='ranking';'周末任务'='weekend';'五分钟挖宝'='treasure'}
foreach ($label in $launchModes.Keys) {
    $link = $linkShell.CreateShortcut((Join-Path $desktopRoot ($label+'.lnk')))
    $link.TargetPath = Join-Path $desktopRoot ($packageName+'.exe')
    $link.Arguments = '--task '+$launchModes[$label]
    $link.WorkingDirectory = $desktopRoot
    $link.Save()
    $outerLink = $linkShell.CreateShortcut((Join-Path $usableRoot ('火影OL'+$label+'.lnk')))
    $outerLink.TargetPath = Join-Path $desktopRoot ($packageName+'.exe')
    $outerLink.Arguments = '--task '+$launchModes[$label]
    $outerLink.WorkingDirectory = $desktopRoot
    $outerLink.Save()
}
Get-FileHash -LiteralPath (Join-Path $desktopRoot ($packageName+'.exe')) -Algorithm SHA256
Write-Output $desktopRoot
