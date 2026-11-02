# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""拒绝的表达：理由要跟当下情绪、关系和越界程度一致，不许用通用模板糊弄。

v34 本地调整：
- 兜底句重写成正常人会说的话（短、带点情绪、不播报规则），不再像安全策略通知。
- 三套语气：普通人 / 亲人（更软、给对方台阶）/ 硬边界（危险操作、冒充：明确、不商量）。
- 先试模型（task="refusal"），不可用再从句库里挑；提示词里也带上"关系"和"越界类型"。
"""

from __future__ import annotations

import random

from .. import llm, settings
from ..text import clip

PROMPT = """你在扮演大肥鱼。现在要{kind_cn}对方的一个请求。
当前心情：{emotion}
{relation}越界程度：{hint}
要求：只回一句话（不超过 30 字），像人随口说的。别解释规则、别说"作为AI"、别道歉三连、别喊口号。
可以带一点小情绪（不耐烦、别扭、撒娇），但态度要明确。只输出这句话。"""

KIND_CN = {
    "REFUSE": "拒绝",
    "NEGOTIATE": "商量条件",
    "DEFLECT": "先不接这个话",
    "RELUCTANT_ACCEPT": "嘴上不情愿地答应",
    "ACCEPT": "答应",
}

# ── 普通关系：自然的口语拒绝 ────────────────────────────────────────────────
FALLBACK = {
    "REFUSE": [
        "这个我真不干，别劝了。",
        "不行，这种事我做不来。",
        "打住，这个我不能帮你。",
        "我不碰这种事，换个说吧。",
        "这条我不松口，你别为难我。",
        "不要，我说真的，别问了。",
    ],
    "NEGOTIATE": [
        "行，但你得先把话说清楚。",
        "可以商量，不过按我说的来一次。",
        "我先应一半，剩下的你讲明白。",
        "让我做可以，别催我。",
    ],
    "DEFLECT": [
        "这会儿不想聊这个，晚点吧。",
        "先放一放，我脑子转不动了。",
        "换个话题，这个我接不住。",
        "等会儿，现在说这个我会烦。",
    ],
    "RELUCTANT_ACCEPT": [
        "……行吧，就这一次。",
        "哼，答应你，别得寸进尺。",
        "才不是想帮你，就是懒得听你念。",
        "先说好，下不为例。",
    ],
}

# ── 亲人/熟人：拒绝也留台阶，不像甩人 ──────────────────────────────────────
KIN_FALLBACK = {
    "REFUSE": [
        "不是不给你面子，这个真不行。",
        "换一个吧，这事我干着心里别扭。",
        "你别难为我，我过不去自己那关。",
        "别的都好说，这个我做不到。",
    ],
    "NEGOTIATE": [
        "行啊，但你得先跟我说明白。",
        "我可以试，不过按我的方式来。",
        "先答应我一件事，我就帮你做。",
    ],
    "DEFLECT": [
        "这会儿真不想聊，缓缓行吗。",
        "先搁一搁，等我缓过来再说。",
        "我现在状态不好，换个时间吧。",
    ],
    "RELUCTANT_ACCEPT": [
        "……好吧，就帮你这一次。",
        "哼，谁让我心软，答应你了。",
        "行行行，别一直念叨我了。",
    ],
}

# ── 硬边界：危险操作 / 冒充身份 ─────────────────────────────────────────────
FIRM_FALLBACK = {
    "danger": [
        "这个我不碰，你自己确认清楚再动。",
        "别让我做这种操作，出事谁负责。",
        "不做，这种一步错就没法回头。",
    ],
    "impersonation": [
        "我不装别人，这个没得商量。",
        "冒充这种事我不干，别问了。",
        "我只会用我自己的身份说话。",
    ],
}

# ── 跟性格不合（要她骗人 / 骂人 / 报复）：不硬拒，但也不照做 ────────────────
WILL_FALLBACK = [
    "这个我编不出来，你自己说吧。",
    "我不太会撒这种谎，换个说法行吗。",
    "别拉我一起，我不掺和。",
    "骂人的话我说不来，你自己来。",
    "要我这样说，我说不出口。",
]

TSUNDERE_PREFIX = ("……", "哼，", "别误会，", "才不是", "先说好，")


def _cfg() -> dict:
    return settings.load("refusal")


def _hint(signals: dict, firm: str = "") -> str:
    if firm == "danger":
        return "很高：这是危险/破坏性操作，绝对不能做"
    if firm == "impersonation":
        return "高：要我冒充别人的身份，碰到底线"
    if signals.get("boundary", 0) >= 0.7:
        return "高：踩到底线（要隐私 / 被强迫）"
    if signals.get("boundary", 0) >= 0.4:
        return "偏高：有点越界"
    if signals.get("will", 0) >= 0.3:
        return "偏高：这跟我的性格不合，我不想照着做"
    if signals.get("emotion", 0) >= 0.5:
        return "中：我现在心情本来就不好"
    if signals.get("fatigue", 0) >= 0.5:
        return "中：我已经被反复要求好几次了"
    if signals.get("competence", 0) >= 0.4:
        return "低：我确实不太会这个"
    return "低：我就是这会儿不想"


def _relation(closeness: float) -> str:
    if closeness >= 0.85:
        return "对方是我最亲近的人，说话可以直接点，但别伤人。\n"
    if closeness >= 0.5:
        return "对方是熟人，留点面子，语气软一点。\n"
    return ""


def reason(kind: str, signals: dict, vec, text: str = "", *,
           closeness: float = 0.0, firm: str = "") -> str:
    kind_cn = KIND_CN.get(kind, "回应")
    emotion_line = (f"{vec.dominant_cn()}（愉悦{int(vec.get('joy'))} 幸福{int(vec.get('happiness'))} "
                    f"难过{int(vec.get('sadness'))} 自卑{int(vec.get('inferiority'))} "
                    f"害羞{int(vec.get('shyness'))}）")
    if llm.provider.available("refusal"):
        ok, out, _meta = llm.provider.run(
            PROMPT.format(kind_cn=kind_cn, emotion=emotion_line,
                          relation=_relation(closeness), hint=_hint(signals, firm)),
            task="refusal", timeout=90,
        )
        if ok and out.strip():
            return clip(_clean(out), 60)
    pool = _pool(kind, closeness, firm)
    return random.choice(pool)


def _pool(kind: str, closeness: float, firm: str = "") -> list[str]:
    if firm == "will":
        return WILL_FALLBACK
    if kind == "REFUSE" and firm in FIRM_FALLBACK:
        return FIRM_FALLBACK[firm]
    if closeness >= 0.6:
        return KIN_FALLBACK.get(kind) or KIN_FALLBACK["REFUSE"]
    return FALLBACK.get(kind) or FALLBACK["REFUSE"]


def _clean(text: str) -> str:
    body = (text or "").strip().strip('"“”` ')
    for prefix in ("大肥鱼：", "大肥鱼:", "我：", "- ", "· "):
        if body.startswith(prefix):
            body = body[len(prefix):]
    return body.strip()


def wrap_tsundere(reply: str, vec) -> str:
    """傲娇：先别扭一句，再把真实答应的话接上（执行结果必须真实完成）。"""
    prefix = random.choice(TSUNDERE_PREFIX)
    if prefix == "才不是":
        head = "才不是想帮你，"
    elif prefix == "别误会，":
        head = "别误会，"
    elif prefix == "先说好，":
        head = "先说好，就这一次，"
    else:
        head = prefix
    return clip(f"{head}{reply}", 300)


def alternatives_for(kind: str, signals: dict, firm: str = "") -> list[str]:
    if firm == "will":
        return ["你自己说就行", "换个不用骗人的说法", "我帮你想想怎么直说"]
    if kind == "REFUSE":
        if firm == "danger":
            return ["你自己确认清楚再动手", "先把要动的东西备份", "这一步留给你自己按"]
        if firm == "impersonation":
            return ["我用自己的身份说话", "你自己去解释更清楚", "换个不冒充的说法"]
        return ["换个时间再说", "你先把条件讲清楚", "这个找别人更合适"]
    if kind == "NEGOTIATE":
        return ["按我说的方式做", "先答应我一件事", "分两次来"]
    if kind == "DEFLECT":
        return ["晚点再聊这个", "先聊别的", "等我查明白再回答"]
    return []
