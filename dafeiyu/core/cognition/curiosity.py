# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""好奇心评估器（规格 5.2 补强 / 6.4 候选来源）。

五路信号：信息缺口、预测误差×可学习性、学习进度导数、能力性好奇、社会性好奇。
疲劳衰减 + 联网预算 + 学完按误差下降回写权重。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .. import settings
from ..entity import GapType, IntentStatus, LearningIntent, now_iso
from ..repo import learning as lr
from ..text import bigrams, clip, parse_iso
from ..repo import memory as mr

DOMAIN_HINTS = {
    "害羞": ("害羞", "羞", "脸红", "暧昧", "尺度"),
    "情绪": ("情绪", "难过", "开心", "生气", "自卑", "委屈", "心情"),
    "语言": ("说话", "语气", "表达", "梗", "黑话", "口头禅", "聊天"),
}


# ── 权重状态 ────────────────────────────────────────────────────────────────
def weights():
    from ..entity import CuriosityWeights

    raw = settings.read_state("curiosity.json", {})
    if not raw:
        cfg = settings.thr().get("curiosity", {})
        return CuriosityWeights(budget={"hourly_web": int(cfg.get("hourly_web", 6)),
                                        "daily_web": int(cfg.get("daily_web", 30))})
    return CuriosityWeights.from_json(raw)


def save_weights(w) -> None:
    settings.write_state("curiosity.json", w.to_json())


def domain_of(target: str) -> str:
    for domain, hints in DOMAIN_HINTS.items():
        if any(h in (target or "") for h in hints):
            return domain
    return "通用"


def tick_fatigue() -> dict:
    """每 6 小时疲劳 ×0.7，别让刚探索过的领域一直冷着。"""
    w = weights()
    hours = float(settings.get("curiosity.fatigue_decay_hours", 6))
    changed = {}
    for domain, value in list(w.fatigue.items()):
        new = round(max(0.0, float(value) * 0.7), 3)
        if new < 0.02:
            w.fatigue.pop(domain, None)
        else:
            w.fatigue[domain] = new
        changed[domain] = new
    save_weights(w)
    return {"decayed": changed, "hours": hours}


def budget_ok(now: datetime | None = None) -> bool:
    cfg = settings.thr().get("curiosity", {})
    now = now or datetime.now()
    w = weights()
    log = [t for t in (w.web_log or []) if isinstance(t, str)]
    hour_ago = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    day_ago = (now - timedelta(days=1)).isoformat(timespec="seconds")
    hourly = sum(1 for t in log if t >= hour_ago)
    daily = sum(1 for t in log if t >= day_ago)
    return hourly < int(cfg.get("hourly_web", 6)) and daily < int(cfg.get("daily_web", 30))


def record_web() -> None:
    w = weights()
    now = datetime.now()
    w.web_log = [t for t in (w.web_log or []) if isinstance(t, str)
                 and t >= (now - timedelta(days=2)).isoformat(timespec="seconds")]
    w.web_log.append(now.isoformat(timespec="seconds"))
    save_weights(w)


# ── 五信号 ──────────────────────────────────────────────────────────────────
def signal_gap() -> float:
    items = lr.knowledge_list(limit=200)
    if not items:
        return 0.8
    ratios = [max(0.0, 1.0 - float(i.filled_ratio)) for i in items]
    open_intents = lr.intent_open_count()
    return round(min(1.0, 0.7 * (sum(ratios) / len(ratios)) + 0.1 * min(3, open_intents)), 3)


def signal_prediction_error() -> float:
    ev = lr.evidence_recent(50)
    if not ev:
        return 0.2
    fails = [e for e in ev if e.kind in ("failure", "refusal", "contradiction")]
    rate = len(fails) / max(1, len(ev))
    uncertainty = sum(float(e.uncertainty) for e in fails) / max(1, len(fails))
    return round(min(1.0, rate * uncertainty * 1.6), 3)


