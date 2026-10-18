param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$archiveRoot = 'D:\火影OL脚本归档\20261009_文件整理'
$reportPath = Join-Path $workspaceRoot 'logs\folder_cleanup_20261009.json'
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-StreamDigest($Stream) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($Stream)).Replace('-','').ToLowerInvariant() }
    finally { $hasher.Dispose() }
}
function Get-PathDigest([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try { return Get-StreamDigest $stream } finally { $stream.Dispose() }
}
function Assert-WorkspaceDirectory([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path.TrimEnd('\')
    if (-not $resolved.StartsWith($workspaceRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "目标超出项目目录: $resolved"
    }
    if ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "禁止对链接目录递归操作: $resolved"
    }
    if (Get-ChildItem -LiteralPath $resolved -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1) {
        throw "目标包含链接: $resolved"
    }
    return $resolved
}
function Get-TreeSize([string]$Path) {
    return [long]((Get-ChildItem -LiteralPath $Path -File -Recurse -Force | Measure-Object Length -Sum).Sum)
}
function Save-Report {
    [IO.File]::WriteAllText($reportPath, ($report | ConvertTo-Json -Depth 10) + "`n", [Text.UTF8Encoding]::new($false))
}

$report = [ordered]@{
    started_at = (Get-Date).ToString('o')
    root = $workspaceRoot
    archive_root = $archiveRoot
    apply = [bool]$Apply
    released_zip_hashes_unchanged = $false
    released_expansions = @()
    build_caches = @()
    archived_distributions = @()
    result = 'running'
}
$protectedReleases = @('unified_20261009_115022_582_d0e535cd','team_20261009_115242_139','github_20261009_2026.10.09.2')
if (-not $Apply) {
    [pscustomobject]@{ root = $workspaceRoot; preserved_releases = $protectedReleases; action = 'Verify old release contents against their ZIPs, preserve differences, remove duplicate expansions and reproducible build caches, archive retired distributions.' } | ConvertTo-Json -Depth 4
    exit 0
}
if (Test-Path -LiteralPath $reportPath) { throw '清理回执已存在；先核对之前结果，不重复执行' }
New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null
Save-Report

try {
    $releasesRoot = Join-Path $workspaceRoot 'releases'
    foreach ($release in Get-ChildItem -LiteralPath $releasesRoot -Directory) {
        if ($release.Name -in $protectedReleases) { continue }
        if ($release.Name -notmatch '^(team|unified)_\d{8}_') { continue }
        foreach ($payload in Get-ChildItem -LiteralPath $release.FullName -Directory) {
            if ($payload.Name -notin @('火影OL完整客户端','火影OL组队任务通用版')) { continue }
            $payloadPath = Assert-WorkspaceDirectory $payload.FullName
            $zipPath = Join-Path $release.FullName ($payload.Name + '.zip')
            if (-not (Test-Path -LiteralPath $zipPath)) { throw "缺少原发布ZIP: $zipPath" }
            $zipDigest = Get-PathDigest $zipPath
            $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
            $overrides = @()
            $filesChecked = 0
            $bytesBefore = Get-TreeSize $payloadPath
            try {
                $entries = @{}
                foreach ($entry in $archive.Entries) { $entries[$entry.FullName] = $entry }
                foreach ($file in Get-ChildItem -LiteralPath $payloadPath -File -Recurse -Force) {
                    $relative = $file.FullName.Substring($payloadPath.Length + 1)
                    $entry = $entries[$payload.Name + '/' + $relative.Replace('\','/')]
                    $sourceDigest = Get-PathDigest $file.FullName
                    $same = $false
                    if ($entry -and $entry.Length -eq $file.Length) {
                        $stream = $entry.Open()
                        try { $same = (Get-StreamDigest $stream) -eq $sourceDigest } finally { $stream.Dispose() }
                    }
                    if (-not $same) {
                        $backupPath = Join-Path $archiveRoot ('发布包差异原件\' + $release.Name + '\' + $payload.Name + '\' + $relative)
                        $resolvedBackup = [IO.Path]::GetFullPath($backupPath)
                        if (-not $resolvedBackup.StartsWith($archiveRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '原件归档路径不安全' }
                        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($resolvedBackup)) -Force | Out-Null
                        Copy-Item -LiteralPath $file.FullName -Destination $resolvedBackup
                        if ((Get-PathDigest $resolvedBackup) -ne $sourceDigest) { throw '原件归档哈希不符' }
                        $overrides += [pscustomobject]@{ original = $file.FullName; archived = $resolvedBackup; sha256 = $sourceDigest; bytes = $file.Length }
                    }
                    $filesChecked++
                }
            } finally { $archive.Dispose() }
            if ((Get-PathDigest $zipPath) -ne $zipDigest) { throw '核验期间发布ZIP变化' }
            $row = [ordered]@{ path = $payloadPath; bytes = $bytesBefore; files_checked = $filesChecked; zip = $zipPath; zip_sha256 = $zipDigest; preserved_differences = $overrides; removed = $false }
            $report.released_expansions += $row
            Save-Report
            $verifiedRemovalPath = Assert-WorkspaceDirectory $payloadPath
            Remove-Item -LiteralPath $verifiedRemovalPath -Recurse -Force
            $row.removed = -not (Test-Path -LiteralPath $payloadPath)
            Save-Report
        }
        if ($report.released_expansions.Count % 5 -eq 0) { Write-Output ('已核验并整理旧发布展开目录：' + $report.released_expansions.Count) }
    }

    foreach ($build in Get-ChildItem -LiteralPath $workspaceRoot -Directory | Where-Object { $_.Name -eq 'build' -or $_.Name.StartsWith('build_') }) {
        $buildPath = Assert-WorkspaceDirectory $build.FullName
        $files = @(Get-ChildItem -LiteralPath $buildPath -File -Recurse -Force)
        if (-not ($files | Where-Object { $_.Extension -eq '.toc' }) -or ($files | Where-Object { $_.FullName.Substring($buildPath.Length) -match '\\(state|logs)\\' })) {
            throw "构建缓存范围无法确认: $buildPath"
        }
        $row = [ordered]@{ path = $buildPath; bytes = [long](($files | Measure-Object Length -Sum).Sum); files = $files.Count; removed = $false }
        $report.build_caches += $row
        Save-Report
        $verifiedRemovalPath = Assert-WorkspaceDirectory $buildPath
        Remove-Item -LiteralPath $verifiedRemovalPath -Recurse -Force
        $row.removed = -not (Test-Path -LiteralPath $buildPath)
        Save-Report
    }

    $legacyNames = @('dist','dist_arena','dist_arena_generic','dist_arena_generic_v2','dist_arena_generic_v3','dist_arena_generic_v4','dist_arena_generic_v5','dist_arena_generic_v6','dist_arena_generic_v7','dist_arena_generic_v8','dist_arena_generic_v9','dist_moon')
    foreach ($legacyName in $legacyNames) {
        $legacyPath = Join-Path $workspaceRoot $legacyName
        if (-not (Test-Path -LiteralPath $legacyPath)) { continue }
        $legacyPath = Assert-WorkspaceDirectory $legacyPath
        $zipDirectory = Join-Path $archiveRoot '旧客户端'
        New-Item -ItemType Directory -Path $zipDirectory -Force | Out-Null
        $zipPath = [IO.Path]::GetFullPath((Join-Path $zipDirectory ($legacyName + '.zip')))
        if (-not $zipPath.StartsWith($archiveRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $zipPath)) { throw '旧客户端归档目标不安全或已存在' }
        $files = @(Get-ChildItem -LiteralPath $legacyPath -File -Recurse -Force)
        [IO.Compression.ZipFile]::CreateFromDirectory($legacyPath, $zipPath, [IO.Compression.CompressionLevel]::Optimal, $true)
        $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
        try {
            $entries = @{}
            foreach ($entry in $archive.Entries) { $entries[$entry.FullName] = $entry }
            foreach ($file in $files) {
                $relative = $file.FullName.Substring($legacyPath.Length + 1).Replace('\','/')
                $entry = $entries[$legacyName + '/' + $relative]
                if (-not $entry -or $entry.Length -ne $file.Length) { throw "归档文件不完整: $relative" }
                $stream = $entry.Open()
                try { $entryDigest = Get-StreamDigest $stream } finally { $stream.Dispose() }
                if ($entryDigest -ne (Get-PathDigest $file.FullName)) { throw "归档字节不一致: $relative" }
            }
        } finally { $archive.Dispose() }
        $row = [ordered]@{ path = $legacyPath; files_verified = $files.Count; original_bytes = [long](($files | Measure-Object Length -Sum).Sum); archive = $zipPath; archive_bytes = (Get-Item -LiteralPath $zipPath).Length; archive_sha256 = Get-PathDigest $zipPath; removed = $false }
        $report.archived_distributions += $row
        Save-Report
        $verifiedRemovalPath = Assert-WorkspaceDirectory $legacyPath
        Remove-Item -LiteralPath $verifiedRemovalPath -Recurse -Force
        $row.removed = -not (Test-Path -LiteralPath $legacyPath)
        Save-Report
    }
    $report.released_zip_hashes_unchanged = $true
    $report.result = 'PASS'
} catch {
    $report.result = 'stopped'
    $report.error = $_.Exception.Message
    throw
} finally {
    $report.finished_at = (Get-Date).ToString('o')
    Save-Report
}
[pscustomobject]@{ result = $report.result; old_release_expansions = $report.released_expansions.Count; build_caches = $report.build_caches.Count; archived_distributions = $report.archived_distributions.Count; archive_root = $archiveRoot } | ConvertTo-Json -Depth 3
