$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.build-env\Scripts\python.exe'
$publisher = Join-Path $PSScriptRoot 'publish_stop_cleanup_clients.py'

# Verify all binaries, the ended game run and original user runtime first.
& $python $publisher
if ($LASTEXITCODE -ne 0) { throw 'Publication checks failed; desktop untouched' }

$usable = Join-Path ([Environment]::GetFolderPath('Desktop')) '可用脚本'
$clientExe = Join-Path $usable '火影OL完整客户端\火影OL完整客户端.exe'
$clients = @(Get-Process -Name '火影OL*' -ErrorAction SilentlyContinue)
foreach ($client in $clients) {
    $client.Refresh()
    if ($client.Path -eq $clientExe) {
        if (-not $client.Responding -or -not $client.MainWindowTitle.Contains('2026.10.13.1')) {
            throw 'Unexpected existing client window; preserve it and stop installation'
        }
        # _close refuses while its controller is busy. Never force a game worker.
        $null = $client.CloseMainWindow()
        if (-not $client.WaitForExit(5000)) {
            throw 'Existing client refused to close; no automatic stop or F12 change'
        }
        Write-Output ('Closed idle old GUI: PID '+$client.Id)
    } elseif ($client.Path -and $client.Path.StartsWith($usable+'\',[StringComparison]::OrdinalIgnoreCase)) {
        throw ('Another installed client is open; preserve it: '+$client.Path)
    }
}

& $python $publisher --publish
if ($LASTEXITCODE -ne 0) { throw 'Publication failed; inspect the preserved original binary backups' }
