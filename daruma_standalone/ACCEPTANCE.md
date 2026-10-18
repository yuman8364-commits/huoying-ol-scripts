# 达摩独立版验收记录

当前版本2026.10.18.2，已按用户要求提供独立EXE、桌面快捷方式和GitHub发布包。启动客户端只打开界面。

2026-10-18桌面交付：

- EXE：`桌面/可用脚本/火影OL达摩/火影OL达摩独立版.exe`；桌面和“可用脚本”目录各有“火影OL达摩”快捷方式。
- 39个嵌入模块源码一致、575项素材与六个独立数字/完成样本齐全；冻结程序资源自检确认运行目录在EXE旁。见根目录 `logs/daruma_desktop_20261018_v2_package.json`。
- 真实桌面快捷方式启动通过，窗口正常响应，截图已目视核对：`logs/daruma_desktop_20261018/desktop_gui.json`、`desktop_gui.png`；游戏点击0、未开始任务。
- 统一离线回归1257项通过（287.8秒），含独立达摩73项和打包验收11项；11功能、语法、静态与模板检查通过。六个发布成品的原生GUI启动均通过。见 `logs/daruma_desktop_20261018/offline_full_normal.log` 和 `portable_gui_*.json`。
- 原4150份运行文件及1004份状态/配置/既有快捷方式哈希不变，见同目录 `preservation.json`。
- 本版完整挑战仍为 `pending_user_testing`。历史五个独立断点保留，源码运行继续使用原目录，桌面程序使用其独立runtime目录。

2026-10-18源码版.18.1验证：

- 80项回归通过（70项独立测试+原10项）：`runtime/verification/completion_20261018/regression_final.log`。新增测试先复现奖励遮挡后沿用旧计数、零值仅单帧带“已完成”、身份不符改写断点和GUI残留计数，再验证修复；复现日志同目录 `regression_before_fix.log`。
- 奖励0必须连续两帧同时识别数字和“已完成”；同一标签的角色/进程不符在读取原断点后立即拒绝，不改原文件；开始新观察时清空GUI旧计数。
- 正常桌面原生GUI通过，截图已目视核对：`runtime/verification/completion_20261018/gui_runtime/verification/gui.json`、`gui.png`。沙箱内Tcl报错，正常Windows桌面验证成功；未改Python环境或启动入口。
- 只读预检 `logs/preflight_20261018_202921_884849_c5638fed.json`：ready、healthy/main_ready=true、content_clicks=0。独立观察 `runtime/observations/20261018_202945_314581.json`：field、content_clicks=0；未打开活动或开始挑战。
- 五个主流程源码文件和全部五个既有独立断点SHA256与本轮开始一致；原始清单见 `runtime/verification/completion_20261018/protected_before.json`。
- 本版状态为 `offline_ui_pass`，完整挑战实机复验为 `pending_user_testing`。本版没有消费挑战机会；以下是2026-10-17历史实机证据。

2026.10.17.3历史验收：

| 账号 | 实机结果 | 独立断点 |
|---|---|---|
| 51区鸣路 | 三局18层，18次已核实命中，0次补击；机会0、奖励0及灰色已完成 | `runtime/runs/20261017_213006_569069_81784f/checkpoint.json` |
| 74区月光 | 三局18层，18次已核实命中，0次补击；机会0、奖励0及灰色已完成 | `runtime/runs/20261017_222239_424849_24c387/checkpoint.json` |

每层以实际奖励图标增加及圆柱变化/结算确认。新账号15击另有Perfect区域内的稳定判定轨迹；三个最后一击的轨迹不足，不冒称18次Perfect。最终仍通过六图标、结算、活动奖励归零闭环，两号均无需人工补击，结束保留活动页。

新账号第一次GUI首层确认后，实际调用停止按钮，再由同一按钮继续；核实原窗口、角色及同一断点。第二局首击前被横幅公告遮住奖励栏而停止，`intent=null`，本层未输入；现场保留在 `runtime/observations/20261017_222333_061065.png`。修正遮挡等待与旧稳定帧失效处理后，沿同一断点完成剩余两局，没有重开已消耗的局。开发停顿不计为速度收益。

证据入口：

- 第一号归零图：`runtime/runs/20261017_213006_569069_81784f/220739_379858_reward_0.png`。
- 新账号归零图：`runtime/runs/20261017_222239_424849_24c387/223042_491720_reward_0.png`。
- GUI完整流程：`runtime/verification/live_gui_20261017_222922.json`、同名PNG，含停止/续接、三次开始及18层计数。
- 最终GUI：`runtime/verification/gui.json`、`gui.png`；实际前台截图已目视核对，打开控件测试不启动游戏任务。
- F12、同断点零点击续接、输入锁占用/释放：`runtime/verification/safety_live.json`，使用禁止鼠标输入的验收适配器在完成页实测。
- 65项离线回归（原10项+独立55项）：`runtime/verification/regression_final.log`；错误页面、身份/标签变化、失焦、几何变化、掉线、遮挡、次数0、未决动作均验证不发送敲击。
- 性能原始数据：`runtime/verification/trace_benchmark.json`；相同30帧压缩568.8→87.4ms、8.65→2.30MB，判定位置保持1030。仅压缩6.51倍，不是整局倍数。
- 新账号18条真实轨迹压缩72.6～94.4ms，第三局从开始到六层确认32.703秒；第二局含开发停顿，不用于速度比较。
- .3原始机器可读汇总备份：`runtime/verification/completion_20261018/before/final_acceptance.json`；当前汇总：`runtime/verification/final_acceptance.json`。

隔离核验：旧达摩、旧周末入口、日常入口及主运行库五文件SHA256保持原值；前一账号完成断点SHA256保持 `C5CA621FA6C21663604EDB3A64CAFFF5FBA8B6D2CDA6C5FB778AB14440C9FFAF`。主程序历史待处理任务未迁入独立运行。

随后两次完整实机：`runtime/verification/live_gui_20261017_231730.json`（.4）和 `live_gui_20261017_232820.json`（.5）均记录三次开始、18次核实命中、机会/奖励0及GUI停止后同断点继续。对应断点分别为 `20261017_231731_702573_06e6d6` 和 `20261017_232822_371376_38155f`；这两次结果不外推为当前源码的实机验收。

旧停滞运行 `20261017_225958_659142_e743dd` 在活动页核实奖励0后零点击对账，原未决动作结果为 `superseded_by_verified_reward_zero`。仅有13次自动命中，`layer_details_incomplete=true`；不补写缺失命中、不记为自动三局18层通过。见 `runtime/verification/stalled_resume_live.json`。

待验证：其他窗口尺寸/DPI；真实误击后的换边和两次纠正上限（本轮没有触发，已有实拍回放和模拟）；真实掉线后的停止（已有模板/停止门离线验证，未故意断网）；修复后的公告遮挡等待实机再次触发。以上不影响已完成账号的正向归零记录。
