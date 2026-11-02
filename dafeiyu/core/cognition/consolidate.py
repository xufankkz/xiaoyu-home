# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""巩固调度（规格 6.10）：所有时间规则都在这里落地，各模块不许自己起定时器。

每 10 分钟：强度衰减 + 降级 + 蒸馏 + 情绪回归；
每天凌晨：批量蒸馏高显著/open、3 天升级、150 字压缩、情绪快照归档。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from .. import db, settings
from ..bridge import projection, push
from ..emotion import reward as reward_mod
from ..emotion import vector
from ..memory import forget, instant, shortterm, working
from ..repo import system as sr
from . import curiosity


def periodic(now: datetime | None = None) -> dict:
    """每 10 分钟那次。"""
    now = now or datetime.now()
    # 先把「桥接/旧任务刚写出来的」增量收进 DB，再投影回去（否则会把它们盖掉）
    ingest = {"bridge": 0, "instant": 0, "diary": 0}
    try:
        from ..bridge import legacy_import

        ingest["bridge"] = legacy_import.import_from_bridge()
        ingest["instant"] = legacy_import.import_instant()
        ingest["diary"] = legacy_import.import_diary()
    except Exception:
        pass
    decay = forget.decay_pass(now)
    inst = instant.sweep(now)
    emo = vector.tick(now)
    synced = vector.sync_from_bridge()
    vm = working.decay_tick()
    residual = {"kept": 0, "dropped": 0}
    try:
        from ..thought import residual as residual_mod

        residual = residual_mod.sweep(now, vector.current())
    except Exception:
        pass
    # v14：把桥接账本的增量导进 cost_event（只补没进过的，不重算历史）
    ledger_import = {"engine": 0, "bridge": 0, "skipped": 0}
    try:
        from .. import cost as cost_mod

        ledger_import = cost_mod.import_ledgers()
    except Exception:
        pass
    stats = {"kind": "periodic", "ingest": ingest, "decay": decay, "instant": inst,
             "emotion": {k: round(v, 1) for k, v in emo["values"].items()},
             "emotion_dominant": emo["dominant"], "bridge_sync": synced,
             "residual": residual, "ledger": ledger_import, "working": vm}
    if now.minute < 11:
        vector.snapshot("hourly")
    outbox = push.flush_outbox()
    if outbox.get("sent"):
        stats["outbox"] = outbox
    _project()
    # 花费缓存：桥接会读 state/cost.txt 塞进她的私聊唤醒提示（让她答得出"今天花了多少"）
    try:
        from .. import cost as cost_mod
        cost_mod.write_cache()
    except Exception:
        pass
    sr.consolidation_add(f"periodic-{now:%Y%m%d%H%M}", "periodic", stats)
    return stats


def daily(now: datetime | None = None, light: bool = False) -> dict:
    """凌晨巩固窗口那次：批量蒸馏 + 3 天升级 + 150 字压缩。

    light=True 只做「不花钱」的那半（补日记索引 + 升级 + 衰减），
    压缩留给 03:05 的正规窗口——开机补跑不该一上来就刷一堆模型调用。
    """
    now = now or datetime.now()
    if light:
        shortterm.USE_LLM = False
    try:
        sync = shortterm.sync_diaries()
        promoted = shortterm.promote()
        compressed = shortterm.compress_pending() if not light else {"skipped": "light"}
    finally:
        if light:
            shortterm.USE_LLM = True
    decay = forget.decay_pass(now)
    inst = instant.sweep(now)
    vector.snapshot("daily")
    curiosity.tick_fatigue()
    stats = {
        "kind": "daily", "sync": sync, "promoted": len(promoted),
        "promoted_items": [{"cluster": p["cluster"], "days": p["days"], "emotion": p["emotion"]}
                           for p in promoted],
        "compressed": compressed, "decay": decay, "instant": inst,
    }
    # 视觉截图过期清理（只删图片，摘要与概念留在 vision_asset 里）
    try:
        from ..learning import browse

        stats["vision_cleanup"] = browse.cleanup()
    except Exception:
        pass
    # v14：冷存储只留 18 小时
    try:
        from ..memory import coldstore

        stats["coldstore_purged"] = coldstore.purge()
    except Exception:
        pass
    # v12：节律学习、语言指纹、自我叙事、受挫衰减
    try:
        from .. import circadian, frustration

        stats["circadian"] = circadian.learn_from_history()
        stats["frustration"] = frustration.decay()
    except Exception:
        pass
    try:
        from ..language import fingerprint

        stats["fingerprint"] = fingerprint.learn_from_history()
    except Exception:
        pass
    try:
        from .. import self_narrative

        stats["narrative"] = self_narrative.generate(use_llm=not light)
    except Exception:
        pass
    db.backup(7)
    _project()
    sr.consolidation_add(f"daily-{now:%Y%m%d}", "daily", stats)
    return stats


def _project() -> None:
    try:
        projection.project_all(vector.current())
    except Exception:
        pass


def finish_day(now: datetime | None = None) -> dict:
    """一天结束：把今天该归档的归掉（情绪按天归档 + 快照）。"""
    now = now or datetime.now()
    vector.snapshot("archive")
    return {"snapshot_at": now.isoformat(timespec="seconds"),
            "daily": vector.status()}
