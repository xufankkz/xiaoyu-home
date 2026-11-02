"""Daily ranges for fictional character UI values; no scientific inference."""
from __future__ import annotations

from datetime import date, datetime
import threading
from zoneinfo import ZoneInfo

from .. import db, homeostasis, hormones, settings
from ..entity import CN, DIMS
from . import vector

FILE = "emotion-daily.json"
ZONE = ZoneInfo("Asia/Shanghai")
_lock = threading.RLock()
METRICS = [("emotion."+key, CN[key], "情绪") for key in DIMS] + [
    ("needs.energy", "能量", "日常状态"),
    ("needs.social_connection", "社交连接", "日常状态"),
    ("needs.cognitive_novelty", "新鲜感", "日常状态"),
    ("parameters.dopamine", "多巴胺", "角色参数"),
    ("parameters.cortisol", "皮质醇", "角色参数"),
    ("parameters.oxytocin", "催产素", "角色参数"),
    ("parameters.serotonin", "血清素", "角色参数"),
    ("personality.extraversion", "外向", "动态性格"),
    ("personality.neuroticism", "敏感", "动态性格"),
    ("personality.agreeableness", "亲和", "动态性格"),
]


def _stamp(value: str) -> str:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZONE)
    return parsed.astimezone(ZONE).isoformat(timespec="seconds")


def values_now(vec=None, needs=None, parameters=None) -> dict:
    vec = vec or vector.read_snapshot()
    needs = needs if needs is not None else homeostasis.status()
    parameters = parameters if parameters is not None else hormones.status()
    values = {"emotion."+d: vec.get(d) for d in DIMS}
    values.update({"needs."+k: float(v)*100 for k, v in needs.get("values", {}).items()})
    values.update({"parameters."+k: float(v["value"])*100
                   for k, v in parameters.get("hormones", {}).items()})
    values.update({"personality."+k: float(v)*100 for k, v in parameters.get("personality", {}).items()})
    return {key: round(values[key], 3) for key, _, _ in METRICS if key in values}


def record(*, now: datetime | None = None, values: dict | None = None) -> None:
    """Called by the existing core heartbeat/ingestion, never by a GET request."""
    now = now or datetime.now(ZONE)
    stamp = _stamp(now.isoformat())
    day = stamp[:10]
    values = values if values is not None else values_now()
    with _lock:
        data = settings.read_state(FILE, {}) or {}
        data.setdefault("started_at", stamp)
        metrics = data.setdefault("days", {}).setdefault(day, {})
        for key, _, _ in METRICS:
            if key not in values:
                continue
            value = round(float(values[key]), 3)
            row = metrics.get(key)
            if row is None:
                metrics[key] = {"open": value, "close": value, "low": value, "high": value,
                                "first_at": stamp, "last_at": stamp, "samples": 1}
                continue
            row["low"], row["high"] = min(row["low"], value), max(row["high"], value)
            if stamp < row["first_at"]:
                row["open"], row["first_at"] = value, stamp
            if stamp >= row["last_at"]:
                row["close"], row["last_at"] = value, stamp
            row["samples"] += 1
        settings.write_state(FILE, data)


def view(day: str = "") -> dict:
    day = day or datetime.now(ZONE).date().isoformat()
    date.fromisoformat(day)
    with _lock:
        data = settings.read_state(FILE, {}) or {}
    metrics = {k: dict(v) for k, v in (data.get("days", {}).get(day) or {}).items()}
    # Original snapshots remain untouched and also supply days before this feature.
    history = db.q("SELECT t, joy, happiness, sadness, inferiority, shyness FROM emotion_snapshot "
                   "WHERE substr(t,1,10)=? ORDER BY t,id", (day,))
    for raw in history:
        stamp = _stamp(raw["t"])
        for dim in DIMS:
            key, value = "emotion."+dim, float(raw[dim] or 0)
            row = metrics.get(key)
            if row is None:
                metrics[key] = {"open": value, "close": value, "low": value, "high": value,
                                "first_at": stamp, "last_at": stamp, "samples": 1}
                continue
            row["low"], row["high"] = min(row["low"], value), max(row["high"], value)
            if stamp < row["first_at"]:
                row["open"], row["first_at"] = value, stamp
            if stamp >= row["last_at"]:
                row["close"], row["last_at"] = value, stamp
            row["samples"] += 1
    available = set(data.get("days", {}))
    available.update(r["day"] for r in db.q("SELECT DISTINCT substr(t,1,10) AS day FROM emotion_snapshot") if r["day"])
    items = []
    for key, label, group in METRICS:
        raw = metrics.get(key)
        item = {"key": key, "label": label, "group": group, "available": raw is not None}
        if raw:
            item.update({k: round(float(raw[k]), 2) for k in ("open", "close", "low", "high")})
            item.update(delta=round(raw["close"]-raw["open"], 2),
                        swing=round(raw["high"]-raw["low"], 2),
                        first_at=raw["first_at"], last_at=raw["last_at"], samples=raw["samples"])
        items.append(item)
    return {"date": day, "today": datetime.now(ZONE).date().isoformat(), "items": items,
            "available_dates": sorted(available), "started_at": data.get("started_at", ""),
            "basis": "当日首条至末条的净变化；波动范围为已记录的最高值减最低值。各项统一为 0–100 点。",
            "sampling_note": "按互动及约每分钟记录；范围反映已记录样本，不保证覆盖两次记录之间的瞬时峰值。"}
