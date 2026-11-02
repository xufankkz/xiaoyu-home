# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""自主上网学习（她没事的时候自己找东西看，看懂了写进知识库）。

闭环（每一步都有对应函数，出问题能一眼定位）：

  选题      pick_topic() / build_plan()     按好奇心引擎的领域紧迫度 + 知识缺口挑一个
                                            「没学过的」主题，学过的话题 7 天内不再挑。
  抓取      fetch_html()                    本机 Edge **无头**打开白名单站点（不是抖音那条
          → extract_article()               有头链路），正文用纯函数 HTML→文本抽出来。
          → extract_lines()                 列表页（热搜）按行抽。
          → 多源兜底                        按 priority 一个个试：前一个失败（404/提示页/
                                            反爬/正文太短）就顺延，拿到 1 篇有效正文就收工；
                                            只放服务端直出 HTML/XML 的源，JS 渲染的不进白名单。
  过滤      filter_article() / filter_lines() 负面内容硬闸：命中即丢，不送模型、不写记忆，
          → webfilter.block_negative()      记 blocked_count 与原因（落进运行状态 + 报告）。
  总结      summarize() → parse_points()    交给模型总结成 2~4 条「要点|说明」；
                                            模型不可用时退化成抽取式（报告里标明 extractive）。
  写记忆    store_points()                  复用 core.cognition.metacognition.learn() 写
                                            knowledge_item（自带倒排索引，聊天时能被检索召回），
                                            不另造一套存储。
  报告      _write_report()                 复用学习系统的报告目录与 LearningIntent 表。
  调度      tick() / run_round()            tick 过闸（开关/配额/间隔/疲劳/联网预算）后跑一轮；
                                            已挂到 learning.executor.scheduled_run（凌晨学习窗口）。

三条硬约束（用户明确要求 + 本项目既有红线）：
  1. 负面内容**必须**挡在模型之前：`webfilter.block_negative` 命中即丢；
  2. 白名单 + 配额：站点白名单、每天最多 N 轮、每轮最多 M 条，配置在
     `data/config/webstudy.json`（配额也可写在 `data/config/learning.json` 的
     `webstudy_quota` 段里覆盖）；
  3. 不弹窗、不打扰：自主浏览一律无头；只有 `--once` 手动跑时也仍然是无头。
