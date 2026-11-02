from pathlib import Path
from datetime import datetime
import hashlib
import json
import shutil
import threading
from .credentials import Credentials
from .files import atomic_json, atomic_bytes, read_json
from .model import endpoint, number

_locks = {}
_locks_guard = threading.Lock()

def synchronize_model(workspace, runtime):
    """Refresh per-run gateway addresses without importing host credentials."""
    workspace = Path(workspace)
    model = read_json(workspace / 'release/settings.json').get('models', {}).get('chat')
    llm_path = workspace / 'dafeiyu/data/config/llm.json'
    llm = read_json(llm_path)
    if model:
        llm['default'] = 'http'
        llm.setdefault('providers', {}).setdefault('http', {}).update(
            base_url='http://127.0.0.1:' + str(runtime['ports']['model']) + '/v1',
            api_key='', api_key_env='XIAOYU_INSTANCE_CHAT_KEY', model=model['model'])
        llm['tasks'] = {key: 'http' if key != 'judge' else 'offline' for key in llm.get('tasks', {})}
    atomic_json(llm_path, llm)

class Settings:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.path = self.workspace / 'release/settings.json'
        self.credentials = Credentials(self.workspace / 'release/credentials.dpapi')
        with _locks_guard:
            self.lock = _locks.setdefault(str(self.path.resolve()).lower(), threading.RLock())

    def revision(self):
        paths = (self.path, self.workspace / 'dafeiyu/data/config/personality.md')
        return hashlib.sha256(b''.join(path.read_bytes() if path.exists() else b'' for path in paths)).hexdigest()

    def read(self):
        data = read_json(self.path)
        data['persona'] = (self.workspace / 'dafeiyu/data/config/personality.md').read_text(encoding='utf-8')
        data['revision'] = self.revision()
        for group, model in data.get('models', {}).items():
            model['key_configured'] = bool(self.credentials.get('model:' + group))
        data['first_run'] = not bool(data.get('profile', {}).get('name'))
        return data

    def snapshot(self, paths):
        folder = self.workspace / 'release/config-history' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        rows = []
        for path in paths:
            path = Path(path)
            if path.exists():
                relative = path.relative_to(self.workspace)
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ValueError('配置快照核验失败，未保存')
                rows.append({'path': relative.as_posix(), 'sha256': digest})
        atomic_json(folder / 'manifest.json', {'files': rows, 'verified': True})

    def save(self, body):
        with self.lock:
            if body.get('revision') != self.revision():
                raise ValueError('配置已在另一端修改，请重新加载后再保存。')
            data = read_json(self.path)
            if 'profile' in body:
                profile = body['profile']
                if not isinstance(profile, dict):
                    raise ValueError('角色资料格式无效')
                for key, limit in (('name', 60), ('owner_address', 60), ('description', 2000)):
                    value = profile.get(key, '')
                    if not isinstance(value, str) or len(value) > limit:
                        raise ValueError('角色资料过长或格式无效')
                data['profile'] = {key: profile.get(key, '').strip() for key in ('name', 'owner_address', 'description')}
            if 'daily_budget' in body:
                data['daily_budget'] = number(body['daily_budget'], 10000)
            if 'models' in body:
                if not isinstance(body['models'], dict):
                    raise ValueError('模型设置无效')
                for group, model in body['models'].items():
                    if group not in ('chat', 'vision', 'drawing', 'douyin') or not isinstance(model, dict):
                        raise ValueError('模型类型无效')
                    name = str(model.get('model', '')).strip()
                    if not name or len(name) > 200:
                        raise ValueError('请填写模型名')
                    data.setdefault('models', {})[group] = {'endpoint': endpoint(model['endpoint']), 'model': name,
                        'input_price': number(model.get('input_price', 0)), 'output_price': number(model.get('output_price', 0)),
                        'price_per_request': number(model.get('price_per_request', 0)),
                        'key_optional': model.get('key_optional') is True}
            if 'persona' in body:
                if not isinstance(body['persona'], str) or not body['persona'].strip() or len(body['persona']) > 400000:
                    raise ValueError('人设内容不能为空或超过长度限制')
            self.snapshot([self.path, self.credentials.path, self.workspace / 'dafeiyu/data/config/personality.md'])
            for group, model in body.get('models', {}).items():
                if model.get('api_key'):
                    self.credentials.set('model:' + group, model['api_key'])
            if 'persona' in body:
                # Existing shared saver updates both QQ presets and generated bundles.
                from persona_settings import PersonaSettings
                store = PersonaSettings(self.workspace, self.workspace / 'dafeiyu/data/config/personality.md')
                store.save(body['persona'], store.read()['revision'])
            atomic_json(self.path, data)
            return self.read()
