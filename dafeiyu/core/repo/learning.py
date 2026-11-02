# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""学习意图 / 技能知识库 / 失败证据。"""

from __future__ import annotations

from typing import Any

from .. import db
from ..entity import Evidence, KnowledgeItem, LearningIntent, dumps

INTENT_COLS = (
    "id, target, gap_type, curiosity_score, expected_learnability, source, context, "
    "proposed_query, status, result_ref, attempts, last_error, t_created"
)
_KNOW_COLS = (
    "id, name, domain, concepts, content, slots, filled_ratio, uncertainty, confidence, "
    "source, intent_id, learned_at"
)


# ── 学习意图 ────────────────────────────────────────────────────────────────
def intent_insert(intent: LearningIntent) -> LearningIntent:
    row = intent.to_row()
    keys = [k for k in row if k != "id"]
    db.ex(
        f"INSERT INTO learning_intent(id, {', '.join(keys)}) VALUES(?{', ?' * len(keys)})",
        [row["id"]] + [row[k] for k in keys],
    )
    return intent


def intent_get(iid: str) -> LearningIntent | None:
    row = db.q1(f"SELECT {INTENT_COLS} FROM learning_intent WHERE id=?", (iid,))
    return LearningIntent.from_row(row) if row else None


def intent_update(iid: str, **fields: Any) -> None:
    allowed = {"target", "gap_type", "curiosity_score", "expected_learnability", "source",
               "context", "proposed_query", "status", "result_ref", "attempts", "last_error"}
    pairs, args = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise KeyError(f"learning_intent 没有可写字段 {key}")
        if isinstance(value, (list, dict)):
            value = dumps(value)
        pairs.append(f"{key}=?")
        args.append(value)
    if not pairs:
        return
    args.append(iid)
    db.ex(f"UPDATE learning_intent SET {', '.join(pairs)} WHERE id=?", args)


def intent_queue(status: str = "queued", limit: int = 50) -> list[LearningIntent]:
    rows = db.q(
        f"SELECT {INTENT_COLS} FROM learning_intent WHERE status=? "
        f"ORDER BY curiosity_score DESC, t_created LIMIT ?",
        (status, int(limit)),
    )
    return [LearningIntent.from_row(r) for r in rows]


def intent_open_count() -> int:
    return int(db.scalar("SELECT COUNT(*) FROM learning_intent WHERE status IN ('queued','learning')", (), 0) or 0)


def intent_find(target: str) -> LearningIntent | None:
    row = db.q1(
        f"SELECT {INTENT_COLS} FROM learning_intent WHERE target=? AND status IN ('queued','learning')",
        (target,),
    )
    return LearningIntent.from_row(row) if row else None


# ── 知识 / 技能 ─────────────────────────────────────────────────────────────
def knowledge_insert(item: KnowledgeItem) -> KnowledgeItem:
    row = item.to_row()
    keys = [k for k in row if k != "id"]
    db.ex(
        f"INSERT INTO knowledge_item(id, {', '.join(keys)}) VALUES(?{', ?' * len(keys)})",
        [row["id"]] + [row[k] for k in keys],
    )
    return item


def knowledge_get(kid: str) -> KnowledgeItem | None:
    row = db.q1(f"SELECT {_KNOW_COLS} FROM knowledge_item WHERE id=?", (kid,))
    return KnowledgeItem.from_row(row) if row else None


def knowledge_by_name(name: str) -> KnowledgeItem | None:
    row = db.q1(f"SELECT {_KNOW_COLS} FROM knowledge_item WHERE name=? ORDER BY learned_at DESC", (name,))
    return KnowledgeItem.from_row(row) if row else None


def knowledge_list(limit: int = 200, domain: str | None = None) -> list[KnowledgeItem]:
    sql = f"SELECT {_KNOW_COLS} FROM knowledge_item"
    args: list[Any] = []
    if domain:
        sql += " WHERE domain=?"
        args.append(domain)
    sql += " ORDER BY learned_at DESC LIMIT ?"
    args.append(int(limit))
    return [KnowledgeItem.from_row(r) for r in db.q(sql, args)]


def knowledge_update(kid: str, **fields: Any) -> None:
    allowed = {"name", "domain", "concepts", "content", "slots", "filled_ratio",
               "uncertainty", "confidence", "source", "intent_id"}
    pairs, args = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise KeyError(f"knowledge_item 没有可写字段 {key}")
        if isinstance(value, (list, dict)):
            value = dumps(value)
        pairs.append(f"{key}=?")
        args.append(value)
    if pairs:
        args.append(kid)
        db.ex(f"UPDATE knowledge_item SET {', '.join(pairs)} WHERE id=?", args)


def knowledge_count(domain: str | None = None) -> int:
    if domain:
        return int(db.scalar("SELECT COUNT(*) FROM knowledge_item WHERE domain=?", (domain,), 0) or 0)
    return int(db.scalar("SELECT COUNT(*) FROM knowledge_item", (), 0) or 0)


# ── 证据（失败 / 拒答 / 模拟失败）────────────────────────────────────────────
def evidence_add(ev: Evidence) -> Evidence:
    row = ev.to_row()
    keys = [k for k in row if k != "id"]
    db.ex(
        f"INSERT INTO evidence(id, {', '.join(keys)}) VALUES(?{', ?' * len(keys)})",
        [row["id"]] + [row[k] for k in keys],
    )
    return ev


def evidence_recent(limit: int = 30) -> list[Evidence]:
    rows = db.q(
        "SELECT id, kind, target, detail, uncertainty, t FROM evidence ORDER BY t DESC LIMIT ?",
        (int(limit),),
    )
    return [Evidence.from_row(r) for r in rows]


def evidence_for(target: str, limit: int = 10) -> list[Evidence]:
    rows = db.q(
        "SELECT id, kind, target, detail, uncertainty, t FROM evidence WHERE target=? ORDER BY t DESC LIMIT ?",
        (target, int(limit)),
    )
    return [Evidence.from_row(r) for r in rows]
