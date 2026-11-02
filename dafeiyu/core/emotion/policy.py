# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""响应策略层：只调制表现（回不回、多快、多长、什么语气、要不要碎碎念）。

规格 6.9 红线：不改变记忆与学习逻辑。
"""

from __future__ import annotations

import random

from .. import settings
from ..entity import DIMS, ResponsePolicy


def build(vec=None, curiosity: float = 0.5, rng: random.Random | None = None) -> ResponsePolicy:
    from .vector import current

    vec = vec or current()
    rng = rng or random
    beh = settings.emotion_cfg().get("behavior", {})
    pos_th = float(beh.get("positive_threshold", 60))
    neg_th = float(beh.get("negative_threshold", 60))
    shy_th = float(beh.get("shy_threshold", 70))

    pos = (vec.get("joy") + vec.get("happiness")) / 2.0
    neg = (vec.get("sadness") + vec.get("inferiority")) / 2.0
    shy = vec.get("shyness")

    if neg >= neg_th:
        ignore_prob = float(beh.get("ignore_prob_negative", 0.65))
        lo, hi = beh.get("delay_negative_ms", [30000, 600000])
        return ResponsePolicy(
            reply=rng.random() >= ignore_prob,
            delay_ms=rng.randint(int(lo), int(hi)),
            max_chars=40,
            tone="low",
            chitchat=False,
            reason=f"负面情绪高（难过/自卑 均值 {neg:.0f} ≥ {neg_th:.0f}）：不理人、简短、低沉",
        )

    if shy >= shy_th:
        lo, hi = beh.get("delay_shy_ms", [2000, 10000])
        # 规格 5.5：害羞高时由 curiosity 与 shyness 的相对强度决定话多/话少
        # curiosity 是 0~1，shyness 是 0~100，先拉到同一把尺子上再比
        curiosity_scaled = float(curiosity) * 100.0
        if curiosity_scaled > shy:
            return ResponsePolicy(True, rng.randint(int(lo), int(hi)), 300, "shy_verbose", True,
                                  f"害羞高（{shy:.0f}）但好奇更强（{curiosity_scaled:.0f}）：害羞着话多、追问")
        return ResponsePolicy(True, rng.randint(int(lo), int(hi)), 60, "shy_short", False,
                              f"害羞高（{shy:.0f}）压过好奇（{curiosity_scaled:.0f}）：话少、省略号、躲闪")

    if pos >= pos_th:
        lo, hi = beh.get("delay_positive_ms", [0, 2000])
        return ResponsePolicy(True, rng.randint(int(lo), int(hi)), 400, "lively", True,
                              f"正面情绪高（愉悦/幸福 均值 {pos:.0f} ≥ {pos_th:.0f}）：活泼、话多、会主动碎碎念")

    lo, hi = beh.get("delay_normal_ms", [1000, 4000])
    return ResponsePolicy(True, rng.randint(int(lo), int(hi)), 200, "normal", True, "平稳")


def may_ignore(policy: ResponsePolicy) -> bool:
    """负面高时是否走「不理人」——规格 5.5 允许她这轮不接话。"""
    return not policy.reply


def tone_hint(policy: ResponsePolicy) -> str:
    return {
        "lively": "轻快、话多、会开玩笑，可以连发两三条",
        "low": "失落、低沉、句子很短，能一句就一句",
        "shy_verbose": "害羞但话变多：结巴、追问、想确认对方是不是在逗你",
        "shy_short": "害羞到话少：多用省略号、答非所问、被夸会否认",
        "normal": "正常语气，别太亢奋也别太冷",
    }.get(policy.tone, "正常语气")


def demand_hint() -> str:
    """v12 3.2：索取度（0–100）决定这轮她"能要多少"。0 = 完全不许索取。"""
    try:
        from .. import relationship

        stat = relationship.status()
    except Exception:
        return ""
    level = int(stat.get("demand_level", 65))
    if level <= 0:
        return "索取度 0：这轮**不要主动要任何东西**（不撒娇、不要陪、不赌气），只回应他说的话。"
    if level < 25:
        return "索取度很低：最多顺口问一句「今天忙吗」，不缠、不追问。"
    if level < 55:
        return "索取度正常：可以撒一点娇，被拒绝就收，别重复要。"
    if level < 80:
        return "索取度偏高：可以直接说想让他陪一会儿，被敷衍了可以小声赌气一句。"
    return "索取度很高：想缠就缠、想闹就闹，但别伤人、别刷屏，闹完要能自己收。"


def summary(vec=None) -> dict:
    from .vector import current

    vec = vec or current()
    policy = build(vec)
    return {
        "reply": policy.reply, "delay_ms": policy.delay_ms, "max_chars": policy.max_chars,
        "tone": policy.tone, "chitchat": policy.chitchat, "reason": policy.reason,
        "tone_hint": tone_hint(policy),
    }
