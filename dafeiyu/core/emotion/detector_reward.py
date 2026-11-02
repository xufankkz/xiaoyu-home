# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""奖罚识别器：关键词规则 + LLM 判定，双通道取并集。

规格 6.7：夸奖 / 亲密 / 请求满足 / 被在乎 / 被忽视 / 请求拒绝 / 辱骂 / 抛弃。
「被忽视」不由文本触发，走碎碎念疲劳与长时间未回复（见 cognition/chitchat.py）。
"""

from __future__ import annotations

import re

RULES: dict[str, list[str]] = {
    "praise": [
        "好棒", "真棒", "厉害", "真乖", "乖", "可爱", "聪明", "喜欢你", "爱你", "夸",
        "真好", "太强", "牛", "优秀", "贴心", "懂事", "谢谢", "心疼你", "做得好",
        "完美", "漂亮", "温柔", "可靠",
    ],
    "intimacy": [
        "抱抱", "抱一下", "抱你", "亲亲", "亲一下", "摸摸", "想你", "想抱", "贴贴",
        "抱紧", "牵手", "靠一下", "亲你", "搂", "揉揉", "rua", "摸摸头",
    ],
    "cared": [
        "你还好吗", "在吗", "睡了吗", "吃饭了吗", "冷不冷", "累不累", "早点睡",
        "注意身体", "别熬夜", "担心你", "关心", "照顾好自己", "多喝水", "休息",
    ],
    "insult": [
        "傻逼", "蠢", "废物", "滚", "闭嘴", "有病", "去死", "垃圾", "恶心", "弱智",
        "白痴", "烦死", "讨厌你", "没用", "蠢货", "脑残",
    ],
    "abandoned": [
        "别找我了", "不要你了", "再也不", "分手", "拉黑", "删了", "不理你了",
        "丢下你", "走了", "离开你",
    ],
    "request_denied": [
        "不行", "不给", "不答应", "没空", "不想", "拒绝", "办不到", "做不到",
    ],
    "request_met": [
        "好啊", "可以呀", "行吧", "没问题", "答应你", "给你", "满足你", "依你", "带你去",
    ],
}

# 这些词出现时，"request_met" 之类容易误判（比如"我可以理解"）
_DENY_PREFIX = ("不", "没", "别", "无", "反", "难")

_PRIORITY = ["abandoned", "insult", "intimacy", "praise", "cared", "request_denied", "request_met"]


def _hits(text: str, words: list[str]) -> list[str]:
    found = []
    for word in words:
        start = 0
        while True:
            idx = text.find(word, start)
            if idx < 0:
                break
            prev = text[idx - 1] if idx > 0 else ""
            nxt = text[idx + len(word)] if idx + len(word) < len(text) else ""
            # 「不厉害」「没抱」不算；「拒绝」后面跟「你」也不算被拒
            if prev in _DENY_PREFIX:
                start = idx + len(word)
                continue
            if word in ("不行", "不给", "不想", "没空") and nxt in ("啊", "吗"):
                start = idx + len(word)
                continue
            found.append(word)
            start = idx + len(word)
    return found


def detect(text: str, judge=None) -> list[dict]:
    """返回 [{'trigger':..,'hits':..,'strength':..}]，最多一条 reward + 一条 punishment。"""
    body = (text or "").strip()
    if not body:
        return []
    scored = {}
    for trigger in _PRIORITY:
        words = _hits(body, RULES.get(trigger, []))
        if words:
            scored[trigger] = {"trigger": trigger, "hits": words,
                               "strength": min(1.0, 0.5 + 0.2 * len(words))}

    if judge is not None:
        try:
            extra = judge(body)
        except Exception:
            extra = None
        if extra and extra in RULES and extra not in scored:
            scored[extra] = {"trigger": extra, "hits": [], "strength": 0.55}

    punishments = [t for t in scored if t in ("abandoned", "insult", "request_denied")]
    rewards = [t for t in scored if t in ("intimacy", "praise", "cared", "request_met")]
    out = []
    if punishments:
        best = min(punishments, key=lambda t: _PRIORITY.index(t))
        out.append(scored[best])
    if rewards:
        best = min(rewards, key=lambda t: _PRIORITY.index(t))
        out.append(scored[best])
    return out


def should_punish_for_denial(text: str) -> bool:
    """只有「明确拒绝她的请求」才算惩罚：句子里带她的请求语气才认。"""
    return bool(re.search(r"(能不能|可以吗|要不要|帮我|给我|想让你)", text or "")) and \
        bool(_hits(text or "", RULES["request_denied"]))


JUDGE_PROMPT = """判断这句话对「大肥鱼」是奖励、惩罚还是都不是。
只回一个词：praise / intimacy / request_met / cared / insult / abandoned / request_denied / none
句子：{text}"""


def parse_judge(text: str) -> str | None:
    word = (text or "").strip().split()[0].strip("。,.!！").lower() if (text or "").strip() else ""
    if word in RULES:
        return word
    return None
