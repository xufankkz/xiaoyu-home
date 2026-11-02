"""Local shared controls for per-contact preferences and release information."""
import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from product_settings import load, atomic, SettingsError, clock, numeric

from version import VERSION, RELEASE


class ContactPreferences:
    def __init__(self, root, snapshot):
        self.root, self.snapshot = Path(root), snapshot
        self.path = self.root / 'qq-bridge/state/contact-preferences.json'
        self.lock = threading.RLock()

    def roster(self):
        c = load(self.root / 'qq-bridge/config.json')
        return {'private:' + str(uid) for uid in c.get('allow', {}).get('private', [])}

    def revision(self):
        return hashlib.sha256(self.path.read_bytes() if self.path.exists() else b'').hexdigest()

    def read(self):
        with self.lock:
            data = load(self.path)
            return {'revision': self.revision(), 'contacts': {k: v for k, v in data.get('contacts', {}).items() if k in self.roster()}}

    def save(self, body):
        key = body.get('key')
        if key not in self.roster():
            raise SettingsError('请选择现有私聊白名单联系人')
        patch = body.get('preferences')
        allowed = {'proactive_enabled', 'daily_count', 'start', 'end', 'voice_mode', 'voice_percent', 'pause_until', 'notes'}
        if not isinstance(patch, dict) or set(patch) - allowed:
            raise SettingsError('联系人偏好字段无效')
        cleaned = {}
        for k, v in patch.items():
            if v is None or v == '': continue
            if k == 'proactive_enabled':
                if type(v) is not bool: raise SettingsError('主动联系选项无效')
            elif k in {'daily_count', 'voice_percent'}:
                v = numeric(v, 0, 3 if k == 'daily_count' else 100, integer=True)
            elif k in {'start', 'end'}: v = clock(v)
            elif k == 'voice_mode':
                if v not in {'inherit', 'text', 'prefer'}: raise SettingsError('语音偏好无效')
            elif k == 'pause_until':
                try:
                    value = datetime.fromisoformat(v)
                    if value.tzinfo is None: raise ValueError()
                except (TypeError, ValueError): raise SettingsError('暂缓联系时间无效') from None
            elif k == 'notes':
                if not isinstance(v, str) or len(v) > 240: raise SettingsError('相处备注最多 240 字')
            cleaned[k] = v
        if bool(cleaned.get('start')) != bool(cleaned.get('end')):
            raise SettingsError('请一起填写联系开始和结束时间')
        if cleaned.get('start', '00:00') >= cleaned.get('end', '23:59'):
            raise SettingsError('联系结束时间应晚于开始时间')
        with self.lock:
            if body.get('revision') != self.revision():
                raise SettingsError('联系人偏好已被另一端更新，请重新加载')
            data = load(self.path)
            self.snapshot([self.path])
            data.setdefault('contacts', {})[key] = cleaned
            atomic(self.path, data)
            return {**self.read(), 'saved': True}

