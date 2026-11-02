"""Keep only plugins used by the embedded desktop's supported inputs."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies
hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, destination) for source, destination in binaries if Path(source).name.lower() not in ('qpdf.dll', 'qtvirtualkeyboardplugin.dll')]
