# 第三方软件、归属与重新构建

小鱼之家 0.2.0-beta.2 的上游核心来自大肥鱼。原许可和立场声明保留在根目录及 dafeiyu 目录；发行改动包括独立路径和进程平台、空白初始化、通用 OneBot 客户端、DPAPI 凭据、统一请求账本、本地聊天、迁入适配和发行配置界面。原作者署名、文件头及 core_ethos.py 保留。

## 主软件

- CPython：PSF License 及随附声明。
- PySide6 / Shiboken6 / Qt 6.12：使用动态链接的 QWidget、Qt WebEngine、WebChannel、Network、SVG 等所需模块，按所分发模块的 LGPLv3/GPLv3/第三方条款分别保留。Qt WebEngine 的 Chromium 第三方通知保留在 wheel 的 licenses 集合及 Qt 资源中。没有将 Qt Charts、Graphs、DataVisualization 或 Quick3D 作为产品功能。
- Node.js：随 runtime/node/LICENSE 保留 Node 与其依赖的完整通知。
- APScheduler、Pillow、NumPy、Cryptography、PyYAML、psutil、Zstandard、PyWinRT 与桥接 npm 依赖：见 `third_party/inventory.json` 及对应许可证目录。
- PyInstaller 的引导加载器按其例外条款使用；构建工具不因被列入构建输入清单而自动成为发行运行依赖。

## 可选组件

- DeepSeek Harness 官方 npm 发行 `@deepseek-ai/dsh@0.2.1-rc.3`：MIT，及其每个依赖自有许可。保留组件原文件、package-lock 和许可证；其中 LibreOffice kit 的 MPL/第三方通知与源码说明保留在原包 licenses、sources 目录。没有配置使用者的账号或模型 Key。
- 离线英文到中文：CTranslate2（MIT）、SentencePiece（Apache-2.0）和 OPUS en→zh 1.9 模型。原 OPUS 模型按 CC-BY-4.0；归属 Jörg Tiedemann、Santhosh Thottingal，2020，OPUS-MT。保留模型原 README、metadata 和引用。Argos 公共模型档案地址 https://argos-net.com/v1/translate-en_zh-1_9.argosmodel ，SHA-256 `433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab`。本发行增加独立本地运行封装，未更改模型权重。

## LGPL 动态库替换与源码

本软件采用 PyInstaller onedir 目录，未加密 Python 代码，也没有校验锁阻止替换 Qt/PySide6 库。可备份安装目录后，在 `_internal/PySide6`、`_internal/shiboken6` 中替换 ABI 匹配的自行修改版本；保持对应 DLL、pyd、插件和 WebEngine 资源一致。也可用随附完整应用源码及 BUILD.md 重新构建，让修改的库重新链接。对库修改的调试和兼容分析不受本项目额外禁止。请在独立副本操作并保留个人数据。

对应 Qt/PySide6 源码由 Qt 官方提供：https://download.qt.io/archive/qt/6.12/ 与 https://code.qt.io/cgit/pyside/pyside-setup.git/ （匹配 v6.12.0）；CPython、各 npm/Python 包源码出处见其原元数据。发布者须随修改后的二进制持续提供对应版本应用源码和适用的库源码获取方式，不以本说明代替第三方条款。

`third_party/inventory.json` 是构建输入的许可与来源索引，包含构建/测试依赖；实际二进制文件清单另列于发行清单。少数上游 npm 包只在 README/package.json 中声明 MIT，没有单独 LICENSE 文件，本发行保留其原始声明与作者元数据，索引中单独标注，未虚构版权持有人或额外授权。未包含 QQ 登录客户端、SnowLuma SDK/运行时、声音权重、用户素材、FFmpeg 二进制或私人配置。
