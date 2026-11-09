# 修改、运行和重新打包

## 环境

在 Windows 10/11 x64 使用 Python 3.12，准备 WebView2 Runtime、.NET Framework 4.8。在仓库根目录或标准版 `source/` 中执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r client/requirements-build.txt -r client/requirements-desktop.txt
.\.venv\Scripts\python.exe -B launch.py --demo
```

`--demo` 只演示界面。普通 `launch.py` 连接真实运行层，但启动后不自动执行任务。界面静态资源直接编辑 `client/web/`，任务实现编辑 `client/hyol_*.py`。账号识别、原动作恢复、次数与奖励核验分别由运行层处理。

标准版附带的是与本版成品对应的源码。修改它不会改变已经编译好的 EXE；通过源码入口验证后再构建自己的成品。

## 公开测试

从源码根目录执行：

```powershell
.\.venv\Scripts\python.exe -B check.py
```

公开测试使用合成画面、替身控制器和临时目录，不向游戏发送输入。历史测试位于 `tests/legacy/`，部分依赖未发布的私人回放资料；它们不计入公开测试集。

## 构建标准版

在 `client/` 中执行。每次使用不存在的新输出目录，保留旧成品：

```powershell
..\.venv\Scripts\python.exe -B tools/build_shareable_folder.py --output releases/my-standard-build
```

得到 `releases/my-standard-build/dist/火影OL最终版/火影OL.exe` 及配套文件、ZIP、源码一致性与资源审计报告。整个文件夹一起分发。此构建器生成运行成品；发布页的标准版还另外附加同版 `source/` 供修改。

## 构建 EXE版

仍在 `client/` 中执行：

```powershell
..\.venv\Scripts\python.exe -B tools/build_shareable_exe.py --output releases/my-exe-build --name 火影OL_自定义版
```

得到 `releases/my-exe-build/dist/火影OL_自定义版.exe`。资源、默认配置和 Python 运行库嵌入 EXE，接收者无需另装 Python。首次运行仍会在当前用户目录生成自己的配置和状态。

## 核对源码和空闲启动

```powershell
..\.venv\Scripts\python.exe -B tools/verify_packaged_source.py releases/my-exe-build/dist/火影OL_自定义版.exe --root .
```

源码比对应确认 `source_code_matches=true`。EXE 的 `--hidden --smoke-report <绝对输出路径>` 参数用于原生空闲启动核验，不运行游戏任务；以回执里的 `success`、`mode=native`、`game_input_count=0`、`game_task_started=false` 和后台空闲状态判断。

打包工具只纳入通用配置和运行必需模板，排除私人账号素材与日志。不要把自己使用后生成的账号、日志、断点或登录缓存打包上传。原拼图组件保留既有 SHA256，源脚本未包含在仓库；有关第三方范围见 `THIRD_PARTY_NOTICES.md`。
