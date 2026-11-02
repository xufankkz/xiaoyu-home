"""Inventory installed build inputs and preserve all available license texts."""
from pathlib import Path
import importlib.metadata as md
import hashlib
import json
import shutil
import sys

source = Path(__file__).resolve().parents[1]
build = source.parent
out = source / 'third_party'
out.mkdir(exist_ok=True)
rows = []

for distribution in sorted(md.distributions(), key=lambda d: d.metadata.get('Name','').lower()):
    name, version = distribution.metadata['Name'], distribution.version
    licenses = []
    for member in distribution.files or []:
        parts = str(member).lower().replace('\\','/').split('/')
        if not any(part.startswith(('license','licence','copying','notice','copyright','authors')) for part in parts):
            continue
        file = Path(distribution.locate_file(member))
        if not file.is_file():
            continue
        target = out / 'python' / (name + '-' + version) / Path(*[part for part in Path(str(member)).parts if part != '..'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        licenses.append(target.relative_to(source).as_posix())
    rows.append({'ecosystem':'python','name':name,'version':version,
                 'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License',''),
                 'texts':licenses, 'source':distribution.metadata.get('Home-page','')})

for group, directory in [('bridge',source/'qq-bridge/node_modules'),('dsh',build/'components/dsh/node_modules')]:
    for manifest in directory.rglob('package.json'):
        try:
            data = json.loads(manifest.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        name, version = data.get('name'), data.get('version')
        if not name or not version:
            continue
        rel = manifest.parent.relative_to(directory)
        # Actual package roots, excluding examples, fixtures and vendored subtrees.
        if len(rel.parts) > 2 and rel.parts[-2] != 'node_modules' and not (len(rel.parts)>=3 and rel.parts[-3]=='node_modules' and rel.parts[-2].startswith('@')):
            continue
        texts = []
        for file in manifest.parent.iterdir():
            if file.is_file() and file.name.lower().startswith(('license','licence','notice','copying','copyright')):
                target = out/'npm'/group/(name.replace('/','__')+'-'+version)/file.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file,target)
                texts.append(target.relative_to(source).as_posix())
        rows.append({'ecosystem':'npm','group':group,'name':name,'version':version,'license':data.get('license','UNKNOWN'),
                     'texts':texts,'source':data.get('repository',{})})

(out/'inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'packages':len(rows),'unknown_license':sum(not row['license'] or row['license']=='UNKNOWN' for row in rows),
                  'missing_text':sum(not row['texts'] for row in rows)},ensure_ascii=False))
