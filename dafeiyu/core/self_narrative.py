# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v12 2.6 自我叙事：每天给自己写一小段"我是谁、我在想什么"。

每日巩固时生成，存 state/self_narrative.json，最近 7 天保留完整；
同时投一条到 1 号长期记忆（老大模块），让她对自己的状态有连续的记忆。
没装模型时退化为事实汇总，不编造。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import db, settings
from .entity import new_id, now_iso
from .text import clip, keywords, parse_iso

FILE = "self_narrative.json"
KEEP_DAYS = 7


def _state() -> dict:
    raw = settings.read_state(FILE, {})
    raw.setdefault("days", [])
    raw.setdefault("latest", "")
    return raw


def _save(data: dict) -> dict:
    settings.write_state(FILE, data)
    return data


def _facts(now: datetime) -> dict:
    day = now.strftime("%Y-%m-%d")
    mem = db.q1("SELECT COUNT(*) n FROM memory_item WHERE t_created >= ?", (day + "T00:00:00",))
    chats = db.q1("SELECT COUNT(*) n FROM instant_memory WHERE ts >= ?", (day + "T00:00:00",))
    thoughts = db.q1("SELECT COUNT(*) n FROM thought_node WHERE t_created >= ?", (day + "T00:00:00",))
    refuse = db.q1("SELECT COUNT(*) n FROM refusal_decision WHERE t >= ?", (day + "T00:00:00",))
    return {"memories": int(mem["n"] or 0) if mem else 0,
            "chats": int(chats["n"] or 0) if chats else 0,
            "thoughts": int(thoughts["n"] or 0) if thoughts else 0,
            "refusals": int(refuse["n"] or 0) if refuse else 0}


def generate(now: datetime | None = None, *, use_llm: bool = True) -> dict:
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    facts = _facts(now)
    try:
        from .emotion import vector

        vec = vector.current()
        emotion = f"{vec.dominant_cn()}（正面 {vec.status()['positive']:.0f}／负面 {vec.status()['negative']:.0f}）"
        dominant = vec.dominant_cn()
    except Exception:
        emotion = "平静"
        dominant = "平静"

    body = ""
    if use_llm:
        try:
            from . import llm

            prompt = (
                "用第一人称写一小段今天的状态自述（60~120 字，像日记里的一句话，不要列表）。"
                f"今天是 {day}；心情 {emotion}；今天新增记忆 {facts['memories']} 条、"
                f"对话 {facts['chats']} 轮、想法 {facts['thoughts']} 个、拒绝 {facts['refusals']} 次。"
                "要具体、别喊口号，别编不存在的事。"
            )
            ok, text, _meta = llm.provider.run(prompt, task="summary",
                                               feature_tag="memory_compress", timeout=180)
            if ok and text.strip():
                body = clip(text.strip(), 400)
        except Exception:
            body = ""
    if not body:
        body = (f"今天是 {day}。心情{emotion}。今天记下了 {facts['memories']} 条东西，"
                f"和老大聊了 {facts['chats']} 轮，脑子里过了 {facts['thoughts']} 个念头"
                + (f"，拒绝过 {facts['refusals']} 次" if facts["refusals"] else "")
                + "。")

    data = _state()
    days = [d for d in (data.get("days") or []) if d.get("date") != day]
    days.append({"date": day, "text": body, "mood": dominant,
                 "at": now.isoformat(timespec="seconds")})
    days.sort(key=lambda d: d.get("date", ""))
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    data["days"] = [d for d in days if d.get("date", "") >= cutoff][-KEEP_DAYS:]
    data["latest"] = body
    _save(data)

    # 投一条到 1 号长期记忆（只加不删）
    try:
        from .repo import memory as mr

        mr.insert(layer="long1", subtype="summary", content=body,
                  summary=clip(body, 60), concepts=keywords(body, 5) or ["自我叙事"],
                  salience=0.7, emotion_tag=dominant, status="resolved", origin="self_narrative")
    except Exception:
        pass
    return {"date": day, "text": body, "facts": facts}


def hint(days: int = 3) -> str:
    data = _state()
    recent = (data.get("days") or [])[-int(days):]
    if not recent:
        return ""
    lines = ["【最近几天的你】"]
    for item in recent:
        lines.append(f"- {item.get('date')}：{clip(item.get('text') or '', 120)}")
    return "\n".join(lines)


def status() -> dict:
    data = _state()
    return {"latest": data.get("latest", ""),
            "days": [{"date": d.get("date"), "text": clip(d.get("text") or "", 200),
                      "mood": d.get("mood")} for d in (data.get("days") or [])],
            "count": len(data.get("days") or [])}
