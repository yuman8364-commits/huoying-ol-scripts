# 火影ol全自动客户端

Windows 桌面自动化客户端 · OpenCV 标准版

[下载客户端](https://github.com/yuman8364-commits/huoying-ol-scripts/releases/latest) · [版本说明](docs/archive/ACCEPTANCE_REPORT.json)

下载发布页的 desktop-folder ZIP（88.4MiB），完整解压后双击“火影OL.exe”。无需安装 Python；需要 Windows 10/11 64 位、WebView2 Runtime 和 .NET Framework 4.8。打开后选择任务再开始，F12 或停止按钮结束。

支持日常、竞技场、生存、修行录、组队、独立羁绊、拼图、达摩及周年庆薪火签到。未知操作保留凭据，不自动重发。

| 目录 | 内容 |
| --- | --- |
| client/ | 客户端源码、运行配置、通用识别资源与界面 |
| client/tools/ | 构建、校验和维护工具 |
| tests/portable/ | 可独立运行的公开专项测试 |
| tests/legacy/ | 历史测试，部分需要本机回放资料 |
| docs/archive/ | 历史说明、验收与源码清单 |

源码启动：在 client 目录创建环境，安装 requirements-build.txt 和 requirements-desktop.txt，然后从仓库根目录执行 `python launch.py`。

公开专项：在仓库根目录执行 `python check.py`（31项）。历史测试不作为独立可运行的验收集。

构建：进入 client 目录，执行 `python tools/build_shareable_folder.py --output releases/my-folder-build`，使用新的输出目录。

本次整理仅移动仓库目录，现有下载包保持不变。原完整离线2158项包含2项既有跳过；实机仅有单账号首日签到验收，后续签到日未自然解锁，历史未知任务保留。目录包和独立源码包不含本机运行日志；旧提交历史中的游戏昵称和路径保留。
