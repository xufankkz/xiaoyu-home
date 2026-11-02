# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""长期记忆：1 号（性格 / 朋友 / 老大）与 2 号（按情绪分类）。

2 号平时不读；只在「概念匹配」或「情绪相似度 ≥ 阈值」时召回（规格 5.1 / 6.11）。
"""

from __future__ import annotations

from .. import settings
from ..entity import DIMS, LT2_CATEGORIES, Layer, MemoryItem
from ..repo import memory as mr
from ..text import clip


def lt1_groups() -> list[dict]:
    items = mr.list_layer(layer=Layer.LONG1.value, limit=200, alive_only=True, order="t_created DESC")
    groups: dict[str, list[dict]] = {"personality": [], "friend": [], "owner": [], "conclusion": []}
    for item in items:
        key = item.subtype if item.subtype in groups else "conclusion"
        groups[key].append({
            "id": item.id, "subtype": item.subtype, "content": clip(item.content, 2000),
            "chars": len(item.content), "t_created": item.t_created,
            "salience": round(item.salience, 2), "summary": item.summary,
            "concepts": item.concepts[:8],
        })
    return [{"key": k, "title": {"personality": "性格设定", "friend": "朋友档案",
                                 "owner": "老大", "conclusion": "沉淀结论"}[k],
             "items": groups[k], "count": len(groups[k])} for k in ("personality", "friend", "owner", "conclusion")]


def lt2_groups() -> list[dict]:
    out = []
    for cat in LT2_CATEGORIES:
        items = mr.list_layer(layer=Layer.LONG2.value, emotion_tag=cat, limit=500,
                              alive_only=True, order="t_created DESC")
        out.append({
            "category": cat,
            "count": len(items),
            "chars": sum(len(i.content) for i in items),
            "items": [{
                "id": i.id, "date": (i.ext or {}).get("date") or i.t_created[:10],
                "text": clip(i.content, 1200), "summary": i.summary,
                "impact": (i.ext or {}).get("impact", ""),
                "labels": (i.ext or {}).get("labels", ""),
                "salience": round(i.salience, 2), "t_created": i.t_created,
            } for i in items[:60]],
        })
    return out


def lt2_counts() -> dict:
    groups = lt2_groups()
    return {"total": sum(g["count"] for g in groups),
            "by_category": {g["category"]: g["count"] for g in groups}}


def recall(query: str, vec=None, limit: int | None = None) -> list[dict]:
    from . import retrieval

    return retrieval.lt2_recall(query, vec=vec, limit=limit)


def recall_line(query: str, vec=None, limit: int = 2, max_chars: int = 600) -> str:
    """给桥接/对话用的「翻到的旧事」段落（与旧系统同款文案）。"""
    hits = recall(query, vec=vec, limit=limit)
    if not hits:
        return ""
    lines = [f"- [{h['category']}·{h['date'] or '未知日期'}] {clip(h['text'], 82)}"
             + (f"（影响：{clip(h['impact'], 34)}）" if h.get("impact") else "") for h in hits]
    block = (
        "【翻到的旧事（2号长期记忆）】\n" + "\n".join(lines)
        + "\n（这是我自己记的事，不是别人说的；自然相关才提一句，不相关就别硬提。）\n\n"
    )
    return clip(block, max_chars, "…）\n\n")


def remember(layer: str, subtype: str, content: str, *, emotion_tag: str = "平静",
             summary: str = "", concepts: list[str] | None = None, salience: float = 0.7,
             origin: str = "manual", ext: dict | None = None,
             status: str = "resolved") -> MemoryItem:
    from ..bridge.legacy_import import _id
    from ..text import bigrams, keywords

    item = MemoryItem(
        id=_id(layer, subtype, content[:60]),
        layer=layer, subtype=subtype, content=content, summary=summary or clip(content, 60),
        skeleton=clip(content, 40), concepts=concepts or keywords(content, 6),
        emotion_tag=emotion_tag, salience=salience, status=status, origin=origin, ext=ext or {},
    )
    mr.upsert(item)
    mr.set_index(item.id, "memory", bigrams(content), item.concepts)
    return item


def emotion_gate(category: str, vec) -> dict:
    """2 号某分类此刻是否够格被读（界面要显示这个状态）。"""
    from ..entity import TAG_DIM

    dim = TAG_DIM.get(category, "")
    threshold = float(settings.get("long2.emotion_read_threshold", 65))
    value = vec.get(dim) if dim else 0.0
    return {"category": category, "dim": dim, "value": round(value, 1),
            "threshold": threshold, "open": bool(dim) and value >= threshold}


def gates(vec) -> list[dict]:
    return [emotion_gate(c, vec) for c in LT2_CATEGORIES]
