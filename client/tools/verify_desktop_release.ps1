param(
    [string]$ExpectedVersion = '2026.10.05.16',
    [Parameter(Mandatory=$true)][string]$RuntimeSnapshot,
    [switch]$SkipGui,
    [string]$OutputReport,
    [string[]]$PackageKeys
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildPython = Join-Path $projectRoot '.build-env\Scripts\python.exe'
$desktop = [Environment]::GetFolderPath('Desktop')
$usable = Join-Path $desktop '可用脚本'
$shell = New-Object -ComObject WScript.Shell
if (-not $OutputReport) { $OutputReport = Join-Path $projectRoot ('logs\release_verified_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '.json') }
if (Test-Path -LiteralPath $OutputReport) { throw 'Preserve prior release evidence; select a new report path' }
if (-not (Test-Path -LiteralPath $RuntimeSnapshot -PathType Leaf)) { throw 'A current pre-publication runtime snapshot is required' }
$snapshot = Get-Content -LiteralPath $RuntimeSnapshot -Raw -Encoding UTF8 | ConvertFrom-Json
if ($snapshot.schema_version -ne 1 -or $null -eq $snapshot.roots -or $null -eq $snapshot.files) { throw 'Runtime snapshot schema is not current; do not reuse the 2026-10-03 legacy snapshot' }

function Assert-FileCopies {
    param([string]$First,[string]$Second,[string]$Context)
    $hash = (Get-FileHash -LiteralPath $First -Algorithm SHA256).Hash
    if ($hash -ne (Get-FileHash -LiteralPath $Second -Algorithm SHA256).Hash) { throw ('File hash mismatch: '+$Context) }
    return $hash
}

function Open-ShortcutWindow {
    param([string]$ShortcutPath,[string]$TargetExe,[string]$ExpectedTitle)
    $oldPids = @(Get-Process | Select-Object -ExpandProperty Id)
    $openedPids = [Collections.Generic.HashSet[int]]::new()
    # Open the real shortcut with its saved arguments, as desktop double-click does.
    $null = $shell.Run(('"'+$ShortcutPath+'"'), 1, $false)
    $clientProcess = $null
    $deadline = (Get-Date).AddSeconds(20)
    try {
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 250
            # The Windows venv redirector owns no window: the actual pythonw
            # GUI is its child in the base runtime. Follow only descendants of
            # our new shortcut target, never another user's existing process.
            $processTree = @(Get-CimInstance Win32_Process)
            foreach ($row in $processTree) {
                if ($row.ExecutablePath -eq $TargetExe -and $row.ProcessId -notin $oldPids) {
                    $null = $openedPids.Add([int]$row.ProcessId)
                }
            }
            do {
                $changed = $false
                foreach ($row in $processTree) {
                    if ($row.ProcessId -notin $oldPids -and $openedPids.Contains([int]$row.ParentProcessId)) {
                        if ($openedPids.Add([int]$row.ProcessId)) { $changed = $true }
                    }
                }
            } while ($changed)
            $candidates = @($openedPids | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
            foreach ($candidate in $candidates) {
                $candidate.Refresh()
                if ($candidate.MainWindowHandle -ne 0) { $clientProcess=$candidate;break }
            }
            if ($null -ne $clientProcess) { break }
        }
        if ($null -eq $clientProcess) { throw ('GUI failed to open: '+$ShortcutPath) }
        $clientProcess.Refresh()
        if (-not $clientProcess.Responding -or -not $clientProcess.MainWindowTitle.Contains($ExpectedTitle)) { throw ('GUI title/response failed: '+$ShortcutPath+' '+$clientProcess.MainWindowTitle) }
        return [pscustomobject]@{Title=$clientProcess.MainWindowTitle;Responding=$true;ProcessId=$clientProcess.Id;MainWindowHandle=$clientProcess.MainWindowHandle.ToInt64();GuiLaunchVerified=$true}
    } finally {
        # Also clean up a redirector's child after a failed launch check.
        # Existing windows and unrelated interpreters are never included.
        foreach ($openedPid in $openedPids) {
            $openedProcess = Get-Process -Id $openedPid -ErrorAction SilentlyContinue
            if ($null -ne $openedProcess -and -not $openedProcess.HasExited -and $openedProcess.MainWindowHandle -ne 0) {
                $null = $openedProcess.CloseMainWindow()
                if (-not $openedProcess.WaitForExit(5000)) { Stop-Process -Id $openedProcess.Id }
            }
        }
    }
}

$packages = @(
    @{Key='daily';Name='火影OL日常三合一';Dist='dist_daily';Folder='火影OL日常任务';Link=(Join-Path $desktop '火影OL单人脚本.lnk');Configs=@('DAILY_Client.ini');Arguments='--task all'},
    @{Key='team';Name='火影OL组队任务通用版';Dist='dist_team_client';Folder='火影OL组队任务';Link=(Join-Path $desktop '火影OL多人脚本.lnk');Configs=@('TEAM_Client.ini','DAILY_Client.ini','HYOL_Daily.ini','ARENA_Client.ini');Arguments=''},
    @{Key='arena';Name='火影OL竞技场通用v5';Dist='dist_arena_generic_v10';Folder='火影OL竞技场';Link=(Join-Path $usable '火影OL竞技场.lnk');Configs=@('ARENA_Client.ini');Arguments=''},
    @{Key='bond';Name='火影OL羁绊连续作战v1';Dist='dist_bond_v1';Folder='火影OL羁绊';Link=(Join-Path $usable '火影OL羁绊.lnk');Configs=@('HYOL_Daily.ini');Arguments=''},
    @{Key='unified';Name='火影OL完整客户端';Dist='dist_unified_client';Folder='火影OL完整客户端';Link=(Join-Path $desktop '火影OL完整客户端.lnk');Configs=@('DAILY_Client.ini','TEAM_Client.ini','HYOL_Daily.ini','ARENA_Client.ini');Arguments=''}
)
$assetRoot = Join-Path $projectRoot 'assets'
if ($PackageKeys) {
    foreach ($packageKey in $PackageKeys) { if ($packageKey -notin @($packages | ForEach-Object { $_.Key })) { throw ('Unknown package key: '+$packageKey) } }
    $packages = @($packages | Where-Object { $_.Key -in $PackageKeys })
}
$sourceAssets = @(Get-ChildItem -LiteralPath $assetRoot -Recurse -File | Where-Object {
    $_.FullName -notmatch '[\\/]accounts[\\/]' -and $_.Name -notmatch '(\.source\.json$|^friend_.+_row\.|^team\d+_(two|three)_member_anchor\.|^team_invite_from_)'
} | ForEach-Object { [pscustomobject]@{Relative=[IO.Path]::GetRelativePath($assetRoot,$_.FullName);Hash=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash} })
$rows = @()
$sources = @()
$runtime = $null
$failure = $null
try {
    foreach ($entry in $packages) {
        $folder = Join-Path $usable $entry.Folder
        $distFolder = Join-Path (Join-Path $projectRoot $entry.Dist) $entry.Name
        $exe = Join-Path $folder ($entry.Name+'.exe')
        $distExe = Join-Path $distFolder ($entry.Name+'.exe')
        if (-not (Test-Path -LiteralPath $entry.Link -PathType Leaf)) { throw ('Shortcut missing: '+$entry.Key) }
        $link = $shell.CreateShortcut($entry.Link)
        if ($link.TargetPath -ne $exe -or $link.WorkingDirectory -ne $folder -or $link.Arguments.Trim() -ne $entry.Arguments) { throw ('Shortcut target/arguments mismatch: '+$entry.Key) }
        $hash = Assert-FileCopies $exe $distExe ($entry.Key+' EXE')
        foreach ($configName in $entry.Configs) {
            $config = @(Get-Content -LiteralPath (Join-Path $folder $configName) -Encoding UTF8)
            foreach ($setting in @(@{Key='SharedStateDirectory';Value=(Join-Path $projectRoot 'state')},@{Key='AllowMarketPurchase';Value='0'},@{Key='MarketDirectBuyVerified';Value='0'})) {
                $matching = @($config | Where-Object { $_ -match ('^\s*'+[regex]::Escape($setting.Key)+'\s*=') })
                if ($matching.Count -ne 1 -or ($matching[0] -split '=',2)[1].Trim() -ne $setting.Value) { throw ('Config safety/shared-state mismatch: '+$entry.Key+' '+$configName+' '+$setting.Key) }
            }
            $distConfig = @(Get-Content -LiteralPath (Join-Path $distFolder $configName) -Encoding UTF8)
            if ($entry.Key -in @('team','unified')) {
                if (@($distConfig | Where-Object { $_ -eq 'SharedStateDirectory=%LOCALAPPDATA%\HYOLClient\state' }).Count -ne 1) { throw 'Clean dist must use per-user state' }
                $localCommon = @($config | Where-Object { $_ -notmatch '^\s*SharedStateDirectory\s*=' })
                $distCommon = @($distConfig | Where-Object { $_ -notmatch '^\s*SharedStateDirectory\s*=' })
                if ((Compare-Object $localCommon $distCommon).Count) { throw 'Config differences exceed the explicit local-upgrade state path' }
            } else {
                $null = Assert-FileCopies (Join-Path $folder $configName) (Join-Path $distFolder $configName) ($entry.Key+' '+$configName)
            }
        }
        $dlls = @(Get-ChildItem -LiteralPath (Join-Path $folder '_internal\onnxruntime') -Recurse -File -Filter '*onnxruntime*.dll')
        if ($dlls.Count -lt 2) { throw ('CPU model dependencies missing: '+$entry.Key) }
        foreach ($dependency in $dlls) {
            $relative = [IO.Path]::GetRelativePath($folder,$dependency.FullName)
            $null = Assert-FileCopies $dependency.FullName (Join-Path $distFolder $relative) ($entry.Key+' '+$relative)
        }
        $manifestPath = Join-Path $folder 'models\detector.json'
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($manifest.mode -ne 'shadow' -or @($manifest.click_validated_targets).Count -ne 0) { throw ('Unexpected model click authorization: '+$entry.Key) }
        $modelRoot = [IO.Path]::GetFullPath((Join-Path $folder 'models'))
        $modelPath = [IO.Path]::GetFullPath((Join-Path $modelRoot $manifest.file))
        if (-not $modelPath.StartsWith($modelRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Model path escaped models folder' }
        if ((Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256) { throw ('Model hash mismatch: '+$entry.Key) }
        $null = Assert-FileCopies $manifestPath (Join-Path $projectRoot 'models\detector.json') ($entry.Key+' current model manifest')
        $null = Assert-FileCopies $manifestPath (Join-Path $distFolder 'models\detector.json') ($entry.Key+' model manifest')
        $null = Assert-FileCopies $modelPath (Join-Path (Join-Path $distFolder 'models') $manifest.file) ($entry.Key+' ONNX model')
        foreach ($asset in $sourceAssets) {
            if ($asset.Hash -ne (Get-FileHash -LiteralPath (Join-Path (Join-Path $folder 'assets') $asset.Relative) -Algorithm SHA256).Hash) { throw ('Current asset mismatch: '+$entry.Key+' '+$asset.Relative) }
        }
        $probeJson = & $buildPython (Join-Path $PSScriptRoot 'verify_packaged_source.py') $exe --root $projectRoot
        if ($LASTEXITCODE -ne 0) { throw ('Frozen source differs from current project: '+$entry.Key) }
        $probe = $probeJson | ConvertFrom-Json
        $gui = if ($SkipGui) { [pscustomobject]@{Title=$null;Responding=$null;GuiLaunchVerified=$false} } else { Open-ShortcutWindow $entry.Link $exe $ExpectedVersion }
        $inventory = @(Get-ChildItem -LiteralPath $folder -Recurse -File | Where-Object { $_.FullName -notmatch '[\\/](state|logs)[\\/]' -and $_.Name -notmatch '_(state|result|failure)\.json$' })
        $rows += [pscustomobject]@{Client=$entry.Key;Exe=$exe;Dist=$distExe;Sha256=$hash;Shortcut=$entry.Link;Arguments=$link.Arguments;Hotkey=$link.Hotkey;Title=$gui.Title;Responding=$gui.Responding;GuiLaunchVerified=$gui.GuiLaunchVerified;OnnxDlls=$dlls.Count;ModelSha256=$manifest.sha256;ModelMode=$manifest.mode;AssetsChecked=$sourceAssets.Count;SourceModulesChecked=$probe.modules_checked;SourceCodeMatches=$probe.source_code_matches;SourceChecks=$probe.checks;RuntimeBytes=($inventory|Measure-Object Length -Sum).Sum;GameTaskStarted=$false}
    }
    foreach ($sourceEntry in @(@{Name='火影OL多账号单人脚本';File='hyol_multi_solo_gui.py'},@{Name='火影OL周末任务';File='hyol_weekend_gui.py'})) {
        $linkPath = Join-Path $desktop ($sourceEntry.Name+'.lnk')
        if (-not (Test-Path -LiteralPath $linkPath -PathType Leaf)) { throw ('Source shortcut missing: '+$sourceEntry.Name) }
        $link = $shell.CreateShortcut($linkPath)
        $expectedPython = Join-Path $projectRoot '.build-env\Scripts\pythonw.exe'
        $expectedArguments = '"'+(Join-Path $projectRoot $sourceEntry.File)+'"'
        if ($link.TargetPath -ne $expectedPython -or $link.Arguments -ne $expectedArguments -or $link.WorkingDirectory -ne $projectRoot) { throw ('Source shortcut mismatch: '+$sourceEntry.Name) }
        $gui = if ($SkipGui) { [pscustomobject]@{Title=$null;Responding=$null;GuiLaunchVerified=$false} } else { Open-ShortcutWindow $linkPath $link.TargetPath $sourceEntry.Name }
        $sources += [pscustomobject]@{Name=$sourceEntry.Name;Shortcut=$linkPath;Target=$link.TargetPath;Arguments=$link.Arguments;Hotkey=$link.Hotkey;WorkingDirectory=$link.WorkingDirectory;Title=$gui.Title;Responding=$gui.Responding;GuiLaunchVerified=$gui.GuiLaunchVerified;GameTaskStarted=$false}
    }
} catch {
    $failure = $_.Exception.Message
} finally {
    $runtimeJson = & $buildPython (Join-Path $PSScriptRoot 'release_runtime_snapshot.py') --verify $RuntimeSnapshot
    $runtimeExit = $LASTEXITCODE
    $runtime = $runtimeJson | ConvertFrom-Json
    if ($runtimeExit -ne 0 -and -not $failure) { $failure = 'Runtime preservation verification failed' }
    $report = [ordered]@{CheckedAt=(Get-Date -Format 'o');ExpectedVersion=$ExpectedVersion;Result=$(if ($failure) {'FAIL'} else {'PASS'});GuiLaunchVerified=(-not $SkipGui -and -not $failure);Packages=$rows;SourceShortcuts=$sources;RuntimeSnapshot=$RuntimeSnapshot;RuntimePreservation=$runtime;GameTaskStarted=$false;LiveAcceptance='pending';Failure=$failure}
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutputReport -Encoding UTF8
}
if ($failure) { throw $failure }
Write-Output ('PASS: '+$rows.Count+' packaged clients / '+$sources.Count+' source shortcuts; GUI launch='+(-not $SkipGui)+'; '+$runtime.runtime_files_checked+' original runtime hashes preserved. Report: '+$OutputReport)
