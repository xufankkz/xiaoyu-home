# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""会话边界：把"一段时间内的连续对话"识别成一个 session。

之前没有会话概念：`chain_for(dialogue_id)` 每次拿到新的 dialogue_id，于是**每条消息都开一条新链**，
链内永远只有 1 个节点，也没有"上次会话"可言。这里补上：
- 同一个 conv 在 idle_timeout 内算同一会话，复用同一条链；
- 超时（或进程启动/手动新建）就 end()：把该会话的节点批量写 last_active_at / in_residual / session_tag，
  并把索引塞进 residual_index（只存指针，不复制正文）。
"""

from __future__ import annotations

from datetime import datetime

from .. import db, settings
from ..entity import new_id, now_iso
from ..repo import system as sr
from ..repo import thought as tr
from ..text import parse_iso

FILE = "sessions_v4.json"
QQ_PREFIXES = ("private:", "group:")


def cfg() -> dict:
    return settings.load("residual")


def state() -> dict:
    raw = settings.read_state(FILE, {})
    sessions = raw.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    raw["sessions"] = sessions
    return raw


def save(data: dict) -> dict:
    settings.write_state(FILE, data)
    return data


def kind_of(conv: str) -> str:
    return "qq" if str(conv or "").startswith(QQ_PREFIXES) else "desktop"


def idle_minutes(conv: str) -> float:
    table = cfg().get("idle_timeout_min") or {}
    return float(table.get(kind_of(conv), table.get("desktop", 3)))


def current(conv: str) -> dict | None:
    return state()["sessions"].get(conv)


def _expired(rec: dict, now: datetime) -> bool:
    last = parse_iso(rec.get("last_at") or "")
    if last is None:
        return True
    return (now - last).total_seconds() / 60.0 > idle_minutes(rec.get("conv") or "")


def touch(conv: str, now: datetime | None = None) -> dict:
    """记一次活动。返回 {session_tag, chain_id, is_new}。"""
    now = now or datetime.now()
    data = state()
    rec = data["sessions"].get(conv)
    if rec and not _expired(rec, now):
        rec["last_at"] = now.isoformat(timespec="seconds")
        rec["turns"] = int(rec.get("turns") or 0) + 1
        save(data)
        return {"session_tag": rec["session_tag"], "chain_id": rec["chain_id"],
                "is_new": False, "turns": rec["turns"]}

    if rec:
        end(conv, reason="idle")
        data = state()

    stamp = now.strftime("%Y%m%d%H%M%S")
    rec = {
        "conv": conv,
        "session_tag": f"{conv}#{stamp}",
        "chain_id": new_id(),
        "started_at": now.isoformat(timespec="seconds"),
        "last_at": now.isoformat(timespec="seconds"),
        "turns": 1,
        "node_ids": [],
    }
    data["sessions"][conv] = rec
    save(data)
    return {"session_tag": rec["session_tag"], "chain_id": rec["chain_id"],
            "is_new": True, "turns": 1}


def add_nodes(conv: str, node_ids: list[str]) -> None:
    if not node_ids:
        return
    data = state()
    rec = data["sessions"].get(conv)
    if rec is None:
        return
    rec["node_ids"] = list(dict.fromkeys(list(rec.get("node_ids") or []) + list(node_ids)))
    save(data)


def end(conv: str, reason: str = "idle", now: datetime | None = None) -> dict:
    """结束会话：节点转入残留池（只写索引，正文不动）。"""
    now = now or datetime.now()
    data = state()
    rec = data["sessions"].pop(conv, None)
    if not rec:
        return {"ended": False}
    closed_at = now.isoformat(timespec="seconds")
    node_ids = list(rec.get("node_ids") or [])
    touched = 0
    ended_nodes = []
    for node_id in node_ids:
        node = tr.get(node_id)
        if node is None:
            continue
        try:
            tr.update_fields(node_id, last_active_at=closed_at, in_residual=1,
                             session_tag=rec["session_tag"])
        except KeyError:
            continue
        ended_nodes.append(node)
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO residual_index(node_id, chain_id, session_tag, "
                "last_active_at, closed_at, in_residual) VALUES(?,?,?,?,?,1)",
                (node_id, node.chain_id, rec["session_tag"], closed_at, closed_at),
            )
        touched += 1
    # 未完成话题：open 的节点留下来，冷启动优先注入（v12 2.1）
    pending_count = 0
    try:
        from . import pending as pending_mod

        pending_count = pending_mod.open_for(rec["session_tag"], conv, ended_nodes, now)
    except Exception:
        pass
    save(data)
    if touched:
        sr.audit_add("thought", rec["session_tag"], "session_end",
                     {"reason": reason, "nodes": touched, "chain_id": rec["chain_id"],
                      "pending_topics": pending_count})
    return {"ended": True, "session_tag": rec["session_tag"], "nodes": touched,
            "chain_id": rec["chain_id"], "closed_at": closed_at,
            "pending_topics": pending_count}


def chain_for(conv: str) -> str:
    """同一会话复用同一条链（这是"链"第一次真正成立）。"""
    return touch(conv)["chain_id"]


def status() -> dict:
    data = state()
    return {
        "sessions": [{"conv": k, "session_tag": v.get("session_tag"),
                      "chain_id": v.get("chain_id"), "turns": v.get("turns"),
                      "last_at": v.get("last_at"), "nodes": len(v.get("node_ids") or [])}
                     for k, v in data["sessions"].items()],
        "idle": cfg().get("idle_timeout_min"),
    }
