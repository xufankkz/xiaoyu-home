# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""回合节点：把一轮真实对话沉淀成思维链。

以前核心只收到"这条要不要拒绝"，不知道她回了什么、翻到什么、当时什么状态。
现在每轮落两个节点：
- step：老大说了什么 / 她回了什么 / 翻到哪几条记忆（refs 指过去）
- reflection：这一回的事实型元信息（字数、语气、是否被拒），不是编造内心独白

同一会话的所有回合共用一条链（session.chain_for），会话断开时整条转入残留池。
"""

from __future__ import annotations

import hashlib
import threading

_turn_lock = threading.RLock()

from ..entity import ThoughtType
from ..repo import memory as mr
from ..repo import system as sr
from ..text import clip, keywords
from . import session
from . import chain as thought_chain


def _short_memories(memory_ids: list[str], limit: int = 3) -> list[str]:
    out = []
    for item in mr.many(list(memory_ids or [])[:limit]):
        out.append(clip(item.summary or item.content, 40))
    return out


def _concepts(*parts: str) -> list[str]:
    """回合节点的概念：优先用 3~4 字的实词（terms），退化才用 bigram。"""
    from ..text import keywords, terms

    body = " ".join(p for p in parts if p)
    picked = terms(body, 4)
    if not picked:
        picked = keywords(body, 4)
    return picked[:6]


def record_turn(conv: str, *, event_id: str = "", **arguments) -> dict:
    # Serialize duplicate acknowledgements within this core process. Stable node
    # ids also survive a process exit after the write but before the HTTP reply.
    with _turn_lock:
        return _record_turn(conv, event_id=event_id, **arguments)


def _record_turn(conv: str, *, event_id: str = "", user_text: str, reply_text: str = "",
                memory_ids: list[str] | None = None, emotion_tag: str = "平静",
                tone: str = "normal", decision: str = "", salience: float = 0.55) -> dict:
    """落一轮对话的思维链节点。返回 {step_id, reflection_id, chain_id, session_tag, is_new_session}。"""
    identity = hashlib.sha256((conv + ":" + event_id).encode()).hexdigest() if event_id else ""
    step_id = "turn-step-" + identity if identity else ""
    reflection_id = "turn-reflection-" + identity if identity else ""
    from ..repo import thought as tr
    previous = tr.get(step_id) if step_id else None
    reflected = tr.get(reflection_id) if reflection_id else None
    if previous is not None and reflected is not None:
        return {"step_id": step_id, "reflection_id": reflection_id, "chain_id": previous.chain_id, "duplicate": True}
    sess = session.touch(conv)
    chain_id = previous.chain_id if previous else sess["chain_id"]
    memory_ids = list(memory_ids or [])
    hits = _short_memories(memory_ids)

    lines = [f"老大说：{clip(user_text, 200)}"]
    if reply_text:
        lines.append(f"我当时回：{clip(reply_text, 300)}")
    if hits:
        lines.append("翻到的：" + "；".join(hits))
    content = "\n".join(lines)

    step = thought_chain.new_node(
        node_id=step_id, chain_id=chain_id, type=ThoughtType.STEP.value, content=content,
        summary=clip(user_text, 60), task_ref=conv,
        salience=salience, confidence=0.7, refs=memory_ids[:5],
        concepts=_concepts(user_text, reply_text),
    )
    _tag(step.id, sess["session_tag"])

    reply_len = len(reply_text or "")
    reflection = thought_chain.new_node(
        node_id=reflection_id, chain_id=chain_id, type=ThoughtType.REFLECTION.value,
        content=(f"这一回：回了 {reply_len} 字，语气 {tone}，翻了 {len(hits)} 条旧记忆"
                 + (f"，处置是 {decision}" if decision else "")
                 + ("" if reply_text else "，没接话")),
        summary=f"回 {reply_len} 字 / {tone}", task_ref=conv,
        salience=max(0.35, salience - 0.1), confidence=0.6, refs=[step.id],
        concepts=_concepts(reply_text) or ["回合"],
    )
    _tag(reflection.id, sess["session_tag"])

    session.add_nodes(conv, [step.id, reflection.id])
    sr.audit_add("thought", step.id, "turn_recorded",
                 {"conv": conv, "session_tag": sess["session_tag"], "chain_id": chain_id,
                  "reply_len": reply_len, "memory_ids": memory_ids[:5]})
    return {"step_id": step.id, "reflection_id": reflection.id, "chain_id": chain_id,
            "session_tag": sess["session_tag"], "is_new_session": sess["is_new"],
            "concepts": list(step.concepts or [])[:6]}


def _tag(node_id: str, session_tag: str) -> None:
    from ..entity import now_iso
    from ..repo import thought as tr

    try:
        tr.update_fields(node_id, session_tag=session_tag, last_active_at=now_iso(),
                         in_residual=0)
    except KeyError:
        pass