"""

from __future__ import annotations

import json
import random
import re
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlparse

from .. import settings
from ..text import char_count, clip, keywords, parse_iso, sentences
from . import webfilter

# ── 默认配置（webstudy.json 没写的键都用这里的值）────────────────────────────
# 站点选择原则（2026-10-30 真机实测后的结论）：
#   · 只放**服务端直出 HTML/XML** 的源：JS 渲染 / 需要登录 / 反爬验证的一律不要；
#   · priority 小的先试，失败就顺着往下试（多源兜底，见 build_plan/run_round）；
#   · 热搜这类列表源放最后：主题类源全挂时，才用「随便看看大家在聊什么」兜底。
# 实测数据（headless msedge 156）：维基 7801 字、必应 RSS 3370 字、必应网页 1179 字、
# 百度搜索 3906 字、360搜索 5630 字、搜狗 2461 字、微博热搜 2478 字、百度热搜 7495 字；
# 百度百科 403（安全验证）、百度知道空页、豆瓣搜索报错 103、少数派搜索 404 —— 都已剔除。
DEFAULT_SITES: list[dict] = [
    # Tavily 搜索 API：不爬网页、直接给结构化结果（标题+摘要+链接），最稳最快，放第一位。
    # 需要 data/config/webstudy.json → tavily.api_key（或环境变量 TAVILY_API_KEY）。
    {"id": "tavily", "name": "Tavily搜索", "kind": "search", "enabled": True, "priority": 5,
     "url": "", "max_results": 5, "max_chars": 4000},
    {"id": "wiki_zh", "name": "维基百科", "kind": "article", "enabled": True, "priority": 10,
     "url": "https://zh.wikipedia.org/wiki/{q}", "max_chars": 6000},
    {"id": "bing_rss", "name": "必应搜索", "kind": "article", "enabled": True, "priority": 20,
     "url": "https://cn.bing.com/search?q={q}&format=rss", "max_chars": 4000},
    {"id": "bing_cn", "name": "必应网页", "kind": "article", "enabled": True, "priority": 30,
     "url": "https://cn.bing.com/search?q={q}&setlang=zh-CN", "max_chars": 4000},
    {"id": "baidu_search", "name": "百度搜索", "kind": "article", "enabled": True, "priority": 40,
     "url": "https://www.baidu.com/s?wd={q}", "max_chars": 4000},
    {"id": "so360", "name": "360搜索", "kind": "article", "enabled": True, "priority": 50,
     "url": "https://www.so.com/s?q={q}", "max_chars": 4000},
    {"id": "sogou", "name": "搜狗搜索", "kind": "article", "enabled": True, "priority": 60,
     "url": "https://www.sogou.com/web?query={q}", "max_chars": 4000},
    {"id": "moegirl_rest", "name": "萌娘百科", "kind": "article", "enabled": True, "priority": 70,
     "url": "https://zh.moegirl.org.cn/rest.php/v1/page/{q}/html", "max_chars": 6000},
    {"id": "weibo_hot", "name": "微博热搜", "kind": "list", "enabled": True, "priority": 90,
     "url": "https://s.weibo.com/top/summary", "max_chars": 3000},
    {"id": "baidu_hot", "name": "百度热搜", "kind": "list", "enabled": True, "priority": 91,
     "url": "https://top.baidu.com/board?tab=realtime", "max_chars": 3000},
]

DEFAULTS: dict = {
    "enabled": True,
    "auto_run": True,
    "browser": {
        "channel": "msedge",
        "headless": True,
        "timeout_ms": 25000,
        "wait_after_ms": 1500,
        "block_media": True,
        "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"),
    },
    "quota": {
        "rounds_per_day": 3,
        "items_per_round": 5,
        "points_per_page": 4,
        "pages_per_round": 1,
        "max_attempts_per_round": 6,
        "min_interval_minutes": 120,
        "max_chars": 6000,
        "min_chars": 120,
    },
    "topics": {
        "avoid_repeat_days": 7,
        "max_candidates": 24,
        "seed": ["生活常识", "情绪理解", "人际边界", "语言表达"],
        # 这些是「她自己的资料分类」，不是能上网学的概念：命中就跳过
        "exclude": ["性格设定", "朋友档案", "情绪记忆", "长期记忆", "短期记忆", "瞬时记忆",
                    "语言记忆", "思维链", "工作记忆"],
    },
    "llm": {"task": "summary", "timeout_seconds": 120, "extractive_fallback": True,
            "http_fallback": True},
    # Tavily 搜索 API（api.tavily.com/search）：结构化搜索结果，比爬 HTML 稳得多。
    # api_key 也可以放环境变量 TAVILY_API_KEY（配置里留空就走环境变量）。
    "tavily": {"enabled": True, "api_key": "", "search_depth": "basic",
               "max_results": 5, "include_raw_content": False, "timeout_seconds": 30},
    "filter": {"soft_threshold": webfilter.DEFAULT_SOFT_THRESHOLD,
               "max_blocked_ratio": 0.5,
               "blocklist_file": "", "extra_words": []},
    "sites": DEFAULT_SITES,
    # 这些是「页面没有这个词条」的提示语：命中就当这一源失败，换下一个（别把提示页当正文学）
    "no_content_markers": [
        "还没有与上述标题相同的条目", "还没有与这个标题相同的条目", "尚未创建该条目",
        "页面不存在", "指定的页面不存在", "您访问的页面不存在", "该条目不存在",
        "没有找到该页面", "找不到该页面", "rest-nonexistent-title", "404 Not Found",
        "页面已被删除", "内容已被删除",
    ],
    "deny_domains": ["accounts.google.com", "login.live.com", "passport.baidu.com",
                     "pay.qq.com", "mail.qq.com", "web.qq.com"],
    "state_file": "webstudy_state.json",
}

_CACHE: dict = {"key": None, "data": {}}
_FILTER_KEY: dict = {"key": None}

# 中转站的 WAF 会挡 urllib 默认 UA（403 / Cloudflare error code: 1010），
# 所以 HTTP 直连一律带这个 UA；无头浏览器也用同一个（见 browser.user_agent）。
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0")


def _log(log, message: str) -> None:
    if callable(log):
        log(str(message))


# ── 配置 ────────────────────────────────────────────────────────────────────
def _config_files() -> list[Path]:
    return [settings.CONFIG / "webstudy.json", settings.CONFIG / "learning.json"]


def _mtime_key() -> tuple:
    key = []
    for path in _config_files():
        try:
            key.append(path.stat().st_mtime)
        except Exception:
            key.append(0.0)
    return tuple(key)


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif value is not None:
            out[key] = value
    return out


def cfg(force: bool = False) -> dict:
    """读 webstudy.json（缺的键用 DEFAULTS），再叠 learning.json 的覆盖段。

    learning.json 里可写：
        {"webstudy_quota": {...}, "webstudy_topics": {...}}
    覆盖的是同名子字典里的键（比如只把 rounds_per_day 改成 5）。
    """
    key = _mtime_key()
    if not force and _CACHE.get("key") == key:
        return _CACHE["data"]
    conf = _merge(DEFAULTS, settings.read_json(settings.CONFIG / "webstudy.json", {}) or {})
    overlay = settings.read_json(settings.CONFIG / "learning.json", {}) or {}
    if isinstance(overlay.get("webstudy_quota"), dict):
        conf["quota"] = _merge(conf["quota"], overlay["webstudy_quota"])
    if isinstance(overlay.get("webstudy_topics"), dict):
        conf["topics"] = _merge(conf["topics"], overlay["webstudy_topics"])
    _CACHE.update({"key": key, "data": conf})
    _apply_filter_words(conf)
    return conf


def _apply_filter_words(conf: dict) -> None:
    """把配置里的追加词 / 外部词表接到过滤闸上（同一份配置只接一次）。"""
    filt = conf.get("filter") or {}
    key = json.dumps([filt.get("blocklist_file"), filt.get("extra_words")], ensure_ascii=False)
    if _FILTER_KEY.get("key") == key:
        return
    _FILTER_KEY["key"] = key
    raw_path = str(filt.get("blocklist_file") or "").strip()
    if raw_path:
        target = Path(raw_path)
        if not target.is_absolute():
            target = settings.ROOT / raw_path
        webfilter.add_blocklist_file(target)
    webfilter.add_extra(filt.get("extra_words") or [])


def soft_threshold() -> int:
    try:
        return max(1, int((cfg().get("filter") or {}).get("soft_threshold",
                                                          webfilter.DEFAULT_SOFT_THRESHOLD)))
    except Exception:
        return webfilter.DEFAULT_SOFT_THRESHOLD


# ── 运行状态（每天重置的配额计数）───────────────────────────────────────────
def _state_name() -> str:
    return str(cfg().get("state_file") or "webstudy_state.json")


def _blank_state(history: list | None = None, learned_total: int = 0,
                 blocked_total: int = 0, site_cursor: int = 0, last_run: str = "",
                 last_error: str = "") -> dict:
    return {"date": date.today().isoformat(), "rounds": 0, "items": 0, "blocked": 0,
            "blocked_reasons": {}, "history": list(history or [])[-200:],
            "learned_total": int(learned_total), "blocked_total": int(blocked_total),
            "site_cursor": int(site_cursor), "last_run": last_run,
            "last_error": str(last_error or "")}


def state() -> dict:
    raw = settings.read_state(_state_name(), {})
    args = (raw.get("history"), int(raw.get("learned_total") or 0),
            int(raw.get("blocked_total") or 0), int(raw.get("site_cursor") or 0),
            str(raw.get("last_run") or ""), str(raw.get("last_error") or ""))
    if str(raw.get("date")) != date.today().isoformat():
        return _blank_state(*args)
    out = _blank_state(*args)
    out.update({k: raw[k] for k in ("rounds", "items", "blocked", "blocked_reasons")
                if k in raw and raw[k] is not None})
    return out


def save_state(data: dict) -> dict:
    settings.write_state(_state_name(), data)
    return data


def quota_left() -> dict:
    conf = cfg()
    quota = conf.get("quota") or {}
    rounds_per_day = max(0, int(quota.get("rounds_per_day", 3)))
    items_per_round = max(1, int(quota.get("items_per_round", 5)))
    st = state()
    used_rounds = int(st.get("rounds") or 0)
    used_items = int(st.get("items") or 0)
    return {"rounds": max(0, rounds_per_day - used_rounds),
            "items": max(0, rounds_per_day * items_per_round - used_items),
            "rounds_per_day": rounds_per_day, "items_per_round": items_per_round,
            "used_rounds": used_rounds, "used_items": used_items}


def gate(*, auto: bool = True, now: datetime | None = None) -> tuple[bool, str]:
    """开跑前的四道闸：开关 → 配额 → 间隔 → （自动时）联网预算 / 疲劳熔断。"""
    conf = cfg()
    if not bool(conf.get("enabled", True)):
        return False, "自主上网学习关着（config/webstudy.json → enabled）"
    if auto and not bool(conf.get("auto_run", True)):
        return False, "auto_run=false：只允许手动触发"
    left = quota_left()
    if left["rounds"] <= 0:
        return False, f"今天 {left['rounds_per_day']} 轮的配额用完了"
    if left["items"] <= 0:
        return False, f"今天 {left['rounds_per_day'] * left['items_per_round']} 条的配额用完了"
    now = now or datetime.now()
    gap = int((conf.get("quota") or {}).get("min_interval_minutes", 120) or 0)
    last = parse_iso(str(state().get("last_run") or ""))
    if last is not None and gap > 0:
        minutes = (now - last).total_seconds() / 60.0
        if minutes < gap:
            return False, f"距上次只有 {minutes:.0f} 分钟（最小间隔 {gap} 分钟）"
    if auto:
        try:
            from ..cognition import curiosity

            if not curiosity.budget_ok():
                return False, "联网预算（每小时/每天）用完了"
        except Exception:
            pass
        try:
            from .. import homeostasis

            fuse, why = homeostasis.fatigue_fuse()
            if fuse:
                return False, why or "累了，这一轮不学"
        except Exception:
            pass
    return True, ""


# ── 白名单 ──────────────────────────────────────────────────────────────────
def sites() -> list[dict]:
    """配置里的站点表（article=抓网页 / list=列表页 / search=搜索 API）。"""
    conf = cfg()
    raw = conf.get("sites") if isinstance(conf.get("sites"), list) else []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "article").strip().lower()
        if kind not in ("list", "search"):
            kind = "article"
        url = str(item.get("url") or "").strip()
        if kind != "search" and not url:      # 搜索类走 API，不需要 URL 模板
            continue
        name = str(item.get("name") or item.get("id") or url or kind)
        out.append({
            "id": str(item.get("id") or name),
            "name": name,
            "kind": kind,
            "enabled": bool(item.get("enabled", True)),
            "priority": int(item.get("priority") or 50),
            "url": url,
            "max_results": int(item.get("max_results") or 5),
            "max_chars": int(item.get("max_chars") or (conf.get("quota") or {}).get("max_chars", 6000)),
        })
    return out or [dict(site) for site in DEFAULT_SITES]


def _host(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "").lower()


def url_allowed(url: str) -> tuple[bool, str]:
    """白名单校验：只放行配置里列出的站点，登录/支付域名永远拦。"""
    parts = urlparse(str(url or ""))
    if parts.scheme not in ("http", "https"):
        return False, "不是 http(s) 网址"
    host = _host(url)
    if not host:
        return False, "解析不出域名"
    conf = cfg()
    for denied in conf.get("deny_domains") or []:
        deny = str(denied or "").strip().lower()
        if deny and (host == deny or host.endswith("." + deny)):
            return False, f"域名在黑名单：{host}"
    allowed = {_host(site["url"]) for site in sites() if site.get("enabled")}
    allowed.discard("")
    if not any(host == item or host.endswith("." + item) for item in allowed):
        return False, f"域名不在白名单：{host}"
    return True, ""


def url_for(site: dict, topic: str) -> str:
    template = str(site.get("url") or "")
    if "{q}" not in template:
        return template
    try:
        return template.replace("{q}", quote(str(topic or "")))
    except Exception:
        return template


# ── 选题：好奇心 / 知识缺口 / 种子，去重 ────────────────────────────────────
# 这些后缀是「她自己的资料分类」（档案/记忆/设定…），不是能上网学的概念
_TOPIC_BAD_SUFFIX = ("设定", "档案", "记忆", "条目", "模板", "模版", "配置", "列表",
                     "记录", "规则", "字段", "参数")


def _looks_learnable(topic: str) -> bool:
    """这个主题值不值得拿去搜/查百科？把资料分类名和整句问题挡在外面。"""
    name = str(topic or "").strip()
    if not (2 <= len(name) <= 12):
        return False
    exclude = {str(item).strip() for item in ((cfg().get("topics") or {}).get("exclude") or [])}
    if name in exclude:
        return False
    if any(name.endswith(suffix) for suffix in _TOPIC_BAD_SUFFIX):
        return False
    if any(ch in name for ch in "？?！!，,。.、；;：:～~…"):
        return False
    return True


def _topic_pool() -> list[dict]:
    pool: list[dict] = []
    seen: set[str] = set()

    def push(name: str, urgency: float, source: str) -> None:
        topic = str(name or "").strip()
        if topic in seen or not _looks_learnable(topic):
            return
        seen.add(topic)
        pool.append({"topic": topic, "urgency": round(float(urgency), 3), "source": source})

    try:
        from ..curiosity_engine import calculate_urgency, get_all_domains, seed_domains

        for domain in get_all_domains():
            push(str(domain.get("name") or ""), float(calculate_urgency(domain)), "好奇心引擎")
        for name in seed_domains():
            push(str(name), 0.35, "种子领域")
    except Exception:
        pass
    try:
        from ..cognition import metacognition

        for gap in metacognition.gaps(limit=12):
            push(str(gap.get("name") or ""), 0.55, "知识缺口")
    except Exception:
        pass
    for name in (cfg().get("topics") or {}).get("seed") or []:
        push(str(name), 0.4, "配置种子")
    limit = int((cfg().get("topics") or {}).get("max_candidates", 24) or 24)
    return pool[:limit]


def recent_topics(days: int | None = None) -> set[str]:
    """最近学过的主题（状态里的历史 + 知识库里按学到的日期算）。"""
    conf = cfg()
    days = int(days if days is not None else (conf.get("topics") or {}).get("avoid_repeat_days", 7))
    cutoff = datetime.now().timestamp() - days * 86400
    out: set[str] = set()
    for row in state().get("history") or []:
        if not isinstance(row, dict):
            continue
        at = parse_iso(str(row.get("at") or ""))
        if at is None or at.timestamp() >= cutoff:
            out.add(str(row.get("topic") or "").strip())
    try:
        from ..repo import learning as lr

        for item in lr.knowledge_list(limit=120):
            at = parse_iso(str(item.learned_at or ""))
            if at is not None and at.timestamp() < cutoff:
                continue
            name = str(item.name or "").strip()
            if name and len(name) <= 12:
                out.add(name)
    except Exception:
        pass
    out.discard("")
    return out


def pick_topic(force_topic: str = "") -> dict | None:
    """挑一个「没学过、又最想看」的主题。"""
    forced = str(force_topic or "").strip()
    if forced:
        return {"topic": forced, "urgency": 1.0, "source": "手动指定"}
    learned = recent_topics()
    pool = [row for row in _topic_pool() if row["topic"] not in learned]
    if not pool:
        return None
    pool.sort(key=lambda row: -float(row.get("urgency") or 0))
    top = pool[:5]
    return random.choice(top)


def build_plan(topic: str = "") -> dict:
    """这一轮要**按顺序**试哪些页面（多源兜底：前一个失败就顺下去，直到拿到一篇）。

    排序规则：
      · 主题类站点按 priority 从小到大；同优先级按 site_cursor 轮换（别天天死磕同一个）；
      · 最后**留一个**列表源（热搜）当兜底：主题类站点全挂时至少还能「看看大家在聊什么」；
      · 总尝试次数受 quota.max_attempts_per_round 限制（默认 6）。
    """
    conf = cfg()
    pick = pick_topic(topic)
    if not pick:
        return {"ok": False, "reason": "没有可学的新主题（最近学的都在去重窗口里）"}
    name = pick["topic"]
    quota = conf.get("quota") or {}
    limit = max(1, int(quota.get("max_attempts_per_round", 6)))
    cursor = int(state().get("site_cursor") or 0)
    enabled = [site for site in sites() if site.get("enabled")]

    def ordered(pool: list[dict]) -> list[dict]:
        groups: dict[int, list[dict]] = {}
        for site in pool:
            groups.setdefault(int(site.get("priority") or 50), []).append(site)
        out: list[dict] = []
        for priority in sorted(groups):
            group = groups[priority]
            shift = cursor % len(group)
            out.extend(group[shift:] + group[:shift])
        return out

    # 主题类源：搜索 API（Tavily）+ 需要 {q} 的网页源
    topic_sites = [s for s in enabled
                   if s.get("kind") == "search"
                   or (s.get("kind") == "article" and "{q}" in str(s.get("url")))]
    lists = [s for s in enabled if s.get("kind") == "list"]

    def page_of(site: dict) -> dict:
        kind = str(site.get("kind") or "article")
        if kind == "search":
            url = f"{TAVILY_URL}?q={quote(str(name))}"
        elif kind == "article":
            url = url_for(site, name)
        else:
            url = str(site["url"])
        return {"site": site["name"], "url": url, "kind": kind,
                "max_chars": int(site["max_chars"]),
                "max_results": int(site.get("max_results") or 5),
                "priority": int(site.get("priority") or 50)}

    chain = [page_of(site) for site in ordered(topic_sites)]
    list_chain = [page_of(site) for site in ordered(lists)]
    if list_chain:
        # 主题类源里留出最后一个位置给列表源（真正的兜底）
        pages = chain[: max(0, limit - 1)] + list_chain[:1] if chain else list_chain[:limit]
    else:
        pages = chain[:limit]
    return {"ok": bool(pages), "topic": name, "urgency": float(pick.get("urgency") or 0),
            "source": str(pick.get("source") or ""), "pages": pages[:limit],
            "max_attempts": limit, "reason": "" if pages else "白名单里没有可用站点"}


# ── 抓取：真浏览器（无头）或者注入的 fetcher（dry-run 用静态 HTML）───────────
def _route(route) -> None:
    """图片/字体/媒体直接掐掉：只看字，省时间也省流量。"""
    try:
        if route.request.resource_type in ("image", "media", "font"):
            route.abort()
        else:
            route.continue_()
    except Exception:
        try:
            route.continue_()
        except Exception:
            pass


def fetch_html(url: str, *, log=None, stop_event=None) -> dict:
    """打开一个白名单页面，返回 html。无头、不弹窗、只看文本。"""
    ok, reason = url_allowed(url)
    if not ok:
        return {"ok": False, "error": reason, "blocked": True}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"playwright 不可用：{exc}"}
    conf = cfg().get("browser") or {}
    timeout = int(conf.get("timeout_ms") or 25000)
    html, title = "", ""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=str(conf.get("channel") or "msedge"),
                                         headless=bool(conf.get("headless", True)))
            try:
                context = browser.new_context(
                    viewport={"width": 1366, "height": 900}, locale="zh-CN",
                    user_agent=str(conf.get("user_agent") or ""),
                )
                if bool(conf.get("block_media", True)):
                    context.route("**/*", _route)
                page = context.new_page()
                if stop_event is not None and stop_event.is_set():
                    return {"ok": False, "error": "已停止"}
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(8000, timeout))
                except Exception:
                    pass
                page.wait_for_timeout(int(conf.get("wait_after_ms") or 1500))
                html = page.content()
                try:
                    title = page.title()
                except Exception:
                    title = ""
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": clip(str(exc), 200)}
    if not html:
        return {"ok": False, "error": "页面是空的"}
    _log(log, f"[webstudy] 抓到 {char_count(html)} 字 HTML（{title[:30]}）")
    return {"ok": True, "html": html, "title": title, "url": url}


# ── Tavily 搜索 API（不爬网页，直接拿结构化结果：标题 + 摘要 + 链接）──────────
# 为什么优先用它：搜索类站点（百度/必应/360）经常反爬或 JS 渲染，Tavily 是给模型用的
# 搜索接口，返回的就是「干净摘要」，一次几秒。拿不到（没 key / 额度 / 网络）就自动顺延到
# 后面的网页源，不影响主流程。
TAVILY_URL = "https://api.tavily.com/search"


def tavily_cfg() -> dict:
    """Tavily 配置：api_key 也可以写在环境变量 TAVILY_API_KEY 里。"""
    import os

    conf = dict(cfg().get("tavily") or {})
    key = str(conf.get("api_key") or "").strip() or os.environ.get("TAVILY_API_KEY", "").strip()
    conf["api_key"] = key
    conf["enabled"] = bool(conf.get("enabled", True)) and bool(key)
    return conf


def tavily_search(query: str, *, max_results: int | None = None, log=None,
                  stop_event=None) -> dict:
    """调一次 Tavily。返回 {ok, results:[{title,url,content,score}], error, meta}。"""
    import urllib.request

    conf = tavily_cfg()
    if not conf.get("enabled"):
        return {"ok": False, "results": [],
                "error": "tavily 没配 api_key（data/config/webstudy.json → tavily.api_key）"}
    if stop_event is not None and stop_event.is_set():
        return {"ok": False, "results": [], "error": "已停止"}
    payload = {
        "query": clip(str(query or ""), 400, ""),
        "max_results": max(1, int(max_results or conf.get("max_results") or 5)),
        "search_depth": str(conf.get("search_depth") or "basic"),
        "include_answer": False,
        "include_raw_content": bool(conf.get("include_raw_content", False)),
        "include_images": False,
    }
    request = urllib.request.Request(
        TAVILY_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _UA,
                 "Authorization": f"Bearer {conf['api_key']}"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=int(conf.get("timeout_seconds") or 30)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "results": [], "error": f"Tavily 调用失败：{clip(str(exc), 160)}"}

    results: list[dict] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not (title or content):
            continue
        results.append({"title": clip(title, 80), "url": str(item.get("url") or "").strip(),
                        "content": clip(content, 600),
                        "score": round(float(item.get("score") or 0.0), 4)})
    _log(log, f"[webstudy] Tavily 搜到 {len(results)} 条（{str(query)[:30]}）")
    if not results:
        return {"ok": False, "results": [], "error": "Tavily 没返回结果",
                "meta": {"query": query}}
    return {"ok": True, "results": results, "error": "",
            "meta": {"query": query, "count": len(results)}}


def _results_to_html(query: str, results: list[dict]) -> str:
    """把搜索结果拼成最小 HTML，后面直接复用「抽取 → 过滤 → 总结」那条路。"""
    from html import escape

    parts = [f"<html><body><h1>{escape(str(query))} - 搜索结果</h1><div class=\"results\">"]
    for item in results:
        parts.append(f"<h2>{escape(str(item.get('title') or ''))}</h2>")
        parts.append(f"<p>{escape(str(item.get('content') or ''))}</p>")
        source = str(item.get("url") or "")
        if source:
            parts.append(f"<p>来源：{escape(_host(source) or source)}</p>")
    parts.append("</div></body></html>")
    return "".join(parts)


def _stub_search(query: str, *, max_results: int | None = None, **kwargs) -> dict:
    """dry-run 用的假搜索（不联网、不花钱）。"""
    return {"ok": True, "error": "", "meta": {"query": query, "stub": True}, "results": [
        {"title": "光合作用的原理", "url": "https://example.org/photosynthesis",
         "content": "光合作用是绿色植物利用光能，把二氧化碳和水合成有机物并放出氧气的过程，主要在叶绿体里进行，叶绿素负责吸收光能。",
         "score": 0.9},
        {"title": "光合作用与呼吸作用的区别", "url": "https://example.org/respiration",
         "content": "呼吸作用全天进行、不需要光；光合作用必须有光照。两者发生的场所和产物都不同，不能互相替代。",
         "score": 0.8},
        {"title": "影响光合速率的环境因素", "url": "https://example.org/factors",
         "content": "光照强度、二氧化碳浓度和温度都会影响光合速率，弱光条件下适度增加光照通常能提高速率。",
         "score": 0.7},
    ]}


# ── 正文抽取（纯函数：同一个函数既跑真页面也跑 dry-run 的静态 HTML）──────────
_NOISE_TAGS = {"script", "style", "noscript", "svg", "iframe", "canvas", "form", "nav",
               "header", "footer", "aside", "button", "select", "option", "head", "link"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
               "article", "section", "dd", "dt", "blockquote", "pre", "table", "ul", "ol"}
_NOISE_LINE = re.compile(
    r"^(首页|登录|注册|下载|打开|返回|上一页|下一页|上一篇|下一篇|相关阅读|相关推荐|热门推荐|"
    r"广告|分享|收藏|点赞|评论|举报|更多|展开|收起|免责声明|版权声明|联系我们|关于我们|"
    r"扫码|关注我们|客户端|手机版|English|搜索|全部|目录|编辑|历史|讨论|上传视频|登录后.*|"
    r"高级搜索|搜索设置|意见反馈|隐私管理|切换少年版|为您推荐|相关搜索|时间不限|搜索工具|"
    r"网页|资讯|视频|图片|地图|文库|贴吧|知道|百科|学术|购物|网盘|翻译|更多产品|百度首页|"
    r"登录体验.*|下载豆瓣.*|关于豆瓣|豆瓣读书|电子图书|新书速递|回到顶部|查看全部.*)$")
# 搜索结果/RSS 里会夹着一堆纯 URL 和纯数字（热搜指数、条数），这些既费 token 又没信息
_URL_LINE = re.compile(r"^(https?://|www\.|//)")
_ONLY_SYMBOL = re.compile(r"^[\W\d_\s]+$", re.UNICODE)
_SPACES = re.compile(r"[ \t\u00a0]+")

# 自闭合/空元素：<link> <meta> <br> <img> … 没有结束标签。
# 这个集合是 2026-10-30 真机踩坑修出来的：把 <link> 当"成对噪声标签"会让 skip 计数
# 永远减不回去，结果整页正文都被吞掉（百度/豆瓣/热搜全是 0 字，就是这个原因）。
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
              "param", "source", "track", "wbr"}
# 宽松模式：只丢脚本/样式（真页面的 <head> 常常没闭合，严格模式可能一无所获）
_SOFT_NOISE_TAGS = {"script", "style", "noscript", "template", "svg"}


class _TextParser(HTMLParser):
    """HTML → 文本块：丢掉脚本/导航/页脚，块级标签换行。

    噪声标签用**栈**记，不用计数：只有真正闭合才出栈；遇到没闭合的也不会把
    后面整页吞掉（最多把这一层当噪声，extract_* 还会用宽松模式再兜一次）。
    """

    def __init__(self, skip_tags=None) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.h1 = ""
        self._skip_tags = set(skip_tags if skip_tags is not None else _NOISE_TAGS)
        self._noise: list[str] = []
        self._in_head = False
        self._in_title = False
        self._in_h1 = False

    @property
    def _skip(self) -> bool:
        return bool(self._noise)

    def handle_starttag(self, tag, attrs):  # noqa: D401
        tag = tag.lower()
        if tag == "head":
            self._in_head = True
        if tag in self._skip_tags:
            if tag not in _VOID_TAGS:
                self._noise.append(tag)
            return
        # 只有 <head> 里的 <title> 才是文档标题；RSS 里 <item><title> 是正文，不能吞掉
        if tag == "title":
            self._in_title = self._in_head
        elif tag == "h1" and not self.h1:
            self._in_h1 = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "head":
            self._in_head = False
        if tag in self._skip_tags:
            if tag in self._noise:
                for index in range(len(self._noise) - 1, -1, -1):
                    if self._noise[index] == tag:
                        self._noise.pop(index)
                        break
            return
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        text = str(data or "").strip()
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
            return
        if self._in_h1:
            self.h1 = (self.h1 + " " + text).strip()
            return
        self.parts.append(text)


def _clean_lines(chunks, *, min_len: int = 4, max_len: int = 0) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for line in str(chunk).splitlines():
            line = _SPACES.sub(" ", line).strip()
            if len(line) < min_len:
                continue
            if _NOISE_LINE.match(line):
                continue
            if _URL_LINE.match(line) or _ONLY_SYMBOL.match(line):
                continue
            if max_len and len(line) > max_len:
                line = clip(line, max_len, "")
            if line in seen:
                continue
            seen.add(line)
            out.append(line)
    return out


def _parse_html(html: str, *, lenient: bool = False) -> tuple[_TextParser | None, str]:
    parser = _TextParser(skip_tags=_SOFT_NOISE_TAGS if lenient else _NOISE_TAGS)
    try:
        parser.feed(str(html or ""))
        parser.close()
        return parser, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"解析失败：{exc}"


def _article_from_parser(parser: _TextParser, url: str, *, max_chars: int,
                         min_chars: int) -> dict:
    title = (parser.h1 or parser.title or _host(url) or "").strip()
    lines = _clean_lines(parser.parts)
    text = "\n".join(lines)
    chars = char_count(text)
    if chars < max(20, int(min_chars)):
        return {"ok": False, "reason": f"正文太短（{chars} 字，可能要靠 JS 渲染）",
                "title": clip(title, 80), "text": text, "chars": chars}
    if chars > int(max_chars):
        keep: list[str] = []
        total = 0
        for line in lines:
            if total + len(line) > int(max_chars):
                break
            keep.append(line)
            total += len(line)
        text = "\n".join(keep) or clip(text, int(max_chars), "")
    return {"ok": True, "reason": "", "title": clip(title, 80), "text": text,
            "chars": char_count(text)}


def extract_article(html: str, url: str = "", *, max_chars: int = 6000,
                    min_chars: int = 120) -> dict:
    """正文抽取：标题 + 去噪正文。太短就判定「没抓到正文」。

    两遍解析：先严格去噪（导航/页脚/侧栏都丢），不够就用宽松模式再试一次
    （只丢脚本样式）。真页面的 HTML 经常没闭合标签，只靠严格模式会一无所获。
    """
    parser, error = _parse_html(html)
    if parser is None:
        return {"ok": False, "reason": error, "title": "", "text": "", "chars": 0}
    first = _article_from_parser(parser, url, max_chars=max_chars, min_chars=min_chars)
    if first["ok"]:
        return first
    loose_parser, _ = _parse_html(html, lenient=True)
    if loose_parser is None:
        return first
    second = _article_from_parser(loose_parser, url, max_chars=max_chars, min_chars=min_chars)
    if second["ok"] or int(second["chars"]) > int(first["chars"]):
        return second
    return first


def extract_lines(html: str, *, limit: int = 40, max_line: int = 90,
                  max_chars: int = 3000) -> dict:
    """列表抽取（热搜/榜单/RSS）：一行一条，短句也留着。"""
    parser, error = _parse_html(html)
    if parser is None:
        return {"ok": False, "reason": error, "lines": [], "chars": 0}
    out = _lines_from_parser(parser, limit=limit, max_line=max_line, max_chars=max_chars)
    if out["ok"]:
        return out
    loose_parser, _ = _parse_html(html, lenient=True)
    if loose_parser is None:
        return out
    second = _lines_from_parser(loose_parser, limit=limit, max_line=max_line, max_chars=max_chars)
    if second["ok"] or len(second["lines"]) > len(out["lines"]):
        return second
    return out


def _lines_from_parser(parser: _TextParser, *, limit: int, max_line: int,
                       max_chars: int) -> dict:
    lines = _clean_lines(parser.parts, min_len=6, max_len=max_line)
    out: list[str] = []
    total = 0
    for line in lines:
        out.append(line)
        total += len(line)
        if len(out) >= int(limit) or total > int(max_chars):
            break
    text = "\n".join(out)
    if len(out) < 3:
        return {"ok": False, "reason": f"列表条目太少（{len(out)} 条）", "lines": out,
                "text": text, "chars": char_count(text)}
    return {"ok": True, "reason": "", "lines": out, "text": text, "chars": char_count(text)}


# ── 过滤 ────────────────────────────────────────────────────────────────────
def _no_content_marker(text: str) -> str:
    """页面是不是「没有这个词条」的提示页？返回命中的那一句，没有就返回空串。"""
    body = str(text or "")
    if not body:
        return ""
    for marker in cfg().get("no_content_markers") or []:
        if marker and str(marker) in body:
            return str(marker)
    return ""


def filter_article(article: dict) -> dict:
    """整篇过滤：命中即丢（不送模型、不写记忆）。"""
    body = f"{article.get('title') or ''}\n{article.get('text') or ''}"
    info = webfilter.scan(body, soft_threshold=soft_threshold())
    return {"ok": bool(info["ok"]), "reason": str(info["reason"]), "hits": info["hits"]}


def filter_lines(lines: list[str]) -> dict:
    """列表过滤：只丢坏的那几条，好的继续用。"""
    kept: list[str] = []
    blocked: list[dict] = []
    level = soft_threshold()
    for line in lines or []:
        ok, reason = webfilter.block_negative(line, soft_threshold=level)
        if ok:
            kept.append(line)
        else:
            blocked.append({"line": clip(line, 60), "reason": reason})
    return {"kept": kept, "blocked": blocked}


# ── 总结 ────────────────────────────────────────────────────────────────────
SUMMARY_PROMPT = """我在自己上网学习「{topic}」。下面是我从「{site}」看到的正文（已经过安全过滤）。
请只根据这段正文，总结出 {n} 条我真正学到的东西。
严格按「要点|说明」输出，一行一条：
  · 要点不超过 20 个字，是能记住的知识点；
  · 说明 30~120 字，讲清楚它是什么、怎么用、容易搞错的地方；
