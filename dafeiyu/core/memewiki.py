# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""梗百科：本地 6668 条梗词库，毫秒级查词，不联网、不花 token（v24）。

为什么要它：
  她遇到「奶蛙」「纳味」这种新词时，原来只有两条路 —— 要么猜（编），要么
  mcp__web-search-safe__web_search（联网、慢、还可能被限流）。梗词库把第三条路摆在最前面：
  本地 SQLite 一次查询 ≈ 0.2ms，查不到就明说查不到（found=false），绝不编。

和 DSH 的 dsh-memewiki 插件是什么关系：
  插件那版在 DSH 里跑（要重启 Harness 才生效，工具名 search_meme_wiki）。
  这里这一份在**核心**里跑，桥接每条消息前顺手查一次，命中就当作上下文注入 ——
  不需要重启 DSH，也不需要她花一步工具调用。两边共用同一份词库（data/memes.db）。

命中口径（比插件多一层，为了「奶蛙」这种只有长词条的情况）：
  1. 标题完全等于查询词；
  2. 标题是查询词的一部分（「奶蛙是什么」→「奶蛙」）；
  3. 查询词是标题的前缀（「奶蛙」→「奶蛙之母」「奶蛙被嫌弃」，取最短的那条，
     并在注入时**如实标注**这是「最接近的词条」，不是同名条目）。

学习：她用过哪个词、在什么语境下用过、有没有补充理解，都记在 state/memewiki.json，
      下次同一个词再出现时一并注入 —— 越用越准，且完全是本地读写。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import settings
from .text import clip

DB_PATH = settings.LEARNING / "memewiki" / "memes.db"
STATE_FILE = settings.STATE / "memewiki.json"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

DEFAULTS = {
    "enabled": True,
    "maxQueryChars": 10,
    "maxChars": 700,
    "learnLimit": 40,
}


def cfg() -> dict:
    data = settings.read_json(settings.CONFIG / "memewiki.json", {}) or {}
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in DEFAULTS})
    return out


# ── 连接 ────────────────────────────────────────────────────────────────────
def available() -> bool:
    try:
        return DB_PATH.exists() and DB_PATH.stat().st_size > 0
    except Exception:
        return False


def _connect() -> sqlite3.Connection | None:
    global _conn
    if _conn is not None:
        return _conn
    if not available():
        return None
    try:
        # 只读打开：词库是外部快照，核心永远不改它（改了下次更新就白改）
        _conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0,
                                check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        return _conn
    except Exception:
        _conn = None
        return None


# ── 状态（她用过的词 + 补充理解）────────────────────────────────────────────
def _state() -> dict:
    data = settings.read_json(STATE_FILE, {}) or {}
    if not isinstance(data, dict):
        data = {}
    terms = data.get("terms")
    if not isinstance(terms, dict):
        terms = {}
    return {"terms": terms}


