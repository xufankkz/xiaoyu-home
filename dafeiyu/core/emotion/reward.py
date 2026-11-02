# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""奖罚 → 情绪。RewardEvent 的产生、RPE 递减、显著性回填、审计。

规格 4.5 / 5.6 / 6.7：
- delta 可正可负；
- 同一触发重复出现时按 rpe_weight 递减（模拟多巴胺适应性）；
- 事件写 reward_log，可审计；同时作为记忆 salience 的调制源。
"""

from __future__ import annotations

import json
from datetime import datetime

from .. import db, settings
from ..entity import DIMS, RewardEvent, loads, now_iso
from ..repo import emotion as er, system as sr
from . import dynamics, vector

CN2DIM = {"joy": "joy", "happiness": "happiness", "sadness": "sadness",
          "inferiority": "inferiority", "shyness": "shyness"}


def cfg_for(trigger: str) -> dict | None:
    return (settings.emotion_cfg().get("deltas") or {}).get(trigger)


def rpe_for(trigger: str) -> float:
    cfg = settings.emotion_cfg().get("rpe", {})
    count = er.trigger_count(trigger, int(cfg.get("window_min", 120)))
    return dynamics.rpe_weight(count, float(cfg.get("decay", 0.85)), float(cfg.get("floor", 0.2)))


def apply_trigger(trigger: str, source_text: str = "", *, weight: float | None = None,
                  reason: str = "") -> RewardEvent | None:
    """产生一条奖罚事件并立刻作用到情绪向量上。"""
    cfg = cfg_for(trigger)
    if not cfg:
        return None
    delta = {k: float(v) for k, v in cfg.items() if k in DIMS}
    if not delta:
        return None
    rpe = rpe_for(trigger) if weight is None else float(weight)
    return _emit(trigger, str(cfg.get("type") or "reward"), delta, source_text, rpe, reason)


def apply_custom(trigger: str, delta: dict, *, ev_type: str = "punishment",
                 source_text: str = "", reason: str = "") -> RewardEvent | None:
    """自定义 delta 的事件（尺度检测这类不走配置表的通道）。"""
    clean = {k: float(v) for k, v in (delta or {}).items() if k in DIMS and float(v) != 0.0}
    if not clean:
        return None
    return _emit(trigger, ev_type, clean, source_text, 1.0, reason)


def _emit(trigger: str, ev_type: str, delta: dict, source_text: str, rpe: float,
          reason: str) -> RewardEvent:
    event = RewardEvent(
        type=ev_type,
        trigger=trigger,
        source_text=(source_text or "")[:500],
        delta=delta,
        rpe_weight=rpe,
        t=now_iso(),
    )
    er.reward_insert(event)
    vector.apply(delta, rpe)
    _append_log(event, reason)
    _boost_salience(event)
    sr.audit_add("reward", event.id, "created",
                 {"trigger": trigger, "type": event.type, "delta": delta, "rpe": rpe})
    return event


def _append_log(event: RewardEvent, reason: str) -> None:
    path = settings.STATE / "reward_log" / f"{datetime.now():%Y-%m-%d}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": event.id, "t": event.t, "type": event.type, "trigger": event.trigger,
        "delta": event.delta, "rpe_weight": event.rpe_weight,
        "source_text": event.source_text, "reason": reason,
    }
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _boost_salience(event: RewardEvent) -> int:
    """强奖罚事件附近编码的记忆 salience 提升（杏仁核式标记）。"""
    cfg = settings.emotion_cfg().get("salience_boost", {})
    minutes = int(cfg.get("window_min", 5))
    gain = float(cfg.get("reward" if event.type == "reward" else "punishment", 0.3))
    try:
        with db.write() as conn:
            cur = conn.execute(
                "UPDATE memory_item SET salience = MIN(1.0, salience + ?) "
                "WHERE tombstone=0 AND t_created >= datetime('now','localtime', ?)",
                (gain, f"-{minutes} minutes"),
            )
            return int(cur.rowcount or 0)
    except Exception:
        return 0


def stats() -> dict:
    """给桥接契约 状态.json 的 stats（保持旧字段名）。"""
    def n(where: str, args=()) -> int:
        return int(db.scalar(f"SELECT COUNT(*) FROM reward_event WHERE {where}", args, 0) or 0)

    return {
        "rewards": n("type='reward'"),
        "punishments": n("type='punishment'"),
        "shame": n("trigger='scale'"),
        "ignored": n("trigger='ignored'"),
        "care": n("trigger='cared'"),
    }


def last_ignore_ms() -> int:
    row = db.q1("SELECT t FROM reward_event WHERE trigger='ignored' ORDER BY t DESC LIMIT 1")
    if not row:
        return 0
    try:
        return int(datetime.fromisoformat(row["t"]).timestamp() * 1000)
    except Exception:
        return 0


def recent(limit: int = 20) -> list[dict]:
    out = []
    for ev in er.reward_recent(limit):
        out.append({
            "id": ev.id, "t": ev.t, "type": ev.type, "trigger": ev.trigger,
            "delta": ev.delta, "rpe_weight": ev.rpe_weight, "source_text": ev.source_text,
        })
    return out