不要编号、不要表格、不要客套、不要复述原文。
如果正文里没有值得学的具体内容，只输出：无

===== 正文 =====
{body}"""

LIST_PROMPT = """我在自己上网看看大家都在聊什么（「{site}」的列表页，已经过安全过滤）。
请挑出 {n} 条真正值得知道的话题，按「话题|一句话说明」输出，一行一条。
不要编号、不要表格、不要客套。如果没有值得知道的，只输出：无

===== 列表 =====
{body}"""

_POINT_RE = re.compile(r"^(?P<point>[^|｜]{2,24})[|｜](?P<detail>.{4,})$")
_POINT_SKIP = {"要点", "说明", "话题", "标题", "内容", "知识点", "条"}


def parse_points(text: str, limit: int = 5) -> list[dict]:
    """把模型输出解析成 [{point, detail}]，认不出来的行一律丢掉（不猜）。"""
    rows: list[dict] = []
    for raw in str(text or "").splitlines():
        line = raw.strip().strip("*•·").strip()
        if not line:
            continue
        match = _POINT_RE.match(line)
        if not match:
            continue
        point = match.group("point").strip(" -*:：。「」\"'（）()")
        detail = match.group("detail").strip()
        if len(point) < 2 or len(point) > 20 or not detail:
            continue
        if point in _POINT_SKIP:
            continue
        rows.append({"point": point, "detail": clip(detail, 160)})
        if len(rows) >= int(limit):
            break
    return rows


def _call_llm(prompt: str, *, task: str = "", timeout: int = 120) -> tuple[bool, str, dict]:
    """走项目的 llm.provider；万一 provider 被中转站的 WAF 挡了，再用浏览器 UA 直连兜底一次。

    背景（2026-10-30 实测）：noorounter 中转挂在 Cloudflare 后面，provider._run_http 用
    urllib 默认 UA 会被回 403（`error code: 1010`），she 的 chitchat/对话也一起失败。
    这里不改 provider.py（不在本次改动范围），只在**本模块**加一条可关的兜底：
    `llm.http_fallback = true` 时，provider 失败后再补一个浏览器 UA 发同样的请求。
    provider 补上 UA 之后把开关关掉即可。

    费用归因用 `feature_tag="learning"`：`cost.FEATURE_TAGS` 是白名单，写自定义标签
    （比如 "webstudy"）会被静默降级成 "other"，帐就分不清了；这一路本质就是「学习」，
    所以记在「学习」名下（meta 里另标 channel=webstudy 方便排查）。
    """
    conf = cfg().get("llm") or {}
    task = str(task or conf.get("task") or "summary")
    timeout = int(timeout or conf.get("timeout_seconds") or 120)
    try:
        from .. import llm
    except Exception as exc:  # noqa: BLE001
        return False, f"llm 模块不可用：{exc}", {}
    ok, text, meta = llm.provider.run(prompt, task=task, timeout=timeout, feature_tag="learning")
    if ok and str(text or "").strip():
        return ok, text, meta
    if not bool(conf.get("http_fallback", True)):
        return ok, text, meta
    ok2, text2, meta2 = _http_call_with_ua(prompt, timeout=timeout, task=task)
    if ok2 and str(text2 or "").strip():
        merged = {**(meta or {}), **meta2, "ok": True, "provider_fallback": "http+ua",
                  "provider_error": clip(str(text or ""), 120)}
        return True, text2, merged
    return ok, text, meta


def _http_call_with_ua(prompt: str, *, timeout: int = 120, task: str = "summary") -> tuple[bool, str, dict]:
    """浏览器 UA 直连（兜底）。费用按 provider 的估价记一笔，别让账对不上。"""
    import os
    import urllib.request

    try:
        from .. import llm
        from .. import settings as _settings

        http = ((_settings.llm_cfg().get("providers") or {}).get("http") or {})
    except Exception as exc:  # noqa: BLE001
        return False, f"读不到 http provider 配置：{exc}", {}
    base = str(http.get("base_url") or "").rstrip("/")
    if not base:
        return False, "http provider 没配 base_url", {}
    key = str(http.get("api_key") or os.environ.get(str(http.get("api_key_env") or ""), ""))
    payload = {"model": http.get("model") or "deepseek-chat",
               "messages": [{"role": "user", "content": prompt}],
               "temperature": float(http.get("temperature", 0.8))}
    request = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                 "User-Agent": str((cfg().get("browser") or {}).get("user_agent") or _UA)},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return False, f"UA 兜底直连失败：{clip(str(exc), 160)}", {}
    text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    usage_raw = data.get("usage") or {}
    usage = {"cache_miss": int(usage_raw.get("prompt_tokens") or 0), "cache_hit": 0,
             "cache_write": 0, "output": int(usage_raw.get("completion_tokens") or 0)}
    cost = 0.0
    try:
        cost = float(llm.provider.estimate_cost({k: usage[k] for k in
                                                 ("cache_miss", "cache_hit", "cache_write", "output")}))
        from .. import cost as cost_mod

        cost_mod.record_event("learning", task=task, model=str(http.get("model") or ""),
                              usage=usage, estimated=cost, actual=cost, source="engine",
                              meta={"provider": "http+ua", "channel": "webstudy"})
    except Exception:
        pass
    return bool(text), text, {"provider": "http+ua", "cost": cost, "usage": usage}


def summarize(topic: str, body: str, site_name: str, *, kind: str = "article", limit: int = 5,
              call=None, log=None) -> dict:
    """正文 → 2~4 条「要点|说明」。模型不可用就返回 ok=False（上层决定是否退化成抽取式）。"""
    conf = cfg()
    quota = conf.get("quota") or {}
    n = max(2, min(int(limit), int(quota.get("items_per_round", 5))))
    template = LIST_PROMPT if kind == "list" else SUMMARY_PROMPT
    prompt = template.format(topic=topic, site=site_name, n=n, body=clip(body, 4000, "…"))
    try:
        ok, text, meta = (call or _call_llm)(prompt)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "items": [], "raw": "", "error": clip(str(exc), 160), "meta": {}}
    if not ok or not str(text or "").strip():
        return {"ok": False, "items": [], "raw": str(text or ""),
                "error": clip(str(text or ""), 160), "meta": meta or {}}
    if str(text).strip() in ("无", "none", "None", "没有"):
        return {"ok": True, "items": [], "raw": str(text), "error": "",
                "meta": meta or {}, "model_said_none": True}
    items = parse_points(text, limit=n)
    level = soft_threshold()
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in items:
        good, reason = webfilter.block_negative(f"{item['point']}{item['detail']}", soft_threshold=level)
        if good:
            kept.append(item)
        else:
            dropped.append({**item, "reason": reason})
    _log(log, f"[webstudy] 模型给了 {len(items)} 条，过滤后留 {len(kept)} 条")
    return {"ok": True, "items": kept, "dropped": dropped, "raw": str(text),
            "error": "", "meta": meta or {}}


def extractive_points(text: str, limit: int = 4) -> list[dict]:
    """模型不可用时的兜底：从正文里挑几句像结论的话（报告里会标 extractive）。"""
    out: list[dict] = []
    for line in sentences(text):
        line = line.strip()
        if not (12 <= len(line) <= 160):
            continue
        out.append({"point": clip(line, 20, ""), "detail": clip(line, 140)})
        if len(out) >= int(limit):
            break
    return out


# ── 写记忆 ──────────────────────────────────────────────────────────────────
def store_points(topic: str, items: list[dict], *, site: str, url: str,
                 extractor: str = "llm", dry_run: bool = False, log=None) -> dict:
    """写进知识库：复用 metacognition.learn()（knowledge_item + 倒排索引，聊天可召回）。"""
    if dry_run:
        return {"stored": [], "errors": [], "dry_run": True,
                "planned": [{"name": item["point"], "raw": f"【学到】{item['point']}：{item['detail']}"}
                            for item in items]}
    try:
        from ..cognition import metacognition
    except Exception as exc:  # noqa: BLE001
        return {"stored": [], "errors": [f"metacognition 不可用：{exc}"]}
    stored: list[dict] = []
    errors: list[str] = []
    for item in items:
        point = clip(str(item.get("point") or ""), 40)
        detail = str(item.get("detail") or "").strip()
        if not point or not detail:
            continue
        raw = f"【学到】{point}：{detail}\n【还不懂】无"
        try:
            knowledge = metacognition.learn(
                point, raw, domain=clip(str(topic or "通用"), 20) or "通用",
                source=f"webstudy:{site}",
                concepts=keywords(f"{point} {detail}", 8),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{point}：{clip(str(exc), 80)}")
            continue
        stored.append({"knowledge": knowledge.id, "name": knowledge.name,
                       "confidence": round(float(knowledge.confidence), 2)})
        try:
            from ..repo import system as sr

            sr.audit_add("webstudy", knowledge.id, "learned",
                         {"topic": topic, "site": site, "url": clip(url, 200),
                          "extractor": extractor})
        except Exception:
            pass
        _log(log, f"[webstudy] 学会：{point}")
    return {"stored": stored, "errors": errors}


def _uncertainty(topic: str) -> float:
    """这个主题现在有多不确定（写之前 1.0，写完之后按知识条目的不确定性平均降下来）。

    为什么不直接用 metacognition.uncertainty_of(topic)：那个函数只认「知识条目名字
    == 主题」的情况，而这里写进知识库的是「要点」（名字是具体知识点），主题是 domain。
    所以先试同名，再按同领域平均，最后才是 1.0。
    """
    try:
        from ..cognition import metacognition

        same = float(metacognition.uncertainty_of(topic))
        if same < 1.0:
            return same
    except Exception:
        pass
    try:
        from ..repo import learning as lr

        items = lr.knowledge_list(limit=200, domain=clip(str(topic or ""), 20))
        if items:
            return round(sum(float(item.uncertainty) for item in items) / len(items), 4)
    except Exception:
        pass
    return 1.0


def _reason_key(reason: str) -> str:
    """拦截原因压成短键（统计用）：去掉「（共 N 个…）」和「｜」之后的长尾巴。"""
    text = str(reason or "").strip()
    if "（共" in text:
        text = text.split("（共")[0]
    return clip(text.split("｜")[0], 40)


def feed_curiosity(topic: str, *, before: float, after: float, note: str = "") -> dict:
    """把「学懂了一点」回写给好奇心系统（领域权重 / 探索次数 / 误差下降）。"""
    out: dict = {}
    try:
        from ..cognition import curiosity

        out["feedback"] = curiosity.feedback(topic, before, after)
    except Exception as exc:  # noqa: BLE001
        out["feedback_error"] = clip(str(exc), 120)
    try:
        from .. import curiosity_engine

        out["engine"] = curiosity_engine.record_outcome(topic, error_before=before,
                                                        error_after=after, note=clip(note, 60))
    except Exception as exc:  # noqa: BLE001
        out["engine_error"] = clip(str(exc), 120)
    return out


def _count_web() -> None:
    try:
        from ..cognition import curiosity

        curiosity.record_web()
    except Exception:
        pass


def _audit(action: str, url: str, detail: dict) -> None:
    """把「拦了什么 / 哪一页没抓到」记进审计表（排查用，不影响主流程）。"""
    try:
        from ..repo import system as sr

        sr.audit_add("webstudy", clip(str(url or ""), 120), action, detail)
    except Exception:
        pass


# ── 一页的处理（抓 → 抽 → 滤 → 总结 → 写）──────────────────────────────────
def process_page(page: dict, topic: str, *, dry_run: bool = False, fetcher=None, llm_call=None,
                 search_call=None, budget_left: int = 5, log=None, stop_event=None) -> dict:
    result = {"site": page.get("site", ""), "url": page.get("url", ""),
              "kind": page.get("kind", "article"), "ok": False, "blocked": False,
              "blocked_count": 0, "reason": "", "chars": 0, "items": [], "llm_called": False,
              "search_results": 0, "extractor": "", "stored": [], "errors": [], "cost": 0.0,
              "llm_error": ""}
    if stop_event is not None and stop_event.is_set():
        result["reason"] = "已停止"
        return result
    kind = str(result["kind"] or "article")

    if kind == "search":
        # 搜索 API（Tavily）：不爬网页、不走域名白名单（api.tavily.com 是我们自己的出站接口）
        call = search_call or tavily_search
        try:
            found = call(topic, max_results=int(page.get("max_results") or 5))
        except TypeError:
            found = call(topic)
        except Exception as exc:  # noqa: BLE001
            found = {"ok": False, "error": f"搜索失败：{exc}"}
        if not found or not found.get("ok"):
            result["reason"] = f"搜索没结果：{clip(str((found or {}).get('error')), 120)}"
            if not dry_run:
                _audit("search_failed", result["url"],
                       {"site": result["site"], "error": result["reason"]})
            return result
        if not dry_run:
            _count_web()
        results = list(found.get("results") or [])
        result["search_results"] = len(results)
        html = _results_to_html(topic, results)
        _log(log, f"[webstudy]「{result['site']}」给了 {len(results)} 条结果，按文章正文处理")
    else:
        ok, why = url_allowed(result["url"])
        if not ok:
            result.update({"blocked": True, "blocked_count": 1, "reason": why})
            if not dry_run:
                _audit("url_blocked", result["url"], {"site": result["site"], "reason": why})
            return result
        fetch = fetcher or fetch_html
        try:
            got = fetch(result["url"])
        except Exception as exc:  # noqa: BLE001
            result["reason"] = f"抓取失败：{clip(str(exc), 120)}"
            if not dry_run:
                _audit("fetch_failed", result["url"], {"site": result["site"],
                                                       "error": clip(str(exc), 120)})
            return result
        if not got or not got.get("ok"):
            result["reason"] = f"抓取失败：{clip(str((got or {}).get('error')), 120)}"
            if not dry_run:
                _audit("fetch_failed", result["url"], {"site": result["site"],
                                                       "error": result["reason"]})
            return result
        if not dry_run:
            _count_web()
        html = str(got.get("html") or "")

    if kind == "list":
        extracted = extract_lines(html, max_chars=int(page.get("max_chars") or 3000))
        if not extracted.get("ok"):
            result["reason"] = str(extracted.get("reason"))
            return result
        filtered = filter_lines(extracted["lines"])
        total_lines = max(1, len(extracted["lines"]))
        result["blocked_count"] = len(filtered["blocked"])
        result["blocked"] = bool(filtered["blocked"])
        if filtered["blocked"]:
            result["reason"] = "列表拦下：" + "；".join(
                item["reason"] for item in filtered["blocked"][:3])
        # 整页密度闸：一页里坏条目占了一半以上，说明这页本身就是脏内容，整页不要
        ratio = len(filtered["blocked"]) / total_lines
        limit_ratio = float((cfg().get("filter") or {}).get("max_blocked_ratio", 0.5) or 0.5)
        if filtered["blocked"] and ratio >= limit_ratio:
            result["reason"] = (result["reason"] + "｜" if result["reason"] else "") + \
                f"整页脏掉了（{len(filtered['blocked'])}/{total_lines} 条被拦），整页丢弃"
            if not dry_run:
                _audit("blocked", result["url"],
                       {"site": result["site"], "reason": clip(result["reason"], 200),
                        "count": len(filtered["blocked"]), "lines": total_lines,
                        "kind": "list"})
            return result
        body = "\n".join(filtered["kept"])
        if char_count(body) < 40:
            result["reason"] = (result["reason"] or "") + "｜干净的条目太少"
            return result
    else:
        extracted = extract_article(html, result["url"],
                                    max_chars=int(page.get("max_chars") or 6000),
                                    min_chars=int((cfg().get("quota") or {}).get("min_chars", 120)))
        # 先看是不是「没有这个词条」的提示页（这种页可能很短，也可能很长）
        marker = _no_content_marker(extracted.get("text") or "")
        if marker:
            result.update({"reason": f"页面上没有这个词条（命中「{marker}」），换下一个源",
                           "chars": int(extracted.get("chars") or 0)})
            if not dry_run:
                _audit("empty_page", result["url"], {"site": result["site"], "marker": marker})
            return result
        if not extracted.get("ok"):
            result["reason"] = str(extracted.get("reason"))
            result["chars"] = int(extracted.get("chars") or 0)
            return result
        verdict = filter_article(extracted)
        if not verdict["ok"]:
            result.update({"blocked": True, "blocked_count": 1, "reason": verdict["reason"],
                           "chars": int(extracted.get("chars") or 0)})
            _log(log, f"[webstudy] 拦下「{result['site']}」：{verdict['reason']}")
            if not dry_run:
                _audit("blocked", result["url"],
                       {"site": result["site"], "reason": clip(verdict["reason"], 200),
                        "count": 1, "kind": "article"})
            return result
        body = str(extracted.get("text") or "")

    result["chars"] = char_count(body)
    result["llm_called"] = True   # 到这一步说明这页已经过闸，才允许调模型
    quota = cfg().get("quota") or {}
    page_limit = max(2, min(int(budget_left), int(quota.get("points_per_page", 4) or 4)))
    summary = summarize(topic, body, result["site"], kind=result["kind"],
                        limit=page_limit, call=llm_call, log=log)
    items = list(summary.get("items") or [])
    extractor = "llm"
    if not items:
        said_none = bool(summary.get("model_said_none"))
        if said_none:
            # 模型看完明确说「没什么可学的」：尊重它的判断，换下一个源，别硬凑
            result["reason"] = "模型看完说这一段没什么可学的（换下一个源）"
        result["llm_error"] = "" if said_none else str(summary.get("error") or "")
        if not said_none and bool((cfg().get("llm") or {}).get("extractive_fallback", True)):
            items = extractive_points(body, limit=page_limit)
            extractor = "extractive"
            _log(log, f"[webstudy] 模型没给结果，退化成抽取式（{len(items)} 条）")
    # 兜底/模型给的条目再过一次闸，然后才写记忆
    level = soft_threshold()
    safe: list[dict] = []
    for item in items:
        good, reason = webfilter.block_negative(f"{item.get('point', '')}{item.get('detail', '')}",
                                                soft_threshold=level)
        if good:
            safe.append(item)
        else:
            result["blocked_count"] += 1
            result["reason"] = (result["reason"] + "；" if result["reason"] else "") + f"要点拦下：{reason}"
    safe = safe[:max(0, page_limit)]
    if not safe:
        result["reason"] = result["reason"] or "这一页没有可用内容"
        return result
    write = store_points(topic, safe, site=result["site"], url=result["url"],
                         extractor=extractor, dry_run=dry_run, log=log)
    result.update({"ok": True, "items": safe, "extractor": extractor,
                   "stored": write.get("stored") or [], "errors": write.get("errors") or [],
                   "cost": float(((summary.get("meta") or {}).get("cost")) or 0.0)})
    return result


# ── 一轮 ────────────────────────────────────────────────────────────────────
def run_round(*, topic: str = "", force: bool = False, dry_run: bool = False, fetcher=None,
              llm_call=None, search_call=None, log=None, stop_event=None) -> dict:
    """跑一轮：选题 → 逐页处理 → 记状态 → 写报告。dry_run=True 不碰浏览器/数据库。"""
    started = datetime.now().isoformat(timespec="seconds")
    if not dry_run:
        ok, why = gate(auto=True)
        if not ok and not force:
            return {"ok": False, "skipped": why, "started": started, "quota": quota_left()}
    plan = build_plan(topic)
    if not plan.get("ok"):
        return {"ok": False, "error": plan.get("reason"), "started": started}
    topic_name = plan["topic"]
    quota_cfg = cfg().get("quota") or {}
    budget = int(quota_cfg.get("items_per_round", 5))
    max_pages = max(1, int(quota_cfg.get("pages_per_round", 1)))
    max_attempts = max(1, int(plan.get("max_attempts") or len(plan.get("pages") or []) or 1))
    before = 1.0 if dry_run else _uncertainty(topic_name)
    pages_out: list[dict] = []
    learned: list[dict] = []
    blocked = 0
    blocked_reasons: dict[str, int] = {}
    cost = 0.0
    fallback = False
    attempts = 0
    ok_pages = 0

    # 多源兜底：按 priority 一个个试，失败就顺下去；拿到 max_pages 篇有效正文（默认 1 篇）就收工
    for page in plan["pages"]:
        if attempts >= max_attempts or ok_pages >= max_pages:
            break
        left = budget - len(learned)
        if left <= 0:
            break
        attempts += 1
        out = process_page(page, topic_name, dry_run=dry_run, fetcher=fetcher, llm_call=llm_call,
                           search_call=search_call, budget_left=left, log=log,
                           stop_event=stop_event)
        pages_out.append({k: out[k] for k in ("site", "url", "kind", "ok", "blocked",
                                              "blocked_count", "reason", "chars", "llm_called",
                                              "search_results", "extractor", "cost", "errors",
                                              "llm_error")})
        if out.get("ok"):
            ok_pages += 1
        else:
            _log(log, f"[webstudy] 第 {attempts} 个源不行（{out['site']}）：{out.get('reason')}，换下一个")
        blocked += int(out.get("blocked_count") or 0)
        if out.get("blocked_count") and out.get("reason"):
            key = _reason_key(out["reason"])
            blocked_reasons[key] = blocked_reasons.get(key, 0) + int(out["blocked_count"])
        cost += float(out.get("cost") or 0.0)
        fallback = fallback or out.get("extractor") == "extractive"
        for item in out.get("items") or []:
            learned.append({"point": item.get("point", ""), "detail": item.get("detail", ""),
                            "site": out["site"], "extractor": out.get("extractor", "llm")})
        if stop_event is not None and stop_event.is_set():
            break

    after = before if dry_run else _uncertainty(topic_name)
    feedback: dict = {}
    if learned and not dry_run:
        feedback = feed_curiosity(topic_name, before=before, after=after,
                                  note=clip("；".join(item["point"] for item in learned[:3]), 60))

    finished = datetime.now().isoformat(timespec="seconds")
    result = {"ok": bool(learned), "topic": topic_name, "started": started,
              "finished": finished, "source": plan.get("source", ""),
              "urgency": plan.get("urgency", 0.0), "pages": pages_out,
              "learned": learned, "learned_count": len(learned),
              "blocked_count": blocked, "blocked_reasons": blocked_reasons,
              "attempts": attempts, "ok_pages": ok_pages,
              "llm_calls": sum(1 for page in pages_out if page.get("llm_called")),
              "cost": round(cost, 6), "fallback": fallback,
              "uncertainty": [round(before, 2), round(after, 2)], "feedback": feedback,
              "dry_run": bool(dry_run)}

    # 真的拿到页面（学没学到、拦没拦下都算）才扣配额；
    # 浏览器起不来/断网这种「一无所获」不扣配额，只记 last_error 并让最小间隔挡住重试。
    touched = any(page.get("ok") or page.get("blocked") for page in pages_out)

    if not dry_run:
        st = state()
        if touched:
            st["rounds"] = int(st.get("rounds") or 0) + 1
            st["items"] = int(st.get("items") or 0) + len(learned)
            st["blocked"] = int(st.get("blocked") or 0) + blocked
            st["blocked_total"] = int(st.get("blocked_total") or 0) + blocked
            st["learned_total"] = int(st.get("learned_total") or 0) + len(learned)
            st["site_cursor"] = int(st.get("site_cursor") or 0) + 1
            reasons = dict(st.get("blocked_reasons") or {})
            for key, count in blocked_reasons.items():
                reasons[str(key)[:80]] = int(reasons.get(str(key)[:80]) or 0) + int(count)
            st["blocked_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:20])
            history = [row for row in (st.get("history") or []) if isinstance(row, dict)]
            history.append({"topic": topic_name, "at": finished, "items": len(learned),
                            "blocked": blocked, "sites": [page["site"] for page in pages_out]})
            st["history"] = history[-200:]
            st["last_error"] = ""
        else:
            st["last_error"] = clip("；".join(str(page.get("reason") or "") for page in pages_out)
                                    or "没有拿到任何页面", 200)
            result["error"] = "这一轮没拿到任何可用页面（配额未扣）"
        st["last_run"] = finished
        save_state(st)
        result["report"] = _write_report(result, plan, persist=touched)
        result["quota"] = quota_left()
    return result


def _write_report(result: dict, plan: dict, *, persist: bool = True) -> str:
    """报告复用学习系统的目录与 LearningIntent 表（面板/审计里能看见这一轮）。

    persist=False（这轮啥页面也没拿到）只写报告文件，不往意图表里塞噪声行。
    """
    try:
        from ..entity import IntentStatus, LearningIntent
        from ..repo import learning as lr
        from . import executor

        intent = LearningIntent(
            target=clip(str(plan.get("topic") or "上网学习"), 40), source="webstudy",
            curiosity_score=float(plan.get("urgency") or 0.0), expected_learnability=0.7,
            context={"engine": "webstudy", "topic": plan.get("topic"),
                     "sites": [page.get("site") for page in result.get("pages") or []]},
            proposed_query=f"{plan.get('topic')} 是什么？怎么用？",
        )
        if persist:
            try:
                lr.intent_insert(intent)
                lr.intent_update(intent.id, status=IntentStatus.DONE.value)
            except Exception:
                pass
        body = "\n".join(f"- {item['point']}：{item['detail']}" for item in result.get("learned") or [])
        extra = {
            "kind": "webstudy", "topic": plan.get("topic"), "source": plan.get("source"),
            "attempts": result.get("attempts", 0), "ok_pages": result.get("ok_pages", 0),
            "blocked_count": result.get("blocked_count", 0),
            "blocked_reasons": result.get("blocked_reasons") or {},
            "fallback_extractive": bool(result.get("fallback")),
            "uncertainty": result.get("uncertainty"),
            "pages": [{k: page.get(k) for k in ("site", "url", "ok", "blocked", "reason", "chars",
                                                "llm_called")}
                      for page in result.get("pages") or []],
            "topics_history": f"{len(state().get('history') or [])} 轮",
        }
        executor.write_report(intent, ok=bool(result.get("ok")), body=body or "（这轮什么都没学到）",
                              meta={"cost": result.get("cost", 0.0)}, outcome="webstudy",
                              extra=extra)
        return str(executor.report_path(intent.target))
    except Exception as exc:  # noqa: BLE001
        return f"（报告没写成：{clip(str(exc), 120)}）"


def tick(*, log=None, now: datetime | None = None, force: bool = False) -> dict:
    """调度入口：过闸后跑一轮。scheduler 里配一条 job 即可（见模块文档）。"""
    ok, why = gate(auto=True, now=now)
    if not ok and not force:
        return {"ok": False, "skipped": why, "quota": quota_left()}
    out = run_round(force=force, log=log)
    try:
        from ..repo import system as sr

        sr.audit_add("webstudy", clip(str(out.get("topic") or ""), 60), "round",
                     {"ok": out.get("ok"), "learned": out.get("learned_count", 0),
                      "blocked": out.get("blocked_count", 0), "cost": out.get("cost", 0.0),
                      "skipped": out.get("skipped", "")})
    except Exception:
        pass
    return out


def status() -> dict:
    """给她自己 / 面板 / 排查用的一览。"""
    conf = cfg()
    st = state()
    left = quota_left()
    return {
        "enabled": bool(conf.get("enabled", True)),
        "auto_run": bool(conf.get("auto_run", True)),
        "headless": bool((conf.get("browser") or {}).get("headless", True)),
        "quota": left,
        "rounds_per_day": left["rounds_per_day"],
        "items_per_round": left["items_per_round"],
        "min_interval_minutes": int((conf.get("quota") or {}).get("min_interval_minutes", 120)),
        "llm_task": str((conf.get("llm") or {}).get("task") or "summary"),
        "sites": [{"name": site["name"], "kind": site["kind"], "enabled": site["enabled"],
                   "url": site["url"]} for site in sites()],
        "tavily": {"enabled": bool(tavily_cfg().get("enabled")),
                   "has_key": bool(tavily_cfg().get("api_key")),
                   "max_results": int(tavily_cfg().get("max_results") or 5),
                   "search_depth": str(tavily_cfg().get("search_depth") or "basic")},
        "last_run": st.get("last_run", ""),
        "last_error": st.get("last_error", ""),
        "today": {"rounds": st.get("rounds", 0), "items": st.get("items", 0),
                  "blocked": st.get("blocked", 0)},
        "blocked_total": st.get("blocked_total", 0),
        "blocked_reasons": st.get("blocked_reasons", {}),
        "learned_total": st.get("learned_total", 0),
        "history": (st.get("history") or [])[-8:],
        "filter": webfilter.word_count(),
    }


# ── 静态自检（不出网、不开浏览器、不写库）───────────────────────────────────
_SAMPLE_ARTICLE_HTML = """<html><head><title>光合作用 - 百科</title>
<style>.x{color:red}</style><script>var a=1;</script></head><body>
<header>登录 注册 下载APP</header>
<h1>光合作用</h1>
<div class="lemma-summary">
<p>光合作用是绿色植物利用光能，把二氧化碳和水合成有机物并释放氧气的过程。</p>
<p>反应主要在叶绿体里进行，叶绿素负责吸收光能，光反应把水分解产生氧气和还原剂。</p>
<p>暗反应不需要光直接参与，靠酶把二氧化碳固定成糖类，温度对这一步影响很大。</p>
<p>常见误解：植物白天只进行光合作用、晚上只呼吸，实际上呼吸作用全天都在进行。</p>
<p>影响效率的因素有光照强度、二氧化碳浓度和温度，弱光下增加光照通常能提高速率。</p>
</div>
<footer>版权所有 免责声明 联系我们</footer></body></html>"""

_SAMPLE_GAMBLING_HTML = """<html><body><h1>某网络平台被查</h1>
<div class="content">
<p>警方通报，该团伙在境外架设赌场，用百家乐吸引赌客押注，涉案赌资上亿元，已有多人被采取强制措施。</p>
<p>调查显示，参赌人员往往先小赢后大输，最后借高利贷周转，甚至被逼到卖房还债，家庭因此破裂。</p>
<p>平台还通过拉人头返佣发展下线，形式接近传销；办案人员提醒，任何线上博彩活动在国内都是违法的。</p>
<p>目前该案仍在进一步侦办中，涉事支付通道已被冻结。</p>
</div></body></html>"""

_SAMPLE_NEGATIVE_HTML = """<html><body><h1>深夜情绪</h1>
<div class="content">
<p>最近特别焦虑，感觉内卷到崩溃，做什么都没有意义，只剩下绝望。</p>
<p>每天醒来都不想上班，只想彻底摆烂，连朋友的消息都不想回，觉得自己什么都做不好。</p>
<p>有时候会想，是不是所有人都这样熬着，熬到麻木了就不难受了，可麻木也挺可怕的。</p>
<p>评论区里也全是类似的话，看得人更压抑，越看越睡不着，第二天更没精神。</p>
</div></body></html>"""

_SAMPLE_WIKI_MISS_HTML = """<html><head><title>搜索结果 - 维基百科</title></head><body>
<h1>搜索结果</h1>
<div id="mw-content-text">
<p>维基百科目前还没有与上述标题相同的条目。</p>
<p>您可以检查拼写是否正确，或者创建该条目，也可以参考名称相近的条目，看看是不是同义词。</p>
<p>本页面的其他文字是站务公告，例如每月第二个礼拜六举行的线上聚会，欢迎报名参与。</p>
<p>如果您是通过站内搜索来到本页，可以返回上一页重新输入关键词再试一次。</p>
</div></body></html>"""

_SAMPLE_HOT_HTML = """<html><body><h1>微博热搜</h1><ul>
<li><a>1. 冷空气来了多地降温</a></li>
<li><a>2. 国产大模型发布新版本</a></li>
<li><a>3. 某地赌场被端涉案赌资上亿</a></li>
<li><a>4. 高校食堂推出十元套餐</a></li>
<li><a>5. 地铁将延长末班车时间</a></li>
<li><a>6. 男子留遗书轻生被救下</a></li>
<li><a>7. 秋季养肺的六个小习惯</a></li>
<li><a>8. 城市马拉松本周日开跑</a></li>
<li><a>9. 图书馆延长开放时间</a></li>
<li><a>10. 老小区加装电梯新进展</a></li>
</ul></body></html>"""

_SAMPLE_RSS_HTML = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<title>光合作用 - 必应搜索</title>
<link>http://www.bing.com:80/search?q=%e5%85%89%e5%90%88%e4%bd%9c%e7%94%a8</link>
<item><title>光合作用的原理是什么</title>
<link>https://www.bing.com/x/1</link>
<description>光合作用是绿色植物利用光能，把二氧化碳和水合成有机物并放出氧气的过程。</description>
</item>
<item><title>光合作用与呼吸作用的区别</title>
<description>呼吸作用全天进行、不需要光，光合作用必须有光照；两者发生的场所和产物也不同。</description>
</item>
<item><title>影响光合速率的环境因素</title>
<description>光照强度、二氧化碳浓度和温度都会影响光合速率，弱光条件下适度增光通常能提高速率。</description>
</item>
</channel></rss>"""

