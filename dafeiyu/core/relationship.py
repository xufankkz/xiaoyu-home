# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""关系层（v12 2.5 / 3.2）：亲密度 + 索取度。

索取度是老大要的那根 0–100 的滑杆：
- 0   = 完全不允许索取（不撒娇、不要陪、不赌气，只安静回应）
- 65  = 默认，会撒娇、会要陪，但不会没完没了
- 100 = 无上限（想闹就闹、想缠就缠）

只做**状态 + 闸门**，不改情绪五维，也不写奖罚事件；具体怎么说由语言层决定。
"""

from __future__ import annotations

from datetime import datetime

from . import settings
from .entity import now_iso

FILE = "relationship.json"

DEFAULTS = {
    "intimacy": 50.0,          # 亲密度 0–100
    "demand_level": 65,        # 索取度 0–100（默认 65）
    "milestones": [],          # [{"at": ..., "what": ...}]
    "conflict_count": 0,       # 累计别扭次数
    "last_repair_at": "",
    "updated_at": "",
}

# 不同"索取动作"需要的门槛：越黏人要求越高
GATES = {
    "ask": 20,        # 问一句"今天陪我吗"
    "cling": 45,      # 连着撒娇、要抱
    "demand": 70,     # 直接要求（今天必须陪我）
    "cold_war": 85,   # 赌气冷战
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        raw = settings.read_state(FILE, {})
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        data.update({k: v for k, v in raw.items() if k in DEFAULTS})
    data["demand_level"] = int(clamp(data.get("demand_level", 65)))
    data["intimacy"] = float(max(0.0, min(100.0, float(data.get("intimacy", 50)))))
    return data


def clamp(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return 65


def save(**changes) -> dict:
    data = load()
    data.update({k: v for k, v in changes.items() if k in DEFAULTS})
    data["demand_level"] = clamp(data["demand_level"])
    data["intimacy"] = float(max(0.0, min(100.0, float(data["intimacy"]))))
    data["updated_at"] = now_iso()
    try:
        settings.write_state(FILE, data)
    except Exception:
        pass
    return data


def demand_level() -> int:
    return load()["demand_level"]


def set_demand(level) -> dict:
    return save(demand_level=clamp(level))


def demand_gate(kind: str) -> bool:
    """这个动作现在允不允许做。0 档一律不许。"""
    level = demand_level()
    if level <= 0:
        return False
    return level >= GATES.get(kind, 50)


def intimacy() -> float:
    return load()["intimacy"]


def add_intimacy(delta: float, reason: str = "") -> dict:
    data = load()
    value = float(max(0.0, min(100.0, data["intimacy"] + float(delta))))
    milestones = list(data.get("milestones") or [])
    if reason and abs(delta) >= 2:
        milestones.append({"at": now_iso(), "what": reason, "delta": round(float(delta), 2)})
        milestones = milestones[-50:]
    return save(intimacy=value, milestones=milestones)


def bump_conflict(reason: str = "") -> dict:
    data = load()
    milestones = list(data.get("milestones") or [])
    milestones.append({"at": now_iso(), "what": reason or "闹别扭", "delta": -1})
    return save(conflict_count=int(data.get("conflict_count", 0)) + 1, milestones=milestones[-50:])


def repair() -> dict:
    return save(last_repair_at=now_iso())


def status() -> dict:
    data = load()
    return {
        "intimacy": round(data["intimacy"], 1),
        "demand_level": data["demand_level"],
        "gates": {k: (data["demand_level"] > 0 and data["demand_level"] >= v) for k, v in GATES.items()},
        "conflict_count": data["conflict_count"],
        "milestones": (data.get("milestones") or [])[-8:],
        "tone": _tone(data["demand_level"]),
    }


def _tone(level: int) -> str:
    if level <= 0:
        return "不索取：只回应，不主动要任何东西"
    if level < 25:
        return "很克制：最多问一句，不会缠"
    if level < 55:
        return "正常：会撒娇，被拒绝会收"
    if level < 80:
        return "偏黏：会直接要陪，会小赌气"
    return "很黏：想闹就闹，要陪要到底"
