# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""EmotionVector 的全局单例：加载、回归、保护窗口、落盘。

规格红线：情绪向量是**全局调制变量**，被记忆、检索、语言、好奇心、碎碎念实时读取，
所以这里只允许有一个实例，任何模块都通过 current() 拿。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from .. import settings
from ..entity import CN, DIMS, EmotionVector, now_iso
from . import dynamics

_lock = threading.RLock()
_vec: EmotionVector | None = None
_protect: dict[str, str] = {}          # dim -> 保护到什么时候（ISO）
_last_tick: str = ""


# ── 加载 / 保存 ─────────────────────────────────────────────────────────────
def load() -> EmotionVector:
    global _vec, _protect
    with _lock:
        if _vec is not None:
            return _vec
        raw = settings.read_state("emotion.json", {})
        cfg = settings.emotion_cfg()
        if raw and (raw.get("positive") or raw.get("negative") or raw.get("other")):
            float_values = {}
            for group in ("positive", "negative", "other"):
                for k, v in (raw.get(group) or {}).items():
                    if k in DIMS:
                        float_values[k] = float(v)
            for d in DIMS:
                float_values.setdefault(d, float(cfg.get("initial", {}).get(d, 0)))
            _vec = EmotionVector(
                values=float_values,
                baseline={d: float((raw.get("baseline") or {}).get(d, cfg.get("baseline", {}).get(d, 5))) for d in DIMS},
                dominant=str(raw.get("dominant") or ""),
                updated_at=(_parse(raw.get("updated_at")) or datetime.now()).isoformat(timespec="seconds"),
            )
            _vec.recompute_dominant()
        else:
            _vec = EmotionVector.from_config(cfg)
        _protect = {k: v for k, v in (raw.get("_protect") or {}).items() if k in DIMS}
        return _vec


def current() -> EmotionVector:
    return load()


def read_snapshot() -> EmotionVector:
    """Copy the live vector without advancing its clock or writing its state."""
    with _lock:
        vec = load()
        return EmotionVector(values=dict(vec.values), baseline=dict(vec.baseline),
                             dominant=vec.dominant, updated_at=vec.updated_at)


def save(vec: EmotionVector | None = None, write_json: bool = True) -> EmotionVector:
    global _vec
    with _lock:
        vec = vec or _vec
        if vec is None:
            return load()
        _vec = vec
        if write_json:
            data = vec.to_json()
            data["_protect"] = dict(_protect)
            settings.write_state("emotion.json", data)
        return vec


def reload() -> EmotionVector:
    global _vec, _protect
    with _lock:
        _vec = None
        _protect = {}
        return load()


def reset(vec: EmotionVector) -> EmotionVector:
    global _protect
    _protect = {}
    return save(vec)


# ── 阈值保护窗口 ────────────────────────────────────────────────────────────
def _protect_window_min() -> float:
    return float(settings.emotion_cfg().get("protect", {}).get("window_min", 10))


def protected_dims(at: datetime | None = None) -> set[str]:
    at = at or datetime.now()
    out = set()
    for dim, until in list(_protect.items()):
        dt = _parse(until)
        if dt and dt > at:
            out.add(dim)
        else:
            _protect.pop(dim, None)
    return out


def protect(dims: list[str], at: datetime | None = None) -> None:
    """越阈后给一个可感知窗口：这段时间内不做自然回归，只允许奖罚直接改写。"""
    if not dims:
        return
    at = at or datetime.now()
    until = (at + timedelta(minutes=_protect_window_min())).isoformat(timespec="seconds")
    for dim in dims:
        if dim in DIMS:
            _protect[dim] = until


def _parse(text: str) -> datetime | None:
    from ..text import parse_iso

    return parse_iso(text)


# ── 应用奖罚 ────────────────────────────────────────────────────────────────
def apply(delta: dict, weight: float = 1.0) -> dict:
    """改情绪 + 越阈保护。返回实际生效的增量。"""
    with _lock:
        vec = load()
        before = {d: vec.get(d) for d in DIMS}
        applied = vec.apply(delta, weight)
        thresholds = settings.emotion_cfg().get("protect", {}).get("thresholds", {})
        newly = [d for d in dynamics.crossed(vec.values, thresholds)
                 if before[d] < float(thresholds.get(d, 999))]
        if newly:
            protect(newly)
        save(vec)
        return applied


