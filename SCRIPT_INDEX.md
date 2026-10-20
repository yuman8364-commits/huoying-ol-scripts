# 火影OL脚本索引

> 本索引按用途整理根目录脚本。为避免破坏导入、打包和桌面快捷方式，当前仅做逻辑归类，不移动历史文件。

## 当前 Python 客户端

| 入口 | 依赖/核心 | 用途 |
|------|-----------|------|
| `hyol_arena.py` | `hyol_scheduler.py` | 当前标签竞技场独立客户端 |
| `daruma_standalone_gui.py` | `daruma_standalone/` | 达摩独立客户端；桌面“火影OL达摩”打开EXE |
| `hyol_daruma_task.py` | `hyol_daily_tasks.py` / `daruma_standalone/` | 多人解散后及单人达摩选项；同线程递归互斥锁、本轮独立记录、奖励0及回村门 |
| `hyol_bond.py` | `hyol_scheduler.py` | 羁绊连续作战独立客户端 |
| `hyol_team_tasks.py` | `hyol_daily_tasks.py`、`hyol_scheduler.py` | 组队副本＋通缉任务入口 |
| `hyol_daily_tasks.py` | `hyol_scheduler.py`、`hyol_treasure.py` | 单人日常统一实现；藏宝图为默认不选实验项 |
| `hyol_group_instance.py` | `hyol_daily_tasks.py` | 仅组队副本 |
| `hyol_survival.py` | `hyol_daily_tasks.py` | 仅生存试炼 |
| `hyol_wanted.py` | `hyol_daily_tasks.py` | 仅通缉任务 |
| `hyol_panel.py` | `hyol_scheduler.py` | 六标签综合任务面板，可勾选复合队列并传递次数 |

## 启动、构建与辅助

综合面板“执行勾选任务”按显示顺序运行：识别、邮箱、福利、第一队、第二队、竞技场、生存、黑市、托管。只运行勾选项，失败或暂停后不运行后续项；托管安排在最后，全天值守保留独立入口。竞技场胜场、两队副本/强者场数均传递到实际调度命令。单人日常默认竞技场追加5胜；藏宝图因游戏“自动挖宝”会连续执行而默认不选。

- `run_hyol_panel.ps1`、`run_hyol_daily.ps1`：源码启动入口。
- `build_daily_client.ps1`、`build_team_client.ps1`：当前发布包构建入口。
- `build_daruma_client.ps1 -InstallDesktop`：构建达摩独立EXE并安装桌面快捷方式；原运行记录保存在独立runtime目录。
- `run_panel_command_once.py`：单次面板命令辅助入口。
- `capture_rescue_template.py`、`probe_survival_state.py`、`collect_market_modal.py`：模板采集和诊断工具，不属于日常执行入口。
- `tools/enroll_daily_templates.py`：日常模板登记工具。
- `run_moon_arena_test.ps1`、`tools/team_tab6_live.ps1`：实机专项脚本，不能作为离线验收运行。

## 离线验证

- `run_offline_tests.ps1`：统一离线测试入口，自动使用 `.build-env` 中的 Python。
- `test_hyol_scheduler.py`：调度、安全门、竞技场和组队状态机测试。
- `test_hyol_panel.py`：面板参数与命令测试。
- `test_offline_full_flow.py`：11功能键固定回放测试；战斗按钮采用图像定位模拟点击，并覆盖0.8×、1.0×、1.25×缩放与目标缺失零点击。
- `test_hyol_bond.py`：羁绊客户端测试。
- `test_hyol_daily_tasks.py`：日常独立入口测试。
- `test_market_safety_static.py`、`validate_multi_static.py`、`validate_templates.py`：静态安全、多人逻辑和模板回放验证。
- `test_multi_discovery.ps1`：只读窗口发现与几何检查，会检查当前实机窗口，不纳入纯离线套件。

## 配置、资源和生成物

- 当前独立配置：`ARENA_Client.ini`、`TEAM_Client.ini`、`HYOL_Daily.ini`。
- 图像资源：`assets/`；账号身份素材位于 `assets/accounts/`，不要在未确认用途时删除。
- 运行证据：`logs/`、`state/` 以及根目录 `*_state.json` / `*_result.json`。
- `build*/`、`dist*/`、`__pycache__/`：构建或缓存生成物，不是源码。
- `references/MaaNOP/`：第三方架构参考，不纳入本项目测试发现。

## 旧版 Q 脚本

`HYOL_*.Q` 与对应 `.utf8` 文件为历史按键精灵实现；当前 Python 客户端不依赖它们。保留用于追溯，新增功能优先维护 Python 入口。

## 推荐命令

```powershell
.\run_offline_tests.ps1
```

离线测试通过只证明代码、固定回放和安全门符合预期，不能替代真实游戏窗口中的账号身份、次数归零或战斗结果证据。
