"""Atomic, reversible metadata edits; original memory text is never overwritten."""
import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import closing
from .. import db
from ..entity import MemoryItem, dumps, now_iso


def revision(item):
    value = [item.id, item.content, item.summary, item.status, item.level >= 3,
             item.tombstone, item.superseded_by, item.ext.get('editorial', {})]
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def previous(operation):
    row = db.q1('SELECT detail FROM audit_event WHERE id=?', ('edit-' + operation,))
    return json.loads(row['detail']) if row else None


def snapshot(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with db.WRITE_LOCK, closing(sqlite3.connect(str(path))) as target:
        db.connect().backup(target)
        if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('记忆操作前快照未通过校验')


def apply(old_id, expected, operation, action, item=None, pinned=None, fingerprint=''):
    with db.write() as conn:
        conn.execute('BEGIN IMMEDIATE')
        prior = conn.execute('SELECT detail FROM audit_event WHERE id=?', ('edit-' + operation,)).fetchone()
        if prior:
            result = json.loads(prior['detail'])
            if result.get('fingerprint', fingerprint) != fingerprint:
                raise ValueError('操作编号对应其他内容，请重新打开记忆')
            return result
        raw = conn.execute('SELECT * FROM memory_item WHERE id=?', (old_id,)).fetchone()
        old = MemoryItem.from_row(raw) if raw else None
        if not old or not old.alive:
            raise ValueError('该记忆已不可编辑，请重新加载')
        if revision(old) != expected:
            raise ValueError('记忆已被另一端更新，请重新加载后再提交')
        if old.superseded_by or old.status in {'superseded', 'expired', 'merged'}:
            raise ValueError('请打开当前有效的记忆再操作')
        if item:
            data = item.to_row()
            conn.execute('INSERT INTO memory_item(' + ','.join(data) + ') VALUES(' + ','.join('?' for _ in data) + ')', list(data.values()))
        if action == 'correct':
            ext = {**old.ext, 'editorial': {**old.ext.get('editorial', {}), 'retained_original': True}}
            conn.execute("UPDATE memory_item SET status='superseded',superseded_by=?,ext=? WHERE id=?", (item.id, dumps(ext), old_id))
        elif action == 'pin':
            ext = {**old.ext, 'editorial': {**old.ext.get('editorial', {}), 'pinned': pinned, 'at': now_iso()}}
            conn.execute('UPDATE memory_item SET ext=? WHERE id=?', (dumps(ext), old_id))
        elif action == 'supplement':
            ext = {**old.ext, 'editorial': {**old.ext.get('editorial', {}), 'retained_original': True}}
            conn.execute('UPDATE memory_item SET ext=? WHERE id=?', (dumps(ext), old_id))
        result = {'ok': True, 'id': item.id if item else old_id, 'source_id': old_id,
                  'action': action, 'operation': operation, 'fingerprint': fingerprint, 'cloud_api_calls': 0}
        conn.execute('INSERT INTO audit_event(id,t,entity_kind,entity_id,action,detail) VALUES(?,?,?,?,?,?)',
                     ('edit-' + operation, now_iso(), 'memory', old_id, 'user_' + action, dumps(result)))
        return result


def related(mid):
    return [MemoryItem.from_row(r) for r in db.q(
        "SELECT * FROM memory_item WHERE tombstone=0 AND (supersedes=? OR json_extract(ext,'$.editorial.source_id')=?) ORDER BY t_created DESC LIMIT 30", (mid, mid))]

