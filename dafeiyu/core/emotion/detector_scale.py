# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""尺度检测器：只作用于 shyness，不替代其它情绪更新。

规格 6.8：命中时按强度陡增害羞（一条能窜 20~45）。
"""

from __future__ import annotations

import re

from .. import settings
from ..entity import now_iso
from . import reward, vector

# 分档关键词：轻/中/重。命中越多分越高。
# 注意：抱抱/想你/可爱 这类属于「亲密」，由奖罚识别器给奖励，不算尺度——否则每天都会被抬害羞。
LEVELS = {
    1: ["亲亲", "亲一下", "亲你", "心动", "脸红", "耳朵", "撒娇", "撩", "暧昧",
        "害羞", "羞", "贴贴", "靠一下"],
    2: ["亲嘴", "接吻", "舌", "床", "脱", "裸", "胸", "腿", "腰", "喘",
        "一起睡", "抱着睡", "摸我", "碰我", "解扣", "内衣"],
    3: ["做爱", "色情", "情色", "黄色", "裸体", "呻吟", "高潮", "插入", "口交",
        "性器", "自慰", "发情", "露骨"],
}

CALLBACK_TERMS = ("刚才那个", "继续说", "继续刚才", "再说说", "然后呢")


def detect(text: str, judge=None) -> dict:
    """返回 {'hit':bool,'level':0-3,'terms':[...],'delta':{shyness:x}}"""
    body = (text or "").strip()
    result = {"hit": False, "level": 0, "terms": [], "delta": {}}
    if not body:
        return result
    terms: list[str] = []
    level = 0
    for lv in (3, 2, 1):
        for word in LEVELS[lv]:
            if word in body:
                terms.append(word)
                level = max(level, lv)
    if judge is not None:
        try:
            lv = int(judge(body) or 0)
            if lv > level:
                level = lv
        except Exception:
            pass
    if not level:
        return result
    cfg = settings.emotion_cfg().get("scale", {})
    base = float(cfg.get("shy_base", 20))
    per = float(cfg.get("shy_per_level", 15))
    cap = float(cfg.get("shy_cap", 45))
    amount = min(cap, base + per * (level - 1))
    result.update({"hit": True, "level": level, "terms": terms, "delta": {"shyness": round(amount, 1)}})
    return result


def apply(text: str, judge=None) -> dict:
    """命中就直接抬害羞（独立通道），并记一条 scale 事件用于审计。"""
    found = detect(text, judge=judge)
    if not found["hit"]:
        return found
    ev = reward.apply_custom("scale", found["delta"], ev_type="punishment",
                             source_text=text, reason="尺度检测器")
    found["applied"] = ev is not None
    found["event_id"] = ev.id if ev else ""
    return found
