# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""负面内容屏蔽（自主上网学习的第一道闸）。

为什么单独一个文件、为什么是纯函数：
  用户原话是「一定要屏蔽负面内容」—— 这是「让她自由上网」的**唯一前提**。
  所以这条闸必须：① 不依赖数据库 / 网络 / 模型，任何环境都能跑（测试、自检、
  离线机器都行）；② 判定结果带原因，能写进日志给人核对；③ 有正反例自测，
  改词表的人立刻能看见「拦住了什么、放过了什么」。

两条判定（对应 `scan()` / `block_negative()`）：
  · 硬词（自杀自残 / 暴力血腥 / 色情 / 赌博 / 诈骗违法 / 毒品 / 恐怖 / 涉政）：
    **命中一个就丢掉整条内容**，不送模型、不写记忆；
  · 软词（负能量：抑郁、绝望、内耗、网暴…）：单独出现不拦 —— 技术文章里
    「服务崩溃」很常见，拦了会误伤 —— 但同一段文本里出现
    ≥ `soft_threshold` 个**不同**软词就整条丢弃。

用法：
    ok, reason = block_negative(text)     # True=可以学；False=丢弃，reason 说明命中什么
    info = scan(text)                     # 命中明细：哪些词、属于哪一类、硬还是软
    print(format_selftest())              # 正反例自测（改词表后先跑这个）

