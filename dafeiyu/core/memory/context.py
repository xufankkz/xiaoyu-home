"""Shared context assembly for production bridge and native dialogue."""
from __future__ import annotations

import hashlib
import re

from .. import affective, affinity, db, homeostasis, hormones, memewiki, slang
from ..emotion import vector
from ..entity import MemoryItem
from ..repo import memory as mr
from ..text import keywords
from . import reliability, retrieval, self_activity

_ingest_lock = db.WRITE_LOCK


def _term(text: str) -> str:
    query = memewiki.extract_query(text)
    if re.search(r"是什么梗|什么意思|啥梗|啥意思|怎么用", text):
        return query
    if query and 2 <= len(query) <= 6 and not re.search(r"我|你|他|她|今天|明天|昨天|好啊|好的|你好|小鱼|老大|早上|晚上|什么|怎么|为什么|谢谢|晚安|早安|再见|吃饭|睡觉|可以|不用|收到|知道|没事|辛苦", query):
        return query
    return ""


def prepare(messages: list[dict], *, conversation: str, include_memory: bool = True,
            allow_lookup: bool = True) -> dict:
    recent = []
    incoming_ids = set()
    processed = 0
    for message in messages[-12:]:
        if message.get("isSelf"):
            continue
        text = str(message.get("plain") or message.get("text") or "").strip()[:4000]
        if not text:
            continue
        recent.append(text)
        subject = str(message.get("userId") or message.get("user_id") or message.get("sender") or conversation)[:120]
        sequence = str(message.get("messageId") or ":".join(str(message.get(k) or "") for k in ("seq", "time", "userId")))
        if not sequence.strip(":"):
            sequence = hashlib.sha256(text.encode()).hexdigest()
        event_id = conversation + ":" + sequence
        mid = "incoming-" + hashlib.sha256(event_id.encode()).hexdigest()[:24]
        incoming_ids.add(mid)
        with _ingest_lock:
            if not mr.get(mid):
                if include_memory:
                    mr.insert(MemoryItem(id=mid, layer="instant", subtype="chat", content=text,
                                         summary=text[:100], concepts=keywords(text, 6), origin="bridge_message",
                                         status="resolved", ext={"subject": subject, "conversation": conversation,
                                                                 "event_id": event_id, "is_self": False}))
            # A voice transcript can already exist before model context preparation.
            # Affective events use their own event ID for deduplication.
            out = affective.observe(text, event_id=event_id, scope=conversation + ":" + subject, memory_id=mid)
            source = mr.get(mid)
            if source and not source.tombstone and source.level < 3:
                affinity.capture(event_id=event_id, memory_id=mid, conversation=conversation,
                                 content=source.content, subject=subject,
                                 at=message.get('time') or source.t_created, feeling=out.get('appraisal'))
            if not out.get("duplicate"):
                homeostasis.note("user_message")
                hormones.observe(text, companion=True)
                processed += 1
    query = "\n".join(recent)[-2000:]
    recalled = retrieval.recall(query, vec=vector.current(), top_k=5, include_thoughts=False,
                               exclude_ids=incoming_ids) if query and include_memory else {"items": []}
    if recalled["items"]:
        affective.memory_pulses(recalled["items"])
    activities = self_activity.recent() if include_memory else None
    pieces = [activities['block'] if activities else '', retrieval.context_block(recalled, 1000)]
    lookup = None
    term = _term(recent[-1]) if recent else ""
    if term and include_memory:
        explicit = bool(re.search(r"是什么梗|什么意思|啥梗|啥意思|怎么用", recent[-1]))
        lookup = slang.lookup(term, allow_network=allow_lookup and explicit)
        if lookup.get("found"):
            pieces.append(f"【词义记录·{lookup.get('source') or '本地'}】「{term}」：{str(lookup.get('definition') or '')[:700]}")
        else:
            pieces.append(f"【词义未确认】「{term}」尚未查到完整释义，不要据相近词猜测；需要释义时先用 dafeiyu_lookup 查询，未查明就直说暂时不知道。")
    character = affective.status()
    pieces.append(character['block'])
    pieces.append(affinity.prompt_block(conversation))
    return {"ok": True, "block": "\n\n".join(p for p in pieces if p), "processed": processed,
            "memory_count": len(recalled["items"]), "lookup": lookup,
            "self_activity_count": len(activities['items']) if activities else 0,
            "self_activity_ids": [item['id'] for item in activities['items']] if activities else [],
            "expression": character['expression'], "extra_cloud_api_calls": 0}
