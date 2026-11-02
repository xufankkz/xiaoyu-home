"""One backed-up, revision-checked save for Markdown and both QQ preset bundles."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import threading
import uuid

_LOCK = threading.RLock()


class PersonaSettings:
    def __init__(self, project_root, personality):
        self.root = Path(project_root)
        self.personality = Path(personality)
        bridge = self.root / 'qq-bridge'
        self.sources = {name: bridge / 'dsh/agent-presets' / name / 'agent.cordis.yml'
                        for name in ('qq-chat', 'qq-chat-v2')}
        self.metadata = {name: path.with_name('preset.yml') for name, path in self.sources.items()}
        self.bundles = {name: bridge / 'plugins/qq-agent-presets/presets' / (name + '.patch.yml')
                        for name in self.sources}
        self.paths = [self.personality, *self.sources.values(), *self.bundles.values()]
        self.dependencies = [*self.paths, *self.metadata.values()]
        self.planner = bridge / 'scripts/plan-character-persona.mjs'

    @staticmethod
    def _hash(body):
        return hashlib.sha256(body).hexdigest()

    def _read(self):
        return {p: p.read_bytes() if p.is_file() else None for p in self.dependencies}

    def _revision(self, bodies):
        return self._hash(json.dumps([(str(p), self._hash(b) if b is not None else None)
                                     for p, b in bodies.items()], ensure_ascii=False).encode('utf-8'))

    @staticmethod
    def _embedded(body):
        if body is None:
            return None
        match = re.search(r'【小鱼·人设·开始[^\n]*\n([\s\S]*?)【小鱼·人设·结束】', body.decode('utf-8-sig').replace('\r\n', '\n'))
        return textwrap.dedent(match[1]).strip() if match else None

    def read(self):
        with _LOCK:
            bodies = self._read()
            raw = bodies[self.personality]
            text = raw.decode('utf-8-sig') if raw is not None else ''
            normalized = text.replace('\r\n', '\n').strip()
            synced = bool(normalized) and all(self._embedded(bodies[p]) == normalized
                                             for p in [*self.sources.values(), *self.bundles.values()])
            return {'path': str(self.personality), 'text': text, 'exists': bool(text), 'chars': len(text),
                    'revision': self._revision(bodies), 'presets_synced': synced,
                    'modified': datetime.fromtimestamp(self.personality.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    if raw is not None else ''}

    def _plan(self, text, bodies):
        sources = {name: {'text': bodies[p].decode('utf-8-sig'),
                          'metadata': bodies[self.metadata[name]].decode('utf-8-sig')}
                   for name, p in self.sources.items()}
        result = subprocess.run(['node', str(self.planner)],
                                input=json.dumps({'text': text, 'sources': sources}, ensure_ascii=False),
                                capture_output=True, text=True, encoding='utf-8', timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('QQ人设同步准备失败，原设置未保存；请检查预设与人设正文')
        return json.loads(result.stdout)

    @staticmethod
    def _replace(path, body):
        fd, staged = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged, path)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)

    def save(self, text, expected_revision):
        if not isinstance(text, str) or not text.strip() or len(text) > 400000:
            raise ValueError('人设内容不能为空或超过长度限制')
        normalized = text.replace('\r\n', '\n').strip()
        with _LOCK:
            before = self._read()
            if not expected_revision or expected_revision != self._revision(before):
                raise ValueError('人设或QQ预设已在其他位置修改，请重新加载后再保存')
            if any(body is None for body in before.values()):
                raise ValueError('同步所需的人设或QQ预设文件缺失，原设置未保存')
            plan = self._plan(normalized, before)
            proposed = {self.personality: normalized + '\n'}
            for name in self.sources:
                proposed[self.sources[name]] = plan[name]['source']
                proposed[self.bundles[name]] = plan[name]['bundle']
            encoded = {p: (b'\xef\xbb\xbf' if before[p].startswith(b'\xef\xbb\xbf') else b'') + value.encode('utf-8')
                       for p, value in proposed.items()}
            if self._read() != before:
                raise ValueError('保存准备期间设置发生变化，请重新加载后再保存')
            backup = self.root / '_backup/persona-save' / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
            backup.mkdir(parents=True)
            manifest = {'complete': False, 'revision': expected_revision, 'files': []}
            for index, p in enumerate(self.paths):
                dest = backup / (str(index) + '-' + p.name)
                dest.write_bytes(before[p])
                if dest.read_bytes() != before[p]:
                    raise OSError('人设备份核验失败，原设置未保存')
                manifest['files'].append({'source': str(p), 'path': dest.name, 'sha256': self._hash(before[p])})
            manifest['complete'] = True
            (backup / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            changed = []
            try:
                for p, body in encoded.items():
                    if p.read_bytes() != before[p]:
                        raise ValueError('保存期间设置发生变化，已中止同步')
                    if body != before[p]:
                        self._replace(p, body)
                        changed.append(p)
                if self._read() != {**before, **encoded}:
                    raise OSError('同步写入核验失败')
            except Exception:
                for p in reversed(changed):
                    if p.read_bytes() == encoded[p]:
                        self._replace(p, before[p])
                raise
            current = self.read()
            return {'saved': True, 'path': str(self.personality), 'chars': current['chars'],
                    'text': current['text'], 'revision': current['revision'], 'presets_synced': current['presets_synced'],
                    'backup': str(backup), 'requires_reload': True,
                    'note': '人设及两个QQ预设已同步保存；聊天空闲时重新加载DSH后生效。'}
