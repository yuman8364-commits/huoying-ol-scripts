[CmdletBinding()]
param([string[]]$PackageKeys,
      [string]$BuildTag = 'stop_cleanup_20261013',
      [string]$ReportPath = 'logs\stall_fix_staged_20261013.json')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$python = Join-Path $projectRoot '.build-env\Scripts\python.exe'
$version = & $python -c 'from hyol_runtime import VERSION; print(VERSION)'
if ($LASTEXITCODE -ne 0) { throw 'Cannot read current source version' }
if ($BuildTag -notmatch '^[a-z0-9_]+$') { throw 'Invalid build tag' }
$dist = [IO.Path]::GetFullPath((Join-Path $projectRoot ('dist_'+$BuildTag)))
$work = [IO.Path]::GetFullPath((Join-Path $projectRoot ('build_'+$BuildTag)))
foreach ($target in @($dist, $work)) {
    if (-not $target.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build path escaped the workspace'
    }
}
$entries = @(
    @{Key='unified';Name='火影OL完整客户端';Source='hyol_client.py';Folder='火影OL完整客户端'},
    @{Key='team';Name='火影OL组队任务通用版';Source='hyol_team_tasks.py';Folder='火影OL组队任务'},
    @{Key='daily';Name='火影OL日常三合一';Source='hyol_daily_tasks.py';Folder='火影OL日常任务'},
    @{Key='arena';Name='火影OL竞技场通用v5';Source='hyol_arena.py';Folder='火影OL竞技场'},
    @{Key='bond';Name='火影OL羁绊连续作战v1';Source='hyol_bond.py';Folder='火影OL羁绊'}
)
if ($PackageKeys) {
    foreach ($key in $PackageKeys) {
        if ($key -notin @($entries | ForEach-Object { $_.Key })) { throw ('Unknown package key: '+$key) }
    }
    $entries = @($entries | Where-Object { $_.Key -in $PackageKeys })
}
$rows = @()
foreach ($entry in $entries) {
    $job = Join-Path $work $entry.Key
    & $python -m PyInstaller --noconfirm --clean --onedir --windowed --name $entry.Name --distpath $dist --workpath $job --specpath $job (Join-Path $projectRoot $entry.Source)
    if ($LASTEXITCODE -ne 0) { throw ('Build failed: ' + $entry.Key) }
    $exe = Join-Path (Join-Path $dist $entry.Name) ($entry.Name + '.exe')
    $raw = & $python (Join-Path $PSScriptRoot 'verify_packaged_source.py') $exe --root $projectRoot
    if ($LASTEXITCODE -ne 0) { throw ('Source verification failed: ' + $entry.Key) }
    $probe = $raw | ConvertFrom-Json
    $rows += [pscustomobject]@{Key=$entry.Key;Name=$entry.Name;Folder=$entry.Folder;Source=$entry.Source;Exe=$exe;Sha256=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash;SourceCodeMatches=$probe.source_code_matches;Modules=$probe.modules_checked}
}
$receipt = [IO.Path]::GetFullPath((Join-Path $projectRoot $ReportPath))
if (-not $receipt.StartsWith($projectRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Report path escaped the workspace' }
if ($PackageKeys) {
    $existing = Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($existing.Version -ne $version) { throw 'Staged version mismatch' }
    $rows = @($existing.Packages | Where-Object { $_.Key -notin $PackageKeys }) + $rows
}
[ordered]@{Version=$version;Status='staged';Packages=$rows;GameTaskStarted=$false} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receipt -Encoding utf8
