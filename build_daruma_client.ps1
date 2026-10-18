[CmdletBinding()]
param(
    [string]$BuildTag = ('daruma_' + (Get-Date -Format 'yyyyMMdd_HHmmss')),
    [switch]$InstallDesktop
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.build-env\Scripts\python.exe'
if ($BuildTag -notmatch '^[a-z0-9_]+$') { throw 'Invalid build tag' }
$dist = [IO.Path]::GetFullPath((Join-Path $projectRoot ('dist_' + $BuildTag)))
$work = [IO.Path]::GetFullPath((Join-Path $projectRoot ('build_' + $BuildTag)))
$report = Join-Path $projectRoot ('logs\' + $BuildTag + '_package.json')
foreach ($path in @($dist, $work, $report)) {
    if (-not $path.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Build output escaped the project'
    }
    if (Test-Path -LiteralPath $path) { throw ('Preserve previous build: ' + $path) }
}
$name = '火影OL达摩独立版'
$samples = Join-Path $projectRoot 'daruma_standalone\samples'
& $python -m PyInstaller --noconfirm --clean --onedir --windowed --name $name --distpath $dist --workpath $work --specpath $work --add-data ($samples + ';daruma_standalone/samples') (Join-Path $projectRoot 'daruma_standalone_gui.py')
if ($LASTEXITCODE -ne 0) { throw 'Daruma PyInstaller build failed' }
$package = Join-Path $dist $name
& $python (Join-Path $projectRoot 'tools\prepare_daruma_package.py') --package $package --report $report
if ($LASTEXITCODE -ne 0) { throw 'Daruma package verification failed' }
if ($InstallDesktop) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $usable = [IO.Path]::GetFullPath((Join-Path $desktop '可用脚本'))
    $destination = [IO.Path]::GetFullPath((Join-Path $usable '火影OL达摩'))
    if (-not $destination.StartsWith($usable + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Desktop publication path escaped the usable-script folder'
    }
    $backup = Join-Path $projectRoot ('logs\release_preserved\' + $BuildTag)
    if (Test-Path -LiteralPath $destination) {
        New-Item -ItemType Directory -Path $backup -Force | Out-Null
        Get-ChildItem -LiteralPath $destination | Where-Object { $_.Name -ne 'runtime' } | Copy-Item -Destination $backup -Recurse -Force
    }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Get-ChildItem -LiteralPath $package | Where-Object { $_.Name -ne 'runtime' } | Copy-Item -Destination $destination -Recurse -Force
    $shell = New-Object -ComObject WScript.Shell
    foreach ($linkPath in @((Join-Path $desktop '火影OL达摩.lnk'), (Join-Path $usable '火影OL达摩.lnk'))) {
        if (Test-Path -LiteralPath $linkPath) {
            New-Item -ItemType Directory -Path $backup -Force | Out-Null
            Copy-Item -LiteralPath $linkPath -Destination (Join-Path $backup ((Split-Path -Leaf (Split-Path -Parent $linkPath)) + '_达摩.lnk'))
        }
        $link = $shell.CreateShortcut($linkPath)
        $link.TargetPath = Join-Path $destination ($name + '.exe')
        $link.WorkingDirectory = $destination
        $link.Arguments = ''
        $link.IconLocation = $link.TargetPath + ',0'
        $link.Save()
    }
    $expected = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($item in $expected.files) {
        $installed = Join-Path $destination $item.path
        if ((Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash -ne $item.sha256) {
            throw ('Installed file mismatch: ' + $item.path)
        }
    }
    Write-Output ('Desktop installed and verified: ' + $destination)
}
Write-Output ('Daruma package ready: ' + $report)
