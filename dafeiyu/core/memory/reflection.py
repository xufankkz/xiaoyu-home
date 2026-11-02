"""Bounded local maintenance: expiry, exact duplicates and sourced extracts.

No inference of unstated habits or facts, and no cloud/local model invocation.
Original text stays in its row; only effective-state metadata is revised.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json

from .. import settings
from ..entity import MemoryItem
from ..repo import memory as mr, system as sr
from . import reliability, service


def expire(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    updates = []
    expired = 0
    for item in mr.iter_alive():
        if item.ext.get('editorial', {}).get('pinned'):
            continue
        if item.superseded_by or item.status in {"expired", "merged", "superseded"}:
            continue
        if not (item.ext or {}).get("reliability"):
            continue
        confidence = round(reliability.confidence_at(item, now), 6)
        fields = {}
        if abs(confidence - item.confidence) >= .001:
            fields["confidence"] = confidence
        if item.memory_type == "episodic_state" and not reliability.current(item, now):
            fields["status"] = "expired"
            expired += 1
        if fields:
            updates.append((item.id, fields))
    mr.policy_updates(updates, "memory_decay")
    return {"updated": len(updates), "expired": expired, "cloud_api_calls": 0}


def run(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    expiry = expire(now)
    rows = [i for i in mr.iter_alive() if reliability.current(i, now)
            and (i.ext or {}).get("reliability", {}).get("version") == 1
            and i.origin != "local_reflection" and not i.ext.get('editorial', {}).get('pinned')
            and i.layer in {"long1", "long2", "short"}]
    groups = defaultdict(list)
    for item in rows:
        scope = item.ext["reliability"].get("subject")
        # Unknown provenance must not merge different people's records.
        if scope:
            groups[(scope, " ".join(item.content.split()), item.layer)].append(item)
    merged = 0
    merged_ids = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda i: (i.t_created, i.id))
        primary = ordered[-1]
        updates = [(i.id, {"status": "merged", "superseded_by": primary.id}) for i in ordered[:-1]]
        updates.append((primary.id, {"refs": list(dict.fromkeys(primary.refs + [i.id for i in ordered[:-1]]))}))
        mr.policy_updates(updates, "reflection_merge_exact")
        merged_ids.update(i.id for i in ordered[:-1])
        merged += len(group) - 1
    # Synthesis is a labelled extract of explicit records, never a new fact.
    by_scope = defaultdict(list)
    for item in rows:
        if item.id in merged_ids:
            continue
        meta = item.ext["reliability"]
        if meta.get("key") and item.memory_type == "semantic_belief":
            by_scope[meta["subject"]].append(item)
    synthesized = 0
    for subject, group in list(by_scope.items())[:20]:
        unique = {i.content: i for i in sorted(group, key=lambda x: (x.t_created, x.id))}
        members = list(unique.values())[-6:]
        if len(members) < 2:
            continue
        refs = sorted(i.id for i in members)
        mid = "reflection-" + hashlib.sha256("|".join(refs).encode()).hexdigest()[:24]
        if mr.get(mid):
            continue
        body = "已记录的陈述摘录（不是新推断）：\n" + "\n".join(f"{i.t_created}：{i.content}" for i in members)
        mr.insert(MemoryItem(id=mid, layer="long1", subtype="observation_extract", content=body,
                             summary=body[:160], refs=refs, origin="local_reflection", status="resolved",
                             ext={"subject": subject, "synthesis": "verbatim-extract"}))
        synthesized += 1
    result = {"at": now.isoformat(timespec="seconds"), "merged": merged, "superseded": 0,
              "synthesized": synthesized, "expiry": expiry, "cloud_api_calls": 0,
              "method": "local-rules", "original_text_deleted": 0}
    sr.audit_add("memory", "reflection", "reflection_completed", result)
    settings.write_text_atomic(settings.STATE / "reflection.json", json.dumps(result, ensure_ascii=False, indent=2))
    return result


def maintain() -> dict:
    from ..repo import hybrid
    return {"inbox": service.ingest_inbox(), "expiry": expire(), "index_changes": hybrid.flush()}
