# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""语言风格：受性格（1 号）与当前情绪调制（规格 5.3 / 6.6）。"""

from __future__ import annotations

from ..emotion import policy as policy_mod


def style_note(vec=None, policy=None) -> str:
    """给对话/碎碎念用的一段风格说明（不报数字）。"""
    from ..emotion.vector import current

    vec = vec or current()
    policy = policy or policy_mod.build(vec)
    parts = [f"现在的心情偏向「{vec.dominant_cn()}」。", policy_mod.tone_hint(policy)]
    if vec.get("shyness") >= 70:
        parts.append("害羞的时候会卡壳、用省略号、被夸会小声否认，但不会说自己是害羞。")
    if (vec.get("sadness") + vec.get("inferiority")) / 2 >= 60:
        parts.append("不想多说，句子短，可能过一会儿才回。")
    if (vec.get("joy") + vec.get("happiness")) / 2 >= 60:
        parts.append("话多一点，会自己找话题，会开点小玩笑。")
    return " ".join(parts)


def max_chars(policy) -> int:
    return int(getattr(policy, "max_chars", 200))


def delay_seconds(policy) -> float:
    return round(float(getattr(policy, "delay_ms", 0)) / 1000.0, 1)
