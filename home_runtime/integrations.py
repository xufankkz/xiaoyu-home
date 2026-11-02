"""Explicit connector configuration and read-only capability checks."""
from pathlib import Path
import json
import re
import socket
import urllib.parse
import urllib.request
from .credentials import Credentials
from .files import atomic_json, read_json
from .settings import Settings
import release_paths

IDS = ('dsh', 'translation', 'media')

def connector_url(value, scheme=('http', 'https'), local=False):
    value = str(value or '').strip().rstrip('/')
    if not value:
        return ''
    url = urllib.parse.urlsplit(value)
    if (url.scheme not in scheme or not url.hostname or url.username or url.password or url.query or url.fragment
        or (local and url.hostname not in ('127.0.0.1', 'localhost', '::1'))
        or (url.scheme in ('http', 'ws') and url.hostname not in ('127.0.0.1', 'localhost', '::1'))):
        raise ValueError('连接地址请使用 HTTPS/WSS 或本机 HTTP/WS，不含账号、密码或查询参数。')
    return value

def components(workspace):
    root = Path(workspace).parent / 'components'
    result = {}
    entries = {'dsh': 'node_modules/@deepseek-ai/dsh/lib/bin.js', 'translation': 'translator/XiaoyuTranslator.exe', 'media': 'ffmpeg/bin/ffmpeg.exe'}
    for name, entry in entries.items():
        manifest = root / name / 'component.json'
        metadata = read_json(manifest)
        result[name] = {'installed': bool(manifest.is_file() and (root / name / entry).is_file()),
                        'version': metadata.get('version', ''), 'path': str(root / name)}
    return result

