# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""仲裁器（规格 6.4）。

score = 价值 × 紧迫性 × 预期收益 − 中断成本
只有胜出者能中断当前活动；好奇心与碎碎念有疲劳与预算上限。
"""

from __future__ import annotations

from .. import settings
from ..entity import ArbiterCandidate, CandidateKind


def weights() -> dict:
    return settings.thr().get("arbiter", {}).get("weights", {})


def _w(kind: str) -> dict:
    return weights().get(kind, {"value": 0.5, "urgency": 0.5, "expectancy": 0.5, "cost": 0.3, "cap": 1})


def make(kind: str, ref_id: str = "", *, value: float | None = None, urgency: float | None = None,
         expectancy: float | None = None, cost: float | None = None, fatigue: float = 0.0,
         note: str = "", payload: dict | None = None) -> ArbiterCandidate:
    base = _w(kind)
    return ArbiterCandidate(
        kind=kind, ref_id=ref_id,
        value=float(base["value"] if value is None else value),
        urgency=float(base["urgency"] if urgency is None else urgency),
        expectancy=float(base["expectancy"] if expectancy is None else expectancy),
        interrupt_cost=float(base["cost"] if cost is None else cost),
        fatigue=fatigue, note=note, payload=payload or {},
    )


def gather(ctx: dict, vec=None) -> list[ArbiterCandidate]:
    """把本周期所有活跃候选收上来。ctx 里的键都是可选的。"""
    from . import curiosity

    cands: list[ArbiterCandidate] = []
    if ctx.get("dialogue_pending"):
        cands.append(make(CandidateKind.DIALOGUE.value, ctx.get("dialogue_id", ""),
                          urgency=1.0, note="老大正在说话"))
    if ctx.get("task_pending"):
        cands.append(make(CandidateKind.TASK.value, ctx.get("task_id", ""), note="已答应的事"))

    w = curiosity.weights()
    for intent in ctx.get("intents") or []:
        domain = (intent.context or {}).get("domain") or curiosity.domain_of(intent.target)
        fatigue = w.tired(domain)
        cands.append(make(CandidateKind.LEARN.value, intent.id,
                          value=max(0.1, float(intent.curiosity_score)) * (1.0 - min(0.7, fatigue)),
                          urgency=0.6 if intent.source == "scheduled" else 0.4,
                          expectancy=float(intent.expected_learnability or 0.6),
                          fatigue=fatigue, note=intent.target,
                          payload={"domain": domain, "target": intent.target}))

    for node in (ctx.get("open_thoughts") or [])[:3]:
        cands.append(make(CandidateKind.OPEN_THOUGHT.value, node.get("id", ""),
                          note=node.get("summary", "")))

    if ctx.get("chitchat_due"):
        cands.append(make(CandidateKind.CHITCHAT.value, "",
                          value=ctx.get("chitchat_value", 0.5),
                          fatigue=ctx.get("chitchat_fatigue", 0.0), note="到点该碎碎念了"))
    if ctx.get("consolidate_due"):
        cands.append(make(CandidateKind.CONSOLIDATE.value, "", note="巩固窗口到了"))
    if ctx.get("curiosity_candidates"):
        for c in ctx["curiosity_candidates"][:3]:
            cands.append(make(CandidateKind.CURIOSITY.value, c.get("id", ""),
                              value=c.get("score", 0.5), note=c.get("target", "")))
    return cands


def pick(candidates: list[ArbiterCandidate], current: str = "",
         busy_kind: str = "") -> dict:
    """返回 {'winner':..,'picked':[..],'scored':[..],'interrupted':bool}

    picked 是按分数排队、且各类型没超过 cap 的那一批（第一个就是 winner）。
    """
    scored = []
    for cand in candidates:
        value = cand.value * (1.0 - min(0.8, cand.fatigue))
        score = value * cand.urgency * cand.expectancy - cand.interrupt_cost
        scored.append({"kind": cand.kind, "ref_id": cand.ref_id, "score": round(score, 4),
                       "value": round(value, 3), "fatigue": round(cand.fatigue, 2),
                       "note": cand.note, "payload": cand.payload})
    scored.sort(key=lambda x: x["score"], reverse=True)
    if not scored:
        return {"winner": None, "picked": [], "scored": [], "interrupted": False}

    caps: dict[str, int] = {}
    picked = []
    for entry in scored:
        limit = int(_w(entry["kind"]).get("cap", 1))
        if caps.get(entry["kind"], 0) >= limit:
            continue
        caps[entry["kind"]] = caps.get(entry["kind"], 0) + 1
        picked.append(entry)

    winner = picked[0] if picked else None
    interrupted = bool(busy_kind) and winner is not None and winner["kind"] != busy_kind
    return {"winner": winner, "picked": picked, "scored": scored[:8],
            "interrupted": interrupted, "current": current}
