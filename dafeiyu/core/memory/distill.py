# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""蒸馏：删除正文之前，先把结论 / 未决 / 教训搬到该去的地方（红线）。

规格 6.2：level=3 前必须蒸馏——结论入语义记忆，未决入待学队列，教训入技能栏，
然后删除正文，保留墓碑节点与 refs 边。
"""

from __future__ import annotations

from .. import settings
from ..entity import (
    Evidence, GapType, KnowledgeItem, Layer, LearningIntent, MemoryItem, Status, ThoughtNode,
)
from ..repo import learning as lr, memory as mr, thought as tr
from ..repo import system as sr
from ..text import bigrams, clip, keywords, skeleton_of

CONCLUSION_TARGET = {Layer.LONG1.value}
LESSON_MARKERS = ("应该", "下次", "别再", "记住", "以后", "教训", "不能再", "学会")
OPEN_MARKERS = ("?", "？", "为什么", "怎么", "是不是", "要不要", "该怎么办", "到底")


def _target_layer(item: MemoryItem) -> str:
    if item.layer in CONCLUSION_TARGET or item.subtype in ("personality", "friend", "owner"):
        return Layer.LONG1.value
    return Layer.LONG2.value


def _emoji_tag(item: MemoryItem) -> str:
    return item.emotion_tag or "平静"


def distill_item(item: MemoryItem, reason: str = "level3") -> dict:
    """把一条记忆蒸馏成 结论 / 未决 / 教训，然后清空正文留墓碑。"""
    body = item.content or item.summary
    if not body:
        return {"skipped": "empty"}
    produced: dict = {"conclusion": "", "intent": "", "lesson": "", "from": item.id}
    concepts = list(dict.fromkeys(list(item.concepts or []) + keywords(body, 4)))[:8]

    # 1) 结论 → 语义记忆（1 号或 2 号）
    conclusion_text = item.summary or clip(body, 120)
    conclusion = MemoryItem(
        layer=_target_layer(item),
        subtype="conclusion",
        content=conclusion_text,
        summary=clip(conclusion_text, 60),
        skeleton=skeleton_of(conclusion_text),
        concepts=concepts,
        emotion_tag=_emoji_tag(item),
        salience=round(min(1.0, max(0.6, item.salience)), 3),
        status=Status.RESOLVED.value,
        refs=[item.id],
        origin=f"distill:{reason}",
        ext={"distilled_from": item.id, "distill_reason": reason, "original_chars": len(body)},
    )
    mr.insert(conclusion)
    mr.set_index(conclusion.id, "memory", bigrams(conclusion_text), concepts)
    produced["conclusion"] = conclusion.id

    # 2) 未决 → 学习意图（蔡格尼克锚点）
    if item.status == Status.OPEN.value and any(m in body for m in OPEN_MARKERS):
        target = clip(item.skeleton or conclusion_text, 40)
        intent = LearningIntent(
            target=target,
            gap_type=GapType.ATTRIBUTE_MISSING.value,
            curiosity_score=0.5,
            expected_learnability=0.6,
            source="autonomous",
            context={"from_memory": item.id, "concepts": concepts},
            proposed_query=f"{target} 是什么？怎么理解？",
        )
        if lr.intent_find(target) is None:
            lr.intent_insert(intent)
            produced["intent"] = intent.id

    # 3) 教训 → 技能栏
    if any(m in body for m in LESSON_MARKERS):
        name = clip(item.skeleton or conclusion_text, 30)
        item_kn = KnowledgeItem(
            name=name,
            domain="教训",
            concepts=concepts,
            content=body,
            slots={"应有": ["做法", "原因"], "已填": {"做法": clip(body, 80), "原因": "（蒸馏自经历）"}},
            source=f"distill:{item.id}",
        )
        item_kn.recompute()
        lr.knowledge_insert(item_kn)
        produced["lesson"] = item_kn.id

    # 4) 留墓碑：正文删掉，骨架/概念/refs 留着才能被检索到
    mr.update_fields(
        item.id,
        content="",
        summary=item.summary or clip(body, 60),
        skeleton=item.skeleton or skeleton_of(body),
        concepts=concepts,
        level=3,
        tombstone=1,
        status=item.status,
        strength=min(item.strength, 0.1),
        ext={**(item.ext or {}), "distilled": True, "distilled_at": _now(),
             "distill_reason": reason, "produced": produced},
    )
    mr.drop_index(item.id, "memory")
    mr.set_index(item.id, "memory", [], concepts)  # 墓碑只留概念，仍可被概念检索到（闭环 7）
    sr.audit_add("memory", item.id, "distilled", {"reason": reason, "produced": produced})
    return produced


def distill_thought(node: ThoughtNode, reason: str = "level3") -> dict:
    body = node.content or node.summary
    if not body:
        return {"skipped": "empty"}
    produced: dict = {"conclusion": "", "intent": "", "lesson": "", "from": node.id}
    concepts = list(dict.fromkeys(list(node.concepts or []) + keywords(body, 4)))[:8]
    if node.type in ("conclusion", "reflection", "decision"):
        conclusion = MemoryItem(
            layer=Layer.LONG1.value if node.type == "conclusion" else Layer.LONG2.value,
            subtype="conclusion",
            content=clip(body, 200),
            summary=clip(node.summary or body, 60),
            skeleton=node.skeleton or skeleton_of(body),
            concepts=concepts,
            emotion_tag="平静",
            salience=round(max(0.6, node.salience), 3),
            status=Status.RESOLVED.value,
            refs=[node.id],
            origin=f"thought_distill:{reason}",
        )
        mr.insert(conclusion)
        mr.set_index(conclusion.id, "memory", bigrams(conclusion.content), concepts)
        produced["conclusion"] = conclusion.id
    if node.type == "open_question" or node.status == Status.OPEN.value:
        target = clip(node.summary or body, 40)
        intent = LearningIntent(
            target=target, gap_type=GapType.ATTRIBUTE_MISSING.value, curiosity_score=0.6,
            expected_learnability=0.6, source="autonomous",
            context={"from_thought": node.id, "chain_id": node.chain_id, "concepts": concepts},
            proposed_query=f"{target} 到底是怎么回事？",
        )
        if lr.intent_find(target) is None:
            lr.intent_insert(intent)
            produced["intent"] = intent.id
    if any(m in body for m in LESSON_MARKERS):
        kn = KnowledgeItem(name=clip(node.skeleton or node.summary or body, 30), domain="教训",
                           concepts=concepts, content=body, source=f"thought:{node.id}")
        kn.recompute()
        lr.knowledge_insert(kn)
        produced["lesson"] = kn.id

    tr.update_fields(node.id, content="", summary=node.summary or clip(body, 60),
                     level=3, tombstone=1, strength=min(node.strength, 0.1))
    sr.audit_add("thought", node.id, "distilled", {"reason": reason, "produced": produced})
    return produced


def record_failure(target: str, detail: str, kind: str = "failure", uncertainty: float = 0.7) -> Evidence:
    ev = Evidence(kind=kind, target=target, detail=clip(detail, 300), uncertainty=uncertainty)
    lr.evidence_add(ev)
    sr.audit_add("evidence", ev.id, "created", {"kind": kind, "target": target})
    return ev


def _now() -> str:
    from ..entity import now_iso

    return now_iso()