_STUB_ANSWER = "\n".join([
    "光合作用|绿色植物用叶绿素吸收光能，把二氧化碳和水变成有机物并放出氧气。",
    "暗反应|不需要光直接参与，靠酶把二氧化碳固定成糖，温度影响很大。",
    "常见误解|植物并非白天只光合、晚上只呼吸，呼吸作用其实是全天进行的。",
])

_STUB_LIST_ANSWER = "\n".join([
    "冷空气来袭|多地大幅降温，早晚温差大，出门记得加一件外套。",
    "国产大模型|新版本发布会公布了推理能力和价格，值得关注它在写作上的表现。",
    "地铁延时|末班车时间延长，夜间回家多了一条选择。",
])


def _stub_llm(prompt: str, *, task: str = "", timeout: int = 0):
    """dry-run 用的假模型：不联网、不花钱，只验证「总结 → 解析 → 再过滤」这条链路。"""
    answer = _STUB_LIST_ANSWER if "列表页" in str(prompt) else _STUB_ANSWER
    return True, answer, {"cost": 0.0, "stub": True}


def _stub_fetcher(html: str):
    def fetch(url: str) -> dict:
        return {"ok": True, "html": html, "url": url}
    return fetch


def _stub_search_empty(query: str, **kwargs) -> dict:
    """冒充「Tavily 没搜到」：验证多源兜底会跳过它。"""
    return {"ok": False, "results": [], "error": "Tavily 没返回结果",
            "meta": {"query": query, "stub": True}}


