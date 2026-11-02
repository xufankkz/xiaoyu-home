"""One durable write path for bridge, MCP and local UI memory requests."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .. import settings
from ..entity import LT2_CATEGORIES, MemoryItem, new_id
from ..repo import memory as mr, system as sr
from ..text import bigrams, keywords


def remember(content: str, *, category: str = "平静", subject: str = "", conversation: str = "",
             event_id: str = "", origin: str = "dsh", self_activity: dict | None = None) -> dict:
    content = str(content or "").strip()
    if not content or len(content) > 12000:
        raise ValueError("记忆正文为空或超过 12000 字")
    if category not in LT2_CATEGORIES:
        raise ValueError("无效记忆分类")
    match = re.match(r"【与(.+?)的对话】", content)
    subject = str(subject or (match.group(1) if match else ""))[:120]
    event_id = str(event_id or new_id())[:200]
    activity = None
    if self_activity is not None:
        from . import self_activity as activities
        activity = activities.metadata(self_activity, content=content, conversation=conversation, event_id=event_id)
    mid = "remember-" + hashlib.sha256((origin + ":" + event_id).encode()).hexdigest()[:24]
    existing = mr.get(mid)
    if existing:
        if existing.content != content:
            raise ValueError("事件 ID 已用于另一条记忆")
        return {"ok": True, "id": mid, "duplicate": True, "stored": True}
    item = MemoryItem(id=mid, layer="long2", subtype="event", content=content,
                      summary=content[:120], concepts=keywords(content, 8), emotion_tag=category,
                      origin=origin, salience=0.7, status="resolved",
                      ext={"subject": subject, "conversation": str(conversation)[:160], "event_id": event_id,
                           **({"self_activity": activity} if activity else {})})
    mr.insert(item)
    mr.set_index(mid, "memory", bigrams(content), item.concepts)
    sr.audit_add("memory", mid, "remembered", {"origin": origin, "event_id": event_id})
    return {"ok": True, "id": mid, "duplicate": False, "stored": True}


def ingest_inbox(limit: int = 50) -> dict:
    inbox = settings.STATE / "memory-inbox"
    processed = settings.STATE / "memory-inbox-processed"
    count = 0
    errors = []
    for path in sorted(inbox.glob("*.json"))[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out = remember(data["content"], category=data.get("category") or "平静",
                           subject=data.get("subject") or "", conversation=data.get("conversation") or "",
                           event_id=data["event_id"], origin="dsh", self_activity=data.get("self_activity"))
            if out["stored"]:
                processed.mkdir(parents=True, exist_ok=True)
                path.replace(processed / path.name)
                count += 1
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)[:200]})
    return {"imported": count, "pending": len(list(inbox.glob("*.json"))), "errors": errors}
