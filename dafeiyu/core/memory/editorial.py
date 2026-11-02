"""Local management actions for corrections, supplements and retention pins."""
import hashlib
import json
import re
from .. import settings, db
from ..entity import MemoryItem
from ..repo import memory as mr, editorial as repo
from ..text import keywords, bigrams
from . import reliability


def view(mid):
    item = mr.get(mid)
    if not item or item.tombstone:
        raise ValueError('未找到该记忆')
    return {'ok': True, 'revision': repo.revision(item), 'pinned': bool(item.ext.get('editorial', {}).get('pinned')),
            'editable': item.alive and reliability.current(item),
            'related': [{'id': i.id, 'content': i.content, 'action': i.ext.get('editorial', {}).get('action', 'correct'),
                         'current': reliability.current(i), 't_created': i.t_created} for i in repo.related(mid)]}


def change(payload):
    action, mid, operation = payload.get('action'), str(payload.get('id') or ''), str(payload.get('operation') or '')
    if action not in {'correct', 'supplement', 'pin'} or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', operation):
        raise ValueError('无效的记忆操作')
    operation = hashlib.sha256(operation.encode()).hexdigest()[:32]
    fingerprint = hashlib.sha256(json.dumps(
        [mid, action, payload.get('content', '') if action != 'pin' else payload.get('pinned')],
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    prior = repo.previous(operation)
    if prior:
        if prior['source_id'] != mid or prior['action'] != action or prior.get('fingerprint', fingerprint) != fingerprint:
            raise ValueError('操作编号已使用，请重新打开记忆')
        return {**prior, 'duplicate': True}
    text = payload.get('content', '')
    if action != 'pin' and (not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000):
        raise ValueError('请填写 1–12000 字的更正或补充')
    if action == 'pin' and type(payload.get('pinned')) is not bool:
        raise ValueError('固定保留选项无效')
    with db.WRITE_LOCK:
        prior = repo.previous(operation)
        if prior:
            if prior.get('fingerprint', fingerprint) != fingerprint:
                raise ValueError('操作编号对应其他内容，请重新打开记忆')
            return {**prior, 'duplicate': True}
        old = mr.get(mid)
        if not old or not old.alive or not reliability.current(old):
            raise ValueError('请打开当前有效的记忆再操作')
        if payload.get('revision') != repo.revision(old):
            raise ValueError('记忆已被另一端更新，请重新加载后再提交')
        backup = settings.STATE / 'memory-edits' / (operation + '.db')
        repo.snapshot(backup)
        item = None
        if action != 'pin':
            item = MemoryItem(id='edit-' + operation, layer='long2', subtype='event', content=text.strip(),
                              summary=text.strip()[:120], concepts=keywords(text.strip(), 8),
                              emotion_tag=old.emotion_tag, origin='user_editorial', status='resolved',
                              salience=max(.7, old.salience), refs=[old.id], supersedes=old.id if action == 'correct' else '',
                              memory_type='user_correction' if action == 'correct' else 'user_supplement',
                              ext={'subject': old.ext.get('subject', ''),
                                   'conversation': old.ext.get('conversation') or old.ext.get('conv', ''),
                                   'editorial': {'source_id': mid, 'action': action,
                                                 'pinned': bool(old.ext.get('editorial', {}).get('pinned'))},
                                   'reliability': {'version': 1, 'key': '', 'subject': old.ext.get('subject', '')}})
        out = repo.apply(mid, payload['revision'], operation, action, item, payload.get('pinned'), fingerprint)
        if item:
            mr.set_index(item.id, 'memory', bigrams(item.content), item.concepts)
        return {**out, 'snapshot_verified': True, 'revision': view(out['id'])['revision']}

