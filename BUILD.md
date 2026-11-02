# 构建与替换运行库

需要Windows x64、Python3.12.10、Node24.22.1、npm和Inno Setup7.1.1。构建在独立目录进行，不读取任何已有小鱼安装。源代码目录称为SOURCE，其父目录容纳构建产物。所有命令由开发者主动执行；运行软件不自动下载组件。

1. 在SOURCE创建虚拟环境，安装`requirements-build.txt`，再安装Playwright所需测试浏览器（本机也可使用Edge）。
2. 将匹配版本的官方Node `node.exe`与`LICENSE`放在`runtime/node`，核对README中的SHA-256。
3. 在`qq-bridge`执行`npm ci --ignore-scripts --no-audit --no-fund`，保留锁文件及原许可。
4. 从SOURCE运行`python -m unittest discover -s tests -v`，以及`runtime\node\node.exe qq-bridge\test-onebot.mjs`。这些测试仅用合成资料和本地模拟服务，不发QQ或调用收费模型。
5. 构建翻译器：`python -m PyInstaller --noconfirm --distpath ../dist-translator --workpath ../build-translator packaging/XiaoyuTranslator.spec`。
6. 按第三方说明下载公共OPUS模型档案，核对SHA-256。执行`python packaging/prepare-components.py --model-archive <档案路径>`，用锁文件安装官方DSH组件并组装翻译组件。
7. 从SOURCE构建主体：`python -m PyInstaller --noconfirm --distpath ../dist-longpath --workpath ../build-longpath packaging/XiaoyuHome.spec`。spec限制二进制依赖来源，避免本机其他应用的DLL混入。动态Qt模块只收集WebEngine/Widgets所需项，不收集整个QML应用目录。
8. 用Inno的ISCC分别编译`packaging/software.iss`与`packaging/components.iss`，输出到SOURCE父目录的artifacts。

独立启动验证可指定`--data-dir <空白目录>`，`--serve`仅打开本地服务，`--stop --data-dir <同一目录>`只请求该实例退出。GUI验证器为`xiaoyu-web/verify-desktop-parity.py --instance <测试数据目录> --output <报告目录>`。不要对个人生产实例运行合成聊天、迁入或设置测试。

发行时附上完整应用源码、当前依赖锁文件、第三方声明、二进制清单与哈希。源码包不包含虚拟环境、node_modules、个人数据、验证报告或构建日志。`third_party/inventory.json`包括构建输入，不能当作全部已分发模块的精确清单。PyInstaller生成的库位于`_internal`，保留动态库替换能力，修改库或应用后可按以上步骤重建。

首次发行是在本机的独立目录验收，未以新购置的完全干净Windows虚拟机替代；第三方QQ连接端、模型和声音需要下载者自行配置并进行端到端验证。安装包为测试版，不把保留源码的功能等同于所有外部环境的兼容保证。
