# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""把旧系统（文件 + JSON）搬进 SQLite。

设计原则：
- **幂等**：每条记录用「来源 + 日期 + 正文前 40 字」的 sha1 当 id，重跑只会覆盖，不会翻倍。
- **不删原件**：旧 markdown / jsonl 原样留在原地，桥接还在读。
- **保留现场**：情绪现值照搬（不重置成规格默认），冷却状态照搬，奖罚历史一并入库。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .. import db, settings
from ..entity import (
    DIMS, EmotionVector, Layer, MemoryItem, RewardEvent, Status, dumps, now_iso,
)
from ..repo import emotion as er, memory as mr, system as sr
from ..text import char_count, keywords, sha1

LT1_FILES = {
    "01-性格设定.md": ("personality", "性格设定"),
    "02-朋友档案.md": ("friend", "朋友档案"),
    "03-老大.md": ("owner", "老大"),
}
CN2DIM = {"愉悦": "joy", "幸福": "happiness", "难过": "sadness", "自卑": "inferiority", "害羞": "shyness"}
BLOCK = re.compile(r"^###\s+(.+)$", re.M)


def _id(*parts: str) -> str:
    return sha1("|".join(str(p) for p in parts))


def _iso(ms: int | float | None) -> str:
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0).isoformat(timespec="seconds")
    except Exception:
        return now_iso()