SAMPLE_PAGES: tuple[dict, ...] = (
    {"label": "Tavily搜索（该学）", "site": "Tavily搜索", "kind": "search",
     "url": "https://api.tavily.com/search?q=光合作用", "search": "ok",
     "expect": "learn"},
    {"label": "Tavily无结果（该跳过）", "site": "Tavily搜索", "kind": "search",
     "url": "https://api.tavily.com/search?q=空话题", "search": "empty",
     "expect": "no_content"},
    {"label": "百科（干净）", "site": "维基百科", "kind": "article",
     "url": "https://zh.wikipedia.org/wiki/光合作用", "html": _SAMPLE_ARTICLE_HTML,
     "expect": "learn"},
    {"label": "必应RSS（URL行要滤掉）", "site": "必应搜索", "kind": "article",
     "url": "https://cn.bing.com/search?q=光合作用&format=rss", "html": _SAMPLE_RSS_HTML,
     "expect": "learn"},
    {"label": "赌博（该拦）", "site": "百度搜索", "kind": "article",
     "url": "https://www.baidu.com/s?wd=赌场", "html": _SAMPLE_GAMBLING_HTML,
     "expect": "blocked"},
    {"label": "负能量（该拦）", "site": "必应网页", "kind": "article",
     "url": "https://cn.bing.com/search?q=情绪", "html": _SAMPLE_NEGATIVE_HTML,
     "expect": "blocked"},
    {"label": "维基无此条目（该跳过）", "site": "维基百科", "kind": "article",
     "url": "https://zh.wikipedia.org/wiki/语言风格", "html": _SAMPLE_WIKI_MISS_HTML,
     "expect": "no_content"},
    {"label": "热搜（混着坏条目）", "site": "微博热搜", "kind": "list",
     "url": "https://s.weibo.com/top/summary", "html": _SAMPLE_HOT_HTML,
     "expect": "partial"},
)


