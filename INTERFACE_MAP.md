# 界面与运行层接口映射

## 调用链

web/app.js → web/native-adapter.js → pywebview.api → DesktopBridge → 原RuntimeController → 原runner及scheduler。桥接不构造新的调度器，不改变点击、识别或完成判定。

## 任务参数

| 界面 | RunRequest字段 | 规则 |
|---|---|---|
| 当前账号/多账号/组队/独立类型 | mode | solo、multi、team、team_then_multi、arena、bond、weekend、treasure |
| 单人任务勾选 | tasks | 按原任务顺序生成，原dataclass再次验证 |
| 账号数 | account_count | 整数1～6；已有队伍衔接固定6 |
| 竞技场结束条件 | arena_target_mode | tickets或wins |
| 辅助胜场 | arena_wins | 整数1～10 |
| 阵容 | formation | keep、main、fast，默认保持不变 |
| 自动建队队长/成员 | leader、members | 标签1～6，成员与队长规则由原TeamSelection验证 |
| 好友邀请 | friend_shortcut | 布尔值，沿用原好友建队逻辑 |
| 已有队伍队长 | leaders | 留空表示自动识别，否则互不重复 |
| 组队活动 | team_tasks | 当前组队方式支持的任务列表 |
| 羁绊目标/新轮次 | bond_count、new_bond_round | 场数1～99；明确新轮次选项只消费一次 |
| 最后托管 | final_managed | 布尔值，默认开启 |

桥接拒绝未知字段、错误类型、布尔值冒充整数及不支持任务。仅合法RunRequest进入控制器。

## 原生API

| API | 原行为/返回 |
|---|---|
| start(payload) | 校验后调用RuntimeController.start；单worker忙时拒绝，保留当前显示 |
| stop() | 调用原stop，先发停止信号再保存暂停，收到回执后显示停止结果 |
| check_window() | 原只读窗口预检，不自动开始任务 |
| detect_leaders() | 原队长识别，返回实际核实标签 |
| get_state() | 当前busy、操作类型、回执状态、当前运行进度、最近日志和检查结果 |
| read_evidence(path) | 仅当前进度授权且位于本轮目录的PNG/JPG；校验图片后返回data URL |
| export_logs() | 固定保存到logs/exports，无任意路径参数 |
| open_logs() | 打开固定的当前客户端日志目录 |

桥接内部属性及清理方法以下划线开头，不暴露给JavaScript。静态资源服务没有任务HTTP控制接口。

## 状态与事件

| 来源 | 界面行为 |
|---|---|
| 新运行被接受 | 记录本轮run_id，清空上一轮界面数据，进入执行中 |
| unified_status/unified_error | 更新说明与日志，不能自行升级完成 |
| unified_stop_requested | 显示停止中，等待原worker结束 |
| unified_finished | 使用原status：complete/stopped/pending；全体所选活动未开放时显示未开放 |
| unified_idle | 解除本次检查/识别等操作，不把已完成/已停止/待处理重置为就绪 |
| 账号掉线事件 | 账号状态显示掉线；整轮及停止能力仍由实际worker决定 |
| unified_window_check | 显示真实status、healthy、main_ready及content_clicks |
| unified_leaders_detected | 更新队长标签和识别说明 |
| ProgressReader | 读取当前run_id的既有日志，保留增量索引及故障降级 |
| 组队日志/当前run的team_pipeline_latest.json | 补充组队任务；只有完成标记、回村与图片证据齐全才显示完成 |

陈旧运行的统一事件不覆盖当前运行。组队快照必须匹配当前run_id；原有账号身份、活动可用性及未决动作规则继续由原runner负责。行状态或退出码单独为零不能生成整轮完成。

## 更新与边界

前端原生状态轮询约900ms，后端进度读取约2s；轮询不重入，界面保持日志搜索焦点与滚动位置。日志显示最多1000条，任务明细保留既有有界缓存，完整记录仍在磁盘。

已接入：八种请求模式、开始、停止、预检、队长识别、本轮进度、日志、图片查看及导出。未实测：真实游戏整轮、不同机器WebView2/.NET环境、系统DPI、长时内存及拼图子进程。隔离替身用例标为离线，不代表实机通过。
