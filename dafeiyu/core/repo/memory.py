# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""记忆条目的读写 + 倒排索引。"""

from __future__ import annotations

from typing import Any, Iterable

from .. import db
from ..entity import Layer, MemoryItem, dumps, loads, new_id, now_iso

POLICY_FIELDS = ("confidence", "valid_from", "valid_until", "supersedes", "superseded_by",
                 "emotional_valence", "memory_type", "arousal", "evidence_weight")

COLS = (
    "id, layer, subtype, content, summary, skeleton, concepts, emotion_tag, salience, "
    "strength, level, status, refs, t_created, last_access, access_count, tombstone, origin, cluster, ext, "
    + ", ".join(POLICY_FIELDS)
)

_WRITABLE = {
    "layer", "subtype", "content", "summary", "skeleton", "concepts", "emotion_tag",
    "salience", "strength", "level", "status", "refs", "last_access", "access_count",
    "tombstone", "origin", "cluster", "ext", "t_created", "vision_tiles", "animation_meta",
}
_WRITABLE.update(POLICY_FIELDS)


def insert(item: MemoryItem) -> MemoryItem:
    return _persist(item, replace=False)


def upsert(item: MemoryItem) -> MemoryItem:
    return _persist(item, replace=True)


def _persist(item: MemoryItem, replace: bool) -> MemoryItem:
    from ..memory import reliability
    with db.write() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        old = conn.execute(f"SELECT {COLS} FROM memory_item WHERE id=?", (item.id,)).fetchone()
        changes = []
        if old and replace:
            # Imports/projections cannot reset the newer conflict/expiry policy.
            # A repeated import must never restore a deleted or forgotten row.
            if old["tombstone"] or old["level"] >= 3:
                return MemoryItem.from_row(old)
            for field in POLICY_FIELDS:
                setattr(item, field, old[field])
            previous_ext = loads(old["ext"], {})
            if previous_ext.get('editorial'):
                item.ext = {**item.ext, 'editorial': previous_ext['editorial']}
            if previous_ext.get("reliability"):
                item.ext = {**item.ext, "reliability": previous_ext["reliability"]}
                if old["status"] in {"expired", "merged", "superseded"}:
                    item.status = old["status"]
        elif old is None:
            reliability.fingerprint(item)
            if item.ext["reliability"].get("key"):
                candidates = [MemoryItem.from_row(r) for r in conn.execute(
                    f"SELECT {COLS} FROM memory_item WHERE tombstone=0 AND level<3 AND superseded_by='' ORDER BY t_created DESC LIMIT 4000")]
                changes = reliability.reconcile(item, candidates)
        row = item.to_row()
        keys = [k for k in row if k != "id"]
        sql = f"INSERT INTO memory_item(id, {', '.join(keys)}) VALUES(?{', ?' * len(keys)})"
        if replace:
            sql += " ON CONFLICT(id) DO UPDATE SET " + ", ".join(f"{k}=excluded.{k}" for k in keys)
        conn.execute(sql, [row["id"]] + [row[k] for k in keys])
        for previous, fields in changes:
            pairs = ", ".join(f"{k}=?" for k in fields)
            args = [dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
            conn.execute(f"UPDATE memory_item SET {pairs} WHERE id=?", [*args, previous.id])
            conn.execute("INSERT INTO audit_event(id,t,entity_kind,entity_id,action,detail) VALUES(?,?,?,?,?,?)",
                         (new_id(), now_iso(), "memory", previous.id, "evidence_revised", dumps({"new": item.id, "fields": list(fields)})))
    return item


def get(mid: str) -> MemoryItem | None:
    row = db.q1(f"SELECT {COLS} FROM memory_item WHERE id=?", (mid,))
    return MemoryItem.from_row(row) if row else None


def many(ids: Iterable[str]) -> list[MemoryItem]:
    ids = [i for i in ids if i]
    if not ids:
        return []
    marks = ", ".join("?" * len(ids))
    rows = db.q(f"SELECT {COLS} FROM memory_item WHERE id IN ({marks})", ids)
    by_id = {r["id"]: MemoryItem.from_row(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def update_fields(mid: str, **fields: Any) -> None:
    pairs, args = [], []
    for key, value in fields.items():
        if key not in _WRITABLE:
            raise KeyError(f"memory_item 没有可写字段 {key}")
        if isinstance(value, (list, dict)):
            value = dumps(value)
        elif isinstance(value, bool):
            value = int(value)
        pairs.append(f"{key}=?")
        args.append(value)
    if not pairs:
        return
    args.append(mid)
    db.ex(f"UPDATE memory_item SET {', '.join(pairs)} WHERE id=?", args)


def policy_updates(updates: list[tuple[str, dict]], action: str) -> int:
    """Atomically apply local policy metadata with one audit entry per record."""
    with db.write() as conn:
        for mid, fields in updates:
            if not fields or not set(fields) <= _WRITABLE:
                raise ValueError("Invalid policy update")
            pairs = ", ".join(f"{k}=?" for k in fields)
            values = [dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
            conn.execute(f"UPDATE memory_item SET {pairs} WHERE id=?", [*values, mid])
            conn.execute("INSERT INTO audit_event(id,t,entity_kind,entity_id,action,detail) VALUES(?,?,?,?,?,?)",
                         (new_id(), now_iso(), "memory", mid, action, dumps({"fields": list(fields), "cloud_api_calls": 0})))
    return len(updates)


def touch(mid: str, strength: float = 0.9, level_drop: int = 1, when: str = "") -> None:
    """检索命中：强度刷新到高位、level 降一级、计数 +1。"""
    item = get(mid)
    if item is None:
        return
    new_level = max(0, item.level - level_drop) if level_drop else item.level
    db.ex(
        "UPDATE memory_item SET strength=?, level=?, last_access=?, access_count=access_count+1 WHERE id=?",
        (max(item.strength, strength) if level_drop else item.strength, new_level, when or _now(), mid),
    )


def list_layer(
    layer: str | None = None,
    limit: int = 300,
    alive_only: bool = True,
    order: str = "t_created DESC",
    subtype: str | None = None,
    emotion_tag: str | None = None,
) -> list[MemoryItem]:
    where, args = [], []
    if layer:
        where.append("layer=?")
        args.append(layer)
    if subtype:
        where.append("subtype=?")
        args.append(subtype)
    if emotion_tag:
        where.append("emotion_tag=?")
        args.append(emotion_tag)
    if alive_only:
        where.append("tombstone=0 AND level<3")
    sql = f"SELECT {COLS} FROM memory_item"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ?"
    args.append(int(limit))
    return [MemoryItem.from_row(r) for r in db.q(sql, args)]


def iter_alive(layer: str | None = None, chunk: int = 500) -> Iterable[MemoryItem]:
    offset = 0
    while True:
        sql = f"SELECT {COLS} FROM memory_item WHERE tombstone=0 AND level<3"
        args: list[Any] = []
        if layer:
            sql += " AND layer=?"
            args.append(layer)
        sql += " ORDER BY t_created LIMIT ? OFFSET ?"
        args.extend([chunk, offset])
        rows = db.q(sql, args)
        if not rows:
            return
        for r in rows:
            yield MemoryItem.from_row(r)
        offset += chunk


def count(layer: str | None = None, alive_only: bool = True) -> int:
    sql = "SELECT COUNT(*) FROM memory_item"
    where, args = [], []
    if layer:
        where.append("layer=?")
        args.append(layer)
    if alive_only:
        where.append("tombstone=0 AND level<3")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(db.scalar(sql, args, 0) or 0)


def chars(layer: str) -> int:
    return int(db.scalar(
        "SELECT COALESCE(SUM(LENGTH(REPLACE(REPLACE(content,' ',''),char(10),''))),0) "
        "FROM memory_item WHERE layer=? AND tombstone=0", (layer,), 0) or 0)


def by_cluster(cluster: str, alive_only: bool = True) -> list[MemoryItem]:
    sql = f"SELECT {COLS} FROM memory_item WHERE cluster=?"
    if alive_only:
        sql += " AND tombstone=0 AND level<3"
    sql += " ORDER BY t_created"
    return [MemoryItem.from_row(r) for r in db.q(sql, (cluster,))]


# ── 倒排索引 ────────────────────────────────────────────────────────────────
def set_index(owner: str, kind: str, tokens: list[str], concepts: list[str] | None = None) -> int:
    """重建某条目的 token 索引（先删后写）。concepts 权重更高（tf=5）。"""
    tf: dict[str, int] = {}
    for tok in tokens or []:
        tok = str(tok).strip().lower()
        if tok:
            tf[tok] = tf.get(tok, 0) + 1
    for c in concepts or []:
        c = str(c).strip().lower()
        if c:
            tf[c] = max(tf.get(c, 0), 5)
    with db.write() as conn:
        conn.execute("DELETE FROM inverted_index WHERE owner=? AND owner_kind=?", (owner, kind))
        if tf:
            conn.executemany(
                "INSERT OR REPLACE INTO inverted_index(token, owner, owner_kind, tf) VALUES(?,?,?,?)",
                [(tok, owner, kind, n) for tok, n in tf.items()],
            )
    return len(tf)


def drop_index(owner: str, kind: str) -> None:
    db.ex("DELETE FROM inverted_index WHERE owner=? AND owner_kind=?", (owner, kind))


def search(tokens: Iterable[str], kind: str = "memory", limit: int = 200) -> dict[str, float]:
    """返回 {owner: 命中强度}。distinct token 数为主，tf 为辅。"""
    toks = []
    for t in tokens or []:
        t = str(t).strip().lower()
        if t and t not in toks:
            toks.append(t)
    toks = toks[:48]
    if not toks:
        return {}
    marks = ", ".join("?" * len(toks))
    rows = db.q(
        f"SELECT owner, COUNT(*) AS n, SUM(tf) AS hits FROM inverted_index "
        f"WHERE owner_kind=? AND token IN ({marks}) GROUP BY owner LIMIT ?",
        [kind, *toks, int(limit)],
    )
    return {r["owner"]: float(r["n"]) + 0.15 * float(r["hits"] or 0) for r in rows}


def rebuild_all() -> dict:
    from ..text import bigrams

    total = 0
    for item in iter_alive():
        set_index(item.id, "memory", bigrams(f"{item.summary} {item.content} {item.skeleton}"), item.concepts)
        total += 1
    return {"indexed": total}


def _now() -> str:
    from ..entity import now_iso

    return now_iso()
