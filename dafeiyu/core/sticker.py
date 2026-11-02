# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""表情包字典：把每张表情的含义、用法记在本地，下次直接用，不再花钱识图。

设计要点：
- 键是图片字节的 sha256（同一张图，无论从哪来，都是同一条）；
- 值不只是"是什么"，还有"什么时候用"（contexts）和用了几次（usages）；
- 首次识别要花钱，之后每次查表 ¥0；
- 她会自己修正：再次识图结果不同就覆盖摘要，人工/使用观察追加用法。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from . import db
from .entity import loads, now_iso
from .text import clip


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except Exception:
        return ""
    return digest.hexdigest()


def lookup(sha: str) -> dict | None:
    """按 sha 查字典。命中就返回含义与用法，不调模型。"""
    if not sha:
        return None
    row = db.q1("SELECT * FROM sticker_cache WHERE sha256=?", (sha,))
    if row is None:
        return None
    try:
        db.ex("UPDATE sticker_cache SET hits=hits+1, last_seen=?, last_used_at=? WHERE sha256=?",
              (now_iso(), now_iso(), sha))
        row = db.q1("SELECT * FROM sticker_cache WHERE sha256=?", (sha,)) or row
    except Exception:
        pass
    data = dict(row)
    data["concepts"] = loads(data.get("concepts"), [])
    data["contexts"] = loads(data.get("contexts"), [])
    return data


def remember(sha: str, *, summary: str, concepts: list[str] | None = None,
             kind: str = "sticker", frames: int = 0, duration_ms: int = 0,
             note: str = "") -> bool:
    """首次识别（或修正）后写进字典。同一张图再写就是覆盖摘要，不新增行。"""
    if not sha or not (summary or "").strip():
        return False
    try:
        existing = db.q1("SELECT hits, usages, contexts FROM sticker_cache WHERE sha256=?", (sha,))
        if existing:
            db.ex("UPDATE sticker_cache SET summary=?, concepts=?, note=?, kind=?, frames=?, "
                  "duration_ms=?, last_seen=? WHERE sha256=?",
                  (clip(summary, 800), json.dumps(concepts or [], ensure_ascii=False),
                   clip(note or existing.get("hits") and note or note, 200) if note else "",
                   kind, int(frames), int(duration_ms), now_iso(), sha))
        else:
            db.ex("INSERT INTO sticker_cache(sha256, t, frames, duration_ms, summary, concepts, "
                  "note, hits, last_seen) VALUES(?,?,?,?,?,?,?,1,?) "
                  "ON CONFLICT(sha256) DO UPDATE SET summary=excluded.summary",
                  (sha, now_iso(), int(frames), int(duration_ms), clip(summary, 800),
                   json.dumps(concepts or [], ensure_ascii=False), clip(note, 200), now_iso()))
        return True
    except Exception:
        return False


def annotate(sha: str, note: str) -> bool:
    """人工批注（老大教她认表情）。没收录过就建一条空壳，光有称呼也算数。"""
    if not sha or not note:
        return False
    try:
        if db.q1("SELECT 1 FROM sticker_cache WHERE sha256=?", (sha,)) is None:
            db.ex("INSERT INTO sticker_cache(sha256, t, summary, note, hits, last_seen) "
                  "VALUES(?,?,?,?,0,?)", (sha, now_iso(), clip(note, 200), clip(note, 200), now_iso()))
        else:
            db.ex("UPDATE sticker_cache SET note=? WHERE sha256=?", (clip(note, 200), sha))
        return True
    except Exception:
        return False


def observe_usage(sha: str, context: str = "", *, sent: bool = True) -> bool:
    """她用/看到这张表情时的语境：攒起来，越用越懂什么时候该发。"""
    if not sha:
        return False
    try:
        row = db.q1("SELECT contexts, usages FROM sticker_cache WHERE sha256=?", (sha,))
        if row is None:
            return False
        contexts = loads(row["contexts"], [])
        text = clip(context or "", 60)
        if text and text not in contexts:
            contexts = (contexts + [text])[-8:]
        db.ex("UPDATE sticker_cache SET contexts=?, usages=usages+?, last_used_at=? WHERE sha256=?",
              (json.dumps(contexts, ensure_ascii=False), 1 if sent else 0, now_iso(), sha))
        return True
    except Exception:
        return False


def describe(data: dict | None, *, with_usage: bool = True) -> str:
    """给提示词用的一句话。"""
    if not data:
        return ""
    text = str(data.get("summary") or "").strip()
    note = str(data.get("note") or "").strip()
    contexts = data.get("contexts") or []
    parts = [p for p in (text, f"老大叫它「{note}」" if note else "") if p]
    if with_usage and contexts:
        parts.append("这么用：" + "；".join(str(c) for c in contexts[-3:]))
    return "｜".join(parts)


def status() -> dict:
    row = db.q1("SELECT COUNT(*) n, SUM(CASE WHEN hits > 1 THEN 1 ELSE 0 END) reused, "
                "SUM(usages) uses FROM sticker_cache")
    top = db.q(
        "SELECT sha256, summary, usages, hits, contexts FROM sticker_cache "
        "ORDER BY usages DESC, hits DESC LIMIT 6")
    return {
        "total": int(row["n"] or 0) if row else 0,
        "reused": int(row["reused"] or 0) if row else 0,
        "uses": int(row["uses"] or 0) if row else 0,
        "top": [{"sha": r["sha256"][:12], "summary": clip(r["summary"] or "", 60),
                 "usages": r["usages"], "hits": r["hits"],
                 "contexts": loads(r["contexts"], [])[-2:]} for r in top],
    }


def all_entries(limit: int = 200) -> list[dict]:
    rows = db.q("SELECT sha256, summary, note, concepts, contexts, hits, usages, last_used_at "
                "FROM sticker_cache ORDER BY last_used_at DESC, hits DESC LIMIT ?", (int(limit),))
    out = []
    for row in rows:
        item = dict(row)
        item["concepts"] = loads(item.get("concepts"), [])
        item["contexts"] = loads(item.get("contexts"), [])
        out.append(item)
    return out