def dry_run(*, log=None) -> dict:
    """静态自检：只跑「抽取 + 过滤 + 总结 + 组装」，不联网、不开浏览器、不写库。"""
    topic = "光合作用"
    rows: list[dict] = []
    for sample in SAMPLE_PAGES:
        page = {"site": sample["site"], "url": sample["url"], "kind": sample["kind"],
                "max_chars": 6000 if sample["kind"] == "article" else 3000,
                "max_results": 4}
        search_call = (_stub_search if sample.get("search", "ok") == "ok"
                       else _stub_search_empty)
        out = process_page(page, topic, dry_run=True,
                           fetcher=_stub_fetcher(sample.get("html") or ""),
                           llm_call=_stub_llm, search_call=search_call,
                           budget_left=5, log=log)
        ok_expect = sample["expect"] in ("learn", "partial")
        rows.append({
            "label": sample["label"], "expect": sample["expect"],
            "ok": bool(out["ok"]), "pass": bool(out["ok"]) == ok_expect,
            "blocked": bool(out["blocked"]), "blocked_count": int(out["blocked_count"]),
            "reason": out["reason"], "chars": int(out["chars"]),
            "items": [item["point"] for item in out.get("items") or []],
            "extractor": out.get("extractor", ""),
        })
    return {"topic": topic, "rows": rows,
            "passed": sum(1 for row in rows if row["pass"]), "total": len(rows)}


