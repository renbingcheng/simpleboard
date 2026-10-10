# 第三方依赖与发行说明

SimpleBoard 自身代码采用 **GPL-3.0-only**，见根目录 [`LICENSE`](../LICENSE)。该选择不改变第三方组件原有的许可，不能将所有依赖统一改写为本项目许可证。

本清单依据 `requirements.txt`、`requirements-dev.txt`、`simpleboard.spec`、现有许可证原文和 [`licenses/sources.json`](../licenses/sources.json)。它是发布准备记录，**不是已完成全部发行义务的证明**。

## 当前版本与来源

| 组件 | 版本与作用 | 许可与本地声明 | 对应上游源码 |
| --- | --- | --- | --- |
| CPython | 参考构建为 3.11.2；项目基于 Python 3.11，requirements 未锁解释器补丁版本 | PSF 及历史、随发行版依赖条款；[`python/LICENSE`](../licenses/python/LICENSE)、[`WINDOWS-DISTRIBUTION-LICENSE.txt`](../licenses/python/WINDOWS-DISTRIBUTION-LICENSE.txt) | [CPython v3.11.2](https://github.com/python/cpython/tree/v3.11.2) |
| PySide6 / Shiboken6 | 6.11.1；Qt Python 绑定与绑定运行库。PySide6 元包会安装同版本 Essentials、Addons 和 Shiboken6；安装了模块不代表应用打包使用它 | 所用库采用适用的 LGPL-3.0 选项；文件另有 GPL/商业选项及第三方条款；[`pyside-setup/LICENSES`](../licenses/pyside-setup/LICENSES) | [pyside-setup v6.11.1](https://github.com/pyside/pyside-setup/tree/v6.11.1) |
| Qt | 6.11.1；主要为 Core、GUI、Widgets 和 Windows 平台插件；PDF 写出使用 QtGui 的 QPdfWriter | 适用的 LGPL-3.0 选项及内含组件条款；[`qtbase/LICENSES`](../licenses/qtbase/LICENSES) 与各 `qt_attribution.json` | [qtbase v6.11.1](https://github.com/qt/qtbase/tree/v6.11.1) |
| pyclipper / Clipper | pyclipper 1.4.0，内含 Clipper 6.4.2；笔迹轮廓布尔运算 | MIT / BSL-1.0；[`MIT 原文`](../licenses/pyclipper/LICENSE)、[`Clipper 声明`](../licenses/pyclipper/clipper/NOTICE.txt)、[`Boost 原文`](../licenses/pyclipper/clipper/LICENSE_1_0.txt) | [pyclipper 1.4.0，含 Clipper 源码](https://github.com/fonttools/pyclipper/tree/1.4.0) |
| PyInstaller | 6.12.0；构建工具，生成物含 bootloader 与相关运行文件 | GPL-2.0-or-later 加 bootloader exception；运行 hooks 为 Apache-2.0；以 [`COPYING.txt`](../licenses/pyinstaller/COPYING.txt) 的文件范围为准 | [PyInstaller v6.12.0](https://github.com/pyinstaller/pyinstaller/tree/v6.12.0) |

Qt/PySide6 文件头提供多种许可选项，但不能据此认定所有 Qt 模块均有 LGPL。现用库可参见 6.11.1 标签下的 [Qt Core 文件头](https://github.com/qt/qtbase/blob/v6.11.1/src/corelib/kernel/qobject.cpp)、[PySide 文件头](https://github.com/pyside/pyside-setup/blob/v6.11.1/sources/pyside6/libpyside/pyside.cpp) 和 [Shiboken 文件头](https://github.com/pyside/pyside-setup/blob/v6.11.1/sources/shiboken6/libshiboken/basewrapper.cpp)。新增模块、插件或第三方素材时需单独核对。商业许可引用或 Qt exception 的存在，不表示项目取得了商业授权，也不把例外扩大到未受其覆盖的文件。

`licenses/qtsvg/` 和 `licenses/qtimageformats/` 保留 [qtsvg v6.11.1](https://github.com/qt/qtsvg/tree/v6.11.1)、[qtimageformats v6.11.1](https://github.com/qt/qtimageformats/tree/v6.11.1) 声明。1.0.7 的 spec 明确保留 `qwindows.dll`、`qoffscreen.dll`、`qico.dll` 和用于 JPEG 导入的 `qjpeg.dll`，Qt 翻译仅保留 `qtbase_zh_CN.qm`；英文使用 Qt 原文。当前 spec 排除 Qt6Svg、Qt6Pdf、Qt6Network、Python SSL/OpenSSL 运行组件及多数可选插件；这些是偏保守的上游材料集合，不能反推二进制实际组件。pytest 等开发依赖也不应直接列为应用运行库。发行时需核对实际 DLL、PYD、运行 hooks、插件与 Windows 运行时文件。

PyInstaller 的例外允许其规定范围内的 bootloader 随应用组合分发，**不豁免其他依赖的义务**。单独分发或修改其代码，与仅使用工具打包的条件不同。参见 [6.12.0 官方说明](https://pyinstaller.org/en/v6.12.0/license.html) 和本地完整 COPYING。

## 声明清单的范围

- `licenses/sources.json` 记录 137 份许可证或 attribution 文件，每项有来源 URL、版本/标签、字节数和 SHA-256；Qt 四个仓库另有 tree SHA。这些是声明文件的哈希，**不是 wheel、DLL 或源码归档的哈希**。
- 来源是 Qt/PySide、Python、PyInstaller、fonttools/pyclipper 官方上游仓库，以及 Boost 官方许可文本。上游作者姓名、版权邮箱应保留，不能将其当作本项目维护者联系方式。
- `qt_attribution.json` 保存 Qt 内含组件的版本、版权与许可引用，并非完整源码。`unresolved_references` 为空只表示收集器选取的声明引用已处理，不表示实际二进制已完成全面审计。
- Shiboken 的嵌入 Python 许可使用上游单独的 `PSF-3.7.0.txt` 保存，映射见 `embedded_license_references`；Clipper NOTICE 保存 1.4.0 标签 `clipper.hpp` 的完整开头声明。
- 仓库没有附带 Qt/PySide6 等完整上游源码或完整 wheel 构建配方。应用源码 ZIP、上游 URL 和许可证目录，不能被描述为“已附全部第三方对应源码”。

下面的命令离线校验声明文件，不下载内容：

```powershell
python scripts/prepare_licenses.py
```

只有 `--refresh` / `--refresh-pyclipper` 会联网收集声明。Qt 标签固定为 `v6.11.1`，pyclipper 固定为 `1.4.0`，但全量刷新时 CPython/PyInstaller 版本取自执行环境。换解释器或构建工具后，应对照实际发行组件更新、审核清单；不要未经审查覆盖已发布版本的出处记录。该脚本不由白板应用导入或执行。

## 对应源码与用户修改库

项目采用 GPL-3.0-only，发布应用二进制时仍需按 GPL 处理本应用的对应源码与构建材料。LGPL 库另按其适用条款处理；以下依据本地 [LGPL v3](../licenses/qtbase/LICENSES/LGPL-3.0-only.txt) 第 4 节及其纳入的 [GPL v3](../licenses/qtbase/LICENSES/GPL-3.0-only.txt)。[Qt 官方义务说明](https://www.qt.io/development/open-source-lgpl-obligations)也要求处理所用库的对应源码，即使库未修改。

发行者应为**二进制实际使用的版本**提供适用方式的完整对应源码，包括修改、必需的构建脚本/配置等。上表标签便于定位，但项目主页或可变下载链接不能自动证明已履行交付要求。选择 GPL 第 6 节的下载、书面要约等方式时，应满足对应条件与期限；本文件本身不是源码书面要约。

LGPL 4(d) 允许提供适合重新组合/链接的相应材料，或使用能够运行接口兼容修改版库的合适共享库机制。还应保留醒目的库使用声明、LGPL/GPL 文本，不得限制修改库或为调试修改进行的逆向工程；条款要求时须提供必要的安装信息。“源码可下载”与“重新组合已验证可行”是两件事。

## 源码运行、onedir 与 onefile

| 交付方式 | 修改依赖的工程路径 | 仍需验证的事项 |
| --- | --- | --- |
| 仅应用源码 | 在独立 Python 3.11 x64 环境安装兼容依赖，运行 `python main.py`；可安装自行构建的匹配 Qt/PySide6/Shiboken6 | 源码包不含这些运行库，不是完整离线运行包或全部第三方源码包 |
| PyInstaller onedir | 关闭应用，在发行目录副本中替换 `_internal/PySide6/`、`_internal/shiboken6/` 下兼容 DLL/PYD 与插件，或用源码重构 | 以实际文件清单为准；Python ABI、架构、Qt/PySide/Shiboken 版本与构建选项须匹配，不能假定只替换一个 DLL 就兼容 |
| PyInstaller onefile | 在构建环境安装用户修改的兼容库后重新打包；提供并验证该路线或其他符合条款的方案 | `_MEI...` 临时解包不是稳定替换接口，修改可能在退出后消失或下次被覆盖。嵌入动态 DLL 不自动满足 LGPL；另发 onedir 也不自动免除 onefile 自身义务 |

以下说明应用重构入口，不代替上游完整构建说明，也不承诺不同工具链产生逐字节相同的 EXE：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# 在这个环境安装自行构建、接口兼容的一整组 Qt/PySide6/Shiboken6。
# 保留其许可证、补丁和构建记录；之后不要用原始 wheel 覆盖修改版。
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$env:QBOARD_BUILD_MODE = 'onedir'
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean simpleboard.spec
```

首次准备 Python/依赖可能需要网络，白板运行不需要。先在源码运行中检查版本与 DLL 加载路径，再构建并验证绘制、文件读写、导出、笔输入和恢复。将 `QBOARD_BUILD_MODE` 改为 `onefile` 可使用单文件入口。**仓库尚未完成“用户修改 Qt/PySide6 后重新运行两类发行包”的专项验收**，上述流程不是已执行成功的记录。

## 二进制发布材料

1. 记录精确解释器、wheel、DLL/PYD、插件与 bootloader 的版本/哈希及构建配置，核对变更组件的许可；Windows 运行时 DLL 若随包复制也需核对再分发依据。
2. 落实对应源码交付方式，包含必要修改与构建材料；只发应用源码、依赖链接或许可证文本不能替代此项。
3. 在最终产物实测兼容修改版库的替换或重新组合、安装与运行，保留用户操作说明。
4. 将 `THIRD_PARTY_NOTICES.txt`、`licenses/` 与本说明作为可直接阅读的发布附件；即使 EXE 内有副本，也不要让用户只能启动程序才能阅读许可。检查最终 ZIP 条目。
5. 源码发布排除个人白板、恢复副本、机器路径、输入诊断和原始运行日志；清理后的验证记录区分“已测试”与“待验证”。

本文件未自动下载第三方源码大包，也不承诺未完成的发行义务已经满足。
