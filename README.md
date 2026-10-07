# SimpleBoard

**简体中文** | [English](README.en.md)

**一款轻量、离线、无需登录的 Windows 手写白板，为寻找 Microsoft Whiteboard 本地替代方案的用户提供一个选择。**

打开就能写，写完可以保存，也可以导出分享。SimpleBoard 专注于日常手写、草稿推演、课堂板书和会议讲解，让你在电脑上随时拥有一块可用的白板。

当前版本：**1.0.6 预览版** · 目标系统：**Windows 10 1809+ / Windows 11 x64** · 开源协议：**GPL-3.0-only**

![SimpleBoard 画布、缩放辅助网格与底部画笔栏](docs/images/simpleboard.png)

截图使用自动生成的测试笔迹，展示局部擦除、荧光笔和网格，不代表 Surface 真实书写轨迹。

## 为什么会有 SimpleBoard

微软宣布停用 Microsoft Whiteboard 独立应用后，习惯在桌面上打开白板、拿起笔就写的用户，需要一个可以继续使用的本地工具。SimpleBoard 正是出于这个需求而开发的：为日常手写和演示提供一个简单、轻量的白板选择。

有时候，我们需要的只是写一道题、画一张草图，或把一个想法讲清楚。SimpleBoard 希望让这些操作尽量直接：无需注册或登录账户，日常书写不依赖网络，白板文件由你保存在本机。

你可以把它看作面向**个人手写与本地演示**的微软白板“平替”。目前已提供画笔、荧光笔、擦除、套索、缩放以及保存和导出功能；多人协作、云端同步和微软白板文件导入不在当前支持范围内。

