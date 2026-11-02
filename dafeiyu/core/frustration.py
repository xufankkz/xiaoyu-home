# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v12 2.5 受挫与冷战：攒着的是委屈，散掉的是时间。

- 连续被拒/被忽视 → frustration 累积（0-1），按小时衰减，示好会恢复；
- >= 0.6 进入「闹别扭」：话少、语气淡、不主动索取（除非索取度很高）；
- 和好（示好/道歉/被认真回应）→ 快速回落，并记一次里程碑。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import relationship, settings
from .entity import now_iso
from .text import parse_iso

FILE = "frustration.json"
COLD_WAR_AT = 0.6


def _state() -> dict:
    raw = settings.read_state(FILE, {})
    raw.setdefault("value", 0.0)
    raw.setdefault("last_at", now_iso())
    raw.setdefault("cold_war", False)
    raw.setdefault("since", "")
    raw.setdefault("events", [])
    return raw


def _save(data: dict) -> dict:
    settings.write_state(FILE, data)
    return data


def current() -> float:
    data = _state()
    return float(data.get("value") or 0)


def decay(now: datetime | None = None) -> dict:
    """每小时衰减 0.08（被冷落一晚也不会一直满格）。"""
    now = now or datetime.now()
    data = _state()
    last = parse_iso(data.get("last_at") or "") or now
    hours = max(0.0, (now - last).total_seconds() / 3600.0)
    value = max(0.0, float(data.get("value") or 0) - 0.08 * hours)
    data["value"] = round(value, 4)
    data["last_at"] = now.isoformat(timespec="seconds")
    data["cold_war"] = value >= COLD_WAR_AT
    if not data["cold_war"]:
        data["since"] = ""
    return _save(data)


def bump(amount: float = 0.18, reason: str = "", now: datetime | None = None) -> dict:
    now = now or datetime.now()
    data = decay(now)
    data["value"] = round(min(1.0, float(data.get("value") or 0) + abs(float(amount))), 4)
    data["events"] = (list(data.get("events") or []) + [{"at": now.isoformat(timespec="seconds"),
                                                         "what": reason}])[-30:]
    was = bool(data.get("cold_war"))
    data["cold_war"] = data["value"] >= COLD_WAR_AT
    if data["cold_war"] and not was:
        data["since"] = now.isoformat(timespec="seconds")
        try:
            relationship.bump_conflict(reason or "闹别扭了")
        except Exception:
            pass
    return _save(data)


def soothe(amount: float = 0.35, reason: str = "", now: datetime | None = None) -> dict:
    now = now or datetime.now()
    data = decay(now)
    data["value"] = round(max(0.0, float(data.get("value") or 0) - abs(float(amount))), 4)
    data["cold_war"] = data["value"] >= COLD_WAR_AT
    if not data["cold_war"]:
        data["since"] = ""
    data["events"] = (list(data.get("events") or []) + [{"at": now.isoformat(timespec="seconds"),
                                                         "what": reason or "和好"}])[-30:]
    try:
        relationship.repair()
    except Exception:
        pass
    return _save(data)


def is_cold_war(now: datetime | None = None) -> bool:
    data = decay(now)
    return bool(data.get("cold_war"))


def hint(now: datetime | None = None) -> str:
    """给提示词用：现在是不是在闹别扭。"""
    value = current()
    if value >= COLD_WAR_AT:
        return ("现在你在闹别扭：话少、语气淡，不用感叹号，不要主动索取，"
                "他要是认真哄你，就慢慢软下来。")
    if value >= 0.35:
        return "心里有点堵，语气比平时淡一点，别刻意热情。"
    return ""


def status(now: datetime | None = None) -> dict:
    data = _state()
    return {"value": round(float(data.get("value") or 0), 3),
            "cold_war": bool(data.get("cold_war")),
            "since": data.get("since", ""),
            "recent": (data.get("events") or [])[-6:]}
