"""The shared desktop uses QWidget/WebEngine and has no QML documents.

Keep WebEngine's QtQml library dependencies without collecting unrelated QML
applications, plugins, Charts, Graphs or Quick3D from the developer's wheel.
"""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

# The desktop embeds no QML debugger/profiler; its Quick3D profiler alone
# would otherwise pull an unrelated Quick3DUtils runtime into the bundle.
binaries = [(source, destination) for source, destination in binaries
            if "qmltooling" not in str(destination).replace("\\", "/").split("/")]
