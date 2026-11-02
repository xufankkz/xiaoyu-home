from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files
root = Path(SPECPATH).parent
binaries = collect_dynamic_libs('ctranslate2')
datas = collect_data_files('ctranslate2')
a=Analysis([str(root/'xiaoyu-web/local_thought_worker.py')],pathex=[str(root/'xiaoyu-web')],binaries=binaries,datas=datas,
           hiddenimports=['ctranslate2','sentencepiece','numpy'],excludes=['torch','spacy','stanza','transformers','PySide6','matplotlib','PIL','tkinter'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='XiaoyuTranslator',debug=False,strip=False,upx=False,console=True)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='XiaoyuTranslator')