def _save_state(state: dict) -> None:
    """写 STATE_FILE（不是固定文件名）：测试和迁移都能换路径，且只写这一个文件。"""
    settings.write_text_atomic(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def learned(title: str) -> dict:
    return _state()["terms"].get(str(title), {})


def learn(title: str, *, note: str = "", context: str = "", hits: int = 0) -> bool:
    """记一笔「这个词我用过/我这么理解」。纯本地 JSON，写坏了也不影响查词。"""
    title = str(title or "").strip()
    if not title:
        return False
    try:
        with _lock:
            state = _state()
            row = state["terms"].get(title) or {"hits": 0, "contexts": [], "note": ""}
            row["hits"] = int(row.get("hits") or 0) + max(0, int(hits or 0))
            if context:
                contexts = [c for c in (row.get("contexts") or []) if isinstance(c, str)]
                contexts.append(clip(context, 80))
                row["contexts"] = contexts[-8:]
            if note:
                row["note"] = clip(note, 300)
            row["last"] = datetime.now().isoformat(timespec="seconds")
            limit = max(5, min(500, int(cfg().get("learnLimit") or 40)))
            state["terms"][title] = row
            if len(state["terms"]) > limit:
                # 只留最近的：超了就把最久没用的丢掉（词库本体不动）
                ordered = sorted(state["terms"].items(),
                                 key=lambda kv: str((kv[1] or {}).get("last") or ""))
                for old_key, _ in ordered[: max(0, len(state["terms"]) - limit)]:
                    state["terms"].pop(old_key, None)
            _save_state(state)
        return True
    except Exception:
        return False


# ── 查词 ────────────────────────────────────────────────────────────────────
def _row_dict(row: sqlite3.Row) -> dict:
    tags = [t.strip() for t in str(row["tags"] or "").split("、") if t.strip()]
    usage = str(row["usage"] or "").strip()
    origin = str(row["origin"] or "").strip()
    other = str(row["other"] or "").strip()
    if not usage and not origin:
        other = other or str(row["summary"] or "").strip()
    return {
        "title": str(row["title"]),
        "slug": str(row["slug"] or "").strip(),
        "tags": tags,
        "usage": usage,
        "origin": origin,
        "other": other,
    }


def _fetch(title: str) -> dict | None:
    conn = _connect()
    if conn is None or not title:
        return None
    try:
        row = conn.execute(
            "SELECT title, slug, tags, usage, origin, other, summary FROM memes WHERE title = ?",
            (title,),
        ).fetchone()
        return _row_dict(row) if row else None
    except Exception:
        return None


def user_terms() -> list[dict]:
    """她自己学来的词（刷抖音 → 网页版 DeepSeek 理解 → 存本地）。纯本地 JSON，读坏了就当空。"""
    try:
        from . import webstudy

        return webstudy.load_terms()
    except Exception:
        return []


def _match_user_term(q: str) -> str:
    """先查自己学的：同名 > 词条是句子一部分。自己学的一般比快照新。"""
    rows = user_terms()
    for row in rows:
        if str(row.get("title") or "").strip() == q:
            return q
    hit = [str(r.get("title") or "") for r in rows
           if len(str(r.get("title") or "")) >= 2 and str(r.get("title")) in q]
    return max(hit, key=len) if hit else ""


def _fetch_user_term(title: str) -> dict | None:
    for row in user_terms():
        if str(row.get("title") or "").strip() == title:
            return {"title": title, "slug": "", "tags": ["自学"],
                    "usage": str(row.get("usage") or ""), "origin": "",
                    "other": "", "source": str(row.get("source") or "自学")}
    return None


def _match_title(query: str) -> tuple[str, str]:
    """返回 (命中标题, 命中方式)。命中方式：exact / inside / prefix / tag。"""
    q = str(query or "").strip()
    if not q:
        return "", ""
    conn = _connect()
    if conn is None:
        return "", ""
    try:
        row = conn.execute("SELECT title FROM memes WHERE title = ?", (q,)).fetchone()
        if row:
            return str(row["title"]), "exact"
        row = conn.execute(
            "SELECT title FROM memes WHERE length(title) >= 2 AND instr(?, title) > 0 "
            "ORDER BY length(title) DESC LIMIT 1", (q,)).fetchone()
        if row:
            return str(row["title"]), "inside"
        # 「奶蛙」→「奶蛙之母」：只有长词条时，给最接近的那条，并如实说明
        row = conn.execute(
            "SELECT title FROM memes WHERE title LIKE ? ORDER BY length(title) ASC LIMIT 1",
            (f"{q}%",)).fetchone()
        if row:
            return str(row["title"]), "prefix"
        row = conn.execute(
            "SELECT title FROM memes WHERE tags LIKE ? ORDER BY length(title) ASC LIMIT 1",
            (f"%{q}%",)).fetchone()
        if row:
            return str(row["title"]), "tag"
    except Exception:
        return "", ""
    return "", ""


_ASK_RE = re.compile(r"(?:是什么梗|是什么|啥梗|什么意思|啥意思|怎么用|怎么接|怎么玩|啥来头|什么来头|啥意思啊|啥东西)[?？。!！~～\s]*$")
_BARE_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9·]{2,12}$")
_WAKE_WORDS = ("大肥鱼", "大肥鱼", "小肥鱼", "大肥鱼", "奶鱼", "鲸鱼", "deepseek", "ds")
_STOP_WORDS = {"在吗", "在不在", "哈哈", "哈哈哈", "笑死", "好的", "好吧", "晚安", "早安", "睡觉", "吃饭", "干嘛", "没事", "收到", "谢谢", "好惹"}
_PRONOUN_HEADS = {"这个", "那个", "它", "他", "她", "这", "那", "这个梗", "那句话", "上面", "刚才", "下文", "前面"}