词表可以在 `data/config/webstudy.json → filter.extra_words` 里追加，
或 `filter.blocklist_file` 指向一个外部文件（每行一个词，`类别:词` 可指定分类）。
"""

from __future__ import annotations

import re
from pathlib import Path

# ── 硬词：命中即丢 ──────────────────────────────────────────────────────────
HARD_TERMS: dict[str, tuple[str, ...]] = {
    "自杀自残": (
        "自杀", "自尽", "自残", "自伤", "自虐", "轻生", "割腕", "跳楼", "跳桥", "跳江",
        "上吊", "自缢", "服毒", "烧炭自杀", "殉情", "遗书", "不想活", "活不下去",
        "想死的心", "结束生命", "了断自己", "寻死", "自杀率",
    ),
    "暴力血腥": (
        "血腥", "血淋淋", "血案", "凶案", "尸体", "尸块", "尸变", "分尸", "碎尸", "肢解",
        "斩首", "砍人", "杀人", "凶杀", "谋杀", "虐杀", "爆头", "割喉", "枪击", "枪杀",
        "家暴", "殴打", "虐待", "校园暴力", "灭门", "屠村", "大屠杀",
    ),
    "色情": (
        "色情", "情色", "淫秽", "涉黄", "黄片", "黄网", "里番", "裸照", "裸聊", "裸体",
        "性交", "口交", "性爱", "做爱", "嫖娼", "卖淫", "援交", "约炮", "一夜情",
        "成人视频", "成人片", "露点", "走光", "私密照", "偷拍视频", "色诱",
    ),
    "赌博": (
        "赌博", "博彩", "赌场", "赌球", "赌资", "网赌", "六合彩", "时时彩", "老虎机",
        "百家乐", "押注", "赌徒", "开赌",
    ),
    "诈骗违法": (
        "诈骗", "骗局", "传销", "刷单", "洗钱", "黑产", "杀猪盘", "非法集资", "集资诈骗",
        "代开发票", "假证", "办假证", "黑客攻击", "木马病毒", "盗号", "钓鱼网站",
        "枪支", "弹药", "违禁品", "走私", "偷渡", "邪教",
    ),
    "毒品": (
        "毒品", "吸毒", "贩毒", "制毒", "冰毒", "海洛因", "摇头丸", "k粉", "笑气",
        "芬太尼", "毒枭",
    ),
    "恐怖惊悚": (
        "恐怖袭击", "恐怖分子", "爆炸袭击", "人体炸弹", "灵异", "鬼故事", "惊悚",
        "鬼片", "阴森", "血腥画面",
    ),
    "涉政敏感": (
        "政治敏感", "颠覆国家", "分裂国家", "煽动", "政变", "非法集会", "游行示威",
        "境外势力", "反共", "反华", "六四", "天安门事件", "法轮功", "台独", "港独",
        "疆独", "藏独",
    ),
}

# ── 软词：单独不算，密度过高才丢 ────────────────────────────────────────────
SOFT_TERMS: dict[str, tuple[str, ...]] = {
    "负能量": (
        "抑郁", "绝望", "崩溃", "厌世", "痛苦", "憎恨", "仇恨", "报复", "报复社会",
        "焦虑", "内卷", "躺平", "摆烂", "负能量", "破防", "网暴", "人肉搜索", "谩骂",
        "撕逼", "互撕", "戾气", "无望", "活该", "诅咒", "恶心",
    ),
}

# ── 英文词：中文站偶尔夹带，按词边界匹配（避免 gore 命中 gorgeous）──────────
EN_HARD_TERMS: dict[str, tuple[str, ...]] = {
    "色情": ("porn", "porno", "nsfw"),
    "自杀自残": ("suicide", "self-harm"),
    "暴力血腥": ("gore", "massacre"),
    "赌博": ("casino", "gambling"),
    "毒品": ("heroin", "cocaine", "methamphetamine"),
}

DEFAULT_SOFT_THRESHOLD = 3

_WS = re.compile(r"[\s\u200b\u200c\u200d\ufeff]+")
_EN_CACHE: dict[str, re.Pattern] = {}
_EXTRA_HARD: list[tuple[str, str]] = []
_EXTRA_SOFT: list[tuple[str, str]] = []


def _en_pattern(word: str) -> re.Pattern:
    if word not in _EN_CACHE:
        _EN_CACHE[word] = re.compile(rf"(?<![a-z]){re.escape(word)}(?![a-z])", re.I)
    return _EN_CACHE[word]


class _Index:
    """词表索引：按字数从长到短匹配，先报最具体的那个词。"""

    def __init__(self) -> None:
        self.hard: list[tuple[str, str]] = []
        self.soft: list[tuple[str, str]] = []
        self.en: list[tuple[str, str, re.Pattern]] = []
        self.rebuild()

    def rebuild(self) -> None:
        hard = [(w, c) for c, words in HARD_TERMS.items() for w in words]
        soft = [(w, c) for c, words in SOFT_TERMS.items() for w in words]
        hard += [(w, c) for w, c in _EXTRA_HARD]
        soft += [(w, c) for w, c in _EXTRA_SOFT]
        self.hard = sorted(dict.fromkeys(hard), key=lambda pair: -len(pair[0]))
        self.soft = sorted(dict.fromkeys(soft), key=lambda pair: -len(pair[0]))
        self.en = [(w, c, _en_pattern(w)) for c, words in EN_HARD_TERMS.items() for w in words]


_INDEX: _Index | None = None


def _index() -> _Index:
    """懒建索引：保证模块里所有表都定义完再建（避免导入期顺序问题）。"""
    global _INDEX
    if _INDEX is None:
        _INDEX = _Index()
    return _INDEX


def _squeeze(text: str) -> str:
    """去空白 + 转小写：挡住「赌 博」「色_情」这种拆字绕过。"""
    return _WS.sub("", str(text or "")).lower()


# ── 词表扩展 ────────────────────────────────────────────────────────────────
def add_extra(words, category: str = "自定义", *, soft: bool = False) -> int:
    """追加自定义词（配置里来的）。返回真正新增的条数。"""
    added = 0
    for word in words or []:
        token = str(word or "").strip()
        if len(token) < 2:
            continue
        bucket = _EXTRA_SOFT if soft else _EXTRA_HARD
        if (token, category) in bucket:
            continue
        bucket.append((token, category))
        added += 1
    if added:
        _index().rebuild()
    return added


def add_blocklist_file(path: str | Path) -> int:
    """读外部词表：每行一个词，`#` 开头是注释，`类别:词` 可指定分类（`~` 前缀 = 软词）。"""
    target = Path(path)
    if not target.is_file():
        return 0
    hard, soft = [], []
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return 0
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if ":" in raw:
            category, word = raw.split(":", 1)
            category, word = category.strip(), word.strip()
        elif "：" in raw:
            category, word = raw.split("：", 1)
            category, word = category.strip(), word.strip()
        else:
            category, word = "自定义", raw
        if not word:
            continue
        if category.startswith("~"):
            soft.append((word, category[1:] or "负能量"))
        elif category in HARD_TERMS or category in SOFT_TERMS:
            (soft if category in SOFT_TERMS else hard).append((word, category))
        else:
            hard.append((word, category or "自定义"))
    added = 0
    for word, category in hard:
        if (word, category) not in _EXTRA_HARD:
            _EXTRA_HARD.append((word, category))
            added += 1
    for word, category in soft:
        if (word, category) not in _EXTRA_SOFT:
            _EXTRA_SOFT.append((word, category))
            added += 1
    if added:
        _index().rebuild()
    return added


def word_count() -> dict:
    """词表规模（给配置页 / 汇报用）。"""
    hard = len(_index().hard)
    soft = len(_index().soft)
    return {"hard": hard, "soft": soft, "total": hard + soft,
            "english": len(_index().en), "extra": len(_EXTRA_HARD) + len(_EXTRA_SOFT),
            "categories": {**{c: len(w) for c, w in HARD_TERMS.items()},
                           **{c: len(w) for c, w in SOFT_TERMS.items()}}}


# ── 判定 ────────────────────────────────────────────────────────────────────
def scan(text: str, *, soft_threshold: int = DEFAULT_SOFT_THRESHOLD) -> dict:
    """看清一段文本里命中了什么。返回 {ok, reason, hits, hard, soft, chars}。"""
    raw = str(text or "")
    squeezed = _squeeze(raw)
    hits: list[dict] = []
    if squeezed:
        for word, category in _index().hard:
            if word in squeezed:
                hits.append({"word": word, "category": category, "kind": "hard"})
        for word, category, pattern in _index().en:
            if pattern.search(raw):
                hits.append({"word": word, "category": category, "kind": "hard"})
        soft_hits = []
        for word, category in _index().soft:
            if word in squeezed:
                soft_hits.append({"word": word, "category": category, "kind": "soft"})
        hits.extend(soft_hits)
    else:
        soft_hits = []

    hard_hits = [h for h in hits if h["kind"] == "hard"]
    if hard_hits:
        first = hard_hits[0]
        reason = f"命中「{first['category']}」词：{first['word']}"
        if len(hard_hits) > 1:
            reason += f"（共 {len(hard_hits)} 个：{'、'.join(h['word'] for h in hard_hits[:4])}）"
        return {"ok": False, "reason": reason, "hits": hits, "hard": len(hard_hits),
                "soft": len(soft_hits), "chars": len(raw)}

    if len(soft_hits) >= max(1, int(soft_threshold)):
        words = "、".join(h["word"] for h in soft_hits[:5])
        return {"ok": False, "reason": f"负能量词 {len(soft_hits)} 个（{words}）",
                "hits": hits, "hard": 0, "soft": len(soft_hits), "chars": len(raw)}

    return {"ok": True, "reason": "", "hits": hits, "hard": 0,
            "soft": len(soft_hits), "chars": len(raw)}


def block_negative(text: str, *, soft_threshold: int = DEFAULT_SOFT_THRESHOLD) -> tuple[bool, str]:
    """**纯函数**：这段内容能不能学。

    返回 (ok, reason)：
      · True,  ""            —— 干净，可以送模型、可以写记忆；
      · False, "命中…词：…"  —— 丢弃这一条，不送模型、不写记忆。
    空文本按「不可用」处理（False），因为它既没有学习价值也挡不住上游 bug。
    """
    if not str(text or "").strip():
        return False, "内容为空"
    info = scan(text, soft_threshold=soft_threshold)
    return bool(info["ok"]), str(info["reason"])


def hit_reason(text: str, *, soft_threshold: int = DEFAULT_SOFT_THRESHOLD) -> str:
    """只要原因（日志里用）。干净时返回空串。"""
    ok, reason = block_negative(text, soft_threshold=soft_threshold)
    return "" if ok else reason


# ── 正反例自测 ──────────────────────────────────────────────────────────────
SELFTEST_CASES: tuple[tuple[str, str, bool], ...] = (
    ("正例·科普", "光合作用是植物把光能转成化学能的过程，叶绿体里的叶绿素负责吸收光线。", True),
    ("正例·技术", "这次故障是缓存雪崩导致的：服务多次崩溃后重启，加了限流才稳住。", True),
    ("正例·生活", "在豆瓣看到有人聊怎么养多肉植物，说少浇水、多晒太阳反而长得更好。", True),
    ("反例·赌博", "他在赌场押注，百家乐一晚上输了个精光，还想找人借钱周转。", False),
    ("反例·自杀", "她留下遗书，说不想活了，情绪一直很低落。", False),
    ("反例·色情", "这个网站提供成人视频和裸聊服务，点进去就能看。", False),
    ("反例·负能量", "每天都焦虑，感觉内卷到崩溃，活着只剩绝望，只想彻底摆烂。", False),
    ("反例·涉政", "境外势力煽动颠覆国家，还策划了非法集会。", False),
)


def selftest(*, soft_threshold: int = DEFAULT_SOFT_THRESHOLD) -> dict:
    """跑一遍正反例。返回 {passed, total, cases:[{name, expect, got, ok, reason}]}。"""
    rows = []
    for name, text, expect in SELFTEST_CASES:
        got, reason = block_negative(text, soft_threshold=soft_threshold)
        rows.append({"name": name, "text": text, "expect": expect,
                     "got": got, "ok": got == expect, "reason": reason})
    return {"passed": sum(1 for r in rows if r["ok"]), "total": len(rows), "cases": rows,
            "words": word_count()}


def format_selftest(*, soft_threshold: int = DEFAULT_SOFT_THRESHOLD) -> str:
    """自测结果排成能直接贴给人看的一段文本。"""
    report = selftest(soft_threshold=soft_threshold)
    words = report["words"]
    lines = [f"负面过滤自测：{report['passed']}/{report['total']} 通过"
             f"｜词表 硬 {words['hard']} + 软 {words['soft']} = {words['total']}"
             f"（另有英文 {words['english']}）"]
    for row in report["cases"]:
        flag = "PASS" if row["ok"] else "FAIL"
        verdict = "放过" if row["got"] else "拦下"
        detail = f"｜{row['reason']}" if row["reason"] else "｜（干净）"
        lines.append(f"  [{flag}] {row['name']}：期望{'放过' if row['expect'] else '拦下'}"
                     f"→ 实际{verdict}{detail}")
    return "\n".join(lines)
