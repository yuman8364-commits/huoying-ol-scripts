[CmdletBinding()]
param(
    [string]$LogPath = "logs/offline_test_latest.log"
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

$pythonPath = Join-Path $projectRoot '.build-env\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    $runtimePython = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $runtimePython) {
        throw '未找到 Python。请恢复 .build-env 或安装 Python 3.11+。'
    }
    $pythonPath = $runtimePython.Source
}

$logFullPath = Join-Path $projectRoot $LogPath
$logDirectory = Split-Path -Parent $logFullPath
if ($logDirectory -and -not (Test-Path -LiteralPath $logDirectory)) {
    New-Item -ItemType Directory -Path $logDirectory | Out-Null
}

$steps = @(
    @{ Name = '达摩独立客户端与打包路径回归'; Arguments = @('-m', 'unittest', '-v', 'test_daruma_standalone.py') },
    @{ Name = '新运行隔离、一次识别与停止行为回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_fresh_run.py') },
    @{ Name = '实跑中断、窗口检测与身份遮挡关闭回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_interruption_recovery.py', 'test_hyol_identity_overlays.py') },
    @{ Name = '战斗防卡死回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_combat_watchdog.py') },
    @{ Name = '标签进度、首次技能与断点修复回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_progress_repair.py') },
    @{ Name = '真实调度器观察清理与待续回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_observation_lifecycle.py') },
    @{ Name = '双队长衔接与奖励归零连续作战回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_team_pipeline.py', 'test_hyol_group_repeat.py') },
    @{ Name = '本轮组队完成门、未开放跳过与发布回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_pipeline_resume.py', 'test_hyol_publish_resume.py') },
    @{ Name = '拼图自动程序与逐难度断点回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_puzzle.py', 'test_hyol_puzzle_backend.py') },
    @{ Name = '账号、历史来源与托管阵容安全回归'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_accounts.py', 'test_hyol_current_identity.py', 'test_hyol_account_results.py', 'test_hyol_history.py', 'test_hyol_live_solo_fixes.py', 'test_hyol_solo_improvements.py', 'test_hyol_managed_resume_fixes.py') },
    @{ Name = '核心脚本语法检查'; Arguments = @('-m', 'py_compile', 'hyol_run_context.py', 'hyol_client.py', 'hyol_runtime.py', 'hyol_team_pipeline.py', 'hyol_inventory.py', 'hyol_managed.py', 'hyol_scheduler.py', 'hyol_checkpoints.py', 'hyol_treasure.py', 'hyol_sealed.py', 'hyol_survival_counts.py', 'hyol_panel.py', 'hyol_arena.py', 'hyol_bond.py', 'hyol_daily_tasks.py', 'hyol_multi_solo.py', 'hyol_multi_solo_gui.py', 'hyol_weekend.py', 'hyol_weekend_gui.py', 'hyol_daruma.py', 'hyol_group_instance.py', 'hyol_survival.py', 'hyol_wanted.py', 'hyol_team_tasks.py', 'hyol_team_six.py', 'hyol_navigation.py', 'hyol_quiz.py', 'hyol_ranking.py', 'hyol_network_retry.py', 'offline_full_flow.py') },
    @{ Name = '单元与固定回放测试'; Arguments = @('-m', 'unittest', '-v', 'test_hyol_inventory.py', 'test_hyol_release.py', 'test_hyol_scheduler.py', 'test_hyol_survival_counts.py', 'test_hyol_panel.py', 'test_offline_full_flow.py', 'test_hyol_bond.py', 'test_hyol_daily_tasks.py', 'test_hyol_team_six.py', 'test_hyol_multi_solo.py', 'test_hyol_multi_solo_gui.py', 'test_hyol_daruma.py', 'test_hyol_infrastructure.py', 'test_hyol_continuation.py', 'test_hyol_target_card.py', 'test_hyol_strong_entry.py', 'test_hyol_ranking.py', 'test_hyol_network_retry.py', 'test_hyol_connection.py', 'test_hyol_checkpoints.py', 'test_hyol_treasure.py', 'test_hyol_navigation.py', 'test_hyol_managed.py', 'test_hyol_recognition_performance.py', 'test_solo_acceptance_tool.py', 'test_hyol_model_gate.py', 'test_hyol_client.py', 'test_codex_quota_continuation.py') },
    @{ Name = '生成11功能键离线验收产物'; Arguments = @('offline_full_flow.py') },
    @{ Name = '黑市安全静态检查'; Arguments = @('test_market_safety_static.py') },
    @{ Name = '多账号静态检查'; Arguments = @('validate_multi_static.py') },
    @{ Name = '模板离线验证'; Arguments = @('validate_templates.py') }
)

$startedAt = Get-Date
$transcriptStarted = $false
try {
    Start-Transcript -LiteralPath $logFullPath -Force | Out-Null
    $transcriptStarted = $true
    Write-Host "项目目录: $projectRoot"
    Write-Host "Python: $pythonPath"

    foreach ($step in $steps) {
        Write-Host "`n[$($step.Name)]"
        # unittest writes ordinary progress to stderr. Windows PowerShell 5
        # wraps this as NativeCommandError; decide success by exit code.
        $previousErrorPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $pythonPath @($step.Arguments) 2>&1 | ForEach-Object { Write-Host $_ }
            $stepExitCode = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previousErrorPreference }
        if ($stepExitCode -ne 0) {
            throw "$($step.Name)失败，退出码 $stepExitCode。"
        }
    }

    $elapsed = (Get-Date) - $startedAt
    Write-Host "`nPASS：全部离线测试通过，用时 $([math]::Round($elapsed.TotalSeconds, 1)) 秒。"
}
finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
