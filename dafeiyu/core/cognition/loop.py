# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""认知循环（规格 6.5）：唯一业务入口，11 步按顺序落地。

调度器只负责叫醒 tick，不许任何模块自己起线程/定时器。
"""

from __future__ import annotations

import time
from datetime import datetime

from .. import settings
from ..emotion import policy as policy_mod, reward as reward_mod, vector
from ..memory import forget, instant, retrieval, shortterm, working
from ..repo import learning as lr
from ..repo import memory as mr
from ..repo import system as sr
from ..repo import thought as tr
from ..text import clip
from ..thought import chain as thought_chain
from . import arbiter, chitchat, consolidate, curiosity, dialogue, metacognition

_ACTIVITY = {"kind": "idle", "at": "", "note": ""}


def _log(step: str, payload: dict) -> None:
    sr.audit_add("tick", step, "step", payload)


def tick(ctx: dict | None = None) -> dict:
    """一次认知周期。ctx 可带 dialogue_pending / dialogue_text / task_pending / idle。"""
    from ..entity import TickResult

    ctx = dict(ctx or {})
    now = datetime.now()
    result = TickResult(at=now.isoformat(timespec="seconds"))
    steps: list[str] = []

    # 1) 采集上下文
    try:
        recent = sr.dialogue_recent(3)
        ctx.setdefault("idle", not bool(ctx.get("dialogue_pending")))
        steps.append("sense")
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"sense 失败：{exc}")
        recent = []

    # 2) 工作记忆
    try:
        for msg in recent:
            working.bind("percept", msg["id"], weight=0.6)
        for node in tr.open_nodes(3):
            working.bind("thought", node.id, weight=0.8)
        steps.append("working_memory")
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"工作记忆失败：{exc}")

    # 3) 情绪回归
    try:
        emo = vector.tick(now)
        vec = vector.current()
        result.emotion = {k: round(v, 1) for k, v in emo["values"].items()}
        steps.append("emotion")
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"情绪失败：{exc}")
        vec = vector.current()

    # 4) 缺口检测 + 五信号好奇
    try:
        made = curiosity.assess(text=ctx.get("dialogue_text", ""), vec=vec, source="autonomous")
        made += metacognition.intents_from_gaps(vec)
        result.new_intents = len(made)
        steps.append("curiosity")
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"好奇心失败：{exc}")

    # 5) 可学习性过滤
    try:
        queue = [i for i in lr.intent_queue(limit=10) if curiosity.learnability(i) >= 0.25]
    except Exception:
        queue = []

    # 6) 仲裁
    ch_state = chitchat.load()
    ch_gate = chitchat.gate(vec, ch_state)
    ch_due = chitchat.due(ch_state, now) and ch_gate.get("allowed", False)
    cands = arbiter.gather({
        "dialogue_pending": bool(ctx.get("dialogue_pending")),
        "dialogue_id": ctx.get("dialogue_id", ""),
        "task_pending": bool(ctx.get("task_pending")),
        "task_id": ctx.get("task_id", ""),
        "intents": queue,
        "open_thoughts": [{"id": n.id, "summary": n.summary} for n in tr.open_nodes(5)],
        "chitchat_due": ch_due,
        "chitchat_value": 0.7 if ch_state.fatigue_level == 0 else 0.45,
        "chitchat_fatigue": min(0.8, 0.2 * ch_state.fatigue_level),
        "consolidate_due": now.hour == 3 and now.minute < 15,
    }, vec)
    decision = arbiter.pick(cands, current=_ACTIVITY["kind"])
    winner = decision.get("winner") or {"kind": "consolidate", "score": 0}
    steps.append("arbiter")

    activity = winner["kind"]
    # 7) 思考 / 对话
    if activity in ("dialogue", "open_thought", "task") and ctx.get("dialogue_text"):
        try:
            out = dialogue.handle_owner_message(ctx["dialogue_text"],
                                               dialogue_id=ctx.get("dialogue_id", ""))
            result.notes.append(f"对话：{clip(out.get('reply', ''), 40)}")
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"对话失败：{exc}")
    elif activity == "learn" and queue:
        try:
            from ..learning import executor

            out = executor.execute(queue[0], allow_web=True)
            result.learned = 1 if out.get("ok") else 0
            result.notes.append(f"学习：{out.get('outcome')} {queue[0].target}")
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"学习失败：{exc}")
    elif activity == "chitchat" and ch_due:
        try:
            out = chitchat.run(vec=vec)
            result.chitchat = out.get("text", "")
            result.notes.append(f"碎碎念{'成功' if out.get('ok') else '未送达'}")
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"碎碎念失败：{exc}")
    elif activity == "consolidate" or (ctx.get("idle") and winner.get("score", 0) < 0.6):
        try:
            stats = consolidate.periodic(now)
            result.decayed = stats["decay"].get("scanned", 0)
            result.distilled = stats["decay"].get("distilled", 0)
            activity = "consolidate"
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"巩固失败：{exc}")

    # 9.5) 碎碎念疲劳检查（被忽视的惩罚在这里联动）
    try:
        fatigue = chitchat.no_reply_tick(now, vec)
        if fatigue.get("punished"):
            result.notes.append("被晾太久，记了一次「被忽视」")
    except Exception:
        pass

    result.activity = activity
    result.at = now.isoformat(timespec="seconds")
    _ACTIVITY.update({"kind": activity, "at": result.at,
                      "note": clip("；".join(result.notes), 120)})
    sr.audit_add("tick", f"tick-{now:%Y%m%d%H%M%S}", "done",
                 {"activity": activity, "steps": steps, "emotion": result.emotion,
                  "notes": result.notes, "arbiter": decision.get("scored", [])[:3]})
    return result.__dict__


def status() -> dict:
    return dict(_ACTIVITY)


def run_once(ctx: dict | None = None) -> dict:
    start = time.time()
    out = tick(ctx)
    out["elapsed_ms"] = int((time.time() - start) * 1000)
    return out
