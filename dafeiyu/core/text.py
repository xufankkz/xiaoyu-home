# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""大肥鱼 · 文本工具（中文 bigram 分词、字数、机械压缩）。

不引 jieba：一是少一个依赖，二是桥接那边也是 bigram 口径，两边检索行为一致才好对账。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

# 高频虚词：这些 bigram 命中不算「提到类似的事」，否则每条消息都能翻出一堆旧事。
STOP_BIGRAMS = {
    "今天", "昨天", "明天", "现在", "刚才", "什么", "怎么", "可以", "这个", "那个",
    "我们", "你们", "他们", "一个", "就是", "还是", "但是", "因为", "所以", "如果",
    "已经", "没有", "知道", "觉得", "时候", "这样", "那样", "真的", "不是", "有点",
    "一下", "出来", "起来", "东西", "事情", "问题", "然后", "而且", "或者", "还有",
    "只是", "不过", "其实", "自己", "感觉", "好像", "应该", "可能", "不会", "不要",
    "干嘛", "咋了", "多少", "哪里", "这里", "那里", "一直", "看到", "说的", "我觉",
    "是的", "的话", "一样", "这么", "那么", "为了", "对于", "关于", "以后", "以前",
    "现在", "当时", "后来", "开始", "比较", "非常", "特别", "反正", "而且", "于是",
    "虽然", "既然", "只要", "只有", "除了", "以及", "并且", "而是", "并不", "一点",
}

_CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_+#.]{1,}")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?\n])")


def char_count(text: str) -> int:
    """字数口径：非空白字符数（与现有系统一致）。"""
    return len(re.sub(r"\s", "", text or ""))


def clip(text: str, limit: int, tail: str = "…") -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + tail


def sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:12]


def bigrams(text: str) -> list[str]:
    """检索用 token（去停用词、去重、保序）。"""
    seen: list[str] = []
    mark: set[str] = set()
    for run in _CJK.findall(text or ""):
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram in STOP_BIGRAMS or gram in mark:
                continue
            mark.add(gram)
            seen.append(gram)
    for word in _LATIN.findall(text or ""):
        w = word.lower()
        if w not in mark:
            mark.add(w)
            seen.append(w)
    return seen


def keywords(text: str, limit: int = 8) -> list[str]:
    """从一段话里挑概念标签（bigram 频次 + 位置加权）。"""
    counts: dict[str, float] = {}
    order: list[str] = []
    for run in _CJK.findall(text or ""):
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram in STOP_BIGRAMS:
                continue
            if gram not in counts:
                counts[gram] = 0.0
                order.append(gram)
            counts[gram] += 1.0 + max(0.0, 1.0 - i / 60.0)
    for word in _LATIN.findall(text or ""):
        w = word.lower()
        if w not in counts:
            counts[w] = 0.0
            order.append(w)
        counts[w] += 1.5
    picked: list[str] = []
    for gram in sorted(counts, key=lambda g: -counts[g]):
        if any(gram in p or p in gram for p in picked):
            continue
        picked.append(gram)
        if len(picked) >= limit:
            break
    return picked


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


# 这些字出现在 n-gram 里，说明它多半是句子碎片而不是概念
STOP_CHARS = set(
    "的了是我你他她它们在这就不也都还很要会有那个和吗吧啊呢着过得地与及或但而被把让给对从到"
    "说想觉得没太更最又再并如果因为所以然后时候一下自己感觉好像应该可能不会不要什么怎么这样"
)


def terms(text: str, limit: int = 6) -> list[str]:
    """提取「像概念」的 3~4 字词（不靠词典，靠停用字过滤 + 频次 + 长优先）。

    比裸 bigram 靠谱得多："边界感" 会被留下，"老大说/我不太" 会被停用字挡掉。
    """
    counts: dict[str, int] = {}
    for run in _CJK.findall(text or ""):
        for n in (4, 3):
            for i in range(len(run) - n + 1):
                gram = run[i:i + n]
                if any(ch in STOP_CHARS for ch in gram):
                    continue
                counts[gram] = counts.get(gram, 0) + 1
    picked: list[str] = []
    for gram, _count in sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]))):
        if any(gram in p or p in gram for p in picked):
            continue
        picked.append(gram)
        if len(picked) >= limit:
            break
    return picked


def compress_mechanical(text: str, target: int, phase: str = "压缩自短期记忆，原文在日记里") -> str:
    """模型不可用时的兜底压缩：保结构、留结论句、从最早/最不重要的删。"""
    body = (text or "").strip()
    if char_count(body) <= target:
        return body
    parts = sentences(body)
    if not parts:
        return body[:target]
    # 结论句优先（末句往往是想明白的那句），其次首句
    keep: list[str] = [parts[0]] if parts else []
    if len(parts) > 1 and parts[-1] not in keep:
        keep.append(parts[-1])
    rest = [p for p in parts[1:-1]]
    for part in rest:
        if char_count("".join(keep) + part) > target:
            continue
        keep.insert(1, part)
    out = "".join(keep)
    if char_count(out) > target:
        out = out[:target].rstrip()
    return out.rstrip("。") + f"。…（{phase}）" if char_count(out) < target else out


def shrink_markdown(text: str, target: int) -> str:
    """整份文档压到目标字数：保留标题行 + 每段首句，直到够。"""
    lines = (text or "").splitlines()
    head = [ln for ln in lines if ln.startswith("#")][:3]
    body = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#") and not re.fullmatch(r"-{3,}", ln.strip())]
    out = list(head)
    for ln in body:
        if char_count("\n".join(out) + ln) > target:
            break
        out.append(ln)
    if char_count("\n".join(out)) < target // 2 and body:
        out.extend(body[len(out) - len(head):][: max(0, (target - char_count("\n".join(out))) // 30)])
    return "\n".join(out) + f"\n\n> 机械压缩（模型不可用）｜目标 {target} 字\n"


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def parse_iso(text: str) -> datetime | None:
    """解析 ISO 时间，统一返回**本地裸时间**（带时区的先转本地再去掉 tzinfo）。"""
    if not text:
        return None
    raw = str(text).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def hours_since(iso_text: str) -> float:
    dt = parse_iso(iso_text)
    if dt is None:
        return 0.0
    return max(0.0, (datetime.now() - dt).total_seconds() / 3600.0)


def days_since(iso_text: str) -> float:
    return hours_since(iso_text) / 24.0


def skeleton_of(text: str, limit: int = 60) -> str:
    """一句话骨架：取首句 + 转折词提示。"""
    body = re.sub(r"\s+", " ", (text or "").strip())
    if not body:
        return ""
    first = sentences(body)
    base = first[0] if first else body
    for hook in ("因为", "但是", "不过", "所以", "结果"):
        if hook in body:
            base = f"{base}（含「{hook}」）"
            break
    return clip(base, limit)