class Integrations:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.settings = Settings(workspace)
        self.secrets = self.settings.credentials

    def state(self):
        values = self.settings.read().get('integrations', {})
        for name, value in values.items():
            for field in ('token', 'ws_token', 'api_key'):
                value[field + '_configured'] = bool(self.secrets.get(name + ':' + field))
        return {'values': values, 'components': components(self.workspace),
                'automation_enabled': self.settings.read().get('automation_enabled', False)}

    def save(self, body):
        with self.settings.lock:
            if body.get('revision') != self.settings.revision():
                raise ValueError('配置已变化，请重新打开接入页面后保存。')
            values = body.get('integrations')
            if not isinstance(values, dict):
                raise ValueError('接入设置格式无效')
            data = read_json(self.settings.path)
            current = data.setdefault('integrations', {})
            secret_changes = {}
            for name, value in values.items():
                if name not in ('onebot', 'dsh', 'tts', 'asr', 'media') or not isinstance(value, dict):
                    raise ValueError('不支持的组件设置')
                saved = dict(current.get(name, {}))
                for key in ('enabled', 'managed'):
                    if key in value:
                        if type(value[key]) is not bool:
                            raise ValueError('开关格式无效')
                        saved[key] = value[key]
                if 'http_url' in value:
                    saved['http_url'] = connector_url(value['http_url'], local=name == 'dsh')
                if 'ws_url' in value:
                    saved['ws_url'] = connector_url(value['ws_url'], ('ws', 'wss'))
                for key in ('owner_qq', 'reference_path', 'prompt_text', 'voice', 'model', 'provider', 'backend', 'executable_dir'):
                    if key in value:
                        text = str(value[key]).strip()
                        if len(text) > 2000:
                            raise ValueError('接入设置过长')
                        if key == 'owner_qq' and text and not re.fullmatch(r'\d{5,12}', text):
                            raise ValueError('主人 QQ 号码格式无效')
                        saved[key] = text
                for key in ('allow_private', 'allow_groups'):
                    if key in value:
                        numbers = value[key]
                        if not isinstance(numbers, list) or any(not re.fullmatch(r'\d{5,12}', str(n)) for n in numbers):
                            raise ValueError('白名单请逐个填写 QQ 或群号')
                        saved[key] = list(dict.fromkeys(str(n) for n in numbers))
                for key in ('token', 'ws_token', 'api_key'):
                    if value.get(key):
                        credential = value[key]
                        if not isinstance(credential, str) or len(credential) > 4096 or '\n' in credential or '\r' in credential:
                            raise ValueError('连接凭据格式无效')
                        secret_changes[name + ':' + key] = credential
                    if value.get('clear_' + key) is True:
                        secret_changes[name + ':' + key] = ''
                if 'price_per_request' in value:
                    from .model import number
                    saved['price_per_request'] = number(value['price_per_request'], 10000)
                current[name] = saved
            if 'automation_enabled' in body:
                if type(body['automation_enabled']) is not bool:
                    raise ValueError('自动任务开关格式无效')
                data['automation_enabled'] = body['automation_enabled']
            self.settings.snapshot([self.settings.path, self.secrets.path, self.workspace / 'qq-bridge/config.json',
                self.workspace / 'astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json'])
            before = self.secrets.path.read_bytes() if self.secrets.path.exists() else None
            try:
                for name, secret in secret_changes.items():
                    self.secrets.set(name, secret)
                atomic_json(self.settings.path, data)
            except Exception:
                from .files import atomic_bytes
                if before is not None:
                    atomic_bytes(self.secrets.path, before)
                else:
                    self.secrets.path.unlink(missing_ok=True)
                raise
            self.apply_files()
            atomic_json(self.workspace / 'release/control.json', {'action': 'reconcile', 'id': self.settings.revision()})
            return self.state()

    def apply_files(self):
        integrations = read_json(self.settings.path).get('integrations', {})
        runtime_path = Path(__import__('os').environ['XIAOYU_RUNTIME'])
        runtime = read_json(runtime_path)
        for name in ('tts', 'dsh', 'onebot'):
            runtime['integrations'][name] = integrations.get(name, {}).get('http_url', '')
        if integrations.get('dsh', {}).get('managed'):
            runtime['integrations']['dsh'] = 'http://127.0.0.1:' + str(runtime['ports']['dsh'])
        atomic_json(runtime_path, runtime)
        path = self.workspace / 'qq-bridge/config.json'
        bridge = read_json(path)
        onebot, dsh = integrations.get('onebot', {}), integrations.get('dsh', {})
        bridge.setdefault('snowluma', {}).update(httpUrl=onebot.get('http_url', ''), wsUrl=onebot.get('ws_url', ''), accessToken='', wsAccessToken='')
        bridge.setdefault('dsh', {}).update(baseUrl=runtime['integrations']['dsh'], authToken='',
            provider='xiaoyu-local' if dsh.get('managed') else dsh.get('provider', ''),
            model='configured-model' if dsh.get('managed') else dsh.get('model', ''))
        bridge['ownerQQ'] = onebot.get('owner_qq') or None
        bridge['allow'] = {'private': onebot.get('allow_private', []), 'groups': onebot.get('allow_groups', [])}
        bridge['allowAllWhenEmpty'] = False
        bridge['aliasesFile'] = str(self.workspace / 'astrbot/data/plugins/astrbot_plugin_xiaoyu_alias/aliases.json')
        media = integrations.get('media', {}).get('executable_dir')
        media_bin = Path(media) if media else self.workspace.parent / 'components/media/ffmpeg/bin'
        bridge.setdefault('socialV2', {}).setdefault('voice', {})['ffmpegPath'] = str(media_bin / 'ffmpeg.exe')
        bridge['socialV2']['voice']['enabled'] = integrations.get('tts', {}).get('enabled', False)
        atomic_json(path, bridge)
        tts = integrations.get('tts', {})
        voice_path = self.workspace / 'astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json'
        voice = read_json(voice_path)
        voice.update(backend=tts.get('backend', 'gpt-sovits'), gsv_api=tts.get('http_url', ''),
                     gsv_text_lang='zh', gsv_prompt_lang='zh')
        voice.setdefault('default_tone', 'gentle')
        if tts.get('reference_path'):
            voice.setdefault('tones', {})['gentle'] = {'ref': tts['reference_path'], 'prompt': tts.get('prompt_text', ''), 'temperature': 0.8, 'speed': 1.0}
        atomic_json(voice_path, voice)

    def check(self):
        state = self.state()
        status = {}
        for name, value in state['values'].items():
            address = value.get('http_url', '')
            if name == 'dsh' and value.get('managed'):
                address = release_paths.url('dsh')
            parsed = urllib.parse.urlsplit(address)
            reachable = False
            if parsed.hostname:
                try:
                    with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)), timeout=1):
                        reachable = True
                except OSError:
                    pass
            status[name] = {'configured': bool(address), 'port_reachable': reachable,
                            'note': '仅检查地址和连接端口；未发送消息、合成语音或调用模型。'}
        return {**state, 'status': status, 'processes': read_json(self.workspace / 'release/processes.json')}
