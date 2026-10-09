param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$packageName = '火影OL完整客户端'
$distRoot = Join-Path $projectRoot 'dist_unified_client'
$packageRoot = Join-Path $distRoot $packageName
$buildRoot = Join-Path $projectRoot 'build_unified_client'
$buildPython = Join-Path $projectRoot '.build-env\Scripts\python.exe'
$desktopRoot = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('Desktop')) '可用脚本\火影OL完整客户端'))
$usableRoot = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('Desktop')) '可用脚本'))
if (-not $desktopRoot.StartsWith($usableRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unified publish path escaped usable-script folder' }
$releaseStamp = (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$backup = Join-Path $projectRoot ('logs\release_preserved\unified_' + $releaseStamp)
if (Test-Path -LiteralPath $packageRoot) {
    foreach ($item in Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -in @('state','logs') -or $_.Name -match '_(state|result|failure)\.json$' }) {
        New-Item -ItemType Directory -Path $backup -Force | Out-Null
        Copy-Item -LiteralPath $item.FullName -Destination $backup -Recurse -Force
    }
}
if (-not $SkipBuild) {
    & $buildPython -m PyInstaller --noconfirm --clean --onedir --windowed --name $packageName --distpath $distRoot --workpath $buildRoot --specpath $buildRoot (Join-Path $projectRoot 'hyol_client.py')
    if ($LASTEXITCODE -ne 0) { throw 'Unified PyInstaller build failed' }
}
if (Test-Path -LiteralPath $backup) { Get-ChildItem -LiteralPath $backup | Copy-Item -Destination $packageRoot -Recurse -Force }
& $buildPython (Join-Path $projectRoot 'tools\prepare_generic_release.py') (Join-Path $projectRoot 'assets') (Join-Path $packageRoot 'assets') --assets-only --backup (Join-Path $backup 'personal_assets_dist')
if ($LASTEXITCODE -ne 0) { throw 'Unified generic asset copy failed' }
$global:LASTEXITCODE = 0
Copy-Item -LiteralPath (Join-Path $projectRoot 'models') -Destination $packageRoot -Recurse -Force
foreach ($name in @('DAILY_Client.ini','TEAM_Client.ini','HYOL_Daily.ini','ARENA_Client.ini')) {
    $lines = Get-Content -LiteralPath (Join-Path $projectRoot $name) -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*(SharedStateDirectory|AllowMarketPurchase|MarketDirectBuyVerified)\s*=' }
    $lines += @(
        'SharedStateDirectory=%LOCALAPPDATA%\HYOLClient\state'
        'AllowMarketPurchase=0'
        'MarketDirectBuyVerified=0'
    )
    Set-Content -LiteralPath (Join-Path $packageRoot $name) -Value $lines -Encoding UTF8
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'README_统一客户端.md') -Destination $packageRoot -Force
foreach ($name in @('state','logs')) { New-Item -ItemType Directory -Path (Join-Path $packageRoot $name) -Force | Out-Null }
New-Item -ItemType Directory -Path $desktopRoot -Force | Out-Null
Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -notin @('state','logs') -and $_.Name -notmatch '_(state|result|failure)\.json$' } | Copy-Item -Destination $desktopRoot -Recurse -Force
foreach ($name in @('DAILY_Client.ini','TEAM_Client.ini','HYOL_Daily.ini','ARENA_Client.ini')) {
    $lines = Get-Content -LiteralPath (Join-Path $desktopRoot $name) -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*SharedStateDirectory\s*=' }
    $lines += ('SharedStateDirectory=' + (Join-Path $projectRoot 'state'))
    Set-Content -LiteralPath (Join-Path $desktopRoot $name) -Value $lines -Encoding UTF8
}
& $buildPython (Join-Path $projectRoot 'tools\prepare_generic_release.py') (Join-Path $projectRoot 'assets') (Join-Path $desktopRoot 'assets') --assets-only --backup (Join-Path $backup 'personal_assets_desktop')
if ($LASTEXITCODE -ne 0) { throw 'Unified desktop asset cleanup failed' }
foreach ($name in @('state','logs')) { New-Item -ItemType Directory -Path (Join-Path $desktopRoot $name) -Force | Out-Null }
$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) '火影OL完整客户端.lnk'
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $desktopRoot ($packageName + '.exe')
$shortcut.WorkingDirectory = $desktopRoot
$shortcut.Arguments = ''
$shortcut.Save()
$desktopExe = $shortcut.TargetPath
$distExe = Join-Path $packageRoot ($packageName + '.exe')
$desktopHash = (Get-FileHash -LiteralPath $desktopExe -Algorithm SHA256).Hash
$distHash = (Get-FileHash -LiteralPath $distExe -Algorithm SHA256).Hash
if ($desktopHash -ne $distHash) { throw 'Unified desktop/dist executable hash mismatch' }
$onnxFiles = @(Get-ChildItem -LiteralPath (Join-Path $desktopRoot '_internal\onnxruntime\capi') -File -Filter '*.dll')
if ($onnxFiles.Count -lt 2) { throw 'Unified CPU ONNX runtime dependencies missing' }
$inventory = @(Get-ChildItem -LiteralPath $desktopRoot -File -Recurse | Where-Object { $_.FullName -notmatch '[\\/](state|logs)[\\/]' })
$checksJson = & $buildPython (Join-Path $projectRoot 'tools\verify_packaged_source.py') $desktopExe --root $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Unified embedded source inspection failed' }
$sourceProbe = $checksJson | ConvertFrom-Json
$sourceChecks = @($sourceProbe.checks)
$sourceMatches = $sourceProbe.source_code_matches
$checksJson | Set-Content -LiteralPath (Join-Path $projectRoot ('logs\unified_source_verified_' + $releaseStamp + '.json')) -Encoding UTF8
$releaseVersion = (& $buildPython -c 'from hyol_runtime import VERSION; print(VERSION)').Trim()
$report = [ordered]@{Version=$releaseVersion;Exe=$desktopExe;Dist=$distExe;Sha256=$desktopHash;Shortcut=$shortcutPath;Arguments=$shortcut.Arguments;WorkingDirectory=$shortcut.WorkingDirectory;OnnxDlls=$onnxFiles.Count;RuntimeBytes=($inventory|Measure-Object Length -Sum).Sum;SourceModulesChecked=$sourceChecks.Count;SourceCodeMatches=$sourceMatches;GameTaskStarted=$false;GuiLaunchVerified=$false;LiveAcceptance='pending'}
$reportPath = Join-Path $projectRoot ('logs\unified_release_' + $releaseStamp + '.json')
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8
if (-not $sourceMatches) { throw 'Source changed during build; rebuild before final publication verification' }
& $buildPython (Join-Path $projectRoot 'tools\prepare_generic_release.py') $packageRoot (Join-Path $projectRoot ('releases\unified_' + $releaseStamp)) --root $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Unified clean release preparation failed' }
Write-Output ('Unified release hash and CPU dependencies verified: ' + $desktopHash)
Write-Output $desktopRoot