def extract_query(raw: str) -> str:
    """从一句话里认出「要查哪个词」：`奶蛙是什么` → `奶蛙`；认不出返回空串。

    口径和 DSH 的 dsh-memewiki 插件一致（同一套词库，不该有两套识别规则）：
    宁可漏查，也不要把一整句话当成词去前缀匹配 —— 那样会命中一堆无关词条。
    """
    text = str(raw or "").replace("\u3000", " ").strip()
    text = re.sub(r"@\S+\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    lowered = text.lower()
    for word in _WAKE_WORDS:
        if lowered.startswith(word):
            text = re.sub(r"^[\s,，。:：]+", "", text[len(word):])
            break
    if not text:
        return ""
    ask = _ASK_RE.search(text)
    if ask:
        head = text[: ask.start()].strip().strip("，,。.！!？?")
        head = re.sub(r"是$", "", head).strip()
        head = re.sub(r"(这个梗|那个梗|这个|那个)$", "", head).strip()
        head = re.sub(r"(到底|究竟)$", "", head).strip()
        if not head or head in _PRONOUN_HEADS or len(head) > 12:
            return ""
        return head
    if not _BARE_RE.match(text) or text in _STOP_WORDS:
        return ""
    return text


def search(query: str, *, record: bool = False, context: str = "") -> dict:
    """查一个梗。查不到就 found=False（明确告诉她查不到，别让她编）。"""
    q = str(query or "").strip()
    if not q or len(q) > 40:
        return {"found": False, "query": q, "reason": "查询词为空或过长"}
    title = _match_user_term(q)
    if title == q:
        data = _fetch_user_term(title)
        if data:
            return {"found": True, "query": q, "match": "exact" if title == q else "inside",
                    "exact": title == q, **data,
                    "my_note": "", "my_hits": 0, "source": data.get("source") or "自学"}
    title, how = _match_title(q)
    if not title:
        return {"found": False, "query": q, "reason": "词库里没有"}
    if how != "exact":
        return {"found": False, "query": q, "reason": "只有相近词条，不能当作完整词义",
                "candidate_title": title, "match": how, "exact": False}
    data = _fetch(title)
    if not data:
        return {"found": False, "query": q, "reason": "词库读取失败"}
    mine = learned(title)
    out = {
        "found": True,
        "query": q,
        "match": how,
        "exact": how == "exact",
        **data,
        "my_note": str((mine or {}).get("note") or ""),
        "my_hits": int((mine or {}).get("hits") or 0),
        "source": "本地梗词库",
    }
    if record:
        learn(title, context=context or q, hits=1)
    return out


def block(query: str, *, max_chars: int = 0, record: bool = False) -> str:
    """给提示词用的那一小段：命中才返回，查不到返回空串（不占 token）。

    record 默认 False：只有「真的把这段注入给模型」的调用方（桥接/API）才记一次使用，
    界面上点「查一下」预览**不**算她用了一次 —— 否则学习计数会被预览污染。
    """
    conf = cfg()
    if not conf.get("enabled", True):
        return ""
    data = search(query, record=record)
    if not data.get("found"):
        return ""
    cap = max(200, int(max_chars or conf.get("maxChars") or 700))
    head = f"【梗百科·本地】「{data['query']}」"
    if data["match"] == "exact":
        head += f" → 词条「{data['title']}」"
    else:
        head += f" → 词库里没有同名条目，最接近的是「{data['title']}」（按这个理解，别当成别的梗）"
    lines = [head]
    if data["tags"]:
        lines.append(f"标签：{'、'.join(data['tags'][:8])}")
    if data["usage"]:
        lines.append(f"【怎么用/怎么接】{clip(data['usage'], 420)}")
    if data["origin"]:
        lines.append(f"【出处（只用来理解，别照搬）】{clip(data['origin'], 220)}")
    if data.get("my_note"):
        lines.append(f"【我自己的理解】{data['my_note']}")
    elif data.get("my_hits"):
        lines.append(f"（这个词我用过 {data['my_hits']} 次）")
    text = "\n".join(lines)
    if len(text) > cap:
        text = text[:cap] + "…"
    return text


def status() -> dict:
    count = 0
    built = ""
    conn = _connect()
    if conn is not None:
        try:
            count = int(conn.execute("SELECT COUNT(*) AS n FROM memes").fetchone()["n"])
            row = conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
            built = str(row["value"]) if row else ""
        except Exception:
            pass
    state = _state()
    return {
        "available": available(),
        "user_terms": len(user_terms()),
        "db": str(DB_PATH),
        "count": count,
        "built_at": built,
        "learned": len(state["terms"]),
        "enabled": bool(cfg().get("enabled", True)),
        "top": sorted(
            ({"title": k, **{kk: vv for kk, vv in (v or {}).items() if kk != "contexts"}}
             for k, v in state["terms"].items()),
            key=lambda item: int(item.get("hits") or 0), reverse=True)[:10],
    }


def all_learned() -> list[dict]:
    state = _state()
    rows = []
    for title, row in state["terms"].items():
        row = row or {}
        rows.append({
            "title": title,
            "note": str(row.get("note") or ""),
            "hits": int(row.get("hits") or 0),
            "last": str(row.get("last") or ""),
            "contexts": list(row.get("contexts") or []),
        })
    rows.sort(key=lambda r: (r["hits"], r["last"]), reverse=True)
    return rows
