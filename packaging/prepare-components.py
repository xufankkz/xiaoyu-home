"""Explicit developer-only component assembly from pinned public inputs."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,shutil,subprocess,zipfile
ROOT=Path(__file__).resolve().parents[1]
MODEL_SHA256='433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab'
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--model-archive',required=True,type=Path);args=parser.parse_args()
 raw=args.model_archive.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=MODEL_SHA256:raise ValueError('Public model archive SHA-256 does not match the release input')
 components=ROOT.parent/'components';dsh=components/'dsh';dsh.mkdir(parents=True,exist_ok=True)
 for name in ('package.json','package-lock.json'):shutil.copy2(ROOT/'packaging/component-locks/dsh'/name,dsh/name)
 npm=shutil.which('npm.cmd') or shutil.which('npm')
 if not npm:raise RuntimeError('Install npm before preparing the DSH component')
 subprocess.run([npm,'ci','--ignore-scripts','--no-audit','--no-fund'],cwd=dsh,check=True,shell=False)
 translation=components/'translation';model=translation/'packages/translate-en_zh-1_9';model.mkdir(parents=True,exist_ok=True)
 import io
 with zipfile.ZipFile(io.BytesIO(raw)) as archive:
  for info in archive.infolist():
   parts=PurePosixPath(info.filename).parts
   if not parts or info.is_dir() or '..' in parts:continue
   if parts[0].startswith('translate-'):parts=parts[1:]
   if not parts:continue
   if parts[0]!='model' and '/'.join(parts) not in ('sentencepiece.model','metadata.json','README.md'):continue
   target=model.joinpath(*parts);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(info))
 for name in ('model/model.bin','sentencepiece.model','metadata.json','README.md'):
  if not (model/name).is_file():raise ValueError('Model archive is missing required public files: '+name)
 shutil.copytree(ROOT.parent/'dist-translator/XiaoyuTranslator',translation/'translator',dirs_exist_ok=True)
 for name,version in [('dsh','0.2.1-rc.3'),('translation','1.9-ct2-4.8.4')]:
  (components/name/'component.json').write_text(json.dumps({'format':'xiaoyu-home-component','id':name,'version':version,'credentials_included':False,'user_data_included':False},indent=2),encoding='utf-8')
 print('Prepared pinned public components; no application user data was read')
if __name__=='__main__':main()