> 背景核实于 2026 年 10 月 6 日：微软官方公告的独立应用停用日期为 **2026 年 10 月 16 日**，涉及 Windows、iOS 和 Android。工作或学校账户仍可使用 Teams 和网页版 Whiteboard。详情见[微软官方停用公告](https://support.microsoft.com/zh-cn/whiteboard/retirement-standalone-microsoft-whiteboard-apps)。

## 下载与使用

源码与 Windows 便携包通过 **GitHub** 和 **Gitee** 提供，可按网络情况选择访问渠道。

| 渠道 | 下载入口 |
| --- | --- |
| GitHub | [源码仓库](https://github.com/renbingcheng/simpleboard) · [版本下载](https://github.com/renbingcheng/simpleboard/releases/tag/v1.0.6) |
| Gitee | [源码仓库](https://gitee.com/renbingcheng/simpleboard) · [版本下载](https://gitee.com/renbingcheng/simpleboard/releases) |

按以下步骤使用：

1. 打开上方任一版本下载页面，查看版本说明。
2. 下载 `SimpleBoard-<版本>-win-x64-onefile.zip`，例如 `SimpleBoard-1.0.6-win-x64-onefile.zip`。
3. 解压后双击 `SimpleBoard.exe`，即可开始使用，**无需安装 Python 或另行配置运行环境**。
4. 用 `Ctrl+S` 将白板保存为 `.qboard` 文件，下次可继续编辑；需要分享时，用 `Ctrl+Shift+E` 导出 PNG 或 PDF。

`source.zip` 是供开发者使用的源码包；直接使用软件请选择上述 Windows 运行包。单文件程序启动时会将运行组件解包到系统临时目录，设置和恢复草稿保存在应用数据目录。

当前为预览版，Surface Pen 的真实书写体验与不同设备的兼容性仍在验证。发布状态和已知问题请以对应版本说明为准。

## 界面语言

点击左上角 **☰ → 语言 / Language → English** 切换为英文；点击 **☰ → Language / 语言 → 简体中文** 切回中文。切换立即生效并记住选择，下次启动继续使用；新安装及旧版设置默认使用简体中文。

菜单、工具设置、提示、诊断面板、状态及应用错误和恢复对话框均提供中英文。切换会保留当前白板、选区、视图和撤销记录。文件名、文档内容和诊断 JSON 字段名不随语言改变。Windows 原生文件及颜色对话框跟随系统语言，Qt 标准按钮跟随应用语言。

源码启动也可指定语言：

```powershell
.\.venv\Scripts\python.exe main.py --language en
.\.venv\Scripts\python.exe main.py --language zh_CN
.\.venv\Scripts\python.exe main.py --language en --help
```

`SimpleBoard.exe` 同样支持 `--language`；启动时指定的语言会在下一次保存设置或正常退出时记住。新增翻译的方法见[翻译与文档维护](CONTRIBUTING.md#翻译与文档维护)。

## 可以做什么

- 六个可配置画笔槽，支持颜色、粗细、压感开关和灵敏度；再次点击当前工具打开设置。
- 荧光笔：同一笔自交不会重复加深，不同笔叠加会加深。
- 局部擦除和整笔擦除；局部擦除覆盖相邻采样之间的连续轨迹，保留可编辑的矢量文档。
- 基础套索选择、移动和删除；擦断后的残片仍属于同一逻辑笔划，会一起选中。
- 单指平移、双指缩放，鼠标中键平移、滚轮缩放；视图范围为 10%—800%。
- `.qboard` 保存与重开、异常退出后的恢复草稿、撤销与重做。
- 自选目录导出 PNG 或矢量 PDF。导出范围是**当前视图**，不含工具栏、网格、选框和橡皮光标；要分享全部内容，可先“适应全部笔迹”。
- 全屏、缩放辅助网格，以及可主动导出记录的输入诊断。

Surface Pen 笔尾用于临时擦除，侧键用于临时套索。笔接近时画布会抑制触摸导航，离开后重新触摸可继续操作。这些行为已覆盖软件回归，实际压感、掌托、侧键和笔尾仍需对应设备验收。

近期版本改进了笔尾擦除和压感平滑，具体变更与验证边界见 [版本记录](CHANGELOG.md)。

## 常用操作

| 操作 | 快捷键 |
| --- | --- |
| 新建 / 打开 / 保存 | `Ctrl+N` / `Ctrl+O` / `Ctrl+S` |
| 另存为 / 导出 PNG 或 PDF | `Ctrl+Shift+S` / `Ctrl+Shift+E` |
| 撤销 / 重做 | `Ctrl+Z` / `Ctrl+Y` 或 `Ctrl+Shift+Z` |
| 六个画笔槽 / 当前画笔 | `1`—`6` / `P` |
| 荧光笔 / 橡皮 / 套索 / 平移 | `H` / `E` / `L` / `V` |
| 删除所选笔迹 | `Delete` 或 `Backspace` |
| 100% / 适应全部笔迹 | `Ctrl+0` / `Ctrl+1` |
| 放大 / 缩小 | `Ctrl++` 或 `Ctrl+=` / `Ctrl+-` |
| 全屏 / 输入诊断 | `F11` / `F12` |
| 退出全屏或取消选择 | `Esc` |

左上角 ☰ 菜单提供保存、导出、语言切换和“始终显示网格”等操作。默认网格仅在缩放时出现，停止后淡出，不进入保存文档或导出文件。

## 保存、恢复与本地数据

`.qboard` 是继续编辑的文档格式；PNG/PDF 用于展示和分享，不能替代它。保存位置由你选择。文档文件采用先写同目录临时文件、再原子替换的方式提交；保存失败不会主动截断已有目标文件。

**1.0.5 及当前版本可读取格式 1 和 2，保存统一使用格式 2；1.0.4 及更早版本不能打开格式 2。** 旧笔迹保留 `legacy-v1` 渲染，新笔迹保存 `pressure-v2` 和落笔时的缩放比例 `input_scale`，避免升级或改变视图后重放成另一种粗细。需要回退旧程序时，请保留原文件副本；同一数据目录中新写的格式 2 恢复草稿也需要新版读取。

编辑停止约 2 秒后触发恢复保存，持续编辑时约每 10 秒触发一次；实际完成时间受文档大小和磁盘影响。恢复草稿不等于保存到指定文档，标题仍会保留未保存状态。异常退出后，下次启动可选择恢复、丢弃或取消。

Windows 本地数据目录为：

```text
%LOCALAPPDATA%\LocalWhiteboard\LocalWhiteboard
```

其中 `settings.json` 保存画笔、语言和最近文件设置，`recovery.qboard` 保存恢复草稿，`whiteboard.log` 记录运行错误。**SimpleBoard 继续使用历史上的 `LocalWhiteboard` 组织名和应用名**，以兼容旧版设置、恢复草稿和单实例锁；品牌更新不会迁移这些数据。程序一次只允许一个普通运行实例。

F12 输入诊断默认关闭。开启后仅在内存中保留最近 512 条原生笔采样、Qt 笔事件和操作边界，点击“导出输入记录…”才写出 JSON。应用没有遥测或自动上传功能；分享诊断或白板前请自行检查内容。

## 当前限制与验证状态

- 暂无文字框、图片导入、形状、直尺、模板、多人协作和微软白板格式兼容。
- 压感曲线、平滑和擦除是本项目自己的实现，不是 Microsoft Whiteboard 私有算法的复刻。
- Surface 物理笔、掌托、屏幕旋转、跨屏 DPI、休眠恢复和真实书写延迟尚待设备验收。Windows 系统合成笔检查未能保留预期笔尾标志，**不计为原生尾擦端到端通过**。
- 原生尾擦依赖 Windows Pointer API；接口不可用时保留 Qt 输入路径。若驱动本身报告真实的抬起与再次接触，软件会分开处理，不用延时猜测把两次擦除连起来。
- 大文档首次绘制和复杂擦除提交可能有停顿。图块缓存上限 128 MiB、几何缓存上限 64 MiB、撤销上限 200 次操作；这些限制不是整个进程的内存上限。
- PNG 按当前画布实际像素尺寸导出，受屏幕 DPI 影响。PDF 保留矢量轮廓，页面比例与当前画布一致。

1.0.5 已通过 220 项自动回归以及源码启动、保存重开和导出检查；这些结果不能代替 Surface 真机验收或具体发行包的验证。当前仍定位为预览版，Surface 真机、无开发环境的离线电脑及长期稳定性仍待验收。二进制发行还需处理[第三方依赖的源码交付及重新构建要求](docs/THIRD_PARTY.md)。

## 问题反馈

仓库开放后，欢迎通过 GitHub 或 Gitee 的 Issues 反馈问题和建议。反馈时请尽量提供软件版本、Windows 版本、设备与笔型号，以及可以复现问题的操作步骤。书写或擦除问题请说明鼠标、笔尖和笔尾各自的表现。

详细反馈方法见 [参与贡献](CONTRIBUTING.md)。

## 从源码运行

项目使用 Python、PySide6 / Qt Widgets 和 QPainter 开发，需要 **Python 3.11 x64**。在项目根目录打开 PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

第一条命令使用 Windows Python Launcher；没有 `py` 命令时，使用已安装的 Python 3.11 解释器创建虚拟环境。安装依赖需要可用的软件包源或预先准备的本地包，安装完成后的白板功能可离线使用。

也可以用启动脚本，或在命令末尾指定文档：

```powershell
.\launch.ps1
.\launch.ps1 -Python .\.venv\Scripts\python.exe -Document '.\课程演示.qboard'
.\.venv\Scripts\python.exe main.py '.\课程演示.qboard'
```

运行依赖固定为 `PySide6==6.11.1` 和 `pyclipper==1.4.0`，见 [requirements.txt](requirements.txt)。

## 开发与打包

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

默认构建单文件程序，输出为 `dist/SimpleBoard.exe`。目录式构建使用 `-Mode onedir`，适合检查依赖和调试。源码归档运行 `python scripts/package_source.py`；打包脚本使用明确的公开文件清单，包含中英文 README 与贡献指南，不依赖本地内部文档。

其他文档：[参与贡献](CONTRIBUTING.md) · [版本记录](CHANGELOG.md) · [第三方依赖](docs/THIRD_PARTY.md)。

## 许可证

本项目采用 **GPL-3.0-only**（GNU General Public License 第 3 版，仅此版本），完整条款见 [LICENSE](LICENSE)，官方文本见 [GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.html)。

第三方依赖保留各自许可证和归属，见 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) 和 [licenses/](licenses/)。它们的许可说明与本项目的 GPL 声明分别保留。

SimpleBoard 是独立开发的项目，与 Microsoft 无隶属或合作关系。Microsoft Whiteboard 名称仅用于说明项目背景和使用场景；本项目不读取或转换其 `.whiteboard` 文件。
