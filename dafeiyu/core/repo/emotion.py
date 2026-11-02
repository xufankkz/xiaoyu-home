# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""奖罚事件与情绪快照。"""

from __future__ import annotations

from typing import Any

from .. import db
from ..entity import DIMS, RewardEvent, dumps

_COLS = "id, type, trigger, source_text, delta, rpe_weight, t"


def reward_insert(ev: RewardEvent) -> RewardEvent:
    row = ev.to_row()
    keys = [k for k in row if k != "id"]
    db.ex(
        f"INSERT INTO reward_event(id, {', '.join(keys)}) VALUES(?{', ?' * len(keys)})",
        [row["id"]] + [row[k] for k in keys],
    )
    return ev


def reward_recent(limit: int = 20) -> list[RewardEvent]:
    rows = db.q(f"SELECT {_COLS} FROM reward_event ORDER BY t DESC LIMIT ?", (int(limit),))
    return [RewardEvent.from_row(r) for r in rows]


def trigger_count(trigger: str, minutes: int = 120) -> int:
    """近 N 分钟内同一触发出现几次（算 RPE 递减用）。"""
    return int(db.scalar(
        "SELECT COUNT(*) FROM reward_event WHERE trigger=? AND t >= datetime('now','localtime', ?)",
        (trigger, f"-{int(minutes)} minutes"), 0) or 0)


def reward_by_day(days: int = 7) -> list[dict]:
    rows = db.q(
        "SELECT substr(t,1,10) AS day, type, COUNT(*) AS n FROM reward_event "
        "WHERE t >= datetime('now','localtime', ?) GROUP BY day, type ORDER BY day",
        (f"-{int(days)} days",),
    )
    return [dict(r) for r in rows]


# ── 情绪快照 ────────────────────────────────────────────────────────────────
def snapshot_add(values: dict, dominant: str, reason: str = "") -> None:
    cols = ", ".join(DIMS)
    marks = ", ".join("?" * len(DIMS))
    db.ex(
        f"INSERT INTO emotion_snapshot(t, {cols}, dominant, reason) VALUES(datetime('now','localtime'), {marks}, ?, ?)",
        [float(values.get(d, 0.0)) for d in DIMS] + [dominant, reason],
    )


def snapshot_last() -> dict | None:
    row = db.q1(
        f"SELECT t, {', '.join(DIMS)}, dominant, reason FROM emotion_snapshot ORDER BY id DESC LIMIT 1"
    )
    return dict(row) if row else None


def snapshot_daily(days: int = 30) -> list[dict]:
    """按天聚合，给情绪历史曲线。"""
    cols = ", ".join(f"ROUND(AVG({d}),1) AS {d}" for d in DIMS)
    rows = db.q(
        f"SELECT substr(t,1,10) AS day, {cols}, COUNT(*) AS n FROM emotion_snapshot "
        f"WHERE t >= datetime('now','localtime', ?) GROUP BY day ORDER BY day",
        (f"-{int(days)} days",),
    )
    return [dict(r) for r in rows]


def snapshot_recent(limit: int = 200) -> list[dict]:
    rows = db.q(
        f"SELECT t, {', '.join(DIMS)}, dominant, reason FROM emotion_snapshot ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    return [dict(r) for r in reversed(rows)]