# ── 2 号长期记忆 ────────────────────────────────────────────────────────────
def import_lt2() -> int:
    total = 0
    for cat in ("开心", "难过", "生气", "创伤", "平静", "害羞"):
        path = settings.LEGACY_LT2 / f"{cat}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        parts = BLOCK.split(text)
        for i in range(1, len(parts), 2):
            heading, body = parts[i].strip(), parts[i + 1] if i + 1 < len(parts) else ""
            lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not re.fullmatch(r"-{3,}", ln.strip())]
            paragraphs, meta = [], {}
            for ln in lines:
                m = re.match(r"^-\s*([^：:]+)[：:]\s*(.*)$", ln)
                if m:
                    meta[m.group(1).strip()] = m.group(2).strip()
                elif not ln.startswith("- "):
                    paragraphs.append(ln)
            content = " ".join(paragraphs).strip()
            if not content:
                continue
            date = (re.match(r"(\d{4}-\d{2}-\d{2})", heading) or [None, ""])[1] if re.match(r"\d{4}-\d{2}-\d{2}", heading) else ""
            intensity = heading.count("●")
            item = MemoryItem(
                id=_id("lt2", cat, heading, content[:40]),
                layer=Layer.LONG2.value,
                subtype="summary",
                content=content,
                summary=content[:60],
                skeleton="",
                concepts=keywords(content, 6) + [cat],
                emotion_tag=cat,
                salience=round(min(1.0, 0.4 + 0.12 * max(1, intensity)), 2),
                strength=1.0,
                level=0,
                status=Status.OPEN.value,
                t_created=f"{date}T12:00:00" if date else now_iso(),
                origin="legacy_md",
                ext={"heading": heading, "date": date, "intensity": intensity,
                     "impact": meta.get("影响", ""), "labels": meta.get("标签", ""),
                     "source": meta.get("来源", ""), "category": cat, "seq": i // 2},
            )
            mr.upsert(item)
            mr.set_index(item.id, "memory", [], item.concepts)
            total += 1
    return total


# ── 1 号长期记忆 ────────────────────────────────────────────────────────────
def import_lt1() -> int:
    total = 0
    for fname, (subtype, title) in LT1_FILES.items():
        path = settings.LEGACY_MEM / "1号长期记忆" / fname
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            continue
        body = re.sub(r"^#.*$", "", text, flags=re.M).strip()
        item = MemoryItem(
            id=_id("lt1", fname, body[:40]),
            layer=Layer.LONG1.value,
            subtype=subtype,
            content=text.strip(),
            summary=f"{title}（{char_count(body)} 字）",
            skeleton=title,
            concepts=keywords(body, 8) + [title],
            emotion_tag="平静",
            salience=0.9,
            t_created=now_iso(),
            origin="legacy_md",
            status=Status.RESOLVED.value,
            ext={"file": fname},
        )
        mr.upsert(item)
        mr.set_index(item.id, "memory", [], item.concepts)
        total += 1
    return total


# ── 短期：待总结队列 ────────────────────────────────────────────────────────
def import_pending() -> int:
    path = settings.LEGACY_PENDING / "队列.json"
    rows = settings.read_json(path, []) or []
    total = 0
    for r in rows:
        text = str(r.get("text") or "").strip()
        if not text:
            continue
        day, t = str(r.get("date") or ""), str(r.get("time") or "00:00")
        status = {"pending": Status.OPEN.value, "promoted": Status.RESOLVED.value,
                  "consolidated": Status.RESOLVED.value}.get(str(r.get("status")), Status.OPEN.value)
        item = MemoryItem(
            id=_id("pending", day, t, text[:40]),
            layer=Layer.SHORT.value,
            subtype="pending",
            content=text,
            summary=str(r.get("summary") or ""),
            skeleton=str(r.get("topic") or ""),
            concepts=keywords(text, 6),
            emotion_tag=str(r.get("emotion") or "平静"),
            salience=0.6 if status == Status.OPEN.value else 0.4,
            status=status,
            t_created=f"{day}T{t}:00" if day else now_iso(),
            origin="legacy_queue",
            cluster=str(r.get("topic") or ""),
            ext={"legacy_id": r.get("id"), "date": day, "time": t,
                 "legacy_status": r.get("status"), "summary_chars": r.get("summary_chars", 0)},
        )
        mr.upsert(item)
        mr.set_index(item.id, "memory", [], item.concepts)
        total += 1
    return total


# ── 短期：日记 ──────────────────────────────────────────────────────────────
def import_diary() -> int:
    folder = settings.LEGACY_DIARY
    if not folder.exists():
        return 0
    total = 0
    for path in sorted(folder.glob("*.md")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
            continue
        day = path.stem
        text = path.read_text(encoding="utf-8", errors="replace")
        sections = re.split(r"^##\s+(\d{1,2}:\d{2})(.*)$", text, flags=re.M)
        for i in range(1, len(sections), 3):
            t = sections[i]
            body = sections[i + 2] if i + 2 < len(sections) else ""
            body = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("<!--")).strip()
            if char_count(body) < 8:
                continue
            first = re.split(r"[。！？!?\n]", body)[0]
            item = MemoryItem(
                id=_id("diary", day, t, body[:40]),
                layer=Layer.SHORT.value,
                subtype="diary",
                content=body,
                summary=body[:60],
                skeleton=re.sub(r"[，,、；;：:\s]", "", first)[:16],
                concepts=keywords(body, 6),
                emotion_tag="平静",
                salience=0.5,
                t_created=f"{day}T{t}:00",
                origin="diary",
                status=Status.RESOLVED.value,
                cluster=re.sub(r"[，,、；;：:\s]", "", first)[:16],
                ext={"date": day, "time": t, "file": path.name},
            )
            mr.upsert(item)
            mr.set_index(item.id, "memory", [], item.concepts)
            total += 1
    return total


# ── 瞬时记忆 ────────────────────────────────────────────────────────────────
def import_instant() -> int:
    total = 0
    for name in ("热区.jsonl", "温区.jsonl"):
        path = settings.LEGACY_INSTANT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            text = str(r.get("text") or "").strip()
            if not text:
                continue
            ts = r.get("ts")
            is_owner = bool(r.get("is_owner"))
            is_self = bool(r.get("is_self"))
            item = MemoryItem(
                id=_id("instant", str(ts), text[:40]),
                layer=Layer.INSTANT.value,
                subtype="chat",
                content=text,
                summary=text[:60],
                concepts=keywords(text, 5),
                emotion_tag="平静",
                salience=0.6 if is_owner else (0.5 if is_self else 0.4),
                t_created=_iso(ts),
                origin="legacy_instant",
                ext={"conv": r.get("conv"), "label": r.get("label"), "sender": r.get("sender"),
                     "is_self": is_self, "is_owner": is_owner, "ts": ts, "kind": r.get("kind"),
                     "zone": "hot" if name.startswith("热") else "warm"},
            )
            mr.upsert(item)
            mr.set_index(item.id, "memory", [], item.concepts)
            total += 1
    return total


# ── 语言记忆 / 情绪记忆 ─────────────────────────────────────────────────────
def import_language_memory() -> int:
    path = settings.LEGACY_LANG_MEM / "语言记忆.md"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    total = 0
    parts = re.split(r"^##\s+(.+)$", text, flags=re.M)
    for i in range(1, len(parts), 2):
        head, body = parts[i].strip(), parts[i + 1] if i + 1 < len(parts) else ""
        body = body.strip()
        if char_count(body) < 20:
            continue
        date = ""
        m = re.search(r"(\d{4}-\d{2}-\d{2})", head)
        if m:
            date = m.group(1)
        item = MemoryItem(
            id=_id("langmem", head, body[:40]),
            layer=Layer.LANGUAGE.value,
            subtype="summary",
            content=body,
            summary=head[:60],
            concepts=keywords(body, 6) + ["语言"],
            emotion_tag="平静",
            salience=0.7,
            t_created=f"{date}T01:00:00" if date else now_iso(),
            origin="legacy_md",
            status=Status.RESOLVED.value,
            ext={"head": head, "chars": char_count(body)},
        )
        mr.upsert(item)
        mr.set_index(item.id, "memory", [], item.concepts)
        total += 1
    return total


def import_emotion_memory() -> int:
    path = settings.LEGACY_EMO_MEM / "情绪记忆.md"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    total = 0
    parts = re.split(r"^##\s+(.+)$", text, flags=re.M)
    for i in range(1, len(parts), 2):
        head, body = parts[i].strip(), parts[i + 1] if i + 1 < len(parts) else ""
        body = body.strip()
        if char_count(body) < 20:
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", head)
        date = m.group(1) if m else ""
        item = MemoryItem(
            id=_id("emomem", head, body[:40]),
            layer=Layer.EMOTION.value,
            subtype="record",
            content=body,
            summary=head[:60],
            concepts=keywords(body, 6) + ["情绪"],
            emotion_tag="平静",
            salience=0.7,
            t_created=f"{date}T01:30:00" if date else now_iso(),
            origin="legacy_md",
            status=Status.RESOLVED.value,
            ext={"head": head, "chars": char_count(body)},
        )
        mr.upsert(item)
        mr.set_index(item.id, "memory", [], item.concepts)
        total += 1
    return total


# ── 情绪现值 + 奖罚历史 ────────────────────────────────────────────────────
def import_emotion_state() -> dict:
    path = settings.LEGACY_EMOTION_STATE
    data = settings.read_json(path, {}) or {}
    if not data:
        return {"imported": False}
    emo = data.get("emotions") or {}
    setpoints = data.get("setpoints") or {}
    values = {CN2DIM[k]: float(v) for k, v in emo.items() if k in CN2DIM}
    baseline = {CN2DIM[k]: float(v) for k, v in setpoints.items() if k in CN2DIM}
    if not values:
        return {"imported": False}
    vec = EmotionVector.from_json({
        "positive": {k: v for k, v in values.items() if k in ("joy", "happiness")},
        "negative": {k: v for k, v in values.items() if k in ("sadness", "inferiority")},
        "other": {k: v for k, v in values.items() if k == "shyness"},
        "baseline": baseline,
        "updated_at": data.get("updatedAt") or now_iso(),
    })
    settings.write_state("emotion.json", vec.to_json())
    er.snapshot_add(vec.values, vec.dominant, "legacy_import")

    events = data.get("events") or []
    n = 0
    for e in events:
        delta_cn = e.get("delta") or {}
        delta = {CN2DIM[k]: float(v) for k, v in delta_cn.items() if k in CN2DIM}
        if not delta:
            continue
        kind = str(e.get("kind") or "reward")
        rtype = "punishment" if kind in ("punish", "ignored", "punishment") else "reward"
        trigger = {"reward": "praise", "punish": "insult", "care": "cared", "ignored": "ignored"}.get(kind, "praise")
        ev = RewardEvent(
            id=_id("legacy_reward", str(e.get("t")), str(e.get("text"))[:40]),
            type=rtype,
            trigger=trigger,
            source_text=str(e.get("text") or e.get("why") or ""),
            delta=delta,
            rpe_weight=1.0,
            t=_iso(e.get("t")),
        )
        er.reward_insert(ev)
        n += 1
    return {"imported": True, "values": values, "baseline": baseline, "events": n}


# ── 冷却 / 问答状态 ────────────────────────────────────────────────────────
def import_cooldown() -> dict:
    path = settings.LEGACY_RUN / "state.json"
    data = settings.read_json(path, {}) or {}
    if not data:
        return {}
    lang = data.get("language") or {}
    emo = data.get("emotion") or {}
    state = {
        "language": {
            "last_learned_at": lang.get("last_summary") or "",
            "cooldown_until": lang.get("locked_until") or "",
            "memory_chars": 0,
            "awaiting_user": bool(lang.get("ask_pending")),
            "lock_started": lang.get("lock_started") or "",
            "ask_history": lang.get("ask_history") or [],
        },
        "emotion": {
            "last_learned_at": emo.get("last_record") or "",
            "cooldown_until": emo.get("locked_until") or "",
            "memory_chars": 0,
            "awaiting_user": bool(emo.get("ask_pending")),
            "lock_started": emo.get("lock_started") or "",
            "ask_history": emo.get("ask_history") or [],
        },
    }
    settings.write_state("cooldown.json", state)
    return state


# ── 直接从桥接状态读消息（取代旧任务 --maintenance 的瞬时同步）────────────
def import_from_bridge(limit_per_conv: int = 100) -> int:
    """从 qq-bridge\\state\\social-v2.json 的 recentMessages 增量收进瞬时记忆。

    id 与 import_instant 用同一套（来源忽略、按 ts+正文前 40 字），
    所以同一句话不管是旧 jsonl 来的还是这里来的，都只会有一条。
    """
    bridge_dir = settings.app().get("bridge", {}).get("dir") or r""
    path = Path(str(bridge_dir)) / "state" / "social-v2.json"
    data = settings.read_json(path, {}) or {}
    conversations = data.get("conversations") or {}
    total = 0
    for key, conv in conversations.items():
        messages = (conv or {}).get("recentMessages") or []
        for msg in messages[-int(limit_per_conv):]:
            text = str(msg.get("plain") or msg.get("text") or "").strip()
            if not text:
                continue
            ts = msg.get("time")
            is_owner = bool(msg.get("isOwner"))
            is_self = bool(msg.get("isSelf"))
            label = "老大私聊" if key.startswith("private") else "群聊"
            item = MemoryItem(
                id=_id("instant", str(ts), text[:40]),
                layer=Layer.INSTANT.value,
                subtype="chat",
                content=text,
                summary=text[:60],
                concepts=keywords(text, 5),
                emotion_tag="平静",
                salience=0.6 if is_owner else (0.5 if is_self else 0.4),
                status=Status.RESOLVED.value,
                t_created=_iso(ts),
                origin="bridge",
                ext={"conv": key, "label": label, "sender": msg.get("sender") or "",
                     "is_self": is_self, "is_owner": is_owner, "ts": ts, "kind": "chat",
                     "zone": "hot", "messageId": msg.get("messageId")},
            )
            mr.upsert(item)
            mr.set_index(item.id, "memory", [], item.concepts)
            total += 1
    return total


def run() -> dict:
    settings.ensure_dirs()
    db.init_db()
    result = {
        "lt1": import_lt1(),
        "lt2": import_lt2(),
        "pending": import_pending(),
        "diary": import_diary(),
        "instant": import_instant(),
        "language": import_language_memory(),
        "emotion_memory": import_emotion_memory(),
        "emotion_state": import_emotion_state(),
        "cooldown": bool(import_cooldown()),
    }
    result["db"] = db.stats()
    return result


if __name__ == "__main__":
    import sys

    out = run()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0)
