param([ValidateSet('arena','bond','all')][string]$Client = 'all')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$buildPython = Join-Path $projectRoot '.build-env\Scripts\python.exe'
$usableRoot = Join-Path ([Environment]::GetFolderPath('Desktop')) '可用脚本'
$entries = @(
    @{Key='arena';Name='火影OL竞技场通用v5';Source='hyol_arena.py';Dist='dist_arena_generic_v10';Folder='火影OL竞技场';Readme='ARENA_CLIENT_README.md';Config='ARENA_Client.ini'},
    @{Key='bond';Name='火影OL羁绊连续作战v1';Source='hyol_bond.py';Dist='dist_bond_v1';Folder='火影OL羁绊';Readme='BOND_CLIENT_README.md';Config='HYOL_Daily.ini'}
)
foreach ($entry in $entries) {
    if ($Client -ne 'all' -and $Client -ne $entry.Key) { continue }
    $distRoot = Join-Path $projectRoot $entry.Dist
    $packageRoot = Join-Path $distRoot $entry.Name
    $backup = Join-Path $projectRoot ('logs\release_preserved\' + $entry.Key + '_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8))
    if (Test-Path -LiteralPath $packageRoot) {
        foreach ($item in Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -in @('state','logs') -or $_.Name -match '_(state|result|failure)\.json$' }) {
            New-Item -ItemType Directory -Path $backup -Force | Out-Null
            Copy-Item -LiteralPath $item.FullName -Destination $backup -Recurse -Force
        }
    }
    & $buildPython -m PyInstaller --noconfirm --clean --onedir --windowed --name $entry.Name --distpath $distRoot --workpath (Join-Path $projectRoot ('build_' + $entry.Key + '_current')) --specpath (Join-Path $projectRoot ('build_' + $entry.Key + '_current')) (Join-Path $projectRoot $entry.Source)
    if ($LASTEXITCODE -ne 0) { throw ($entry.Key + ' build failed') }
    if (Test-Path -LiteralPath $backup) { Get-ChildItem -LiteralPath $backup | Copy-Item -Destination $packageRoot -Recurse -Force }
    robocopy (Join-Path $projectRoot 'assets') (Join-Path $packageRoot 'assets') /E | Out-Null
    if ($LASTEXITCODE -gt 7) { throw 'Asset copy failed' }
    $global:LASTEXITCODE = 0
    $configLines = @(Get-Content -LiteralPath (Join-Path $projectRoot $entry.Config) -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*(SharedStateDirectory|AllowMarketPurchase|MarketDirectBuyVerified)\s*=' })
    $configLines += @(('SharedStateDirectory=' + (Join-Path $projectRoot 'state')), 'AllowMarketPurchase=0', 'MarketDirectBuyVerified=0')
    Set-Content -LiteralPath (Join-Path $packageRoot $entry.Config) -Value $configLines -Encoding UTF8
    Copy-Item -LiteralPath (Join-Path $projectRoot $entry.Readme) -Destination $packageRoot -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'models') -Destination $packageRoot -Recurse -Force
    $desktopRoot = [IO.Path]::GetFullPath((Join-Path $usableRoot $entry.Folder))
    if (-not $desktopRoot.StartsWith([IO.Path]::GetFullPath($usableRoot) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Publish path escaped usable-script folder' }
    New-Item -ItemType Directory -Path $desktopRoot -Force | Out-Null
    Get-ChildItem -LiteralPath $packageRoot | Where-Object { $_.Name -notin @('state','logs') -and $_.Name -notmatch '_(state|result|failure)\.json$' } | Copy-Item -Destination $desktopRoot -Recurse -Force
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $usableRoot ($entry.Folder + '.lnk')))
    $link.TargetPath = Join-Path $desktopRoot ($entry.Name + '.exe')
    $link.WorkingDirectory = $desktopRoot
    $link.Save()
    Get-FileHash -LiteralPath $link.TargetPath -Algorithm SHA256
}
