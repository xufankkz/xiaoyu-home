# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v14 · 瞬时记忆冷存储 + 回忆检索。

常规对话只用「系统设定 + 长期记忆 + 最近几轮」，不碰这里。
只有当用户说「我刚才说想吃啥」「昨天的那个叫什么来着」这类回忆型问题，
才去 18 小时内的冷库里按关键词/概念捞几条，临时拼进上下文。
超过 18 小时的行由日常巩固清理（与瞬时记忆的规则一致）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .. import db
from ..entity import new_id, now_iso
from ..text import clip, keywords, parse_iso

HOURS = 18.0

# 触发词：优先"带时间指向的回忆"，避免"我记得"这种泛语被误触
RECALL_PATTERNS = [
    r"(我|咱)(刚才|刚刚|之前|上次|昨天|前天|前几天|上午|下午|晚上|今天早上|今晚).{0,12}(说|提|讲|聊|问)",
    r"我.{0,6}(几|两|三|四|五|六|七|八|九|十|半)个?(多)?小时前.{0,8}(说|提|讲|聊|问)",
    r"(你|还)(记得|记不记得).{0,10}(我|咱)",
    r"那个.{0,8}(是什么|叫什么|怎么说的|是啥)",
    r"我.{0,6}(想|要|打算).{0,6}(吃|买|去|做).{0,6}(什么|啥)(来着|来的)?",
    r"(刚才|刚刚|之前|上次|昨天).{0,10}(说|聊|提)的",
]
_RECALL_RE = re.compile("|".join(RECALL_PATTERNS))

# 疑问/时间词，提取实体时剔除
STOP = {"我", "你", "他", "她", "我们", "你们", "刚才", "刚刚", "之前", "上次", "昨天", "前天",
        "今天", "上午", "下午", "晚上", "小时", "什么", "啥", "来着", "说的", "说的什么",
        "记得", "还记", "那个", "这个", "是", "的", "了", "吗", "呢", "吧", "啊", "呀"}


def is_recall_question(text: str) -> bool:
    return bool(_RECALL_RE.search(str(text or "")))


def extract_terms(text: str) -> list[str]:
    body = str(text or "")
    terms = [t for t in keywords(body, 6) if t not in STOP and len(t) >= 1]
    # 中文 2~4 字实体优先，再去重
    out: list[str] = []
    for term in terms:
        if term not in out:
            out.append(term)
    return out[:6]


def remember(role: str, content: str, *, conv: str = "private:owner",
             is_owner: bool = False, ts: str | None = None) -> str:
    """写一条冷存储（role=user/assistant）。"""
    text = clip(content or "", 2000)
    if not text.strip():
        return ""
    row_id = new_id()
    try:
        db.ex("INSERT INTO instant_memory(id, ts, role, content, concepts, conv, is_owner) "
              "VALUES(?,?,?,?,?,?,?)",
              (row_id, ts or now_iso(), role, text,
               json.dumps(keywords(text, 6), ensure_ascii=False), conv, 1 if is_owner else 0))
    except Exception:
        return ""
    return row_id


def search(terms: list[str], *, hours: float = HOURS, limit: int = 8,
           conv: str = "", now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    sql = "SELECT * FROM instant_memory WHERE ts >= ?"
    args: list = [cutoff]
    if conv:
        sql += " AND conv=?"
        args.append(conv)
    if terms:
        like = " OR ".join(["content LIKE ?"] * len(terms))
        sql += f" AND ({like})"
        args.extend([f"%{t}%" for t in terms])
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(limit))
    rows = [dict(r) for r in db.q(sql, args)]
    if not rows and terms:
        # 关键词太窄 → 退回"最近几条"，让模型自己看
        rows = recent(hours=hours, limit=min(limit, 6), conv=conv, now=now)
    return rows


def recent(*, hours: float = HOURS, limit: int = 10, conv: str = "",
           now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    sql = "SELECT * FROM instant_memory WHERE ts >= ?"
    args: list = [cutoff]
    if conv:
        sql += " AND conv=?"
        args.append(conv)
    sql += " ORDER BY ts ASC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in db.q(sql, args)]


def format_block(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = ["[系统提示：以下是用户过去18小时内的相关对话记录，请据此回答]"]
    for row in rows:
        who = "用户" if row["role"] == "user" else "大肥鱼"
        stamp = str(row.get("ts") or "")[5:16].replace("T", " ")
        lines.append(f"- [{stamp}] {who}：{clip(row.get('content') or '', 160)}")
    return "\n".join(lines) + "\n\n"


def purge(hours: float = HOURS, now: datetime | None = None) -> int:
    now = now or datetime.now()
    cutoff = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        with db.write() as conn:
            cur = conn.execute("DELETE FROM instant_memory WHERE ts < ?", (cutoff,))
            return int(cur.rowcount or 0)
    except Exception:
        return 0


def status() -> dict:
    rows = db.q1("SELECT COUNT(*) n, MIN(ts) first, MAX(ts) last FROM instant_memory")
    return {"rows": int(rows["n"] or 0) if rows else 0,
            "first": (rows["first"] if rows else "") or "",
            "last": (rows["last"] if rows else "") or "",
            "hours": HOURS}
