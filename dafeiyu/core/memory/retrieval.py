# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""检索器（规格 6.11）。

双路召回：嵌入相似度 + 关键词倒排；按 strength × salience × 相关性 × 情绪一致性 排序。
命中即刷新 strength / last_access / access_count。
"""

from __future__ import annotations

from .. import settings
from ..entity import DIMS, TAG_DIM, EmotionVector, Layer, MemoryItem, ThoughtNode
from ..repo import memory as mr
from ..repo import thought as tr
from ..repo import hybrid
from ..text import bigrams, clip
from . import embed, reliability

# 2 号只在「概念命中」或「情绪对得上」时才翻（规格 5.1 / 6.11）
LT2_ONLY_GATED = True


def _norm(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values()) or 1.0
    return {k: v / top for k, v in scores.items()}


def mood_bonus(item: MemoryItem, vec: EmotionVector | None) -> float:
    """mood-congruent recall：与当前 dominant 一致的记忆加权。"""
    if vec is None:
        return 1.0
    dim = TAG_DIM.get(item.emotion_tag, "")
    if not dim or dim != vec.dominant:
        return 1.0
    weight = float(settings.get("retrieval.mood_bonus", 0.25))
    gap = abs(vec.get(dim) - float(vec.baseline.get(dim, 0.0))) / 100.0
    return 1.0 + weight * gap


def emotion_match(item: MemoryItem, vec: EmotionVector | None) -> bool:
    if vec is None:
        return False
    dim = TAG_DIM.get(item.emotion_tag, "")
    if not dim:
        return False
    threshold = float(settings.get("long2.emotion_read_threshold", 65))
    return vec.get(dim) >= threshold


def concept_match(item: MemoryItem, tokens: set[str]) -> int:
    """命中几个概念（含 bigram 交叉）。"""
    concepts = [str(c) for c in (item.concepts or [])]
    hits = 0
    for c in concepts:
        if c in tokens:
            hits += 1
            continue
        cb = set(bigrams(c))
        if cb & tokens:
            hits += 1
    return hits


def rank(items: list[MemoryItem], query: str, vec: EmotionVector | None,
         keyword: dict[str, float], semantic_scores: dict[str, float] | None = None,
         include_history: bool = False) -> list[tuple[float, MemoryItem]]:
    tokens = set(bigrams(query))
    kw = _norm(keyword)
    from .. import affective
    active = affective.active_memory_ids()
    scored: list[tuple[float, MemoryItem]] = []
    for item in items:
        if not item.alive:
            continue
        if not include_history and not reliability.current(item):
            continue
        text = f"{item.summary} {item.content} {item.skeleton}"
        sem = (semantic_scores or {}).get(item.id, 0.0) if semantic_scores is not None else (embed.similarity(query, text) if query else 0.0)
        rel = kw.get(item.id, 0.0) if semantic_scores is not None else (float(settings.get("retrieval.embed_weight", 0.55)) * sem
               + float(settings.get("retrieval.keyword_weight", 0.45)) * kw.get(item.id, 0.0))
        if query and rel <= 0 and not emotion_match(item, vec):
            continue
        score = (max(0.05, item.strength) ** 0.5) * max(0.05, item.salience) * (0.1 + 0.9 * rel)
        score *= mood_bonus(item, vec)
        if item.ext.get('editorial', {}).get('pinned'):
            score *= 1.35
        if item.id in active:
            score *= 1.1
        score *= max(.1, reliability.confidence_at(item))
        # 概念直接命中再给一点（倒排已经算过一次，这里只给情绪门控用）
        if item.layer == Layer.LONG2.value and LT2_ONLY_GATED:
            if concept_match(item, tokens) < 1 and not emotion_match(item, vec) and sem < 0.65 and not (len(query) >= 2 and query in text):
                continue
        scored.append((round(score, 5), item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def recall(query: str, vec: EmotionVector | None = None, *, layers: list[str] | None = None,
           top_k: int | None = None, include_thoughts: bool = True,
           touch: bool = True, extra_concepts: list[str] | None = None,
           include_history: bool = False, exclude_ids: set[str] | None = None) -> dict:
    """主检索入口：记忆条目 + 思维链子图 + open 节点。"""
    top_k = int(top_k or settings.get("retrieval.top_k", 12))
    q = " ".join([query or ""] + (extra_concepts or [])).strip()
    tokens = list(dict.fromkeys(bigrams(q)))
    hybrid_result = hybrid.search(q, "memory", 400)
    keyword = {r["id"]: r["score"] for r in hybrid_result["ranked"]}
    semantic_scores = {r["id"]: r["semantic"] for r in hybrid_result["ranked"]}

    ids = set(keyword.keys())
    pool: list[MemoryItem] = mr.many(ids)
    # 补充：近期 + 高显著性的条目（避免倒排漏掉的语义相近项）
    for layer in (layers or [Layer.INSTANT.value, Layer.SHORT.value, Layer.LONG1.value,
                             Layer.LONG2.value, Layer.LANGUAGE.value, Layer.EMOTION.value]):
        for item in mr.list_layer(layer=layer, limit=40, alive_only=True):
            if item.id not in ids:
                pool.append(item)
                ids.add(item.id)
    if layers:
        pool = [i for i in pool if i.layer in layers]
    if exclude_ids:
        pool = [i for i in pool if i.id not in exclude_ids]

    ranked = rank(pool, q, vec, keyword, semantic_scores, include_history or reliability.history_query(q))[:top_k]

    out_items = []
    for score, item in ranked:
        out_items.append({
            "id": item.id, "layer": item.layer, "subtype": item.subtype, "emotion_tag": item.emotion_tag,
            "summary": clip(item.summary or item.content, 90), "score": score,
            "strength": round(item.strength, 3), "salience": round(item.salience, 3),
            "concepts": item.concepts[:6], "t_created": item.t_created,
            "content": clip(item.content or item.summary, 1000), "origin": item.origin,
            "confidence": round(reliability.confidence_at(item), 3), "memory_type": item.memory_type,
            "valid_from": item.valid_from, "valid_until": item.valid_until,
            "superseded_by": item.superseded_by, "status": item.status,
        })
        if touch:
            mr.touch(item.id)
            from ..repo import system as sr
            sr.audit_add("memory", item.id, "retrieved", {"query": clip(q, 40), "score": score})

    thoughts: list[dict] = []
    open_questions: list[dict] = []

    # 技能栏 / 知识库也参与召回（闭环 4：同时捞回疑问节点和学会的技能）
    skills: list[dict] = []
    if tokens:
        knowledge_result = hybrid.search(q, "knowledge", 60)
        know_scores = {r["id"]: r["score"] for r in knowledge_result["ranked"]}
        if know_scores:
            from ..repo import learning as lr

            for kid, kscore in sorted(know_scores.items(), key=lambda kv: -kv[1])[:5]:
                kn = lr.knowledge_get(kid)
                if kn is None:
                    continue
                entry = {
                    "id": kn.id, "layer": "knowledge", "subtype": kn.domain or "skill",
                    "emotion_tag": "平静", "score": round(kscore, 3),
                    "summary": clip(f"{kn.name}：{kn.content}", 110),
                    "strength": 1.0, "salience": 0.8,
                    "concepts": list(kn.concepts or [])[:6], "t_created": kn.learned_at,
                }
                skills.append(entry)
                out_items.append(entry)

    if include_thoughts:
        tkw = mr.search(tokens, "thought", 200) if tokens else {}
        tids = list(tkw.keys())[:8]
        for node in tr.many(tids):
            thoughts.append({"id": node.id, "chain_id": node.chain_id, "type": node.type,
                             "summary": clip(node.summary or node.content, 80), "status": node.status})
            if touch:
                tr.touch(node.id)
                _residual_touch(node.id)
        graph = tr.neighbours(tids, int(settings.get("retrieval.graph_hops", 2)))
        linked = tr.many([i for i in graph if i not in set(tids)])
        for node in linked:
            entry = {"id": node.id, "chain_id": node.chain_id, "type": node.type,
                     "summary": clip(node.summary or node.content, 80), "status": node.status}
            if node.type == "open_question" or node.status == "open":
                open_questions.append(entry)
            elif entry not in thoughts:
                thoughts.append(entry)
        for node in tr.open_nodes(20):
            hay = f"{node.content} {node.summary} {' '.join(node.concepts)}"
            if concept_match(MemoryItem(concepts=node.concepts, emotion_tag="平静"), set(bigrams(q))) or \
                    (set(tokens) & set(bigrams(hay))):
                entry = {"id": node.id, "chain_id": node.chain_id, "type": node.type,
                         "summary": clip(node.summary or node.content, 80), "status": node.status}
                if entry not in open_questions:
                    open_questions.append(entry)

    return {
        "query": q,
        "retrieval": {k: hybrid_result[k] for k in ("backend", "fusion", "fts")},
        "tokens": tokens[:20],
        "items": out_items,
        "skills": skills,
        "thoughts": thoughts[:10],
        "open_questions": open_questions[:10],
    }


def _residual_touch(node_id: str) -> None:
    """被检索命中也算一次复述：刷新残留强度（只加这一笔，不改排序）。"""
    try:
        from ..thought import residual

        residual.touch(node_id)
    except Exception:
        pass


def context_block(result: dict, max_chars: int = 900) -> str:
    """把检索结果拼成注入用的上下文（走检索，禁止读全量历史）。"""
    lines = []
    for item in result.get("items", []):
        lines.append(f"- [{reliability.label(item)}·{item['t_created']}·{item.get('origin') or '来源未注明'}] {item['summary']}"
                     + (f"（有效至{item['valid_until']}）" if item.get('valid_until') else "")
                     + ("（历史记录，已被更新）" if item.get('superseded_by') else ""))
    for q in result.get("open_questions", []):
        lines.append(f"- （还没想通的）{q['summary']}")
    for t in result.get("thoughts", []):
        lines.append(f"- （想过的）{t['summary']}")
    if not lines:
        return ""
    return reliability.context(lines, max_chars)


def lt2_recall(query: str, vec: EmotionVector | None = None, limit: int | None = None) -> list[dict]:
    """2 号长期记忆的按需读取：概念匹配 OR 情绪相似度超阈值。"""
    limit = int(limit or settings.get("long2.top_k", 6))
    tokens = set(bigrams(query))
    items = mr.list_layer(layer=Layer.LONG2.value, limit=500, alive_only=True)
    scored = []
    for item in items:
        if not reliability.current(item) and not reliability.history_query(query):
            continue
        hits = concept_match(item, tokens)
        emo = emotion_match(item, vec)
        if hits < int(settings.get("long2.concept_match_min", 2)) and not emo:
            continue
        base = 1.0 * hits + (3.0 if emo else 0.0) + 0.3 * item.salience
        scored.append((round(base, 3), item, hits, emo))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, item, hits, emo in scored[:limit]:
        out.append({
            "id": item.id, "category": item.emotion_tag, "text": clip(item.content, 160),
            "date": (item.ext or {}).get("date") or item.t_created[:10],
            "impact": (item.ext or {}).get("impact", ""), "score": score,
            "reason": "情绪相似" if emo and hits < 2 else ("概念匹配" if hits >= 2 else "两者都有"),
        })
    return out
