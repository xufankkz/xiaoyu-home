# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""碎碎念日志、对话记录、审计事件、巩固运行记录。"""

from __future__ import annotations

from typing import Any

from .. import db
from ..entity import AuditEvent, dumps, loads


# ── 碎碎念 ──────────────────────────────────────────────────────────────────
def chitchat_add(cid: str, t: str, content: str, emotion: str = "") -> None:
    db.ex(
        "INSERT OR REPLACE INTO chitchat_log(id, t, content, emotion) VALUES(?,?,?,?)",
        (cid, t, content, emotion),
    )


def chitchat_recent(limit: int = 20) -> list[dict]:
    rows = db.q("SELECT * FROM chitchat_log ORDER BY t DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in rows]


def chitchat_last() -> dict | None:
    row = db.q1("SELECT * FROM chitchat_log ORDER BY t DESC LIMIT 1")
    return dict(row) if row else None


def chitchat_mark_replied(cid: str, t: str) -> None:
    db.ex("UPDATE chitchat_log SET replied=1, replied_at=? WHERE id=?", (t, cid))


def chitchat_unanswered() -> list[dict]:
    rows = db.q("SELECT * FROM chitchat_log WHERE replied=0 ORDER BY t DESC LIMIT 20")
    return [dict(r) for r in rows]


def chitchat_since(t_iso: str) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM chitchat_log WHERE t >= ?", (t_iso,), 0) or 0)


# ── 对话 ────────────────────────────────────────────────────────────────────
def dialogue_add(mid: str, role: str, text: str, conv: str = "", emotion: str = "",
                 hits: list[str] | None = None, t: str = "") -> None:
    db.ex(
        "INSERT OR REPLACE INTO dialogue_message(id, t, role, text, conv, emotion, memory_hits) "
        "VALUES(?, COALESCE(NULLIF(?,''), datetime('now','localtime')), ?, ?, ?, ?, ?)",
        (mid, t, role, text, conv, emotion, dumps(hits or [])),
    )


def dialogue_recent(limit: int = 40, conv: str = "") -> list[dict]:
    if conv:
        rows = db.q(
            "SELECT * FROM dialogue_message WHERE conv=? ORDER BY t DESC LIMIT ?", (conv, int(limit))
        )
    else:
        rows = db.q("SELECT * FROM dialogue_message ORDER BY t DESC LIMIT ?", (int(limit),))
    out = []
    for r in reversed(rows):
        d = dict(r)
        d["memory_hits"] = loads(d.get("memory_hits"), [])
        out.append(d)
    return out


def last_owner_message() -> dict | None:
    row = db.q1("SELECT * FROM dialogue_message WHERE role='owner' ORDER BY t DESC LIMIT 1")
    return dict(row) if row else None


# ── 审计 ────────────────────────────────────────────────────────────────────
def audit_add(kind: str, entity_id: str, action: str, detail: dict | None = None) -> AuditEvent:
    ev = AuditEvent(entity_kind=kind, entity_id=entity_id, action=action, detail=detail or {})
    row = ev.to_row()
    db.ex(
        "INSERT INTO audit_event(id, t, entity_kind, entity_id, action, detail) VALUES(?,?,?,?,?,?)",
        (row["id"], row["t"], row["entity_kind"], row["entity_id"], row["action"], row["detail"]),
    )
    return ev


def audit_timeline(entity_id: str, limit: int = 200) -> list[dict]:
    rows = db.q(
        "SELECT * FROM audit_event WHERE entity_id=? ORDER BY t LIMIT ?", (entity_id, int(limit))
    )
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = loads(d.get("detail"), {})
        out.append(d)
    return out


def audit_recent(limit: int = 80, action: str = "", kind: str = "") -> list[dict]:
    where, args = [], []
    if action:
        where.append("action=?")
        args.append(action)
    if kind:
        where.append("entity_kind=?")
        args.append(kind)
    sql = "SELECT * FROM audit_event"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t DESC LIMIT ?"
    args.append(int(limit))
    out = []
    for r in reversed(db.q(sql, args)):
        d = dict(r)
        d["detail"] = loads(d.get("detail"), {})
        out.append(d)
    return out


# ── 巩固运行 ────────────────────────────────────────────────────────────────
def consolidation_add(run_id: str, kind: str, stats: dict) -> None:
    db.ex(
        "INSERT OR REPLACE INTO consolidation_run(id, t, kind, stats) VALUES(?, datetime('now','localtime'), ?, ?)",
        (run_id, kind, dumps(stats)),
    )


def consolidation_recent(limit: int = 20) -> list[dict]:
    rows = db.q("SELECT * FROM consolidation_run ORDER BY t DESC LIMIT ?", (int(limit),))
    out = []
    for r in rows:
        d = dict(r)
        d["stats"] = loads(d.get("stats"), {})
        out.append(d)
    return out