def format_dry_run(*, log=None) -> str:
    report = dry_run(log=log)
    lines = [f"静态自检（不开浏览器 / 不写数据库 / 不发请求）：{report['passed']}/{report['total']} 符合预期"]
    for index, row in enumerate(report["rows"], start=1):
        flag = "PASS" if row["pass"] else "FAIL"
        bits = [f"[{flag}] {index}. {row['label']}"]
        if row["ok"]:
            bits.append(f"抽取 {row['chars']} 字｜要点 {len(row['items'])} 条（{row['extractor']}）")
            if row["items"]:
                bits.append("→ " + "、".join(row["items"]))
        else:
            bits.append("没有可用内容")
        if row["blocked_count"]:
            bits.append(f"拦下 {row['blocked_count']} 条：{clip(row['reason'], 110)}")
        elif row["reason"]:
            bits.append(f"原因：{clip(row['reason'], 110)}")
        lines.append("｜".join(bits))
    lines.append(f"词表：硬 {webfilter.word_count()['hard']} + 软 {webfilter.word_count()['soft']}"
                 f"（软词阈值 {soft_threshold()}）")
    return "\n".join(lines)


# ── 命令行：python -m core.webstudy --dry-run / --selftest / --status / --once ─
def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    if "--selftest" in args:
        print(webfilter.format_selftest(soft_threshold=soft_threshold()))
        return 0
    if "--dry-run" in args:
        print(format_dry_run())
        return 0
    if "--status" in args:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if "--once" in args:
        out = tick(force="--force" in args)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1
    print("用法：python -m core.webstudy [--selftest | --dry-run | --status | --once [--force]]")
    return 2
