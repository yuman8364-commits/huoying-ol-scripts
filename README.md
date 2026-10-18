# 火影OL脚本

主客户端版本：2026.10.17.8。达摩独立客户端版本：2026.10.18.2。

## 直接打开

Windows成品解压后双击各客户端EXE，无需安装Python。本机桌面和“可用脚本”目录提供快捷方式。

| 功能 | 入口 | 使用说明 |
|---|---|---|
| 达摩独立版 | `火影OL达摩独立版.exe` / `daruma_standalone_gui.py` | [达摩说明](daruma_standalone/README.md) |
| 完整客户端 | `火影OL完整客户端.exe` / `hyol_client.py` | [完整客户端说明](README_统一客户端.md) |
| 单人日常 | `火影OL日常三合一.exe` / `hyol_daily_tasks.py` | [单人日常说明](DAILY_TASKS_README.md) |
| 组队任务 | `火影OL组队任务通用版.exe` / `hyol_team_tasks.py` | [组队说明](TEAM_TASKS_README.md) |
| 竞技场 | `火影OL竞技场通用v5.exe` / `hyol_arena.py` | [竞技场说明](ARENA_CLIENT_README.md) |
| 羁绊 | `火影OL羁绊连续作战v1.exe` / `hyol_bond.py` | [羁绊说明](BOND_CLIENT_README.md) |

打开客户端只显示操作界面。登录游戏、选择目标和任务后点击开始。F12或停止按钮结束当前操作并保存结果。

日常和多人入口每次开始创建独立运行，使用当前页面和本轮选择；历史状态保留在磁盘。已有队伍使用本轮一次识别的队长和成员；队伍奖励须真实归零才进入收尾。默认保持阵容和游戏托管设置。

达摩独立版保留其同账号断点续接规则，每层核实实际奖励图标，最终连续两帧核实奖励0及“已完成”。EXE旁runtime目录保存独立记录，更新时保留该目录。

## 源码与构建

建议在Windows使用Python 3.12，建立`.build-env`并安装`requirements.txt`。双击根目录`启动达摩独立版.cmd`可启动达摩源码窗口。

达摩成品构建：`build_daruma_client.ps1 -InstallDesktop`。其他入口使用`build_daily_client.ps1`、`build_independent_clients.ps1`、`build_unified_client.ps1`和`build_team_client.ps1`。

统一离线回归入口：`run_offline_tests.ps1`。达摩及打包专项：`python -m unittest -q test_daruma_standalone test_hyol_daruma test_hyol_release`。部分旧实拍回放依赖本机历史证据；发布包中的测试资料不包含运行日志和账号配置。

## 验收与运行记录

发布成品核对嵌入源码、素材、ZIP校验和及原生GUI启动。游戏任务完成须有当前账号的结果、奖励和页面证据；离线通过和窗口启动不代表游戏挑战完成。

发布包使用通用配置。本地状态、运行日志、本机账号配置和项目进度文档保存在本机。
