# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""思维链节点 + 引用边。"""

from __future__ import annotations

from typing import Any, Iterable

from .. import db
from ..entity import ThoughtNode, dumps

COLS = (
    "id, chain_id, type, content, summary, skeleton, concepts, task_ref, salience, confidence, "
    "status, refs, strength, level, t_created, last_access, access_count, tombstone"
)
_WRITABLE = {
    "type", "content", "summary", "skeleton", "concepts", "task_ref", "salience",
    "confidence", "status", "refs", "strength", "level", "last_access", "access_count",
    "tombstone", "last_active_at", "in_residual", "session_tag",
}


def insert(node: ThoughtNode) -> ThoughtNode:
    row = node.to_row()
    keys = [k for k in row if k != "id"]
    db.ex(
        f"INSERT INTO thought_node(id, {', '.join(keys)}) VALUES(?{', ?' * len(keys)})",
        [row["id"]] + [row[k] for k in keys],
    )
    with db.write() as conn:
        for ref in node.refs:
            conn.execute(
                "INSERT OR IGNORE INTO graph_edge(src, dst, kind) VALUES(?,?,?)", (ref, node.id, "ref")
            )
    return node


def get(nid: str) -> ThoughtNode | None:
    row = db.q1(f"SELECT {COLS} FROM thought_node WHERE id=?", (nid,))
    return ThoughtNode.from_row(row) if row else None


def many(ids: Iterable[str]) -> list[ThoughtNode]:
    ids = [i for i in ids if i]
    if not ids:
        return []
    marks = ", ".join("?" * len(ids))
    rows = db.q(f"SELECT {COLS} FROM thought_node WHERE id IN ({marks})", ids)
    by_id = {r["id"]: ThoughtNode.from_row(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def update_fields(nid: str, **fields: Any) -> None:
    pairs, args = [], []
    for key, value in fields.items():
        if key not in _WRITABLE:
            raise KeyError(f"thought_node 没有可写字段 {key}")
        if isinstance(value, (list, dict)):
            value = dumps(value)
        elif isinstance(value, bool):
            value = int(value)
        pairs.append(f"{key}=?")
        args.append(value)
    if not pairs:
        return
    args.append(nid)
    db.ex(f"UPDATE thought_node SET {', '.join(pairs)} WHERE id=?", args)


def by_chain(chain_id: str) -> list[ThoughtNode]:
    rows = db.q(f"SELECT {COLS} FROM thought_node WHERE chain_id=? ORDER BY t_created", (chain_id,))
    return [ThoughtNode.from_row(r) for r in rows]


def open_nodes(limit: int = 50) -> list[ThoughtNode]:
    rows = db.q(
        f"SELECT {COLS} FROM thought_node WHERE status='open' AND tombstone=0 "
        f"AND type IN ('open_question','branch','decision') ORDER BY t_created DESC LIMIT ?",
        (int(limit),),
    )
    return [ThoughtNode.from_row(r) for r in rows]


def recent(hours: float = 3.0, limit: int = 60) -> list[ThoughtNode]:
    # 注意：t_created 是 ISO（带 T），直接跟 datetime() 的字符串比会出错：
    # 第 11 位 'T'(0x54) > 空格(0x20)，导致"今天的全都算在窗口内"。必须先 datetime() 归一化。
    rows = db.q(
        f"SELECT {COLS} FROM thought_node WHERE tombstone=0 "
        f"AND datetime(t_created) >= datetime('now','localtime', ?) ORDER BY t_created DESC LIMIT ?",
        (f"-{float(hours)} hours", int(limit)),
    )
    return [ThoughtNode.from_row(r) for r in rows]


def alive(limit: int = 2000) -> list[ThoughtNode]:
    rows = db.q(f"SELECT {COLS} FROM thought_node WHERE tombstone=0 AND level<3 LIMIT ?", (int(limit),))
    return [ThoughtNode.from_row(r) for r in rows]


def touch(nid: str, strength: float = 0.9, when: str = "") -> None:
    node = get(nid)
    if node is None:
        return
    from ..entity import now_iso

    db.ex(
        "UPDATE thought_node SET strength=?, level=?, last_access=?, access_count=access_count+1 WHERE id=?",
        (max(node.strength, strength), max(0, node.level - 1), when or now_iso(), nid),
    )


def add_edge(src: str, dst: str, kind: str = "ref") -> None:
    db.ex("INSERT OR IGNORE INTO graph_edge(src, dst, kind) VALUES(?,?,?)", (src, dst, kind))


def neighbours(ids: Iterable[str], hops: int = 1) -> set[str]:
    """两跳内的引用邻居（出边 + 入边都算）。"""
    frontier = {i for i in ids if i}
    seen: set[str] = set(frontier)
    for _ in range(max(0, int(hops))):
        if not frontier:
            break
        marks = ", ".join("?" * len(frontier))
        rows = db.q(
            f"SELECT src, dst FROM graph_edge WHERE src IN ({marks}) OR dst IN ({marks})",
            [*frontier, *frontier],
        )
        nxt: set[str] = set()
        for r in rows:
            for side in (r["src"], r["dst"]):
                if side not in seen:
                    seen.add(side)
                    nxt.add(side)
        frontier = nxt
    return seen


def close_open(concept: str, result_ref: str, note: str = "") -> int:
    """学到东西后关掉相关 open 节点（闭环 3）。"""
    from ..text import bigrams

    toks = set(bigrams(concept)) or {concept}
    rows = db.q(
        f"SELECT {COLS} FROM thought_node WHERE status='open' AND tombstone=0"
    )
    closed = 0
    for r in rows:
        node = ThoughtNode.from_row(r)
        hay = f"{node.content} {node.summary} {' '.join(node.concepts)}"
        if concept and (concept in hay or toks & set(bigrams(hay))):
            update_fields(
                node.id,
                status="resolved",
                summary=(node.summary + (" ｜ " if node.summary else "") + f"已学到：{note or concept}")[:200],
                refs=list(node.refs) + ([result_ref] if result_ref else []),
                confidence=min(1.0, node.confidence + 0.2),
            )
            if result_ref:
                add_edge(node.id, result_ref, "learned")
            closed += 1
    return closed


def set_index(nid: str, tokens: list[str], concepts: list[str] | None = None) -> int:
    from .memory import set_index as _set

    return _set(nid, "thought", tokens, concepts)
