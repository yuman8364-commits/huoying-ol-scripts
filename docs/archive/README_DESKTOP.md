# 火影OL 桌面客户端

当前版本：**2026.11.05.1-opencv**，Windows 10/11 64 位，OpenCV 标准版。

[下载最新目录版](https://github.com/yuman8364-commits/huoying-ol-scripts/releases/tag/2026.11.05.1-opencv)

下载发布页中的 `huoying-ol-desktop-folder-2026.11.05.1-opencv.zip`，完整解压后双击 `火影OL.exe`。保留文件夹里的配套文件；无需安装 Python，需要系统 WebView2 Runtime 与 .NET Framework 4.8。打开后保持空闲，选择任务和账号范围再开始；F12 或停止按钮结束本轮。

## 本次更新

- 新增独立「周年庆活动」板块，支持薪火签到，可处理前 1～6 个账号标签；按实际解锁日核对 10 天，活动未出现或到期时略过。
- 已领取项目不重复领取；领取结果未知时保留凭据，重新启动后先核对。周年庆结束后关闭托管。
- 修复空闲页面的排位入口缺失导致后续任务延后，以及恢复任务前账号绑定未重新取得的问题。
- 目录版可改名、移动和整体压缩分享；设置与共享账本保存在当前 Windows 用户的 `%LOCALAPPDATA%\HYOLFolderClient`。

程序还包含单人日常、竞技场、生存、修行录、组队、独立羁绊、拼图与达摩等既有入口。购买保持禁用；执行时保持游戏窗口可见，避免同时运行多个控制游戏的脚本。独立羁绊须手动组队并打开准备页，再由程序进行匹配、观察当前账号自动战斗状态和结算。

## 源码运行与构建

在 Windows 使用 Python 3.12：

```powershell
python -m venv .build-env
.\.build-env\Scripts\python.exe -m pip install -r requirements-build.txt -r requirements-desktop.txt
.\.build-env\Scripts\python.exe hyol_desktop.py
```

构建同类干净目录版，请使用新的输出目录：

```powershell
.\.build-env\Scripts\python.exe -B tools/build_shareable_folder.py --output releases/my-folder-build
```

可独立运行的公开专项：

```powershell
.\.build-env\Scripts\python.exe -B -m unittest -v test_hyol_skip_continue_20261105 test_template_spectra test_hyol_opencv test_hyol_independent_bond_20261031
```

部分历史回放依赖制作者本机的截图和状态资料，未随源码公开；历史测试目录不等于完整可独立运行的验收集。

## 验证范围

本次已在本机完成最终完整离线回归：19 阶段、2158 项，0 失败，2 项既有跳过。目录成品内嵌 68 个项目模块与源码一致，669 项资源检查、1746 个文件清单、隐私检查、ZIP CRC 和原生空闲启动验证通过。发布前重新检查了源码基线和下载包哈希。

实机仅验证本轮单账号签到：第 1 天领取 1 份，重开新增 0 份，第 2 天锁定并回村。第 2～10 天尚未自然解锁；入口缺失的修复已通过离线复现，未重跑原战斗。历史未决战斗、领奖和组队任务继续保留，不计为本次完成。

本次发布的目录包和独立源码包不含制作者个人配置、账号素材、游戏登录数据、运行账本或本机日志。使用后产生的数据属于使用者，再分享请使用未运行过的原始压缩包。原拼图组件以既有哈希校验携带，其内部加密脚本未做完整源码审计。

仓库保留原有提交历史；旧说明与回放中的历史游戏昵称、本机路径仍可能出现在旧版本中。
