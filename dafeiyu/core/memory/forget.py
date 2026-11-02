# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""遗忘：连续衰减 + 分级压缩（不是离散时间桶）。

strength(t) = s0 · exp(−Δt/τ)，τ 随 salience 与 status=open 增大；
阈值触发 level 0→1→2→3，level=3 前先蒸馏。

只对「会忘」的层衰减：instant / short / thought。
long1 / long2 / language / emotion 是沉淀层，不参与衰减（规格 5.1：2 号平时不读但不消失）。
"""

from __future__ import annotations

import math
from datetime import datetime

from .. import settings
from ..entity import MemoryItem, ThoughtNode
from ..repo import memory as mr
from ..repo import thought as tr
from ..text import clip, parse_iso, skeleton_of
from . import distill

DECAYING_LAYERS = ("instant", "short")
TAU_KIND = {"instant": "instant", "short": "short"}


def tau_hours(kind: str, salience: float, status: str, level: int) -> float:
    cfg = settings.thr().get("forget", {})
    base = float((cfg.get("tau_base_hours") or {}).get(kind, 72))
    sal_gain = float(cfg.get("salience_gain", 1.2))
    open_mult = float(cfg.get("open_multiplier", 2.0))
    level_mult = float(cfg.get("level_multiplier", 1.1))
    tau = base * (1.0 + sal_gain * max(0.0, salience))
    if status == "open":
        tau *= open_mult
    if level > 0:
        tau *= level_mult
    return max(0.5, tau)


def thresholds() -> dict:
    return settings.thr().get("forget", {}).get("thresholds", {"level1": 0.6, "level2": 0.35, "level3": 0.15})


def next_level(strength: float, level: int) -> int:
    th = thresholds()
    if strength < float(th.get("level3", 0.15)):
        return 3
    if strength < float(th.get("level2", 0.35)):
        return max(level, 2)
    if strength < float(th.get("level1", 0.6)):
        return max(level, 1)
    return level


def decayed_strength(item: MemoryItem, now: datetime) -> tuple[float, str]:
    """增量衰减：Δ 从上次衰减点算，避免每次从头累积误差。"""
    last = parse_iso((item.ext or {}).get("decay_at") or "") or parse_iso(item.t_created) or now
    dt_h = max(0.0, (now - last).total_seconds() / 3600.0)
    if dt_h <= 0.01:
        return item.strength, last.isoformat(timespec="seconds")
    kind = TAU_KIND.get(item.layer, "thought")
    tau = tau_hours(kind, item.salience, item.status, item.level)
    strength = item.strength * math.exp(-dt_h / tau)
    return round(max(0.0, min(1.0, strength)), 5), now.isoformat(timespec="seconds")


def decay_pass(now: datetime | None = None, dry_run: bool = False) -> dict:
    now = now or datetime.now()
    stats = {"scanned": 0, "level_down": 0, "distilled": 0, "thoughts": 0}
    for item in mr.iter_alive():
        if item.ext.get('editorial', {}).get('pinned') or item.ext.get('editorial', {}).get('retained_original'):
            continue
        if item.layer not in DECAYING_LAYERS:
            continue
        stats["scanned"] += 1
        strength, decay_at = decayed_strength(item, now)
        level = next_level(strength, item.level)
        if level >= 3 and not item.tombstone:
            if not dry_run:
                distill.distill_item(item, reason="strength<level3")
            stats["distilled"] += 1
            continue
        fields = {"strength": strength, "ext": {**(item.ext or {}), "decay_at": decay_at}}
        if level != item.level:
            fields["level"] = level
            if level >= 1 and not item.summary:
                fields["summary"] = clip(item.content, 80)
            if level >= 2 and not item.skeleton:
                fields["skeleton"] = skeleton_of(item.summary or item.content)
            stats["level_down"] += 1
        if not dry_run:
            mr.update_fields(item.id, **fields)

    for node in tr.alive(4000):
        stats["thoughts"] += 1
        last = parse_iso(node.last_access or "") or parse_iso(node.t_created) or now
        dt_h = max(0.0, (now - last).total_seconds() / 3600.0)
        if dt_h <= 0.01:
            continue
        tau = tau_hours("thought", node.salience, node.status, node.level)
        strength = round(max(0.0, node.strength * math.exp(-dt_h / tau)), 5)
        level = next_level(strength, node.level)
        if level >= 3 and not node.tombstone:
            if not dry_run:
                distill.distill_thought(node, reason="strength<level3")
            stats["distilled"] += 1
            continue
        if not dry_run:
            tr.update_fields(node.id, strength=strength, level=level,
                             last_access=now.isoformat(timespec="seconds"))
    return stats


def boost(item_id: str, amount: float = 0.15) -> None:
    item = mr.get(item_id)
    if item is None:
        return
    mr.update_fields(item_id, strength=min(1.0, item.strength + amount))
