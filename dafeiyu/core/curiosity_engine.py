# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v13 自主好奇心引擎：不靠对话触发，后台自己找该学什么。

和对话触发的 curiosity 是两条并行通道：
- 对话发现「具体缺口」→ 提高对应领域的 specific_gap_score；
- 引擎每 30 分钟扫一遍所有领域，算紧迫度，挑一个建 source='autonomous' 的学习意图。

受三道闸：总开关、自动学习开关、单日花费上限（v14）。所有状态只在
state/curiosity_engine.json 里，不污染记忆表。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from . import cost, db, settings
from .entity import LearningIntent, now_iso
from .repo import learning as lr
from .text import clip, parse_iso

FILE = "curiosity_engine.json"
MAX_HOURS_SINCE = 72.0


def cfg() -> dict:
    return settings.load("curiosity_engine")


def _now() -> datetime:
    return datetime.now()


def state() -> dict:
    raw = settings.read_state(FILE, {})
    if not isinstance(raw.get("domains"), dict):
        raw["domains"] = {}
    raw.setdefault("last_run", "")
    raw.setdefault("last_target", "")
    raw.setdefault("last_urgency", 0.0)
    raw.setdefault("created_at", now_iso())
    return raw


def save(data: dict) -> dict:
    settings.write_state(FILE, data)
    return data


def _blank(name: str) -> dict:
    return {"name": name, "last_error": 0.5, "previous_error": 0.5, "progress": 0.0,
            "boredom": 0.0, "specific_gap_score": 0.0, "last_explored_at": "",
            "explore_count": 0, "note": ""}


# ── 领域 ────────────────────────────────────────────────────────────────────
def seed_domains() -> list[str]:
    """从已有的知识/意图/长期记忆概念里提领域；提不到再用配置里的种子领域。

    只收"像领域"的词：3~8 字、不是虚词。bigram 碎片（"是我""这一"）一律不要。
    """
    from .text import terms

    names: list[str] = []
    junk = {"是我", "这一", "一句", "行了", "让我", "别人", "在哪", "这个", "那个",
            "什么", "怎么", "可以", "就是", "没有", "一个", "我们", "你们", "他们"}
    punct = set("？?！!，,。.、；;：:～~…")
    try:
        for row in db.q("SELECT target FROM learning_intent ORDER BY t_created DESC LIMIT 20"):
            text = str(row["target"] or "").strip()
            # 只要"话题式"的目标：短、无标点（整句问题不进领域列表）
            if 2 <= len(text) <= 8 and not (set(text) & punct) and text not in names:
                names.append(text)
    except Exception:
        pass
    try:
        for row in db.q("SELECT concepts, content FROM memory_item WHERE layer IN ('long1','long2') "
                        "AND tombstone=0 ORDER BY salience DESC LIMIT 60"):
            for concept in terms(row["concepts"] or "", 6):
                text = str(concept).strip()
                if 3 <= len(text) <= 8 and text not in junk and text not in names:
                    names.append(text)
            if len(names) >= 12:
                break
    except Exception:
        pass
    for seed in (cfg().get("seed_domains") or []):
        if seed not in names:
            names.append(str(seed))
    return names[: int(cfg().get("max_domains", 12))]


def ensure_domains() -> dict:
    data = state()
    added = 0
    for name in seed_domains():
        if name not in data["domains"]:
            data["domains"][name] = _blank(name)
            added += 1
    if added:
        save(data)
    return data


def get_all_domains() -> list[dict]:
    return list(ensure_domains()["domains"].values())


# ── 紧迫度 ──────────────────────────────────────────────────────────────────
def _hours_since(stamp: str, now: datetime) -> float:
    at = parse_iso(stamp or "")
    if at is None:
        return MAX_HOURS_SINCE
    return max(0.0, (now - at).total_seconds() / 3600.0)


