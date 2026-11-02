"""Persistence for daily contact impressions; SQL stays in the repository layer."""
from __future__ import annotations

import json
from .. import db


def add_material(row: dict) -> None:
    # A later voice transcript can extend an earlier text event. Deleted material
    # is never revived; ordinary repeated context preparation is a no-op.
    db.ex("""INSERT INTO affinity_material(event_id,memory_id,day,at,contact,conversation,role,content,feeling)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
              content=CASE WHEN affinity_material.deleted=0 AND length(excluded.content)>length(affinity_material.content)
                           THEN excluded.content ELSE affinity_material.content END,
              feeling=CASE WHEN affinity_material.feeling='{}' THEN excluded.feeling ELSE affinity_material.feeling END""",
          tuple(row[k] for k in ('event_id', 'memory_id', 'day', 'at', 'contact', 'conversation', 'role', 'content'))
          + (json.dumps(row.get('feeling') or {}, ensure_ascii=False),))


def source_rows(since: str) -> list[dict]:
    return [dict(r) for r in db.q("SELECT id,content,ext,t_created FROM memory_item WHERE origin='bridge_message' "
                                "AND tombstone=0 AND level<3 AND t_created>=? ORDER BY t_created,id", (since,))]


def material(day: str) -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM affinity_material WHERE day=? AND deleted=0 ORDER BY at,event_id", (day,))]


def pending_material(start: str, end: str) -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM affinity_material WHERE day>=? AND day<=? "
                                "AND deleted=0 AND reviewed_on='' ORDER BY at,event_id", (start, end))]


def claim(day: str, at: str) -> bool:
    with db.write() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO affinity_daily_run(day,status,started_at) VALUES(?,'running',?)", (day, at))
        return cur.rowcount == 1


def finish(day: str, status: str, at: str, rows: list[dict], note: str = '', consumed: list[str] | None = None) -> None:
    with db.write() as conn:
        for row in rows:
            conn.execute("INSERT INTO affinity_contact_day(day,contact,payload) VALUES(?,?,?)",
                         (day, row['contact'], json.dumps(row, ensure_ascii=False)))
        conn.execute("UPDATE affinity_daily_run SET status=?,finished_at=?,note=? WHERE day=?",
                     (status, at, note[:300], day))
        if status == 'done':
            conn.executemany("UPDATE affinity_material SET reviewed_on=? WHERE event_id=? AND reviewed_on='' AND deleted=0",
                             [(day, event) for event in consumed or []])


def runs() -> list[dict]:
    return [dict(r) for r in db.q("SELECT * FROM affinity_daily_run ORDER BY day DESC LIMIT 366")]


def interrupt_running(at: str) -> None:
    db.ex("UPDATE affinity_daily_run SET status='interrupted',finished_at=?,note=? WHERE status='running'",
          (at, '上次总结被服务中断，旧结果保留；不重复发起同日模型调用。'))


def entries(day: str = '') -> list[dict]:
    if not day:
        row = db.q1("SELECT MAX(day) FROM affinity_daily_run WHERE status='done'")
        day = row[0] if row else ''
    return [json.loads(r['payload']) for r in db.q("SELECT payload FROM affinity_contact_day WHERE day=? ORDER BY contact", (day or '',))]


def previous(day: str) -> dict[str, dict]:
    row = db.q1("SELECT MAX(day) FROM affinity_daily_run WHERE status='done' AND day<?", (day,))
    return {r['contact']: r for r in entries(row[0])} if row and row[0] else {}


def evidence(contact: str, event_ids: list[str]) -> list[dict]:
    ids = list(dict.fromkeys(event_ids))[:5]
    if not ids:
        return []
    marks = ','.join('?' for _ in ids)
    return [dict(r) for r in db.q(
        'SELECT a.event_id,a.at,a.content FROM affinity_material a LEFT JOIN memory_item m ON m.id=a.memory_id '
        'WHERE a.contact=? AND a.role=\'user\' AND a.deleted=0 AND a.event_id IN (' + marks + ') '
        'AND (a.memory_id=\'\' OR (m.id IS NOT NULL AND (m.tombstone=0 OR '
        "COALESCE(json_extract(m.ext,'$.distilled'),0)=1))) ORDER BY a.at", (contact, *ids))]
