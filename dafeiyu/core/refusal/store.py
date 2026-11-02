# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""拒绝决策的落库与运行态。

表：refusal_decision（结构化，可查）
文件：state/refusal.json（预算窗口、连拒计数、傲娇余额）
      state/refusal_log/<日期>.jsonl（审计流水，一行一条）
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .. import db, settings
from ..entity import dumps, loads, now_iso
from ..repo import system as sr

STATE_FILE = "refusal.json"
REFUSE_KINDS = ("REFUSE", "DEFLECT")


def state() -> dict:
    raw = settings.read_state(STATE_FILE, {})
    return {
        "refusals": list(raw.get("refusals") or []),      # ISO 时间戳列表（近 2 小时）
        "tsundere": list(raw.get("tsundere") or []),      # ISO 时间戳列表
        "consecutive_refusals": int(raw.get("consecutive_refusals") or 0),
        "last_decision_at": str(raw.get("last_decision_at") or ""),
    }


def save_state(data: dict) -> dict:
    settings.write_state(STATE_FILE, data)
    return data


def prune(data: dict) -> dict:
    cutoff = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    data["refusals"] = [t for t in data.get("refusals", []) if t >= cutoff][-50:]
    data["tsundere"] = [t for t in data.get("tsundere", []) if t >= cutoff][-50:]
    return data


def budget_used(data: dict | None = None, window_minutes: int = 60) -> dict:
    data = data or state()
    cutoff = (datetime.now() - timedelta(minutes=window_minutes)).isoformat(timespec="seconds")
    return {
        "refuse": sum(1 for t in data.get("refusals", []) if t >= cutoff),
        "tsundere": sum(1 for t in data.get("tsundere", []) if t >= cutoff),
    }


def insert(decision: dict) -> None:
    db.ex(
        "INSERT OR REPLACE INTO refusal_decision(id, t, request_summary, signals, propensity, "
        "threshold, decision, reason_text, style, alternatives, user_reaction, reacted_at, "
        "linked_thought_node_id, linked_intent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision["id"], decision["t"], decision["request_summary"],
         dumps(decision.get("signals") or {}), float(decision.get("propensity") or 0),
         float(decision.get("threshold") or 0), decision["decision"],
         decision.get("reason_text") or "", decision.get("style") or "normal",
         dumps(decision.get("alternatives") or []), decision.get("user_reaction") or "",
         decision.get("reacted_at") or "", decision.get("linked_thought_node_id") or "",
         decision.get("linked_intent_id") or ""),
    )


def log_line(decision: dict) -> None:
    folder = settings.STATE / "refusal_log"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y-%m-%d}.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent(limit: int = 30, decision: str = "") -> list[dict]:
    if decision:
        rows = db.q("SELECT * FROM refusal_decision WHERE decision=? ORDER BY t DESC LIMIT ?",
                    (decision, int(limit)))
    else:
        rows = db.q("SELECT * FROM refusal_decision ORDER BY t DESC LIMIT ?", (int(limit),))
    out = []
    for row in rows:
        data = dict(row)
        data["signals"] = loads(data.get("signals"), {})
        data["alternatives"] = loads(data.get("alternatives"), [])
        out.append(data)
    return out


def reaction(decision_id: str, reaction: str) -> dict | None:
    db.ex("UPDATE refusal_decision SET user_reaction=?, reacted_at=? WHERE id=?",
          (reaction, now_iso(), decision_id))
    row = db.q1("SELECT * FROM refusal_decision WHERE id=?", (decision_id,))
    return dict(row) if row else None


def counts() -> dict:
    row = db.q1("SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN decision IN ('REFUSE','DEFLECT') THEN 1 ELSE 0 END) AS refused, "
                "SUM(CASE WHEN decision='RELUCTANT_ACCEPT' THEN 1 ELSE 0 END) AS tsundere "
                "FROM refusal_decision")
    if not row:
        return {"total": 0, "refused": 0, "tsundere": 0}
    return {"total": int(row["total"] or 0), "refused": int(row["refused"] or 0),
            "tsundere": int(row["tsundere"] or 0)}