def calculate_urgency(domain: dict, now: datetime | None = None) -> float:
    """核心公式（规格 v13）：specific/进度/新颖 减去 无聊。"""
    now = now or _now()
    c = cfg()
    w = c.get("weights") or {}
    hours = _hours_since(domain.get("last_explored_at", ""), now)

    # ① 特定性好奇：缺口分 + 多久没碰过
    gap = float(domain.get("specific_gap_score", 0) or 0)
    staleness = min(1.0, hours / 24.0)
    specific = clamp01(0.6 * gap + 0.4 * staleness)

    # ② 学习进度：误差下降率（每小时的改善），越高越值得继续
    progress = float(domain.get("progress", 0) or 0)

    # ③ 广泛性好奇：越新（探索次数少）越有吸引力 + 一点随机
    count = int(domain.get("explore_count", 0) or 0)
    diversive = clamp01(1.0 / (1.0 + count / 4.0) * 0.8 + random.random() * 0.2)

    # ④ 无聊惩罚
    boredom = float(domain.get("boredom", 0) or 0)

    urgency = (float(w.get("specific", 0.4)) * specific
               + float(w.get("progress", 0.3)) * progress
               + float(w.get("diversive", 0.2)) * diversive
               - float(w.get("boredom", 0.3)) * boredom)
    return round(clamp01(urgency), 4)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def update_boredom(domain: dict, hours: float, now: datetime | None = None) -> dict:
    """进度停滞 → 无聊累积；有进展或久未探索 → 衰减。"""
    c = cfg()
    threshold = float(c.get("progress_threshold", 0.01))
    rate = float(c.get("boredom_accumulation_rate", 0.08))
    decay = float(c.get("boredom_decay_rate", 0.02))
    progress = float(domain.get("progress", 0) or 0)
    if progress < threshold:
        weight = 1.0 + 0.5 * int(domain.get("explore_count", 0) or 0) / 10.0   # 频繁探索加速无聊
        domain["boredom"] = clamp01(float(domain.get("boredom", 0)) + rate * hours * weight)
    else:
        domain["boredom"] = clamp01(max(0.0, float(domain.get("boredom", 0)) - decay * hours))
    return domain


def bump_gap(domain_name: str, amount: float = 0.25) -> dict:
    """对话里发现了这个领域的缺口 → 提高它的缺口分（覆盖无聊惩罚用）。"""
    data = ensure_domains()
    domain = data["domains"].setdefault(domain_name, _blank(domain_name))
    domain["specific_gap_score"] = clamp01(float(domain.get("specific_gap_score", 0)) + float(amount))
    save(data)
    return domain


def record_outcome(domain_name: str, *, error_before: float, error_after: float,
                   note: str = "") -> dict:
    """学习完成后回写：误差、进步率、探索次数、上次探索时间。"""
    data = ensure_domains()
    domain = data["domains"].setdefault(domain_name, _blank(domain_name))
    now = _now()
    hours = max(0.25, _hours_since(domain.get("last_explored_at", ""), now))
    domain["previous_error"] = float(error_before)
    domain["last_error"] = float(error_after)
    domain["progress"] = clamp01(abs(float(error_before) - float(error_after)) / hours)
    domain["explore_count"] = int(domain.get("explore_count", 0)) + 1
    domain["last_explored_at"] = now.isoformat(timespec="seconds")
    domain["specific_gap_score"] = clamp01(float(domain.get("specific_gap_score", 0)) * 0.5)
    domain["boredom"] = clamp01(max(0.0, float(domain.get("boredom", 0)) - 0.15))
    if note:
        domain["note"] = clip(note, 60)
    save(data)
    return domain


# ── v32：内稳态 → 好奇心（三位一体）────────────────────────────────────────
# 分工铁律：**全局无聊**归内稳态算（一个数，跨领域），**领域无聊**归这里的
# domain["boredom"]（按领域累积）。两边都算会互相打架，所以只在这里「接收信号」，
# 不改 calculate_urgency 的加权公式。
SOCIAL_HINTS = ("聊", "梗", "搞笑", "人际", "社交", "用户", "热门", "视频", "情绪", "开心")


def homeostasis_bias() -> dict:
    """从内稳态取两个信号：无聊强度（降门槛）、孤独强度（往社交类领域偏）。"""
    try:
        from . import homeostasis

        boredom, _dominant = homeostasis.boredom_signal()
        lonely = homeostasis.social_bias()
        return {"boredom": round(float(boredom), 3), "loneliness": round(float(lonely), 3)}
    except Exception:
        return {"boredom": 0.0, "loneliness": 0.0}


def social_like(name: str) -> bool:
    return any(hint in str(name) for hint in SOCIAL_HINTS)


# ── 选目标 ──────────────────────────────────────────────────────────────────
def get_next_learning_target(now: datetime | None = None, bias: dict | None = None) -> dict | None:
    """返回本轮该学的领域（含 urgency），没有就 None。

    bias（v32）：无聊把门槛压下来（她被闲坏了，该主动找点事做）；
    孤独把「人际/梗/搞笑」这类领域的排序抬上去（她难受时想学的是能跟人聊起来的东西）。
    注意：这里只改**门槛**和**排序**，不动 calculate_urgency 里的公式。
    """
    now = now or _now()
    threshold = float(cfg().get("threshold", 0.5))
    boredom = float((bias or {}).get("boredom") or 0.0)
    lonely = float((bias or {}).get("loneliness") or 0.0)
    if boredom > 0.3:
        threshold = max(0.2, threshold * (1.0 - 0.4 * min(1.0, boredom)))
    data = ensure_domains()
    candidates = []
    for domain in data["domains"].values():
        urgency = calculate_urgency(domain, now)
        if lonely > 0.2 and social_like(domain.get("name", "")):
            urgency *= 1.0 + 0.8 * min(1.0, lonely)
        domain["_urgency"] = urgency
        if urgency > threshold:
            candidates.append((domain, urgency))
    if not candidates:
        save({**data, "last_run": now.isoformat(timespec="seconds"),
              "last_target": "", "last_urgency": 0.0})
        return None
    domain, urgency = max(candidates, key=lambda pair: pair[1])
    return {"domain": domain["name"], "urgency": urgency}


