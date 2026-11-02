"""Local image import; original bytes retained and duplicates addressed by hash."""
from pathlib import Path
import base64,hashlib,io,threading
from PIL import Image
from .files import atomic_bytes,atomic_json,read_json
_lock=threading.RLock()

def import_image(workspace,body):
 raw=base64.b64decode(body.get('file',''),validate=True)
 if not 0<len(raw)<=8*1024*1024:raise ValueError('每张表情图需为 1 字节到 8 MiB。')
 try:
  with Image.open(io.BytesIO(raw)) as image:
   kind=image.format;size=image.size
   if kind not in ('PNG','JPEG','GIF','WEBP') or size[0]*size[1]>32000000:raise ValueError('图片格式或尺寸不受支持。')
   image.verify()
 except Exception:raise ValueError('请选择有效的 PNG、JPEG、GIF 或 WebP 图片。') from None
 suffix={'PNG':'.png','JPEG':'.jpg','GIF':'.gif','WEBP':'.webp'}[kind]
 digest=hashlib.sha256(raw).hexdigest();library=Path(workspace)/'meme-library';target=library/(digest+suffix)
 with _lock:
  added=not target.exists()
  if added:atomic_bytes(target,raw)
  notes_path=library/'notes.json';notes=read_json(notes_path);identifier='local:'+target.name
  if identifier not in notes:
   notes[identifier]={'desc':str(body.get('description') or Path(str(body.get('name') or '表情图片')).stem)[:300],'tags':[],'source':'user_import','sha256':digest}
   atomic_json(notes_path,notes)
 return {'added':added,'id':identifier,'sha256':digest,'original_bytes_preserved':True}
