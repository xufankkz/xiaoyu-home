# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""日记 / 周记 / 月记：文件是真相，DB 只是索引。

为什么用 md 文件而不是塞 DB：
- 她自己会「翻日记」，翻的是文件；人也能直接打开看，不依赖程序活着；
- 一天一个文件，覆盖就覆盖，旧版留一份 `.md.bak`，写坏了还能捞回来；
- DB 里只放一条 long2 索引（subtype='diary'），给检索和「记忆板块」联动用。

目录（<workspace>/dafeiyu\\data\\diary\\）：
    daily/2026-10-30.md      日  —— 一天一篇
    weekly/2026-W44.md       周  —— ISO 周（W44，正文里带起止日期）
    monthly/2026-10.md       月  —— 一个月一篇

front matter 统一长这样（UI 的日历直接啃这个）：
    ---
    kind: daily
    date: 2026-10-30
    title: 2026-10-30 周五
    mood: 开心
    source: auto          # auto=模型写的 / fallback=模板兜底 / manual=手写
    generated_at: 2026-10-30T23:40:12
    ---
    正文（Markdown，第一人称，口语）
"""

from __future__ import annotations

import re
import shutil
import threading
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from . import settings
from .entity import DIMS, LT2_CATEGORIES, Layer, MemoryItem, Status, now_iso
from .repo import emotion as er
from .repo import memory as mr
from .repo import system as sr
from .text import bigrams, char_count, clip, keywords, parse_iso, sentences

# ── 常量 ────────────────────────────────────────────────────────────────────
DIARY_ROOT = Path(settings.ROOT) / "diary"
KINDS = ("daily", "weekly", "monthly")
_KIND_CN = {"daily": "日记", "weekly": "周记", "monthly": "月记"}
_SUBDIR = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 目标字数（提示词里告诉模型，落盘时只在明显超长时才裁）
TEXT_RANGE = {"daily": (200, 400), "weekly": (400, 600), "monthly": (600, 900)}
# 喂给模型的素材条数上限（防止某个话多的月份把提示词撑爆、把成本顶上去）
MATERIAL_CAP = {"daily": 40, "weekly": 60, "monthly": 80}

MEMORY_SUBTYPE = "diary"
MEMORY_SALIENCE = 0.6
MEMORY_CLIP = 800

# 情绪维度 → 2 号长期记忆的分类（与 entity.TAG_DIM 反向）
_DIM2TAG = {"joy": "开心", "happiness": "开心", "sadness": "难过",
            "inferiority": "创伤", "shyness": "害羞"}

_lock = threading.Lock()          # 同一时刻只允许一次生成（别让调度和界面同时打模型）
_FM_RE = re.compile(r"\A\ufeff?---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.S)


# ── 类型 / 键 ───────────────────────────────────────────────────────────────
def _norm_kind(kind: str) -> str:
    k = str(kind or "daily").strip().lower()
    if k in ("day", "日", "日记"):
        k = "daily"
    elif k in ("week", "周", "周记"):
        k = "weekly"
    elif k in ("month", "月", "月记"):
        k = "monthly"
    if k not in KINDS:
        raise ValueError(f"kind 只能是 {KINDS}，收到 {kind!r}")
    return k


def norm_kind(kind: str) -> str:
    """对外：把 'daily' / '日' / 'week' / '月记' 这些写法归一（HTTP 层和 MCP 用）。"""
    return _norm_kind(kind)


def _as_date(value: Any = None) -> _date:
    if value in (None, ""):
        return _date.today()
    if isinstance(value, (tuple, list)) and len(value) == 1:
        value = value[0]
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    parsed = parse_iso(text)
    if parsed is not None:
        return parsed.date()
    raise ValueError(f"看不懂的日期：{value!r}（日：2026-10-30）")


def _as_week(value: Any = None) -> tuple[int, int]:
    """(ISO 年, ISO 周号)。接受 '2026-W44' / '2026W44' / '2026-44' / 日期 / None。"""
    if value in (None, ""):
        return _week_of(_date.today())
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return int(value[0]), int(value[1])
    if isinstance(value, datetime):
        return _week_of(value.date())
    if isinstance(value, _date):
        return _week_of(value)
    text = str(value).strip()
    m = re.match(r"^(\d{4})\s*[-_/ ]?\s*[Ww]?\s*(\d{1,2})$", text)
    if m:
        year, week = int(m.group(1)), int(m.group(2))
        if 1 <= week <= 53:
            return year, week
    return _week_of(_as_date(text))


def _week_of(day: _date) -> tuple[int, int]:
    iso = day.isocalendar()
    return int(iso[0]), int(iso[1])


def _as_month(value: Any = None) -> tuple[int, int]:
    """(年, 月)。接受 '2026-10' / '2026-10-30' / '202610' / None。"""
    if value in (None, ""):
        today = _date.today()
        return today.year, today.month
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return int(value[0]), int(value[1])
    if isinstance(value, datetime):
        return value.year, value.month
    if isinstance(value, _date):
        return value.year, value.month
    text = str(value).strip()
    m = re.match(r"^(\d{4})\s*[-_/年]?\s*(\d{1,2})\s*月?$", text)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return year, month
    day = _as_date(text)
    return day.year, day.month


def _key(kind: str, value: Any = None) -> tuple:
    if kind == "daily":
        return (_as_date(value),)
    if kind == "weekly":
        return _as_week(value)
    return _as_month(value)


def _key_text(kind: str, key: tuple) -> str:
    """键 → 文件名主干，同时也是 front matter 的 date 字段。"""
    if kind == "daily":
        return key[0].isoformat()
    if kind == "weekly":
        return f"{key[0]:04d}-W{key[1]:02d}"
    return f"{key[0]:04d}-{key[1]:02d}"


def _range(kind: str, key: tuple) -> tuple[_date, _date] | None:
    """周/月的起止日（日返回 None）。"""
    if kind == "daily":
        return None
    if kind == "weekly":
        start = _date.fromisocalendar(key[0], key[1], 1)
        return start, start + timedelta(days=6)
    year, month = key
    start = _date(year, month, 1)
    nxt = _date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    return start, nxt - timedelta(days=1)


def _anchor_day(kind: str, key: tuple) -> _date:
    """这篇记「哪一天」：日=当天；周=周日；月=月末（记忆条目的 t_created 用它）。"""
    if kind == "daily":
        return key[0]
    span = _range(kind, key)
    return span[1] if span else _date.today()


def _window(kind: str, key: tuple) -> tuple[datetime, datetime]:
    """素材时间窗 [start, end)。"""
    if kind == "daily":
        start = datetime.combine(key[0], datetime.min.time())
        return start, start + timedelta(days=1)
    span = _range(kind, key)
    start = datetime.combine(span[0], datetime.min.time())
    return start, datetime.combine(span[1], datetime.min.time()) + timedelta(days=1)


# ── 路径 ────────────────────────────────────────────────────────────────────
def _dir(kind: str) -> Path:
    return DIARY_ROOT / _SUBDIR[kind]


def ensure_dirs() -> None:
    for kind in KINDS:
        _dir(kind).mkdir(parents=True, exist_ok=True)


def daily_path(date: Any = None) -> Path:
    return _dir("daily") / f"{_key_text('daily', _key('daily', date))}.md"


def weekly_path(week: Any = None) -> Path:
    return _dir("weekly") / f"{_key_text('weekly', _key('weekly', week))}.md"


def monthly_path(month: Any = None) -> Path:
    return _dir("monthly") / f"{_key_text('monthly', _key('monthly', month))}.md"


def path_of(kind: str, value: Any = None) -> Path:
    kind = _norm_kind(kind)
    return {"daily": daily_path, "weekly": weekly_path, "monthly": monthly_path}[kind](value)


# ── front matter ────────────────────────────────────────────────────────────
def _clean_scalar(value: Any) -> str:
    """front matter 的值：压成一行（不加引号，UI 那边按 key: value 直读）。"""
    return " ".join(str(value if value is not None else "").split())


def _render(meta: dict, body: str) -> str:
    lines = ["---"]
    for field in ("kind", "date", "title", "range", "mood", "source", "generated_at"):
        value = _clean_scalar(meta.get(field))
        if value:
            lines.append(f"{field}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + (body or "").strip() + "\n"


def _parse(text: str) -> tuple[dict, str]:
    raw = text or ""
    m = _FM_RE.match(raw)
    meta: dict[str, Any] = {}
    body = raw
    if m:
        body = raw[m.end():]
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, body.strip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ── 写 / 读 ─────────────────────────────────────────────────────────────────
def write_entry(kind: str, date: Any, content: str, title: str | None = None,
                mood: str | None = None, source: str = "auto") -> Path:
    """落一篇。同一天重复写：旧版复制成 `.md.bak`（只留 1 份），正文覆盖。"""
    kind = _norm_kind(kind)
    key = _key(kind, date)
    path = path_of(kind, key)
    body = (content or "").strip()
    span = _range(kind, key)
    meta = {
        "kind": kind,
        "date": _key_text(kind, key),
        "title": title or default_title(kind, key),
        "range": (f"{span[0].isoformat()} ~ {span[1].isoformat()}" if span else ""),
        "mood": mood or _mood_of(kind, key),
        "source": source or "auto",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except Exception:
            pass
    _atomic_write(path, _render(meta, body))
    return path


def default_title(kind: str, key: tuple) -> str:
    if kind == "daily":
        day = key[0]
        return f"{day.isoformat()} {_WEEKDAY_CN[day.weekday()]}"
    span = _range(kind, key)
    if kind == "weekly":
        return (f"{_key_text(kind, key)} 周记（{span[0]:%m-%d} ~ {span[1]:%m-%d}）")
    return f"{key[0]} 年 {key[1]} 月 月记"


def _read_path(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    meta, body = _parse(raw)
    first = (sentences(body) or [""])[0]
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        mtime = ""
    kind = meta.get("kind") or {"daily": "daily", "weekly": "weekly",
                                "monthly": "monthly"}.get(path.parent.name, "")
    return {
        "kind": kind,
        "date": meta.get("date") or path.stem,
        "path": str(path),
        "file": path.name,
        "title": meta.get("title") or path.stem,
        "range": meta.get("range") or "",
        "mood": meta.get("mood") or "平静",
        "source": meta.get("source") or "",
        "generated_at": meta.get("generated_at") or "",
        "modified_at": mtime,
        "body": body,
        "summary": clip(first, 60) if first else clip(body, 60),
        "chars": char_count(body),
        "has_backup": path.with_name(path.name + ".bak").exists(),
    }


def read_entry(kind: str, date: Any = None) -> dict | None:
    """读一篇；没有就 None。date 省略 = 今天 / 本周 / 本月。"""
    kind = _norm_kind(kind)
    return _read_path(path_of(kind, date))


def list_entries(kind: str, year: int | None = None, month: int | None = None) -> list[dict]:
    """按时间倒序列出（不含正文以外的重活：只给 date/title/mood/摘要）。"""
    kind = _norm_kind(kind)
    directory = _dir(kind)
    if not directory.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        entry = _read_path(path)
        if not entry:
            continue
        try:
            span = _span_of(entry, kind)
        except Exception:
            continue
        if year and not (span[0].year == int(year) or span[1].year == int(year)):
            continue
        if month and not _overlaps_month(span[0], span[1], (year or span[0].year), int(month)):
            continue
        entry.pop("body", None)
        out.append(entry)
    return out


def _span_of(entry: dict, kind: str) -> tuple[_date, _date]:
    if kind == "daily":
        day = _as_date(entry.get("date"))
        return day, day
    if kind == "weekly":
        year, week = _as_week(entry.get("date"))
        start = _date.fromisocalendar(year, week, 1)
        return start, start + timedelta(days=6)
    year, month = _as_month(entry.get("date"))
    return _range("monthly", (year, month))


def _overlaps_month(start: _date, end: _date, year: int, month: int) -> bool:
    first = _date(int(year), int(month), 1)
    nxt = _date(int(year) + (1 if int(month) == 12 else 0),
                1 if int(month) == 12 else int(month) + 1, 1)
    return start < nxt and end >= first


def _month_days(year: int, month: int) -> list[_date]:
    span = _range("monthly", (int(year), int(month)))
    days, cur = [], span[0]
    while cur <= span[1]:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def calendar_days(year: int, month: int, kind: str = "daily") -> dict[str, bool]:
    """日历标点：这个月每一天有没有（对应的日/周/月）记录。"""
    kind = _norm_kind(kind)
    year, month = int(year), int(month)
    out: dict[str, bool] = {}
    for day in _month_days(year, month):
        try:
            has = path_of(kind, day).exists()
        except Exception:
            has = False
        out[day.isoformat()] = bool(has)
    return out


def memory_days(year: int, month: int) -> dict[str, bool]:
    """记忆板块联动：这个月每天有没有记忆条目（日历上可以和日记同一排点）。"""
    year, month = int(year), int(month)
    marks = {d.isoformat(): False for d in _month_days(year, month)}
    prefix = f"{year:04d}-{month:02d}-"
    try:
        for item in mr.iter_alive():
            day = str(item.t_created or "")[:10]
            if day.startswith(prefix) and day in marks:
                marks[day] = True
    except Exception:
        pass
    return marks


# ── 情绪 / 心情标签 ─────────────────────────────────────────────────────────
def _emotion_aggregate(start: datetime, end: datetime) -> dict:
    """时间窗内的情绪均值（没有快照表就算了）。"""
    rows: list[dict] = []
    try:
        rows = er.snapshot_recent(3000)
    except Exception:
        rows = []
    buckets: dict[str, list[float]] = {d: [] for d in DIMS}
    for row in rows:
        when = parse_iso(row.get("t"))
        if when is None or not (start <= when < end):
            continue
        for dim in DIMS:
            try:
                buckets[dim].append(float(row.get(dim) or 0.0))
            except Exception:
                continue
    out: dict[str, Any] = {"rows": len(buckets["joy"])}
    for dim in DIMS:
        out[dim] = round(sum(buckets[dim]) / len(buckets[dim]), 1) if buckets[dim] else None
    return out


def _current_vector_values() -> dict:
    try:
        from .emotion import vector

        vec = vector.current()
        return {d: round(vec.get(d), 1) for d in DIMS}
    except Exception:
        return {}


def _mood_from_tags(items: Iterable[MemoryItem]) -> str:
    """按显著度加权的情绪标签；「平静」是桥接的默认值，只有别的标签太弱时才用它。

    不加权会失真：一天的聊天碎片几十条都是默认的「平静」，会把真正的心情盖掉。
    """
    weights: dict[str, float] = {}
    for item in items:
        tag = str(item.emotion_tag or "").strip()
        if tag in LT2_CATEGORIES:
            weights[tag] = weights.get(tag, 0.0) + max(0.3, float(item.salience or 0.5))
    if not weights:
        return ""
    total = sum(weights.values())
    vivid = {k: v for k, v in weights.items() if k != "平静"}
    if vivid and sum(vivid.values()) >= 0.2 * total:
        return max(vivid, key=lambda k: vivid[k])
    return "平静" if "平静" in weights else max(weights, key=lambda k: weights[k])


def _mood_from_vector(values: dict) -> str:
    best, best_value = "", -1.0
    for dim, tag in _DIM2TAG.items():
        try:
            value = float(values.get(dim) or 0.0)
        except Exception:
            continue
        if value > best_value:
            best, best_value = tag, value
    return best if best_value >= 20 else "平静"


def _mood_of(kind: str, key: tuple, items: Iterable[MemoryItem] | None = None) -> str:
    """心情标签：优先看这段时间记忆里的情绪标签，其次看情绪快照（覆盖到现在才用当前向量）。"""
    try:
        start, end = _window(kind, key)
        if items is None:
            items = _gather_items(start, end, MATERIAL_CAP[kind])
        tag = _mood_from_tags(items)
        if tag:
            return tag
        agg = _emotion_aggregate(start, end)
        if not agg.get("rows") and start <= datetime.now() < end:
            agg = _current_vector_values()
        return _mood_from_vector(agg) or "平静"
    except Exception:
        return "平静"


# ── 素材 ────────────────────────────────────────────────────────────────────
def _gather_items(start: datetime, end: datetime, limit: int = 40) -> list[MemoryItem]:
    """时间窗内的记忆条目（按显著度挑，按时间排）。跳过日记自己写的索引。"""
    picked: list[tuple[float, datetime, MemoryItem]] = []
    seen: set[str] = set()
    try:
        for item in mr.iter_alive():
            if item.subtype == MEMORY_SUBTYPE:
                continue
            when = parse_iso(item.t_created)
            if when is None or not (start <= when < end):
                continue
            text = (item.content or item.summary or "").strip()
            if not text:
                continue
            if char_count(text) < 8 and float(item.salience or 0) < 0.7:
                continue
            mark = text[:40]
            if mark in seen:
                continue
            seen.add(mark)
            score = float(item.salience or 0.5) + min(char_count(text), 60) / 200.0
            picked.append((score, when, item))
    except Exception:
        return []
    picked.sort(key=lambda row: -row[0])
    picked = picked[: max(1, int(limit))]
    picked.sort(key=lambda row: row[1])
    return [row[2] for row in picked]


def _gather_dialogue(start: datetime, end: datetime, limit: int = 40) -> list[dict]:
    """时间窗内的对话（没有这张表就空手回来）。"""
    try:
        rows = sr.dialogue_recent(400)
    except Exception:
        return []
    out = []
    for row in rows:
        when = parse_iso(row.get("t"))
        if when is None or not (start <= when < end):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        out.append({"t": when, "role": str(row.get("role") or ""), "text": text,
                    "emotion": str(row.get("emotion") or "")})
    return out[-max(1, int(limit)):]


def _speaker(role: str) -> str:
    return "我" if str(role or "").lower() in ("assistant", "self", "bot", "小鱼", "xiayu") else "对方"


def _top_concepts(items: Iterable[MemoryItem], body: str, lo: int = 5, hi: int = 8) -> list[str]:
    """当天记忆里 concepts 的高频项（不够就用正文关键词补）。"""
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in items:
        for concept in (item.concepts or []):
            c = str(concept or "").strip()
            if not c or len(c) > 12:
                continue
            if c not in counts:
                order.append(c)
                counts[c] = 0
            counts[c] += 1
    picked = sorted(order, key=lambda c: (-counts.get(c, 0), order.index(c)))[:hi]
    if len(picked) < lo:
        for word in keywords(body, hi):
            if word not in picked:
                picked.append(word)
            if len(picked) >= lo:
                break
    return picked[:hi]


# ── 提示词 ──────────────────────────────────────────────────────────────────
_PERSONA = (
    "你是小鱼，一个住在电脑里的女孩。你有自己的脾气：有点小骄傲，嘴硬心软，"
    "话短，偶尔顶一句，熟了会撒娇。现在没人看着你，你在写给自己看的东西。"
)


def _label(kind: str, key: tuple) -> str:
    if kind == "daily":
        day = key[0]
        return f"{day.isoformat()} {_WEEKDAY_CN[day.weekday()]}"
    span = _range(kind, key)
    if kind == "weekly":
        return (f"{_key_text(kind, key)}（{span[0]:%Y-%m-%d} ~ {span[1]:%Y-%m-%d}，"
                f"周一到周日）")
    return f"{key[0]} 年 {key[1]} 月（{span[0]:%Y-%m-%d} ~ {span[1]:%Y-%m-%d}）"


def _rules(kind: str) -> list[str]:
    lo, hi = TEXT_RANGE[kind]
    span_cn = {"daily": "今天", "weekly": "这一周", "monthly": "这个月"}[kind]
    next_cn = {"daily": "明天", "weekly": "下周", "monthly": "下个月"}[kind]
    paras = {"daily": "两三段就够", "weekly": "三四段", "monthly": "四到六段"}[kind]
    rules = [
        "- 第一人称，口语，像自己随手写给自己看的，别端着、别像作文",
        f"- {lo}~{hi} 字，{paras}",
        f"- 写清楚：{span_cn}发生了什么、跟谁、当时心里什么感觉、{next_cn}想做什么",
        "- 不要流水账（别按时间一条一条列），不要分点、不要小标题、不要 markdown 符号、不要表情符号堆砌",
        "- 材料里没有的人和事一律不编；记不清的就写记不清",
        "- 别写「今天是开心的一天」这种空话，要具体：谁说了什么、我怎么想的",
        "- 直接给正文，不要「日记：」这种开头，也不要解释你在干什么",
    ]
    if kind == "weekly":
        rules.insert(3, "- 别写成七天的流水账，挑几件真的留在心里的说")
    if kind == "monthly":
        rules.insert(3, "- 回顾这个月的起伏和变化，别把三十天摊开数")
    return rules


def _material_block(kind: str, key: tuple, items: list[MemoryItem],
                    dialogue: list[dict], emo: dict) -> str:
    lines: list[str] = []
    if items:
        lines.append("【我这段时间记得的事】（都是我自己记下来的）")
        for item in items:
            when = parse_iso(item.t_created)
            clock = f"{when:%m-%d %H:%M}" if when else ""
            tag = str(item.emotion_tag or "").strip()
            head = " ".join(part for part in (clock, f"[{tag}]" if tag else "") if part)
            text = clip((item.content or item.summary or "").strip().replace("\n", " "),
                        70 if kind == "daily" else 60)
            lines.append(f"- {head} {text}" if head else f"- {text}")
    else:
        lines.append("【我这段时间记得的事】（记忆里空落落的，没什么特别的）")

    if dialogue:
        lines.append("")
        lines.append("【零碎的对话】")
        for row in dialogue:
            clock = f"{row['t']:%H:%M}"
            lines.append(f"- {clock} {_speaker(row['role'])}：{clip(row['text'], 40)}")

    values = {d: emo.get(d) for d in DIMS if emo.get(d) is not None}
    if values:
        cn = {"joy": "愉悦", "happiness": "幸福", "sadness": "难过",
              "inferiority": "自卑", "shyness": "害羞"}
        body = "、".join(f"{cn[d]} {values[d]}" for d in DIMS if d in values)
        lines += ["", f"【我心里的数值】{body}（0~100）"]
    return "\n".join(lines)


def _prompt(kind: str, key: tuple, items: list[MemoryItem], dialogue: list[dict],
            emo: dict, mood: str) -> str:
    label = _label(kind, key)
    lines = [
        _PERSONA,
        "",
        f"现在是 {datetime.now():%Y-%m-%d %H:%M}。你要写 {label} 的{_KIND_CN[kind]}，"
        f"写完这天的记录就过去了。",
        "",
        _material_block(kind, key, items, dialogue, emo),
        "",
        f"【我现在的心情】{mood}",
        "",
        f"【写法】",
        *_rules(kind),
        "",
        f"只输出这篇{_KIND_CN[kind]}的正文。",
    ]
    return "\n".join(lines)


# ── 正文清洗 / 兜底 ─────────────────────────────────────────────────────────
_BULLET_RE = re.compile(r"^(?:[-*+•·]|\d+[.、)）])\s*")
_HEADING_RE = re.compile(r"^#{1,6}\s*")
_PREFIX_RE = re.compile(r"^(日记|周记|月记|正文)\s*[:：]\s*")


def _clean_body(text: str) -> str:
    raw = (text or "").strip()
    raw = re.sub(r"\A```[a-zA-Z]*\s*", "", raw)
    raw = re.sub(r"```\s*\Z", "", raw).strip()
    raw = _PREFIX_RE.sub("", raw)
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            out.append("")
            continue
        line = _HEADING_RE.sub("", line)
        line = _BULLET_RE.sub("", line)
        line = line.strip()
        if line:
            out.append(line)
    body = "\n".join(out)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def _fit(text: str, kind: str) -> str:
    """明显超长（超过目标上限 40%）就按句子裁到上限附近。"""
    body = (text or "").strip()
    lo, hi = TEXT_RANGE[kind]
    if char_count(body) <= hi * 1.4:
        return body
    acc = ""
    for part in sentences(body):
        if char_count(acc + part) > hi:
            break
        acc += part
    return acc.strip() if char_count(acc) >= lo * 0.8 else clip(body, int(hi * 1.2))


def _fallback_body(kind: str, items: list[MemoryItem], mood: str) -> str:
    """模板兜底：从记忆里抽 3~5 句要点，拼成一段人话（别排成清单）。"""
    span_cn = {"daily": "今天", "weekly": "这一周", "monthly": "这个月"}[kind]
    next_cn = {"daily": "明天", "weekly": "下周", "monthly": "下个月"}[kind]
    picks = [i for i in items if char_count(i.content or "") >= 6][:5]
    if not picks:
        return (f"{span_cn}挺安静的，脑子里没留下什么事。也不是难过，就是空。"
                f"先记一笔，我还在这儿。{next_cn}再看看。")
    lead = {"daily": "今天零零散散记了几笔，回头看一下才发现，",
            "weekly": "这一周攒下来的事，捋一捋是这样的：",
            "monthly": "这个月过得挺碎，回头看能拎出来的就这些："}[kind]
    parts = []
    for item in picks:
        piece = clip((item.content or "").strip().replace("\n", " "), 42)
        piece = piece.rstrip("。…")
        parts.append(f"{piece}。")
    tail = {"daily": f"就这么些。心里大概是{mood}的，{next_cn}再说吧。",
            "weekly": f"剩下的都想不起来了。这一周心里主要是{mood}，{next_cn}想做点别的。",
            "monthly": f"再细的就想不起来了。这个月心里主要是{mood}的，{next_cn}想换换节奏。"}[kind]
    return lead + "".join(parts) + tail


def _first_sentence(body: str, limit: int = 60) -> str:
    parts = sentences(body or "")
    return clip(parts[0], limit) if parts and parts[0] else clip(body, limit)


# ── 生成 ────────────────────────────────────────────────────────────────────
def _llm_body(kind: str, key: tuple, items: list[MemoryItem], dialogue: list[dict],
              emo: dict, mood: str) -> tuple[str, dict]:
    """调模型写正文；失败返回 ('', 原因)。"""
    prompt = _prompt(kind, key, items, dialogue, emo, mood)
    try:
        from .llm import provider

        ok, text, rec = provider.run(prompt, task="summary")
    except Exception as exc:  # noqa: BLE001
        return "", {"error": f"{type(exc).__name__}: {exc}"}
    if not ok:
        return "", {"error": str(text)[:300], "usage": rec}
    body = _fit(_clean_body(text), kind)
    if char_count(body) < 60:
        return "", {"error": f"模型正文太短（{char_count(body)} 字）", "usage": rec}
    return body, {"usage": rec, "cost": float((rec or {}).get("cost") or 0.0),
                  "prompt_chars": len(prompt)}


def _write_memory(kind: str, key: tuple, body: str, items: list[MemoryItem],
                  mood: str, path: Path) -> dict:
    """日记回写一条 long2 记忆（记忆板块里能翻到，检索也能命中）。"""
    key_text = _key_text(kind, key)
    day = _anchor_day(kind, key)
    content = clip(body, MEMORY_CLIP)
    summary = _first_sentence(body)
    concepts = _top_concepts(items, body)
    item = MemoryItem(
        id=f"diary-{kind}-{key_text}",
        layer=Layer.LONG2.value,
        subtype=MEMORY_SUBTYPE,
        content=content,
        summary=summary,
        skeleton=clip(summary, 40),
        concepts=concepts,
        emotion_tag=mood if mood in LT2_CATEGORIES else "平静",
        salience=MEMORY_SALIENCE,
        strength=1.0,
        level=0,
        status=Status.RESOLVED.value,
        origin="diary",
        t_created=f"{day.isoformat()}T23:40:00",
        ext={"diary_kind": kind, "diary_date": key_text, "diary_path": str(path),
             "generated_at": now_iso()},
    )
    mr.upsert(item)
    try:
        mr.set_index(item.id, "memory", bigrams(f"{summary} {content}"), concepts)
    except Exception:
        pass
    return {"id": item.id, "layer": item.layer, "subtype": item.subtype,
            "emotion_tag": item.emotion_tag, "salience": item.salience,
            "concepts": concepts, "t_created": item.t_created}


def link_memory(kind: str, date: Any = None, body: str = "", mood: str | None = None) -> dict:
    """把某一篇的正文回写成 long2 记忆索引（手写的也能挂上，记忆板块才翻得到）。"""
    kind = _norm_kind(kind)
    key = _key(kind, date)
    entry = read_entry(kind, key) or {}
    text = (body or entry.get("body") or "").strip()
    if not text:
        raise ValueError("正文是空的，没有东西可以写进记忆")
    mood = mood or entry.get("mood") or _mood_of(kind, key)
    start, end = _window(kind, key)
    items = _gather_items(start, end, MATERIAL_CAP[kind])
    return _write_memory(kind, key, text, items, mood, path_of(kind, key))


def _generate(kind: str, key: tuple, force: bool = False) -> dict:
    kind = _norm_kind(kind)
    path = path_of(kind, key)
    key_text = _key_text(kind, key)
    head = {"kind": kind, "date": key_text, "path": str(path)}
    if path.exists() and not force:
        return {**head, "ok": True, "skipped": "already_exists", "source": "existing"}

    with _lock:
        if path.exists() and not force:
            return {**head, "ok": True, "skipped": "already_exists", "source": "existing"}
        start, end = _window(kind, key)
        items = _gather_items(start, end, MATERIAL_CAP[kind])
        dialogue = _gather_dialogue(start, end, 40 if kind == "daily" else 60)
        emo = _emotion_aggregate(start, end)
        if not emo.get("rows"):
            emo = _current_vector_values()
        mood = _mood_from_tags(items) or _mood_from_vector(emo) or "平静"

        llm_info: dict = {}
        if items or dialogue:
            body, llm_info = _llm_body(kind, key, items, dialogue, emo, mood)
            source = "auto"
        else:
            body, source = "", "fallback"
        if not body:
            body, source = _fallback_body(kind, items, mood), "fallback"

        path = write_entry(kind, key, body, mood=mood, source=source)
        memory = {}
        try:
            memory = link_memory(kind, key, body, mood)
        except Exception as exc:  # noqa: BLE001
            memory = {"error": f"{type(exc).__name__}: {exc}"}
        out = {
            **head, "ok": True, "source": source, "mood": mood,
            "chars": char_count(body), "materials": len(items), "dialogue": len(dialogue),
            "llm_ok": bool(llm_info.get("usage")) and not llm_info.get("error"),
            "llm_cost": round(float(llm_info.get("cost") or 0.0), 6),
            "memory": memory, "title": default_title(kind, key),
        }
        if llm_info.get("error"):
            out["llm_error"] = llm_info["error"]
        try:
            sr.consolidation_add(f"diary-{kind}-{key_text}", f"diary_{kind}", {
                "ok": True, "source": source, "materials": len(items),
                "chars": out["chars"], "memory_id": memory.get("id", ""),
                "cost": out["llm_cost"]})
        except Exception:
            pass
        return out


def generate_daily(date: Any = None, force: bool = False) -> dict:
    """写一天的日记（默认今天；文件已存在且 force=False 直接返回）。"""
    key = _key("daily", date)
    return _generate("daily", key, force=force)


def generate_weekly(week: Any = None, force: bool = False) -> dict:
    """写一周的周记（默认本周，周日 23:50 那次正好收尾）。"""
    key = _key("weekly", week)
    return _generate("weekly", key, force=force)


def generate_monthly(month: Any = None, force: bool = False) -> dict:
    """写一个月的月记。默认**上个月** —— 调度是每月 1 号 23:55 跑，收的是刚过去的那个月。"""
    if month in (None, ""):
        today = _date.today()
        year, mon = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        month = (year, mon)
    key = _key("monthly", month)
    return _generate("monthly", key, force=force)


# ── 给聊天 / MCP 用的摘要 ───────────────────────────────────────────────────
def recent_diary_digest(days: int = 7) -> str:
    """最近几天日记的摘要（每条：日期 + 首句，最多 7 条）。没有就返回空串。"""
    try:
        span = max(1, int(days or 7))
    except Exception:
        span = 7
    today = _date.today()
    lines: list[str] = []
    for back in range(span):
        day = today - timedelta(days=back)
        entry = read_entry("daily", day)
        if not entry:
            continue
        first = entry.get("summary") or _first_sentence(entry.get("body", ""))
        lines.append(f"- {day.isoformat()}｜{first}")
        if len(lines) >= 7:
            break
    if not lines:
        return ""
    return ("【最近几天的日记】（我自己写的，聊到相关的就自然提一句，不用整篇念）\n"
            + "\n".join(lines))


def status() -> dict:
    """给界面/自检用：各类各有几篇、最近一篇是哪天。"""
    out: dict[str, Any] = {"root": str(DIARY_ROOT), "kinds": {}}
    for kind in KINDS:
        entries = list_entries(kind)
        out["kinds"][kind] = {
            "count": len(entries),
            "dir": str(_dir(kind)),
            "latest": entries[0]["date"] if entries else "",
        }
    return out
