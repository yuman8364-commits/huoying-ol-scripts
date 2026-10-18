$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundledRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python'
$candidates = @(
    (Join-Path $root '.build-env\Scripts\pythonw.exe'),
    (Join-Path $root '.build-env\Scripts\python.exe'),
    (Join-Path $bundledRoot 'pythonw.exe'),
    (Join-Path $bundledRoot 'python.exe'),
    (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
    (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
if (-not $candidates) {
    throw '未找到可用的 Python。请安装 Python 3，或从 Codex 环境运行本脚本。'
}

Start-Process -FilePath $candidates[0] `
    -ArgumentList @((Join-Path $root 'hyol_panel.py')) `
    -WorkingDirectory $root `
    -WindowStyle Hidden
