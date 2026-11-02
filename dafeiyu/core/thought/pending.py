# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""未完成话题（v12 2.1）：会话断开时把"还没聊完的事"留下来。

为什么单独一层：残留通道按"记得多清楚"排序，但"没说完的话"需要**优先级**——
冷启动时先回到意识里的应该是这次没聊完的话题，而不是随便一条还记得的旧想法。
超过 72 小时没被提起就降级成普通残留节点（不再优先）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .. import db
from ..entity import loads, new_id, now_iso
from ..text import clip, parse_iso

STATUS_OPEN = "open"
STATUS_DOWNGRADED = "downgraded"
STATUS_DONE = "done"
HOURS_BEFORE_DOWNGRADE = 72


def open_for(session_tag: str, conv: str, nodes: list, now: datetime | None = None) -> int:
    """会话断开时调用：把 open 的节点/悬而未决的问题记成未完成话题。"""
    now = now or datetime.now()
    stamp = now.isoformat(timespec="seconds")
    created = 0
    for node in nodes or []:
        is_open = getattr(node, "status", "") == STATUS_OPEN or getattr(node, "type", "") == "open_question"
        if not is_open:
            continue
        topic = clip(getattr(node, "summary", "") or getattr(node, "content", ""), 80)
        if not topic:
            continue
        existing = db.q1(
            "SELECT id FROM pending_topic WHERE session_tag=? AND topic=? AND status='open'",
            (session_tag, topic))
        if existing:
            continue
        db.ex(
            "INSERT INTO pending_topic(id, session_tag, conv, topic, node_ids, opened_at, last_seen, status) "
            "VALUES(?,?,?,?,?,?,?,'open')",
            (new_id(), session_tag, conv, topic, json.dumps([getattr(node, "id", "")], ensure_ascii=False),
             stamp, stamp),
        )
        created += 1
    return created


def active(limit: int = 3, now: datetime | None = None, conv: str = "") -> list[dict]:
    """还没过期（72h 内被提起过）的未完成话题，最新的优先。"""
    now = now or datetime.now()
    args: list = []
    sql = "SELECT * FROM pending_topic WHERE status='open'"
    if conv:
        sql += " AND conv=?"
        args.append(conv)
    sql += " ORDER BY last_seen DESC LIMIT ?"
    args.append(int(limit))
    out = []
    for row in db.q(sql, args):
        data = dict(row)
        last = parse_iso(data.get("last_seen") or data.get("opened_at") or "")
        if last is None:
            continue
        age_hours = (now - last).total_seconds() / 3600.0
        if age_hours > HOURS_BEFORE_DOWNGRADE:
            continue
        data["age_minutes"] = int(age_hours * 60)
        data["nodes"] = loads(data.get("node_ids"), [])
        out.append(data)
    return out


def touch(topic_id: str, now: datetime | None = None) -> None:
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    db.ex("UPDATE pending_topic SET last_seen=? WHERE id=?", (stamp, topic_id))


def close(topic_id: str, now: datetime | None = None) -> None:
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    db.ex("UPDATE pending_topic SET status='done', last_seen=? WHERE id=?", (stamp, topic_id))


def downgrade_stale(hours: int = HOURS_BEFORE_DOWNGRADE, now: datetime | None = None) -> int:
    """超时没被提起的 → 降级（不再优先注入，但仍留在残留通道里）。"""
    now = now or datetime.now()
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = db.q("SELECT id FROM pending_topic WHERE status='open' AND last_seen < ?", (cutoff,))
    for row in rows:
        db.ex("UPDATE pending_topic SET status='downgraded', downgraded_at=? WHERE id=?",
              (now.isoformat(timespec="seconds"), row["id"]))
    return len(rows)


def status() -> dict:
    rows = db.q("SELECT status, COUNT(*) n FROM pending_topic GROUP BY status")
    return {"counts": {r["status"]: r["n"] for r in rows},
            "open": active(limit=5),
            "downgrade_hours": HOURS_BEFORE_DOWNGRADE}
