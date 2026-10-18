param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$logsRoot = Join-Path $workspaceRoot 'logs'
$archiveRoot = 'D:\火影OL脚本归档\20261009_文件整理'
$planPath = Join-Path $logsRoot 'final_cleanup_plan_20261009.json'
$receiptPath = Join-Path $logsRoot 'final_cleanup_applied_20261009.json'
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ($plan.root -ne $workspaceRoot -or $plan.archive_root -ne $archiveRoot -or $plan.tests -ne 1016) { throw '清单范围或依赖验证不符' }
$trace = Get-Content -LiteralPath $plan.dependency_trace -Raw | ConvertFrom-Json
if ($trace.result -ne 'PASS' -or $trace.tests -ne 1016) { throw '依赖追踪回归未通过' }
if (-not $Apply) {
    [pscustomobject]@{delete_files=$plan.delete_files.Count;delete_directories=$plan.delete_directories.Count;logical_delete_gib=[Math]::Round($plan.delete_bytes/1GB,3);retained_log_gib=[Math]::Round($plan.retained_log_bytes/1GB,3)} | ConvertTo-Json
    exit 0
}
if (Test-Path -LiteralPath $receiptPath) { throw '清理回执已存在，先核对结果，不重复删除' }
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FinalCleanupStorage {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern uint GetCompressedFileSizeW(string path, out uint high);
    public static long Bytes(string path) {
        uint high; uint low = GetCompressedFileSizeW(path, out high);
        if (low == 0xffffffff && Marshal.GetLastWin32Error() != 0)
            throw new System.ComponentModel.Win32Exception();
        return (long)(((ulong)high << 32) | low);
    }
}
'@

function Get-TreeMetrics([string]$Path) {
    $files = @(Get-ChildItem -LiteralPath $Path -File -Recurse -Force)
    $stored = 0L
    foreach ($file in $files) { $stored += [FinalCleanupStorage]::Bytes($file.FullName) }
    return [ordered]@{files=$files.Count;logical_bytes=[long](($files | Measure-Object Length -Sum).Sum);stored_file_bytes=$stored}
}
function Assert-SafeDirectory([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path.TrimEnd('\')
    $name = [IO.Path]::GetFileName($resolved)
    $parent = [IO.Path]::GetDirectoryName($resolved)
    $allowed = ($resolved -eq $archiveRoot) -or
        ($parent -eq $workspaceRoot -and $name -in @('dist_daily','dist_arena_generic_v10','dist_bond_v1')) -or
        ($parent -eq $logsRoot -and ($name -match 'source_before_\d{8}_\d{6}$' -or $name -match '^rebuild_\d{8}_\d{4}$'))
    if (-not $allowed) { throw "递归删除目标不在明确范围内: $resolved" }
    $items = @((Get-Item -LiteralPath $resolved)) + @(Get-ChildItem -LiteralPath $resolved -Recurse -Force)
    if ($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } | Select-Object -First 1) { throw "目标包含链接: $resolved" }
    return $resolved
}
function Assert-SafeFile([string]$Path) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $parent = [IO.Path]::GetDirectoryName($resolved)
    $name = [IO.Path]::GetFileName($resolved)
    $allowed = $resolved.StartsWith($logsRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        ($parent -eq $workspaceRoot -and ($name -like '*.pyc*' -or $name -in @('GENERIC_ACCEPTANCE_20261008.md','PIPELINE_ACCEPTANCE_20261007.md')))
    if (-not $allowed) { throw "文件删除目标不在明确范围内: $resolved" }
    $item = Get-Item -LiteralPath $resolved
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '禁止按文件删除目录或链接' }
    return $resolved
}
function Assert-Digests($Rows, [string]$Kind) {
    foreach ($row in $Rows) {
        if (-not (Test-Path -LiteralPath $row.path -PathType Leaf) -or
            (Get-FileHash -LiteralPath $row.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256.ToLowerInvariant()) { throw "$Kind 文件缺失或变化: $($row.path)" }
    }
}
function Save-Receipt {
    [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 12) + "`n", [Text.UTF8Encoding]::new($false))
}