# ── 自然回归 ────────────────────────────────────────────────────────────────
def tick(now: datetime | None = None, force_save: bool = False) -> dict:
    """一次情绪回归。返回 {'elapsed_min':..,'values':..,'dominant':..,'protected':[...]}"""
    global _last_tick
    with _lock:
        vec = load()
        now = now or datetime.now()
        last = _parse(vec.updated_at) or now
        elapsed_min = max(0.0, (now - last).total_seconds() / 60.0)
        cfg = settings.emotion_cfg()
        if elapsed_min < 0.05:
            return {"elapsed_min": 0.0, "values": dict(vec.values), "dominant": vec.dominant,
                    "protected": sorted(protected_dims(now))}
        protected = protected_dims(now)
        new_values = dynamics.step(vec.values, vec.baseline, cfg.get("half_life_min", {}),
                                   elapsed_min, protected)
        vec.values = new_values
        vec.recompute_dominant()
        vec.updated_at = now.isoformat(timespec="seconds")
        _last_tick = vec.updated_at
        save(vec, write_json=force_save or True)
        return {"elapsed_min": round(elapsed_min, 2), "values": dict(vec.values),
                "dominant": vec.dominant, "protected": sorted(protected)}


def status(vec: EmotionVector | None = None) -> dict:
    vec = vec or read_snapshot()
    thr = settings.emotion_cfg().get("behavior", {})
    pos, neg = vec.positive(), vec.negative()
    return {
        "values": {d: round(vec.get(d), 1) for d in DIMS},
        "cn": {d: vec.get(d) for d in DIMS},
        "baseline": {d: round(vec.baseline.get(d, 0), 1) for d in DIMS},
        "deviations": {d: round(vec.get(d) - vec.baseline.get(d, 0), 2) for d in DIMS},
        "dominant_basis": "highest_current_intensity",
        "dominant": vec.dominant,
        "dominant_cn": vec.dominant_cn(),
        "positive": round(pos, 1),
        "negative": round(neg, 1),
        "updated_at": vec.updated_at,
        "protected": sorted(protected_dims()),
        "behavior": {
            "positive_high": pos >= float(thr.get("positive_threshold", 60)),
            "negative_high": neg >= float(thr.get("negative_threshold", 60)),
            "shy_high": vec.get("shyness") >= float(thr.get("shy_threshold", 70)),
        },
    }


def snapshot(reason: str = "tick") -> None:
    from ..repo import emotion as er

    vec = load()
    er.snapshot_add(vec.values, vec.dominant, reason)


def set_values(values: dict, reason: str = "manual") -> EmotionVector:
    """手动改（界面/演示用）。"""
    with _lock:
        vec = load()
        for dim, value in (values or {}).items():
            if dim in DIMS:
                vec.set(dim, value)
        vec.recompute_dominant()
        vec.touch()
        save(vec)
        snapshot(reason)
        return vec


# ── 与桥接对齐（emotion_mode = local 时）──────────────────────────────────
def sync_from_bridge() -> dict:
    """桥接还在自己算情绪时，把它的 状态.json 读过来对齐数值。

    这样桌面面板显示的就是 QQ 那边真实的情绪，不会出现「两个心情」。
    桥接文件比我们新才覆盖；本地刚发生的奖罚事件不会被抹掉。
    """
    from ..text import parse_iso

    if settings.app().get("bridge", {}).get("emotion_mode") == "core":
        return {"skipped": "core 模式，情绪由核心自己写"}
    raw = settings.read_json(settings.LEGACY_EMOTION_STATE, {}) or {}
    emotions = raw.get("emotions") or {}
    if not emotions:
        return {"skipped": "桥接状态文件还没有值"}
    bridge_at = parse_iso(raw.get("updatedAt"))
    with _lock:
        vec = load()
        mine = _parse(vec.updated_at)
        if bridge_at and mine and bridge_at <= mine:
            return {"skipped": "核心的更新", "bridge_at": bridge_at.isoformat(timespec="seconds")}
        cn2dim = {v: k for k, v in CN.items()}
        changed = {}
        for cn, value in emotions.items():
            dim = cn2dim.get(cn)
            if not dim:
                continue
            try:
                number = float(value)
            except Exception:
                continue
            if abs(number - vec.get(dim)) >= 0.5:
                changed[dim] = round(number - vec.get(dim), 1)
                vec.set(dim, number)
        setpoints = raw.get("setpoints") or {}
        for cn, value in setpoints.items():
            dim = cn2dim.get(cn)
            if dim:
                try:
                    vec.baseline[dim] = float(value)
                except Exception:
                    pass
        if changed:
            vec.recompute_dominant()
            vec.updated_at = (bridge_at or datetime.now()).isoformat(timespec="seconds")
            save(vec)
        return {"changed": changed, "from": (bridge_at or datetime.now()).isoformat(timespec="seconds")}