def _query_for(domain: str) -> str:
    return f"{domain} 的核心概念、常见误解和最近的发展"


def budget_used_today() -> float:
    try:
        data = cost.attribute(days=1, tags=["curiosity_autonomous"])
        return float(data["total"]["cost"])
    except Exception:
        return 0.0


def budget_blocked() -> tuple[bool, str]:
    limit = float(cfg().get("budget_limit_per_day", 0) or 0)
    if limit <= 0:
        return False, ""
    used = budget_used_today()
    if used >= limit:
        return True, f"今天好奇心额度用完了（{used:.2f}/{limit:.2f} 元）"
    return False, ""


# ── 调度入口 ────────────────────────────────────────────────────────────────
def tick(now: datetime | None = None) -> dict:
    """每 interval 分钟跑一次。返回本轮结果（给审计和 UI）。"""
    now = now or _now()
    c = cfg()
    if not bool(c.get("enabled", True)):
        return {"skipped": "好奇心引擎已关闭"}

    # v32 疲劳熔断：能量低于阈值，本周期**一律**不学（返回空，不建意图）
    try:
        from . import homeostasis

        fuse, fuse_why = homeostasis.fatigue_fuse()
    except Exception:
        fuse, fuse_why = False, ""
    if fuse:
        return {"skipped": fuse_why, "fuse": True}

    blocked, why = budget_blocked()
    if blocked:
        return {"skipped": why}

    data = ensure_domains()
    # 无聊值按距上次调度的时长推进
    last = parse_iso(data.get("last_run") or "") or (now - timedelta(minutes=int(c.get("interval_minutes", 30))))
    hours = max(0.05, (now - last).total_seconds() / 3600.0)
    for domain in data["domains"].values():
        update_boredom(domain, hours, now)
    save(data)

    bias = homeostasis_bias()
    pick = get_next_learning_target(now, bias=bias)
    stamp = now.isoformat(timespec="seconds")
    if not pick:
        data = state()
        data.update({"last_run": stamp, "last_target": "", "last_urgency": 0.0})
        save(data)
        return {"target": "", "note": "本轮无自发学习", "bias": bias}

    domain = pick["domain"]
    intent = LearningIntent(target=domain, source="autonomous",
                            curiosity_score=round(float(pick["urgency"]), 3),
                            proposed_query=_query_for(domain),
                            context={"engine": "curiosity", "domain": domain,
                                     "urgency": pick["urgency"]})
    try:
        lr.intent_insert(intent)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "domain": domain}

    data = state()
    data.update({"last_run": stamp, "last_target": domain,
                 "last_urgency": round(float(pick["urgency"]), 3)})
    save(data)
    trace = f"[内稳态] 无聊 {bias['boredom']:.2f}/孤独 {bias['loneliness']:.2f} → [好奇心] 选定「{domain}」({pick['urgency']:.2f})"
    return {"target": domain, "urgency": round(float(pick["urgency"]), 3),
            "intent": intent.id, "auto_learn": bool(c.get("auto_learn", True)),
            "bias": bias, "trace": trace}


def status(*, seed: bool = True) -> dict:
    data = ensure_domains() if seed else state()
    now = _now()
    domains = []
    for domain in data["domains"].values():
        domains.append({
            "name": domain["name"],
            "urgency": calculate_urgency(domain, now),
            "progress": round(float(domain.get("progress", 0)), 4),
            "boredom": round(float(domain.get("boredom", 0)), 3),
            "gap": round(float(domain.get("specific_gap_score", 0)), 3),
            "explored": int(domain.get("explore_count", 0)),
            "last": domain.get("last_explored_at", ""),
        })
    domains.sort(key=lambda item: -item["urgency"])
    blocked, why = budget_blocked()
    return {
        "enabled": bool(cfg().get("enabled", True)),
        "auto_learn": bool(cfg().get("auto_learn", True)),
        "interval_minutes": int(cfg().get("interval_minutes", 30)),
        "threshold": float(cfg().get("threshold", 0.5)),
        "budget_limit": float(cfg().get("budget_limit_per_day", 0) or 0),
        "budget_used": round(budget_used_today(), 4),
        "budget_blocked": blocked,
        "budget_note": why,
        "last_run": data.get("last_run", ""),
        "last_target": data.get("last_target", ""),
        "last_urgency": data.get("last_urgency", 0),
        "domains": domains[:8],
    }


def set_config(**changes) -> dict:
    c = cfg()
    c.update({k: v for k, v in changes.items() if k in c})
    settings.save("curiosity_engine", c)
    return c
