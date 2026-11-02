# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""瞬时记忆：6 小时原文 / 6~18 小时摘要 / 18 小时蒸馏后清空（用户既定规则）。

清空只作用于瞬时层，日记永不删；思维链走 forget.py 的衰减，不在这里硬清。
"""

from __future__ import annotations

from datetime import datetime

from .. import settings
from ..entity import Layer, Status
from ..repo import memory as mr
from ..repo import system as sr
from ..text import clip, hours_since
from . import distill


def sweep(now: datetime | None = None, dry_run: bool = False) -> dict:
    hot_h = float(settings.get("instant.hot_hours", 6))
    purge_h = float(settings.get("instant.purge_hours", 18))
    distill_sal = float(settings.get("instant.distill_salience", 0.7))
    distill_str = float(settings.get("instant.distill_strength", 0.4))
    now = now or datetime.now()

    stats = {"scanned": 0, "downgraded": 0, "distilled": 0, "purged": 0, "kept_diary": 0}
    for item in mr.list_layer(layer=Layer.INSTANT.value, limit=8000, alive_only=True):
        stats["scanned"] += 1
        if item.ext.get('editorial', {}).get('pinned') or item.ext.get('editorial', {}).get('retained_original'):
            continue
        age = hours_since(item.t_created)
        if age <= hot_h:
            continue
        if item.subtype == "diary":
            stats["kept_diary"] += 1
            continue
        if age <= purge_h:
            # 6~18h：可降级为摘要（level=1），原文仍留到 18h
            if item.level == 0 and len(item.content or "") > 200:
                if not dry_run:
                    mr.update_fields(item.id, level=1, summary=clip(item.content, 80))
                stats["downgraded"] += 1
            continue
        # >18h：先蒸馏（高显著 / 未决 / 还够强），再清正文
        need = (item.salience >= distill_sal or item.status == Status.OPEN.value
                or item.strength >= distill_str)
        if need and not dry_run:
            distill.distill_item(item, reason="instant_18h")
            stats["distilled"] += 1
            stats["purged"] += 1
            continue
        if not dry_run:
            mr.update_fields(
                item.id,
                content="",
                summary=item.summary or clip(item.content, 60),
                level=3,
                tombstone=1,
                strength=min(item.strength, 0.05),
                ext={**(item.ext or {}), "purged_at": now.isoformat(timespec="seconds"),
                     "purge_reason": "instant>18h"},
            )
            mr.drop_index(item.id, "memory")
            mr.set_index(item.id, "memory", [], item.concepts)
            sr.audit_add("memory", item.id, "tombstoned", {"reason": "instant>18h"})
        stats["purged"] += 1
    return stats


def add_chat(text: str, *, conv: str = "private:owner", sender: str = "老大",
             is_self: bool = False, is_owner: bool = True, ts: int | None = None,
             salience: float | None = None) -> str:
    """把一条消息写进瞬时记忆（桥接/桌面聊天共用）。"""
    import time

    from ..entity import MemoryItem
    from ..text import keywords

    ts = ts or int(time.time() * 1000)
    item = MemoryItem(
        layer=Layer.INSTANT.value,
        subtype="chat",
        content=text,
        summary=clip(text, 60),
        concepts=keywords(text, 5),
        salience=float(salience if salience is not None else (0.6 if is_owner else 0.4)),
        status=Status.RESOLVED.value,
        t_created=datetime.fromtimestamp(ts / 1000).isoformat(timespec="seconds"),
        origin="chat",
        ext={"conv": conv, "sender": sender, "is_self": is_self, "is_owner": is_owner,
             "ts": ts, "kind": "chat"},
    )
    mr.insert(item)
    mr.set_index(item.id, "memory", [], item.concepts)
    return item.id


def view(limit: int = 120) -> dict:
    """界面用：热区原文 + 温区摘要状态。"""
    hot_h = float(settings.get("instant.hot_hours", 6))
    purge_h = float(settings.get("instant.purge_hours", 18))
    hot, warm, tombstones = [], [], 0
    for item in mr.list_layer(layer=Layer.INSTANT.value, limit=4000, alive_only=False,
                              order="t_created DESC"):
        age = hours_since(item.t_created)
        if item.tombstone:
            tombstones += 1
            continue
        row = {
            "id": item.id, "text": clip(item.content or item.summary, 200),
            "age_hours": round(age, 1), "level": item.level, "salience": round(item.salience, 2),
            "t_created": item.t_created, "sender": (item.ext or {}).get("sender", ""),
            "is_owner": bool((item.ext or {}).get("is_owner")),
        }
        if age <= hot_h:
            hot.append(row)
        elif age <= purge_h:
            warm.append(row)
        if len(hot) + len(warm) >= limit:
            break
    return {"hot": hot, "warm": warm, "tombstones": tombstones,
            "hot_hours": hot_h, "purge_hours": purge_h}
