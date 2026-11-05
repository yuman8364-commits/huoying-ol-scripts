[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ExecutablePath,
    [Parameter(Mandatory=$true)][string]$ExpectedTitle,
    [Parameter(Mandatory=$true)][string]$ReportPath,
    [string]$ShortcutPath,
    [string]$ScreenshotPath
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.build-env\Scripts\python.exe'
$exe = [IO.Path]::GetFullPath($ExecutablePath)
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Executable missing' }
if (Test-Path -LiteralPath $ReportPath) { throw 'Preserve previous GUI evidence' }
$shell = New-Object -ComObject WScript.Shell
$launch = $exe
if ($ShortcutPath) {
    $link = $shell.CreateShortcut([IO.Path]::GetFullPath($ShortcutPath))
    if ($link.TargetPath -ne $exe -or $link.WorkingDirectory -ne (Split-Path -Parent $exe) -or $link.Arguments) {
        throw 'Shortcut target, working directory or arguments differ'
    }
    $launch = [IO.Path]::GetFullPath($ShortcutPath)
}
$originalPids = @(Get-Process | Select-Object -ExpandProperty Id)
$ownedPids = [Collections.Generic.HashSet[int]]::new()
$result = [ordered]@{Status='pending';Exe=$exe;Shortcut=$ShortcutPath;GameTaskStarted=$false;ContentClicks=0}
try {
    $null = $shell.Run(('"'+$launch+'"'), 1, $false)
    $deadline = (Get-Date).AddSeconds(25)
    $gui = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
        foreach ($row in @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $exe -and $_.ProcessId -notin $originalPids })) {
            $null = $ownedPids.Add([int]$row.ProcessId)
            $candidate = Get-Process -Id $row.ProcessId -ErrorAction SilentlyContinue
            if ($null -ne $candidate) {
                $candidate.Refresh()
                if ($candidate.MainWindowHandle -ne 0) { $gui = $candidate; break }
            }
        }
        if ($null -ne $gui) { break }
    }
    if ($null -eq $gui) { throw 'GUI did not open before timeout' }
    $gui.Refresh()
    if (-not $gui.Responding -or -not $gui.MainWindowTitle.Contains($ExpectedTitle)) {
        throw ('Unexpected or unresponsive GUI: ' + $gui.MainWindowTitle)
    }
    $result.Title = $gui.MainWindowTitle
    $result.Responding = $true
    $result.ProcessId = $gui.Id
    if ($ScreenshotPath) {
        $imageCode = 'import sys,time; from PIL import ImageGrab; from hyol_scheduler import Win32; hwnd=int(sys.argv[1]); Win32.activate(hwnd); time.sleep(.3); assert Win32.foreground()==hwnd; ImageGrab.grab(bbox=Win32.rect(hwnd),all_screens=True).save(sys.argv[2]); assert Win32.foreground()==hwnd'
        & $python -c $imageCode $gui.MainWindowHandle.ToInt64() $ScreenshotPath
        if ($LASTEXITCODE -ne 0) { throw 'GUI screenshot could not be verified' }
        $result.Screenshot = [IO.Path]::GetFullPath($ScreenshotPath)
    }
    $result.Status = 'pass'
} catch {
    $result.Reason = $_.Exception.Message
} finally {
    foreach ($ownedPid in $ownedPids) {
        $owned = Get-Process -Id $ownedPid -ErrorAction SilentlyContinue
        if ($null -ne $owned -and -not $owned.HasExited) {
            if ($owned.MainWindowHandle -ne 0) { $null = $owned.CloseMainWindow() }
            if (-not $owned.WaitForExit(5000)) { Stop-Process -Id $ownedPid }
        }
    }
    $result.CheckedAt = Get-Date -Format 'o'
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding utf8
}
$result | ConvertTo-Json -Depth 5
if ($result.Status -ne 'pass') { exit 1 }
exit 0
