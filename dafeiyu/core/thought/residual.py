# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""跨会话思维链残留通道。

节点可见性不再绑会话：会话断开 → 进残留池 → 按双指数遗忘曲线 R(t) 慢慢淡出。
R(t) = w_f·exp(-t/τ_f) + (1-w_f)·exp(-t/τ_s)，τ 随 salience 与 open 放大（蔡格尼克效应）。

残留通道**只是可见性层**：不改原 strength 衰减、不改 level 降级、不改蒸馏/墓碑、不改检索排序。
注入粒度取 max(节点原 level, 残留档位)，墓碑（level=3）不回正文。
"""

from __future__ import annotations

import math
from datetime import datetime

from .. import db, settings
from ..entity import TAG_DIM, loads, now_iso
from ..repo import system as sr
from ..repo import thought as tr
from ..text import clip, parse_iso

STATE_FILE = "residual_state.json"


def cfg() -> dict:
    return settings.load("residual")


def enabled() -> bool:
    return bool(cfg().get("enabled", True))


# ── 曲线 ────────────────────────────────────────────────────────────────────
def strength(node: dict, now: datetime | None = None, vec=None) -> float:
    """节点此刻的残留强度 R ∈ [0,1]。node 可以是 row 或 dict。"""
    now = now or datetime.now()
    last = parse_iso(node.get("last_active_at") or "") or parse_iso(node.get("t_created") or "")
    if last is None:
        return 0.0
    minutes = max(0.0, (now - last).total_seconds() / 60.0)
    c = cfg()
    raw_sal = node.get("salience")
    salience = 0.5 if raw_sal is None else float(raw_sal)
    is_open = node.get("status") == "open" or node.get("type") == "open_question"
    factor = (1.0 + float(c.get("salience_boost", 0.5)) * salience) * \
             (1.0 + float(c.get("open_boost", 0.8)) * (1.0 if is_open else 0.0))
    tau_f = float(c.get("tau_fast_min", 8)) * factor
    tau_s = float(c.get("tau_slow_min", 55)) * factor
    w = float(c.get("w_fast", 0.65))
    value = w * math.exp(-minutes / max(0.1, tau_f)) + (1.0 - w) * math.exp(-minutes / max(0.1, tau_s))
    if vec is not None:
        dim = TAG_DIM.get(node.get("emotion_tag") or "平静", "")
        if dim and dim == getattr(vec, "dominant", ""):
            value *= 1.0 + float(c.get("mood_bonus", 0.1))
    return round(max(0.0, min(1.0, value)), 4)


def tier(r: float) -> int | None:
    c = cfg()
    if r >= float(c.get("level0_threshold", 0.50)):
        return 0
    if r >= float(c.get("level1_threshold", 0.22)):
        return 1
    if r >= float(c.get("level2_threshold", 0.08)):
        return 2
    return None


def level_for(node: dict, chunk: int | None) -> int:
    """实际注入粒度：取两者中压缩更狠的那档。"""
    base = int(node.get("level") or 0)
    if base >= 3:
        return 3
    if chunk is None:
        return base
    return max(base, {0: 0, 1: 1, 2: 2}[chunk])


def text_for(node: dict, level: int) -> dict:
    concepts = loads(node.get("concepts"), [])
    if level >= 3:
        return {"text": clip(node.get("summary") or "", 120), "concepts": concepts, "tombstone": True}
    if level == 2:
        return {"text": clip(node.get("skeleton") or node.get("summary") or "", 160),
                "concepts": concepts, "tombstone": False}
    if level == 1:
        return {"text": clip(node.get("summary") or node.get("content") or "", 200),
                "concepts": concepts, "tombstone": False}
    return {"text": (node.get("content") or "")[:800], "concepts": concepts, "tombstone": False}


# ── 池 ──────────────────────────────────────────────────────────────────────
def _rows(in_residual: int | None = 1) -> list[dict]:
    # reflection 是"回了几个字"这类记账，不是想法本身，不进残留池
    sql = ("SELECT n.*, r.closed_at AS r_closed_at FROM thought_node n "
           "JOIN residual_index r ON r.node_id = n.id WHERE n.type != 'reflection'")
    args: list = []
    if in_residual is not None:
        sql += " AND r.in_residual=?"
        args.append(in_residual)
    return [dict(row) for row in db.q(sql, args)]


def _entry(node: dict, now: datetime, vec=None) -> dict:
    r = strength(node, now, vec)
    chunk = tier(r)
    level = level_for(node, chunk)
    last = parse_iso(node.get("last_active_at") or "") or now
    delta = max(0, int((now - last).total_seconds() / 60))
    detail = text_for(node, level)
    return {
        "id": node["id"], "chain_id": node["chain_id"], "type": node["type"],
        "status": node["status"], "session_tag": node.get("session_tag") or "",
        "emotion_tag": node.get("emotion_tag") or "平静",
        "r": r, "tier": chunk, "level": level,
        "minutes": delta, "is_open": node.get("status") == "open" or node.get("type") == "open_question",
        "salience": round(float(node.get("salience") or 0), 3),
        "text": detail["text"], "concepts": detail["concepts"],
        "tombstone": detail["tombstone"],
    }


def pool(limit: int | None = None, vec=None, now: datetime | None = None) -> list[dict]:
    """残留池（按 R 降序，只含 R ≥ level2 门槛的）。"""
    now = now or datetime.now()
    c = cfg()
    floor = float(c.get("level2_threshold", 0.08))
    items = []
    for node in _rows(1):
        entry = _entry(node, now, vec)
        if entry["r"] < floor:
            continue
        items.append(entry)
    items.sort(key=lambda e: e["r"], reverse=True)
    return items[: int(limit or c.get("pool_capacity", 200))]


def sweep(now: datetime | None = None, vec=None) -> dict:
    """每 10 分钟：低于门槛的移出池；超容量按 R 升序淘汰；未完成话题超 72h 降级。"""
    now = now or datetime.now()
    pending_downgraded = 0
    try:
        from . import pending as pending_mod

        pending_downgraded = pending_mod.downgrade_stale(now=now)
    except Exception:
        pass
    c = cfg()
    floor = float(c.get("level2_threshold", 0.08))
    capacity = int(c.get("pool_capacity", 200))
    rows = _rows(1)
    dropped = []
    keep = []
    for node in rows:
        entry = _entry(node, now, vec)
        if entry["r"] < floor:
            dropped.append(entry["id"])
        else:
            keep.append(entry)
    keep.sort(key=lambda e: e["r"], reverse=True)
    overflow = keep[capacity:]
    dropped += [e["id"] for e in overflow]
    if dropped:
        with db.write() as conn:
            conn.executemany("UPDATE residual_index SET in_residual=0 WHERE node_id=?",
                             [(node_id,) for node_id in dropped])
            conn.executemany("UPDATE thought_node SET in_residual=0 WHERE id=?",
                             [(node_id,) for node_id in dropped])
        sr.audit_add("thought", "residual", "sweep",
                     {"dropped": len(dropped), "kept": len(keep) - len(overflow)})
    return {"scanned": len(rows), "dropped": len(dropped), "kept": len(keep) - len(overflow),
            "pending_downgraded": pending_downgraded,
            "dropped_ids": dropped[:20]}


# ── 冷启动注入 ──────────────────────────────────────────────────────────────
def _state() -> dict:
    raw = settings.read_state(STATE_FILE, {})
    if "injected" not in raw or not isinstance(raw.get("injected"), dict):
        raw["injected"] = {}
    return raw


def _save_state(data: dict) -> None:
    # 只留最近 50 个会话的注入记录，别无限长
    injected = data.get("injected") or {}
    for key in list(injected)[:-50]:
        injected.pop(key, None)
    settings.write_state(STATE_FILE, data)


def _format_block(entries: list[dict]) -> str:
    if not entries:
        return ""
    lines = ["【残留思维（会话断开后还记得的）】"]
    for e in entries:
        if e["minutes"] < 1:
            ago = "刚刚"
        elif e["minutes"] < 60:
            ago = f"约{e['minutes']}分钟前"
        else:
            ago = f"约{e['minutes'] // 60}小时前"
        kind = {0: "原文", 1: "摘要", 2: "骨架", 3: "印象"}.get(e["level"], "摘要")
        head = f"[残留思维 | {ago} | {kind}]"
        lines.append(head)
        body = e["text"] or "（只剩概念）"
        lines.append(f"- 内容：{body}")
        if e["is_open"]:
            lines.append("- 未决：还没想完")
        if e["concepts"]:
            # 只展示像"词"的（≥3 字）；全是碎片就干脆不显示，别刷屏
            nice = [str(c) for c in e["concepts"] if len(str(c)) >= 3][:5]
            if nice:
                lines.append(f"- 关联概念：{'、'.join(nice)}")
    lines.append("（这些是我自己刚才还在想的东西，自然相关就提一句，别硬提，也别报时间数字。）")
    return "\n".join(lines) + "\n\n"


def cold_start(conv: str, k: int | None = None, vec=None, session_tag: str | None = None,
               now: datetime | None = None) -> dict:
    """新会话第一次说话时，把残留池里最强的几条拉回工作记忆。每个会话只做一次。"""
    now = now or datetime.now()
    if not enabled():
        return {"skipped": "residual disabled"}
    from . import session as session_mod

    tag = session_tag or (session_mod.current(conv) or {}).get("session_tag") or conv
    data = _state()
    if tag in data["injected"]:
        return {"skipped": "already injected", "session_tag": tag, "nodes": []}

    c = cfg()
    limit = int(k or c.get("cold_start_topk", 6))
    per_chain = int(c.get("per_chain_cap", 3))
    candidates = pool(vec=vec, now=now)
    scored = []
    for e in candidates:
        score = e["r"] * (1.5 if e["is_open"] else 1.0) * (1.0 + 0.3 * e["salience"])
        scored.append((score, e))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    picked: list[dict] = []
    chain_count: dict[str, int] = {}
    for _score, entry in scored:
        if chain_count.get(entry["chain_id"], 0) >= per_chain:
            continue
        chain_count[entry["chain_id"]] = chain_count.get(entry["chain_id"], 0) + 1
        picked.append(entry)
        if len(picked) >= limit:
            break

    # 未完成话题优先：上次没聊完的先回到意识（v12 2.1）
    topics: list[dict] = []
    try:
        from . import pending as pending_mod

        topics = pending_mod.active(limit=3, now=now, conv=conv)
    except Exception:
        topics = []

    if not picked and not topics:
        data["injected"][tag] = {"at": now.isoformat(timespec="seconds"), "nodes": []}
        _save_state(data)
        return {"session_tag": tag, "nodes": [], "block": ""}

    from ..memory import working

    for entry in picked:
        try:
            working.bind("thought", entry["id"], weight=0.9)
        except Exception:
            pass

    data["injected"][tag] = {"at": now.isoformat(timespec="seconds"),
                             "nodes": [{"id": e["id"], "r": e["r"], "tier": e["tier"],
                                        "minutes": e["minutes"]} for e in picked],
                             "topics": [{"id": t["id"], "topic": t["topic"]} for t in topics]}
    _save_state(data)
    sr.audit_add("thought", tag, "residual_injected",
                 {"count": len(picked), "ids": [e["id"] for e in picked],
                  "tiers": [e["tier"] for e in picked], "pending": len(topics)})
    for entry in picked:
        touch(entry["id"], now=now)
    for topic in topics:
        try:
            from . import pending as pending_mod

            pending_mod.touch(topic["id"], now)
        except Exception:
            pass
    block = _format_pending(topics) + _format_block(picked)
    return {"session_tag": tag, "nodes": picked, "topics": topics, "block": block}


def _format_pending(topics: list[dict]) -> str:
    if not topics:
        return ""
    lines = ["【上次没聊完的】"]
    for topic in topics:
        age = int(topic.get("age_minutes") or 0)
        ago = "刚刚" if age < 1 else (f"约{age}分钟前" if age < 60 else f"约{age // 60}小时前")
        lines.append(f"- {topic['topic']}（{ago}停在这里）")
    lines.append("（可以自然地问一句要不要接着说，别硬接。）")
    return "\n".join(lines) + "\n\n"


def inject_block(conv: str, k: int | None = None, vec=None) -> str:
    return cold_start(conv, k=k, vec=vec).get("block", "")


# ── 访问刷新（复述效应）────────────────────────────────────────────────────
def touch(node_id: str, now: datetime | None = None) -> None:
    """被注入/被检索命中：last_active_at 归零重算，salience 轻微上调。"""
    now = now or datetime.now()
    node = tr.get(node_id)
    if node is None:
        return
    gain = float(cfg().get("touch_salience_gain", 0.05))
    try:
        tr.update_fields(node_id, last_active_at=now.isoformat(timespec="seconds"),
                         salience=min(1.0, float(node.salience) + gain))
    except KeyError:
        pass
    with db.write() as conn:
        conn.execute("UPDATE residual_index SET last_active_at=? WHERE node_id=?",
                     (now.isoformat(timespec="seconds"), node_id))


def injected_this_session(conv: str) -> dict:
    from . import session as session_mod

    tag = (session_mod.current(conv) or {}).get("session_tag") or ""
    entry = _state()["injected"].get(tag) or {}
    return {"session_tag": tag, **entry}


def status(vec=None) -> dict:
    items = pool(vec=vec)
    return {
        "enabled": enabled(),
        "pool": len(items),
        "capacity": int(cfg().get("pool_capacity", 200)),
        "top": items[:10],
        "counts": {str(t): sum(1 for e in items if e["tier"] == t) for t in (0, 1, 2)},
        "config": cfg(),
    }
