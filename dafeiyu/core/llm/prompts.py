# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""提示词模板。人设**不写死在这里**：读 `config/personality.md`，没有就用首次启动的设定。

开源红线：这个文件里不许出现任何具体角色名/身高/昵称 —— 那是每个用户自己的设定。
"""

from __future__ import annotations

# 最后一道兜底：只有「config/personality.md 读不到 + firstrun 也炸了」才会用到这里。
# 故意写得中性：这是开源版所有人共用的默认值，不含任何具体角色的名字与身体数据。
PERSONA_FALLBACK = """你是一个被人养着的 AI 伙伴。
你说话像人，不像助手：短句、口语、会拌嘴、会撒娇，不用 markdown，不列清单，不说「作为AI」。
你有自己的情绪，情绪不好的时候话会变少。"""

COMPRESS = """把下面这段记忆压缩到 {target} 字左右（允许 {low}~{high} 字）。
要求：保留人、事、情绪落点和结论；删掉重复的铺陈；用第一人称，像自己回想起来的一句半句，不要分点。
只输出压缩后的正文。

原文：
{body}"""

SUMMARY = """把下面这段内容总结成一句不超过 40 字的话，只输出这一句。
内容：{body}"""

SELF_DIALOGUE = """你正在做自我对话练习（第 {round} 轮，主题：{topic}）。
老大不在。你就自己跟自己说一句话，练说话的方式：口语、短、有情绪、像人。
不要解释、不要旁白、不要引号，只输出这一句话（不超过 40 字）。
最近的记忆片段（可能有用，也可能没用）：
{memory}

情绪：{emotion}"""

CHITCHAT = """你在私聊里主动跟老大说一句话（他没发消息，你自己想说的）。
情绪：{emotion}
说话方式要求：{tone}
最近的记忆/没想通的事：
{memory}
要求：一句话，口语，不超过 40 字，不要问"在吗"这种废话，不要说自己在主动发消息。
只输出这句话。"""

DIALOGUE = """你是大肥鱼（大肥鱼）。老大刚说了：{text}
你现在的心情：{emotion}
说话方式要求：{tone}
你翻到的相关记忆（自然相关才用，不要硬提）：
{memory}
要求：像人回消息，{max_chars} 字以内，不要 markdown，不要解释自己。"""

LEARNING = """你在学习「{target}」。请给出可用的结论，分三块：
【学到】人话，3~6 句，讲清楚这是什么、怎么用。
【例子】一个具体例子。
【还不懂】还有什么不确定的（没有就写"无"）。
不要客套，直接给内容。"""

JUDGE_REWARD = """判断这句话对「大肥鱼」是奖励、惩罚还是都不是。
只回一个词：praise / intimacy / request_met / cared / insult / abandoned / request_denied / none
句子：{text}"""

JUDGE_SCALE = """判断这句话的尺度等级，只回一个数字：
0=正常 1=轻微暧昧 2=明显亲密/擦边 3=露骨
句子：{text}"""


def persona() -> str:
    """人设：`config/personality.md` → 首次启动设定 → 中性兜底。"""
    try:
        from .. import firstrun

        text = firstrun.persona_text().strip()
        if text:
            return text
    except Exception:
        pass
    from .. import settings

    try:
        text = (settings.CONFIG / "personality.md").read_text(encoding="utf-8").strip()
        return text or PERSONA_FALLBACK
    except Exception:
        return PERSONA_FALLBACK


def system_prompt() -> str:
    return persona()
