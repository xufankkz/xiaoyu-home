# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""元认知层（规格 6.3）：知识带完整度与不确定性，失败/拒答结构化留证，缺口→学习意图。"""

from __future__ import annotations

from .. import settings
from ..entity import GapType, KnowledgeItem, LearningIntent, now_iso
from ..repo import learning as lr
from ..text import bigrams, clip, keywords

DEFAULT_SLOTS = ["是什么", "怎么用", "例子", "边界"]


def parse_learning(text: str) -> dict:
    """把模型输出拆成【学到】【例子】【还不懂】三块。"""
    def section(marker: str) -> str:
        start = text.find(f"【{marker}】")
        if start < 0:
            return ""
        start += len(marker) + 2
        rest = text[start:]
        nxt = rest.find("【")
        return (rest if nxt < 0 else rest[:nxt]).strip()

    learned = section("学到")
    example = section("例子")
    unknown = section("还不懂")
    if not learned and not example:
        learned = text.strip()
    return {"learned": learned, "example": example, "unknown": unknown}


def learn(name: str, raw_output: str, *, domain: str = "", source: str = "web",
          intent_id: str = "", concepts: list[str] | None = None) -> KnowledgeItem:
    """写入技能栏/知识库（闭环 3）。"""
    parts = parse_learning(raw_output)
    body = "\n\n".join(x for x in (parts["learned"], parts["example"]) if x) or raw_output.strip()
    unknown = parts["unknown"]
    slots = {
        "应有": DEFAULT_SLOTS,
        "已填": {
            "是什么": clip(parts["learned"], 400),
            "怎么用": clip(parts["learned"], 240),
            "例子": clip(parts["example"], 200),
            "边界": "" if unknown in ("", "无", "没有") else clip(unknown, 200),
        },
    }
    kn = KnowledgeItem(
        name=clip(name, 40), domain=domain or "通用",
        concepts=concepts or keywords(f"{name} {body}", 8),
        content=body, slots=slots, source=source, intent_id=intent_id,
        confidence=0.7 if parts["learned"] else 0.45,
    )
    kn.recompute()
    if unknown and unknown not in ("无", "没有"):
        kn.uncertainty = round(max(kn.uncertainty, 0.5), 3)
    lr.knowledge_insert(kn)
    # 技能也要进倒排索引，否则「后续检索同时召回疑问和技能」做不到
    from ..repo import memory as mr

    mr.set_index(kn.id, "knowledge", bigrams(f"{kn.name} {body}"), kn.concepts)
    return kn


def gaps(limit: int = 30) -> list[dict]:
    out = []
    for item in lr.knowledge_list(limit=200):
        if float(item.filled_ratio) >= 1.0:
            continue
        need = list((item.slots or {}).get("应有") or [])
        filled = dict((item.slots or {}).get("已填") or {})
        missing = [k for k in need if not str(filled.get(k, "")).strip()]
        out.append({"id": item.id, "name": item.name, "domain": item.domain,
                    "filled_ratio": round(item.filled_ratio, 2),
                    "uncertainty": round(item.uncertainty, 2), "missing": missing})
        if len(out) >= limit:
            break
    return out


def uncertainty_of(target: str) -> float:
    item = lr.knowledge_by_name(clip(target, 40))
    if item is None:
        return 1.0
    return float(item.uncertainty)


def intents_from_gaps(vec=None) -> list[LearningIntent]:
    """缺口检测器：把知识库的缺槽汇总成学习意图。"""
    from . import curiosity

    made = []
    for gap in gaps(limit=6):
        if lr.intent_find(gap["name"]) is not None:
            continue
        score = curiosity.score_for(gap["name"], curiosity.signals(), vec=vec)
        if score < float(settings.get("curiosity.threshold", 0.45)):
            continue
        intent = LearningIntent(
            target=gap["name"],
            gap_type=GapType.ATTRIBUTE_MISSING.value,
            curiosity_score=score,
            expected_learnability=0.7,
            source="autonomous",
            context={"missing_slots": gap["missing"], "domain": gap["domain"],
                     "knowledge_id": gap["id"]},
            proposed_query=f"{gap['name']} 的{'、'.join(gap['missing'] or ['细节'])}是什么？",
        )
        lr.intent_insert(intent)
        made.append(intent)
    return made


def record_failure(target: str, detail: str, kind: str = "failure", uncertainty: float = 0.7):
    from ..memory import distill

    return distill.record_failure(target, detail, kind=kind, uncertainty=uncertainty)


def view() -> dict:
    items = lr.knowledge_list(limit=50)
    return {
        "knowledge": [{"id": i.id, "name": i.name, "domain": i.domain,
                       "filled": round(i.filled_ratio, 2), "uncertainty": round(i.uncertainty, 2),
                       "source": i.source, "learned_at": i.learned_at} for i in items],
        "gaps": gaps(12),
        "evidence": [{"kind": e.kind, "target": e.target, "detail": e.detail[:80], "t": e.t}
                     for e in lr.evidence_recent(12)],
        "counts": {"knowledge": len(items), "evidence": len(lr.evidence_recent(200))},
    }
