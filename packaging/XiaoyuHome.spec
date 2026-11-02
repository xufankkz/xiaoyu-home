# Rebuild from this source tree after pip install -r requirements-build.txt and npm ci.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs
root = Path(SPECPATH).parent
pathex = [str(root), str(root/'dafeiyu'), str(root/'xiaoyu-web')]
import sys
import os
import PySide6
sys.path[:0] = pathex
# Dependency discovery must not use libraries injected by unrelated applications
# on the developer's PATH (for example Poppler's incompatible ICU).
os.environ['PATH'] = os.pathsep.join(str(p) for p in (
    Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix)/'DLLs',
    Path(PySide6.__file__).parent, Path(PySide6.__file__).parent.parent/'shiboken6',
    Path(os.environ['SystemRoot'])/'System32', Path(os.environ['SystemRoot'])))
datas = []
for group in ('dafeiyu','xiaoyu-web','qq-bridge','qzone-service','diary-service','meme-service','media-service','runtime','home_runtime','third_party'):
    for file in (root/group).rglob('*'):
        relative = file.relative_to(root)
        if file.name.startswith('verify-') or file.name.startswith('test-'):
            continue
        if group == 'xiaoyu-web' and file.name == 'local_thought_worker.py':
            continue  # The independently packaged translator owns this entry point.
        if not file.is_file() or any(part in ('__pycache__','.git','.venv') for part in relative.parts):
            continue
        if 'node_modules' not in relative.parts and any(part in ('data','state','logs','output') for part in relative.parts):
            continue
        if group == 'home_runtime' and file.suffix != '.mjs':
            continue
        datas.append((str(file), str(Path('source')/relative.parent)))
hidden = collect_submodules('core') + collect_submodules('home_runtime')
hidden += [file.stem for file in (root/'xiaoyu-web').glob('*.py') if not file.stem.startswith(('test','verify')) and file.stem != 'local_thought_worker']
hidden += ['ui.web_window','PySide6.QtSvg','PySide6.QtWebChannel','PySide6.QtNetwork',
           'winrt.windows.media.ocr','winrt.windows.globalization','winrt.windows.graphics.imaging',
           'winrt.windows.storage.streams','winrt.windows.foundation','winrt.windows.foundation.collections',
           'PIL.ImageGrab','numpy','zstandard','yaml','apscheduler.schedulers.background']
a=Analysis([str(root/'launcher.py')],pathex=pathex,binaries=[],datas=datas,hiddenimports=hidden,
           hookspath=[str(root/'packaging/hooks')],
           excludes=['torch','spacy','stanza','ctranslate2','sentencepiece','tkinter','matplotlib','pytest','IPython','imageio_ffmpeg'],noarchive=False)
# Fail closed if a build machine's unrelated runtime would leak into the product.
allowed_roots = [root.resolve(), Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), Path(os.environ['SystemRoot']).resolve()]
for destination, source, kind in a.binaries:
    if not any(Path(source).resolve().is_relative_to(p) for p in allowed_roots):
        raise RuntimeError('Unexpected binary dependency outside build inputs: ' + destination)
for destination, source, kind in a.binaries:
    if any(name in destination for name in ('Qt6Charts', 'Qt6Graphs', 'Qt6DataVisualization', 'Qt6Quick3D', 'Qt6Pdf', 'Qt6VirtualKeyboard', 'Qt6SerialPort')):
        raise RuntimeError('Unneeded Qt module must not enter the release: ' + destination)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='XiaoyuHome',debug=False,bootloader_ignore_signals=False,
        strip=False,upx=False,console=False,disable_windowed_traceback=False,icon=str(root/'xiaoyu-web/static/app.ico'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='XiaoyuHome')
