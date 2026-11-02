"""Idempotent transcript ingestion, without generating a model reply."""
from __future__ import annotations

import hashlib
from .. import db
from ..entity import MemoryItem
from ..repo import memory as mr
from ..text import keywords, bigrams
from .. import affinity

_lock = db.WRITE_LOCK


def source_status(conversation: str, events: list) -> dict:
    """Read-only source validity for local follow-up records; never return bodies."""
    valid, removed = [], []
    for event in list(dict.fromkeys(e for e in events if isinstance(e, str)))[:80]:
        mid = 'incoming-' + hashlib.sha256(event.encode()).hexdigest()[:24]
        item = mr.get(mid)
        if not item or (item.ext.get('conversation') or item.ext.get('conv')) != conversation:
            continue
        if item.superseded_by or (item.tombstone and not item.ext.get('distilled')):
            removed.append(event)
        else:
            valid.append(event)
    return {'ok': True, 'valid': valid, 'removed': removed, 'cloud_api_calls': 0}


def record(payload: dict) -> dict:
    text = str(payload.get('content') or '').strip()
    conversation = str(payload.get('conversation') or '')[:160]
    event_id = str(payload.get('event_id') or '')[:240]
    if not text or len(text) > 12000 or not conversation or not event_id:
        raise ValueError('正文、会话、事件编号不能为空；正文最多 12000 字')
    is_self = bool(payload.get('is_self'))
    # Matches the established incoming-message key, so context preparation is idempotent.
    mid = ('outgoing-' if is_self else 'incoming-') + hashlib.sha256(event_id.encode()).hexdigest()[:24]
    with _lock:
        old = mr.get(mid)
        if old:
            # Context preparation may win the race and create the text row first.
            # Add transport attribution once, retaining policy and deleted states.
            if old.origin == 'bridge_message' and not old.tombstone and old.level < 3 and not old.ext.get('media_type'):
                updates = {'ext': {**old.ext, 'conv': conversation, 'conversation': conversation,
                                  'media_type': str(payload.get('media_type') or 'text')[:32],
                                  'sender': '自己' if is_self else str(payload.get('sender') or '联系人')[:120],
                                  'is_self': is_self, 'is_owner': bool(payload.get('is_owner'))}}
                if len(text) > len(old.content) and text.startswith(old.content):
                    updates.update(content=text, summary=text[:100])
                mr.update_fields(mid, **updates)
                mr.set_index(mid, 'memory', bigrams(text), old.concepts)
            if not old.tombstone and old.level < 3:
                affinity.capture(event_id=event_id, memory_id=mid, conversation=conversation,
                                 content=text, subject=str(payload.get('user_id') or ''), is_self=is_self,
                                 at=payload.get('timestamp') or old.t_created)
            return {'ok': True, 'id': mid, 'duplicate': True, 'stored': True}
        item = MemoryItem(id=mid, layer='instant', subtype='chat', content=text,
                          summary=text[:100], concepts=keywords(text, 6), origin='bridge_message',
                          status='resolved', salience=0.55 if is_self else 0.6,
                          ext={'conv': conversation, 'conversation': conversation, 'event_id': event_id,
                               'sender': '自己' if is_self else str(payload.get('sender') or '联系人')[:120],
                               'subject': str(payload.get('user_id') or conversation)[:120],
                               'is_self': is_self, 'is_owner': bool(payload.get('is_owner')),
                               'kind': 'chat', 'media_type': str(payload.get('media_type') or 'text')[:32]})
        mr.insert(item)
        mr.set_index(mid, 'memory', bigrams(text), item.concepts)
        affinity.capture(event_id=event_id, memory_id=mid, conversation=conversation,
                         content=text, subject=str(payload.get('user_id') or ''), is_self=is_self,
                         at=payload.get('timestamp') or item.t_created)
    return {'ok': True, 'id': mid, 'duplicate': False, 'stored': True}
