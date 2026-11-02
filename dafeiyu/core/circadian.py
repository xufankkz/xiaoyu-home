# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v12 2.4 昼夜节律 + 睡眠忠诚。

模板（config/circadian.json → hours）给出每个小时的底子，
再乘上"工作日/周末"和"14 天学到的系数"（0.6~1.3，学习幅度上限 ±0.3）。
凌晨 0–2 点还陪聊 → 睡眠忠诚：energy +0.3，一周最多 5 晚。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import db, settings
from .entity import now_iso
from .text import parse_iso

LEARNED = "circadian_learned.json"
LOYALTY = "sleep_loyalty.json"


def cfg() -> dict:
    return settings.load("circadian")


def _is_weekend(now: datetime) -> bool:
    return now.weekday() >= 5


def _template(now: datetime) -> float:
    hours = cfg().get("hours") or {}
    return float(hours.get(str(now.hour), 1.0))


def learned() -> dict:
    raw = settings.read_state(LEARNED, {})
    if not isinstance(raw.get("buckets"), dict):
        raw["buckets"] = {}
    return raw


def save_learned(data: dict) -> dict:
    settings.write_state(LEARNED, data)
    return data


def learn_from_history(days: int | None = None, now: datetime | None = None) -> dict:
    """按小时分桶统计最近 14 天她真正说话的量，学一个 0.6~1.3 的系数。"""
    now = now or datetime.now()
    conf = cfg().get("learn") or {}
    days = int(days or conf.get("days", 14))
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    rows = db.q("SELECT ts FROM instant_memory WHERE ts >= ? AND role='assistant'", (cutoff,))
    if not rows:
        rows = db.q("SELECT t AS ts FROM dialogue_message WHERE t >= ? AND role='assistant'", (cutoff,))
    counts: dict[int, int] = {}
    for row in rows:
        at = parse_iso(row["ts"] or "")
        if at is not None:
            counts[at.hour] = counts.get(at.hour, 0) + 1
    if not counts:
        return {"learned": 0, "note": "没有可学的样本"}
    average = sum(counts.values()) / max(1, len(counts))
    max_adjust = float(conf.get("max_adjust", 0.3))
    min_ratio = float(conf.get("min_ratio", 0.6))
    buckets = {}
    for hour in range(24):
        hit = counts.get(hour, 0)
        if average <= 0:
            continue
        ratio = hit / average
        adjust = max(-max_adjust, min(max_adjust, (ratio - 1.0) * 0.5))
        base = float((cfg().get("hours") or {}).get(str(hour), 1.0))
        buckets[str(hour)] = round(max(base * min_ratio, min(1.3, base * (1.0 + adjust))), 3)
    data = learned()
    data["buckets"] = buckets
    data["learned_at"] = now_iso()
    data["samples"] = len(rows)
    save_learned(data)
    return {"learned": len(buckets), "samples": len(rows)}


def factor(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    conf = cfg()
    kind = "weekend" if _is_weekend(now) else "weekday"
    base = dict(conf.get(kind) or {})
    hour_value = float((learned().get("buckets") or {}).get(str(now.hour), _template(now)))
    energy = max(0.3, min(1.3, hour_value * float(base.get("energy", 1.0))))
    return {
        "energy": round(energy, 3),
        "chitchat": round(max(0.0, min(1.5, energy * float(base.get("chitchat", 1.0)))), 3),
        "latency": round(max(0.5, min(2.5, float(base.get("latency", 1.0)) / max(0.3, energy))), 3),
        "hour": now.hour,
        "kind": kind,
    }


def in_sleep_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    win = cfg().get("sleep_loyalty") or {}
    start = int(win.get("window_start", 0))
    end = int(win.get("window_end", 2))
    return start <= now.hour < end


def _loyalty() -> dict:
    raw = settings.read_state(LOYALTY, {})
    if not isinstance(raw.get("nights"), list):
        raw["nights"] = []
    return raw


def sleep_loyalty(now: datetime | None = None) -> dict:
    """凌晨还在聊：给 energy 加成，但一周最多 5 晚。"""
    now = now or datetime.now()
    if not in_sleep_window(now):
        return {"active": False}
    win = cfg().get("sleep_loyalty") or {}
    cap = int(win.get("weekly_cap", 5))
    data = _loyalty()
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    nights = [d for d in data["nights"] if d >= week_ago]
    if today not in nights and len(nights) < cap:
        nights.append(today)
        data["nights"] = nights
        data["updated_at"] = now_iso()
        settings.write_state(LOYALTY, data)
    remaining = max(0, cap - len(set(nights)))
    return {
        "active": True,
        "boost": float(win.get("energy_boost", 0.3)),
        "phrase": str(win.get("phrase") or ""),
        "used_this_week": len(set(nights)),
        "remaining": remaining,
        "capped": today not in nights and len(set(nights)) >= cap,
    }


def status(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    fac = factor(now)
    learned_data = learned()
    return {
        "factor": fac,
        "learned_at": learned_data.get("learned_at", ""),
        "learned_hours": len(learned_data.get("buckets") or {}),
        "sleep": sleep_loyalty(now),
        "weekend": _is_weekend(now),
    }


def hint(now: datetime | None = None) -> str:
    """给提示词用的一句话（精力/话多/回消息快慢）。"""
    now = now or datetime.now()
    fac = factor(now)
    energy = fac["energy"]
    if energy >= 1.05:
        return "现在精神不错，可以多说两句。"
    if energy >= 0.8:
        return ""
    if energy >= 0.55:
        return "现在有点乏，句子短一点。"
    return "现在已经很晚了、很困，能一句就一句，别硬撑。"

