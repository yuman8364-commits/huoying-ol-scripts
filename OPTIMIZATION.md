# 2026.10.24.2-local 优化说明

基线：2026.10.23.8，提交 fe51a66014dd76f65c582638df302aee22df9533。本版本为本地交付版本，基于 2026.10.24.1-local 复核后修复。

## 2026.10.24.2-local 修复内容（相对 2026.10.24.1-local）

- **进度索引不再绑定客户端可用性**：索引按需惰性创建；系统临时目录不可写或 SQLite 初始化失败时，客户端与任务入口照常可用，进度面板显示“进度索引不可用，待刷新”。首次失败返回空的未知状态快照；已有有效快照时保留上次核实结果并标注刷新失败。空数据与索引故障不会被解释成任务完成。
- **运行期 SQLite 故障降级**：查询、写入或提交抛出 `sqlite3.Error` 时丢弃损坏索引（临时目录随即清理）、保留上次核实快照；重建投影仅在全部成功后替换，未提交事务不会出现在界面统计里。后续刷新自动重建索引并恢复正确计数，不重复计数、不积累重复错误。
- **界面清理**：`ProgressView` 关闭时取消 2 秒定时刷新；构造中途失败（如无 reader）时 `destroy` 不再抛错。
- **日志目录发现独立成纯函数** `discover_log_roots`；调度器回溯旧凭据路径不再创建临时索引或进度读取器，查找范围与顺序不变。
- **达摩独立入口线程默认**：`daruma_standalone` 包在首次导入 NumPy 前调用 `configure_numeric_runtime()`（独立捆绑缺该模块时保持原默认）；开发回放入口 `offline_full_flow.py` 同步补齐。用户已设 `OPENBLAS_NUM_THREADS` 时一律保留。
- **暂停审计测试编码**：测试读取 UTF-8 暂停记录时显式指定编码，非 UTF-8 控制台模式下不再乱码；该用例恢复到正常专项集合。生产代码读写本就显式 UTF-8，未改动。

## 使用成品

普通使用下载 standard 标准版，解压后运行“火影OL完整客户端.exe”。完整功能继续通过原有任务入口执行；开始后才运行游戏任务。F12、账号核验、未决动作保护和购买禁用沿用原有规则。

vision 视觉诊断版包含当前 ONNX 模型及 CPU 运行库，不包含开发候选模型。shadow 推理默认关闭；确需诊断时，在使用入口对应 INI 中设 `EnableShadowDetection=1`。shadow 结果不授权点击。标准版不提供模型推理。

将本版本解压到独立目录，配置和断点格式兼容。运行日志、截图及断点保留在磁盘；界面每个任务显示最近 100 条明细，统计仍读取完整历史。临时 SQLite 索引位于系统临时目录、按需创建，界面销毁时清理；索引创建或读取失败只影响进度面板显示（明确提示待刷新），不影响任务执行，也不改写源日志或断点。

## 内存和结构

- 日志仅解码新追加记录，通过临时磁盘索引处理去重、顺序和计数；日志截断、替换或乱序追加触发流式重建。
- 小 JSON 文件缓存最多 32 项，累计原始文件内容最多 8 MiB；切换运行清空无关缓存。
- 同帧 ROI 数组缓存保留预算 32 MiB，模板频谱预算保持 64 MiB；识别计算的临时峰值不受保留预算约束。
- 回执保存后释放 runner、嵌套调度器、截图及识别缓存；磁盘记录继续保留。
- NumPy 导入前将 OpenBLAS 默认线程数设为 2，尊重已有 `OPENBLAS_NUM_THREADS` 环境变量。保留 float64 及既有识别阈值。
- 将 799 行基础实现抽离至 `hyol_vision.py`、`hyol_platform.py`、`hyol_storage.py` 和共享异常模块；`hyol_scheduler.py` 保留兼容导出。两套达摩引擎继续各自工作。

## 源码与构建

Windows、PowerShell 7、Python 3.12。基础运行依赖在 requirements.txt；构建依赖在 requirements-build.txt；可选模型依赖在 requirements-vision.txt。历史训练环境单独保存在 training-requirements.lock.txt。

```powershell
py -3.12 -m venv .build-env
.\.build-env\Scripts\python.exe -m pip install -r requirements-build.txt
.\build_unified_client.ps1
```

可选诊断构建先安装 requirements-vision.txt，再运行 `build_unified_client.ps1 -WithVision`。可用 `-PythonPath` 指定已有环境，用 `-OutputRoot` 指定新输出目录。默认构建不会安装到桌面；显式 `-InstallDesktop` 会创建独立版本目录。

## 回放资料

开发源码和 replay-data 回放资料分包。对需要图像夹具的专项验证，将同版本 replay-data ZIP 的内容解压到源码根目录。ZIP 内含按相对路径分类的 tests/fixtures、fixtures、daruma_standalone/tests，以及 models/candidates 开发候选资料。原始历史运行截图未随原发布提供，仍不在本交付范围。

## 已执行的验证与边界

详细体积、内存、专项检查和 GUI 证据见交付目录的报告。离线回放使用固定随包截图与合成审计日志，确认分数、判定、坐标及计数相同；不代表真实游戏整轮耗时或实机验收。拼图后端本轮未启动游戏任务，其进程内存没有伪造测量值。
