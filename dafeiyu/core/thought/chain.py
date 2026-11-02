# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""思维链：图结构存储 + 上下文注入（规格 6.2）。

不读全量历史：最近 3h 原文 + 相关 level1-2 节点 + 相关 open 节点 + 被引用节点。
"""

from __future__ import annotations

from .. import settings
from ..entity import Status, ThoughtNode, ThoughtType, now_iso
from ..repo import thought as tr
from ..text import bigrams, clip, keywords, skeleton_of


def new_node(*, chain_id: str, type: str, content: str, summary: str = "",
             concepts: list[str] | None = None, task_ref: str = "",
             salience: float = 0.5, confidence: float = 0.5,
             refs: list[str] | None = None, status: str | None = None, node_id: str = "") -> ThoughtNode:
    if node_id:
        existing = tr.get(node_id)
        if existing is not None:
            return existing
    body = (content or "").strip()
    node = ThoughtNode(
        chain_id=chain_id,
        type=type,
        content=body,
        summary=summary or clip(body, 80),
        skeleton=skeleton_of(body),
        concepts=concepts or keywords(body, 6),
        task_ref=task_ref,
        salience=round(max(0.0, min(1.0, salience)), 3),
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        status=status or (Status.OPEN.value if type == ThoughtType.OPEN_QUESTION.value else Status.RESOLVED.value),
        refs=refs or [],
        t_created=now_iso(),
    )
    if node_id:
        node.id = node_id
    tr.insert(node)
    tr.set_index(node.id, bigrams(f"{node.summary} {body}"), node.concepts)
    return node


def open_question(chain_id: str, content: str, *, task_ref: str = "",
                  salience: float = 0.7, refs: list[str] | None = None) -> ThoughtNode:
    return new_node(chain_id=chain_id, type=ThoughtType.OPEN_QUESTION.value,
                    content=content, concepts=keywords(content, 6), task_ref=task_ref,
                    salience=salience, confidence=0.4, refs=refs or [], status=Status.OPEN.value)


def chain_for(dialogue_id: str = "") -> str:
    """一次对话/任务一条链（用 task_ref 复用未结束的链）。"""
    if dialogue_id:
        row = tr.open_nodes(200)
        for node in row:
            if node.task_ref == dialogue_id:
                return node.chain_id
    from ..entity import new_id

    return new_id()


def context_block(query: str, vec=None, max_chars: int = 1200) -> str:
    """上下文注入：走检索器，绝不读全量历史。"""
    from ..memory import retrieval, working

    result = retrieval.recall(query, vec=vec, top_k=8, touch=False)
    parts: list[str] = []
    focus = working.focus(4)
    if focus:
        parts.append("【正在想的事】\n" + "\n".join(f"- {f['text']}" for f in focus))
    recent = tr.recent(hours=3, limit=8)
    if recent:
        parts.append("【刚才在想的】\n" + "\n".join(f"- {clip(n.summary or n.content, 70)}" for n in recent))
    if result.get("open_questions"):
        parts.append("【还没想通的】\n" + "\n".join(f"- {q['summary']}" for q in result["open_questions"]))
    if result.get("items"):
        parts.append("【翻到的】\n" + "\n".join(f"- {i['summary']}" for i in result["items"][:6]))
    body = "\n\n".join(parts)
    return clip(body, max_chars, "…") if body else ""


def prune_orphans(keep_chain_days: int = 30) -> int:
    """清理没有边也不 open 的陈年孤立节点（只是省空间，不影响检索）。"""
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=keep_chain_days)).isoformat(timespec="seconds")
    from .. import db

    with db.write() as conn:
        cur = conn.execute(
            "DELETE FROM thought_node WHERE status!='open' AND t_created < ? "
            "AND id NOT IN (SELECT dst FROM graph_edge UNION SELECT src FROM graph_edge)",
            (cutoff,),
        )
        return int(cur.rowcount or 0)