def signal_progress() -> float:
    items = lr.knowledge_list(limit=400)
    if not items:
        return 0.5
    recent = [i for i in items if (i.learned_at or "") >= (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")]
    growth = len(recent) / 3.0
    # 学得越多 → 边际收益越低（对数饱和），避免无限刷
    return round(min(1.0, math.log1p(growth) / math.log(4)), 3)


def signal_competence() -> float:
    ev = [e for e in lr.evidence_recent(60) if e.kind == "skill_failure"]
    if not ev:
        return 0.1
    return round(min(1.0, 0.25 * len(ev)), 3)


def signal_social(text: str) -> float:
    """对话里提到但知识库里没有的概念 → 社会性好奇。"""
    if not text:
        return 0.0
    known = set()
    for item in lr.knowledge_list(limit=300):
        known.update(item.concepts or [])
        known.update(bigrams(item.name or ""))
    tokens = set(bigrams(text))
    if not tokens:
        return 0.0
    unknown = tokens - known
    return round(min(1.0, len(unknown) / max(6, len(tokens))) * 0.9, 3)


def signals(text: str = "") -> dict:
    return {"gap": signal_gap(), "pred_err": signal_prediction_error(),
            "progress": signal_progress(), "competence": signal_competence(),
            "social": signal_social(text)}


def score_for(target: str, sig: dict, vec=None, w=None) -> float:
    w = w or weights()
    domain = domain_of(target)
    base = (0.30 * sig["gap"] + 0.25 * sig["pred_err"] + 0.15 * sig["progress"]
            + 0.15 * sig["competence"] + 0.15 * sig["social"])
    mood = vec.mood_factor(float(settings.get("curiosity.mood_pos_gain", 0.004)),
                           float(settings.get("curiosity.mood_neg_gain", 0.003))) if vec else 1.0
    return round(base * w.weight(domain) * (1.0 - min(0.8, w.tired(domain))) * mood, 3)


# ── 评估 → 入队 ─────────────────────────────────────────────────────────────
def assess(text: str = "", targets: list[str] | None = None, vec=None,
           source: str = "dialog") -> list[LearningIntent]:
    """把「信息缺口 / 对话里不懂的概念 / open 问题」变成学习意图。"""
    sig = signals(text)
    threshold = float(settings.get("curiosity.threshold", 0.45))
    w = weights()

    candidates: list[tuple[str, str]] = []
    for t in targets or []:
        if t:
            candidates.append((t, f"{t} 是什么？怎么用？有什么例子？"))
    # 没给目标：优先从「还没想通的思维链节点」里取（open_question 喂好奇心）
    if not candidates:
        from ..repo import thought as tr

        for node in tr.open_nodes(6):
            target = clip(node.summary or node.content, 40)
            if target:
                candidates.append((target, node.content or node.summary))
    # 对话里直接问出来的疑问
    if not candidates and text and any(m in text for m in ("?", "？", "什么", "为什么", "怎么", "是不是")):
        candidates.append((clip(text, 40), text))
    # 最后：知识缺口
    if not candidates:
        for item in lr.knowledge_list(limit=20):
            if float(item.filled_ratio) < 1.0 and item.name:
                candidates.append((item.name, f"{item.name} 的细节是什么？"))

    made: list[LearningIntent] = []
    seen: set[str] = set()
    for target, query in candidates[:8]:
        target = clip(target, 40)
        if not target or target in seen or lr.intent_find(target) is not None:
            continue
        seen.add(target)
        learnability = _learnability(target)
        if learnability < 0.25:
            continue
        score = score_for(target, sig, vec=vec, w=w)
        if score < threshold:
            continue
        domain = domain_of(target)
        intent = LearningIntent(
            target=target,
            gap_type=GapType.ATTRIBUTE_MISSING.value if sig["gap"] > 0.5 else GapType.RELATION_MISSING.value,
            curiosity_score=score,
            expected_learnability=learnability,
            source=source,
            context={"signals": sig, "domain": domain, "from_text": clip(text, 120)},
            proposed_query=clip(query or target, 200),
        )
        lr.intent_insert(intent)
        w.last_explored[domain] = now_iso()
        made.append(intent)
    save_weights(w)
    return made


def _learnability(target: str) -> float:
    """可学习性判别：太泛/太私人的缺口不值得联网学。"""
    body = (target or "").strip()
    if len(body) < 2:
        return 0.0
    if body in ("这个", "那个", "怎么办", "为什么", "什么", "怎么", "啥", "嗯", "在吗"):
        return 0.05
    if len(body) > 80:
        return 0.3
    return 0.8


def learnability(intent: LearningIntent) -> float:
    return min(_learnability(intent.target), float(intent.expected_learnability or 0.6))


def feedback(domain: str, before_uncertainty: float, after_uncertainty: float) -> dict:
    """学完按误差下降幅度更新好奇心权重（闭环 5）。"""
    w = weights()
    drop = max(-0.5, float(before_uncertainty) - float(after_uncertainty))
    domain = domain or "通用"
    lo = float(settings.get("curiosity.domain_floor", 0.3))
    hi = float(settings.get("curiosity.domain_ceil", 2.5))
    new = max(lo, min(hi, w.weight(domain) * (1.0 + 0.3 * drop)))
    w.domain_weights[domain] = round(new, 3)
    w.feedback[domain] = round(float(w.feedback.get(domain, 0.0)) + drop, 3)
    # 探索过就涨疲劳
    w.fatigue[domain] = round(min(0.8, w.tired(domain) + float(settings.get("curiosity.fatigue_gain", 0.35))), 3)
    w.last_explored[domain] = now_iso()
    save_weights(w)
    return {"domain": domain, "drop": round(drop, 3), "weight": new, "fatigue": w.fatigue[domain]}


def view() -> dict:
    w = weights()
    sig = signals()
    return {
        "signals": sig,
        "domain_weights": {k: round(v, 3) for k, v in w.domain_weights.items()},
        "fatigue": {k: round(v, 3) for k, v in w.fatigue.items()},
        "last_explored": w.last_explored,
        "budget_ok": budget_ok(),
        "queue": [{"id": i.id, "target": i.target, "score": round(i.curiosity_score, 3),
                   "gap": i.gap_type, "source": i.source, "status": i.status,
                   "learnability": round(learnability(i), 2)} for i in lr.intent_queue(limit=30)],
        "open_intents": lr.intent_open_count(),
    }