$stateRows = (Get-Content -LiteralPath (Join-Path $logsRoot 'folder_cleanup_state_before_20261009.json') -Raw | ConvertFrom-Json).state_files
$runtimeRows = (Get-Content -LiteralPath (Join-Path $logsRoot 'solo_live_fix_runtime_before_20261009.json') -Raw | ConvertFrom-Json).files
$artifactRows = (Get-Content -LiteralPath (Join-Path $workspaceRoot 'releases\github_20261009_2026.10.09.2\github_upload_manifest.json') -Raw | ConvertFrom-Json).artifacts
Assert-Digests $stateRows '任务状态'
Assert-Digests $runtimeRows '桌面运行'
Assert-Digests $artifactRows '最终交付包'
Assert-Digests $plan.preserve '保留依赖'
foreach ($row in $plan.delete_files) {
    $resolved = Assert-SafeFile $row.path
    if ((Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant() -ne $row.sha256) { throw "待删文件自规划后发生变化: $resolved" }
}
foreach ($row in $plan.delete_directories) {
    $resolved = Assert-SafeDirectory $row.path
    $files = @(Get-ChildItem -LiteralPath $resolved -Recurse -Force -File)
    if ($files.Count -ne $row.files -or [long](($files | Measure-Object Length -Sum).Sum) -ne $row.bytes) { throw "待删目录自规划后发生变化: $resolved" }
}

$receipt = [ordered]@{started_at=(Get-Date).ToString('o');root=$workspaceRoot;plan=$planPath;result='running';
    root_before=(Get-TreeMetrics $workspaceRoot);archive_before=(Get-TreeMetrics $archiveRoot);
    removed_files=0;removed_directories=@();deleted_stored_file_bytes=0L;game_input_operations=0}
Save-Receipt
try {
    foreach ($row in $plan.delete_files) {
        $resolved = Assert-SafeFile $row.path
        $bytes = [FinalCleanupStorage]::Bytes($resolved)
        Remove-Item -LiteralPath $resolved -Force
        $receipt.removed_files++
        $receipt.deleted_stored_file_bytes += $bytes
    }
    foreach ($row in $plan.delete_directories) {
        $resolved = Assert-SafeDirectory $row.path
        $metrics = Get-TreeMetrics $resolved
        Remove-Item -LiteralPath $resolved -Recurse -Force
        if (Test-Path -LiteralPath $resolved) { throw "目录仍存在: $resolved" }
        $receipt.removed_directories += [ordered]@{path=$resolved;files=$metrics.files;logical_bytes=$metrics.logical_bytes;stored_file_bytes=$metrics.stored_file_bytes}
        $receipt.deleted_stored_file_bytes += $metrics.stored_file_bytes
        Save-Receipt
    }
    # Remove only empty directories within logs; all retained paths stay intact.
    foreach ($directory in Get-ChildItem -LiteralPath $logsRoot -Directory -Recurse -Force | Sort-Object { $_.FullName.Length } -Descending) {
        $resolved = (Resolve-Path -LiteralPath $directory.FullName).Path
        if (-not $resolved.StartsWith($logsRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '空目录清理路径不符' }
        if (-not (Get-ChildItem -LiteralPath $resolved -Force | Select-Object -First 1)) { Remove-Item -LiteralPath $resolved -Force }
    }
    Assert-Digests $stateRows '任务状态'
    Assert-Digests $runtimeRows '桌面运行'
    Assert-Digests $artifactRows '最终交付包'
    Assert-Digests $plan.preserve '保留依赖'
    $receipt.state_files_verified = $stateRows.Count
    $receipt.runtime_files_verified = $runtimeRows.Count
    $receipt.artifacts_verified = $artifactRows.Count
    $receipt.fixture_files_verified = $plan.preserve.Count
    $receipt.root_after = Get-TreeMetrics $workspaceRoot
    $receipt.archive_removed = -not (Test-Path -LiteralPath $archiveRoot)
    $receipt.result = 'PASS'
} catch {
    $receipt.result = 'stopped'
    $receipt.error = $_.Exception.Message
    throw
} finally {
    $receipt.finished_at = (Get-Date).ToString('o')
    Save-Receipt
}
[pscustomobject]@{result=$receipt.result;removed_files=$receipt.removed_files;removed_directories=$receipt.removed_directories.Count;root_logical_gib=[Math]::Round($receipt.root_after.logical_bytes/1GB,3);root_stored_file_gib=[Math]::Round($receipt.root_after.stored_file_bytes/1GB,3);additional_storage_reclaimed_gib=[Math]::Round($receipt.deleted_stored_file_bytes/1GB,3);state_files=$receipt.state_files_verified;runtime_files=$receipt.runtime_files_verified} | ConvertTo-Json
