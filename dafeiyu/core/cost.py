# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""花费统计：QQ 聊天（桥接账本）+ 后台模型调用（llm_usage.jsonl）。

为什么单独一个模块：v2 重构时这块丢了，界面看不到钱、QQ 里的她也答不出「今天花了多少」。
这里只做**读**：读桥接的 token 账本、读自己的 llm_usage.jsonl，算成
「今日私聊 / 今日群聊 / 今日后台 / 合计 / 本月 / 累计 / 夜学实际与预估」，
再落一份 `state/cost.json`（桥接会把它塞进她的私聊唤醒提示里，让她看得见）。

约定：
  · 价格 = config/llm.json 同款默认价：命中 0.02、未命中 1、输出 4（元/百万 token），工作日 9–12/14–18 高峰 ×2。
  · 「轮」= 桥接账本里的一个 turn（一次唤醒/一次回复）；「次」= 一次模型调用。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import settings
from .llm.provider import PRICE, estimate_cost, is_peak

BRIDGE_DIR = Path(
    str((settings.app().get("bridge") or {}).get("dir")
        or r"")
)
BRIDGE_LEDGER = BRIDGE_DIR / "state" / "token-usage.jsonl"
BRIDGE_BUDGET = BRIDGE_DIR / "state" / "budget-view.json"
ENGINE_LEDGER = settings.LOGS / "llm_usage.jsonl"
LEGACY_LEDGER = settings.LEGACY_RUN / "用量.jsonl"
CACHE_JSON = settings.STATE / "cost.json"
CACHE_TXT = settings.STATE / "cost.txt"

LEARN_TASKS = ("learning", "self_dialogue", "summary", "compress", "diary")
NIGHT_TASKS = ("learning", "self_dialogue")


def _norm_task(raw: str) -> str:
    """把旧引擎的中文任务名归一成和新版一致的一组。"""
    t = str(raw or "")
    if "学习" in t:
        return "learning"
    if "自我对话" in t or "练习" in t:
        return "self_dialogue"
    if "日记" in t:
        return "diary"
    if "压缩" in t:
        return "compress"
    return t or "?"


def _cost_of_buckets(b: dict, at: datetime) -> float:
    mult = 2.0 if is_peak(at) else 1.0
    return (
        float(b.get("cacheHit", 0) or 0) * PRICE["cache_hit"]
        + float(b.get("cacheMiss", 0) or 0) * PRICE["cache_miss"]
        + float(b.get("cacheWrite", 0) or 0) * PRICE["cache_write"]
        + float(b.get("output", 0) or 0) * PRICE["output"]
    ) / 1_000_000 * mult


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return rows


def _day_key(ts_ms: float) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def bridge_turns() -> list[dict]:
    """桥接账本按「轮」归并（同一 session+turn 的多个 step 合成一轮）。"""
    samples: dict[tuple, dict] = {}
    for row in _read_jsonl(BRIDGE_LEDGER):
        if row.get("k") != "turn":
            continue
        sid = str(row.get("sid") or "")
        key = f"{sid}|{row.get('turn')}|{row.get('step')}|{row.get('retry')}"
        samples[key] = row
    turns: dict[tuple, dict] = {}
    for row in samples.values():
        t = float(row.get("t") or 0)
        if t <= 0:
            continue
        b = row.get("b") or {}
        try:
            at = datetime.fromtimestamp(t / 1000)
        except Exception:
            continue
        k = str(row.get("key") or "")
        kind = "private" if k.startswith("private:") else (
            "group" if k.startswith("group:") else "internal")
        slot_key = (row.get("sid"), row.get("turn"))
        slot = turns.get(slot_key)
        if slot is None:
            slot = turns[slot_key] = {
                "t": t, "day": at.strftime("%Y-%m-%d"), "kind": kind, "key": k,
                "sid": row.get("sid"), "turn": row.get("turn"),
                "in": 0, "out": 0, "steps": 0, "cost": 0.0,
            }
        slot["in"] += int(b.get("cacheHit", 0) or 0) + int(b.get("cacheMiss", 0) or 0)
        slot["out"] += int(b.get("output", 0) or 0)
        slot["steps"] += 1
        slot["cost"] += _cost_of_buckets(b, at)
    out = sorted(turns.values(), key=lambda r: r["t"])
    return out


def engine_calls() -> list[dict]:
    """后台模型调用：新版 llm_usage.jsonl + 旧引擎 运行\\用量.jsonl（统一任务名）。"""
    out = []
    for row in _read_jsonl(ENGINE_LEDGER):
        at_raw = str(row.get("at") or "")
        try:
            at = datetime.fromisoformat(at_raw)
        except Exception:
            continue
        out.append({"at": at, "day": at.strftime("%Y-%m-%d"),
                    "task": _norm_task(str(row.get("task") or "")),
                    "ok": bool(row.get("ok")), "cost": float(row.get("cost") or 0),
                    "steps": int((row.get("usage") or {}).get("steps") or 0),
                    "legacy": False})
    for row in _read_jsonl(LEGACY_LEDGER):
        at_raw = str(row.get("at") or "")
        try:
            at = datetime.fromisoformat(at_raw)
        except Exception:
            continue
        out.append({"at": at, "day": at.strftime("%Y-%m-%d"),
                    "task": _norm_task(str(row.get("task") or "")),
                    "ok": bool(row.get("ok")), "cost": float(row.get("cost") or 0),
                    "steps": int((row.get("usage") or {}).get("steps") or 0),
                    "legacy": True})
    out.sort(key=lambda r: r["at"])
    return out


def _sum(turns: list[dict], calls: list[dict], day: str) -> dict:
    d = {"private": 0.0, "group": 0.0, "internal": 0.0, "engine": 0.0, "turns": 0}
    for r in turns:
        if r["day"] != day:
            continue
        d[r["kind"]] = d.get(r["kind"], 0.0) + r["cost"]
        d["turns"] += 1
    for r in calls:
        if r["day"] == day:
            d["engine"] += r["cost"]
    d["qq"] = d["private"] + d["group"] + d["internal"]
    d["total"] = d["qq"] + d["engine"]
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}


def _window(turns: list[dict], calls: list[dict], since: datetime) -> dict:
    stamp = since.strftime("%Y-%m-%d")
    d = {"private": 0.0, "group": 0.0, "internal": 0.0, "engine": 0.0, "turns": 0}
    for r in turns:
        if r["day"] >= stamp:
            d[r["kind"]] = d.get(r["kind"], 0.0) + r["cost"]
            d["turns"] += 1
    for r in calls:
        if r["day"] >= stamp:
            d["engine"] += r["cost"]
    d["qq"] = d["private"] + d["group"] + d["internal"]
    d["total"] = d["qq"] + d["engine"]
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}


def learning_stats(calls: list[dict]) -> dict:
    learn = [c for c in calls if c["task"] in NIGHT_TASKS]
    today = datetime.now().strftime("%Y-%m-%d")
    today_learn = [c for c in learn if c["day"] == today]
    # 按「一次夜学 = 同一个任务连续的一串调用」粗略归并：以 30 分钟为间隔切段
    runs: list[dict] = []
    for c in learn:
        if runs and (c["at"] - runs[-1]["end"]).total_seconds() <= 1800 and runs[-1]["task"] == c["task"]:
            runs[-1]["cost"] += c["cost"]
            runs[-1]["calls"] += 1
            runs[-1]["steps"] += c["steps"]
            runs[-1]["end"] = c["at"]
        else:
            runs.append({"task": c["task"], "start": c["at"], "end": c["at"],
                         "cost": c["cost"], "calls": 1, "steps": c["steps"]})
    recent = runs[-5:]
    avg = round(sum(r["cost"] for r in recent) / len(recent), 4) if recent else 0.0
    per_call = round(sum(c["cost"] for c in learn) / len(learn), 4) if learn else 0.0
    return {
        "today_cost": round(sum(c["cost"] for c in today_learn), 4),
        "today_calls": len(today_learn),
        "last_run": round(recent[-1]["cost"], 4) if recent else 0.0,
        "last_run_at": recent[-1]["end"].strftime("%m-%d %H:%M") if recent else "",
        "last_run_calls": recent[-1]["calls"] if recent else 0,
        "avg_recent": avg,
        "per_call": per_call,
        "runs": [{"task": r["task"], "at": r["start"].strftime("%m-%d %H:%M"),
                  "cost": round(r["cost"], 4), "calls": r["calls"]} for r in runs[-6:]],
    }


def budget_view() -> dict:
    try:
        data = json.loads(BRIDGE_BUDGET.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        "group_spend": round(float((data.get("group") or {}).get("spend") or 0), 4),
        "group_limit": round(float((data.get("group") or {}).get("limit") or 0.7), 4),
        "group_blocked": bool((data.get("group") or {}).get("blocked")),
        "private_today": round(float((data.get("private") or {}).get("spend") or 0), 4),
        "date": data.get("date"),
    }


def summary() -> dict:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    turns = bridge_turns()
    calls = engine_calls()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    all_time = _window(turns, calls, datetime(1970, 1, 1))
    data = {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "today": _sum(turns, calls, today),
        "yesterday": _sum(turns, calls, yesterday),
        "month": _window(turns, calls, month_start),
        "all": all_time,
        "learning": learning_stats(calls),
        "budget": budget_view(),
        "tasks_today": {},
    }
    by_task: dict[str, dict] = {}
    for c in calls:
        if c["day"] != today:
            continue
        slot = by_task.setdefault(c["task"], {"calls": 0, "cost": 0.0})
        slot["calls"] += 1
        slot["cost"] = round(slot["cost"] + c["cost"], 4)
    data["tasks_today"] = by_task
    data["lines"] = _lines(data)
    return data


def _money(v: float) -> str:
    return f"¥{v:.2f}" if abs(v) >= 0.01 else f"¥{v:.3f}"


def _lines(d: dict) -> list[str]:
    t, y, m, a = d["today"], d["yesterday"], d["month"], d["all"]
    le, bt = d["learning"], d["budget"]
    lines = [
        f"今天：私聊 {_money(t['private'])}｜群聊 {_money(t['group'])}｜后台 {_money(t['engine'])}｜"
        f"合计 {_money(t['total'])}（{t['turns']} 轮聊天）",
        f"昨天合计 {_money(y['total'])}；本月 {_money(m['total'])}；累计 {_money(a['total'])}",
    ]
    if le["last_run"]:
        lines.append(f"夜学：最近一次 {_money(le['last_run'])}（{le['last_run_calls']} 次调用，{le['last_run_at']}），"
                     f"近 5 次平均 {_money(le['avg_recent'])}，今晚预估 {_money(le['avg_recent'])}")
    else:
        lines.append(f"夜学：今天还没跑；近 5 次平均 {_money(le['avg_recent'])}，今晚预估 {_money(le['avg_recent'])}")
    lines.append(f"后台单次调用均价 {_money(le['per_call'])}")
    if bt:
        lines.append(f"额度：群 {_money(bt['group_spend'])}/{_money(bt['group_limit'])}"
                     f"{'（已用完，今天不再回群）' if bt['group_blocked'] else ''}")
    return lines


def text(d: dict | None = None) -> str:
    d = d or summary()
    return "\n".join(d.get("lines") or [])


def write_cache(d: dict | None = None) -> dict:
    d = d or summary()
    try:
        CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
        CACHE_JSON.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        CACHE_TXT.write_text(text(d) + "\n", encoding="utf-8")
    except Exception:
        pass
    return d

# ── v5：按功能归因 ─────────────────────────────────────────────────────────
# 原则：estimate 是调用前估算，actual 是用真实 usage 回算，两者不互相冒充；
# 历史账本里归因不出来的，一律标 other，单列"未分类历史"，不猜。

FEATURE_TAGS = (
    "chat", "voice", "vision_static", "vision_animation", "vision_browse",
    "chitchat", "learning", "self_dialogue", "memory_compress", "other",
    "curiosity_autonomous", "memory_recall",
)

TAG_OF_TASK = {
    "dialogue": "chat",
    "chitchat": "chitchat",
    "learning": "learning",
    "self_dialogue": "self_dialogue",
    "compress": "memory_compress",
    "summary": "memory_compress",
    "diary": "memory_compress",
    "vision": "vision_static",
    "voice": "voice",
    "refusal": "other",
}

TAG_CN = {
    "chat": "普通消息", "voice": "语音", "vision_static": "看图·静态",
    "vision_animation": "看图·动图", "vision_browse": "看图·自主浏览",
    "chitchat": "碎碎念", "learning": "学习", "self_dialogue": "自我对话",
    "memory_compress": "记忆压缩", "other": "未分类",
    "curiosity_autonomous": "自主好奇心学习", "memory_recall": "回忆检索",
    "local_browse": "本地刷视频（0 花费）",

}


def tag_of_task(task: str) -> str:
    return TAG_OF_TASK.get(str(task or ""), "other")


def config() -> dict:
    try:
        return settings.load("cost_tracking") or {}
    except Exception:
        return {}


def prices() -> dict:
    table = dict(PRICE)
    try:
        override = settings.load("pricing_override") or {}
    except Exception:
        override = {}
    for key in ("cache_hit", "cache_miss", "cache_write", "output"):
        value = override.get(key)
        if isinstance(value, (int, float)):
            table[key] = float(value)
    return table


def estimate_for(feature_tag: str, prompt_chars: int = 0, at: datetime | None = None) -> float:
    """调用前估算：输入按字数粗估 + 该功能历史平均输出长度。"""
    table = prices()
    out_tokens = (config().get("estimated_output_tokens") or {}).get(feature_tag, 200)
    in_tokens = max(1, int(prompt_chars / 1.6))
    mult = 2.0 if is_peak(at) else 1.0
    return round((in_tokens * table["cache_miss"] + float(out_tokens) * table["output"]) / 1_000_000 * mult, 6)


def record_event(feature_tag: str, *, task: str = "", model: str = "", usage: dict | None = None,
                 estimated: float | None = None, actual: float | None = None,
                 source: str = "engine", session_id: str = "", message_id: str = "",
                 meta: dict | None = None, currency: str = "", at: str = "") -> str:
    """写一条费用事件。engine 侧用 usage 回算 actual；bridge 侧直接给 cost。

    at 是事件**实际发生**的时间（导入历史账本时必须传，否则全写成"现在"）。
    """
    from .entity import new_id, now_iso

    if not config().get("enabled", True):
        return ""
    usage = usage or {}
    tag = feature_tag if feature_tag in FEATURE_TAGS else "other"
    api_in = int(usage.get("cache_miss", 0) or 0)
    cache_read = int(usage.get("cache_hit", 0) or 0)
    cache_write = int(usage.get("cache_write", 0) or 0)
    out_tokens = int(usage.get("output", 0) or 0)
    if actual is None:
        actual = estimate_cost(usage) if usage else 0.0
    if estimated is None:
        estimated = 0.0
    try:
        ev_id = new_id()
        db_ex(
            "INSERT INTO cost_event(id, t, feature_tag, source, model, task, input_tokens, "
            "output_tokens, cache_read_tokens, cache_write_tokens, estimated_cost, actual_cost, "
            "currency, session_id, message_id, meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev_id, at or now_iso(), tag, source, model or "", task or "", api_in, out_tokens,
             cache_read, cache_write, round(float(estimated), 6), round(float(actual), 6),
             currency or str(config().get("currency", "CNY")), session_id, message_id,
             json.dumps(meta or {}, ensure_ascii=False)),
        )
        return ev_id
    except Exception:
        return ""


def db_ex(sql: str, args: tuple = ()) -> None:
    from . import db

    db.ex(sql, args)


def _rows(start: str = "", end: str = "", tags: list[str] | None = None, days: int | None = None) -> list[dict]:
    from . import db

    sql = "SELECT * FROM cost_event WHERE 1=1"
    args: list = []
    if start:
        sql += " AND t >= ?"
        args.append(f"{start}T00:00:00")
    if end:
        sql += " AND t <= ?"
        args.append(f"{end}T23:59:59")
    if days:
        sql += " AND t >= ?"
        args.append((datetime.now() - timedelta(days=int(days))).isoformat(timespec="seconds"))
    if tags:
        sql += f" AND feature_tag IN ({','.join('?' * len(tags))})"
        args.extend(tags)
    sql += " ORDER BY t"
    return [dict(r) for r in db.q(sql, args)]


def _dev(est: float, act: float) -> dict:
    diff = act - est
    pct = (diff / est) if est else None
    cfgc = config()
    warn = float(cfgc.get("estimate_warn_threshold", 0.2))
    fail = float(cfgc.get("estimate_fail_threshold", 0.5))
    level = "ok"
    if pct is None:
        level = "none"
    elif abs(pct) >= fail:
        level = "fail"
    elif abs(pct) >= warn:
        level = "warn"
    return {"diff": round(diff, 6), "pct": (round(pct, 4) if pct is not None else None), "level": level}


def attribute(days: int | None = None, start: str = "", end: str = "",
              tags: list[str] | None = None) -> dict:
    rows = _rows(start=start, end=end, tags=tags, days=days)
    by_tag: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    for row in rows:
        tag = row["feature_tag"] or "other"
        act = float(row["actual_cost"] or 0)
        est = float(row["estimated_cost"] or 0)
        tokens = int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0) + \
            int(row["cache_read_tokens"] or 0) + int(row["cache_write_tokens"] or 0)
        t = by_tag.setdefault(tag, {"tag": tag, "name": TAG_CN.get(tag, tag), "calls": 0,
                                    "cost": 0.0, "est": 0.0, "tokens": 0})
        t["calls"] += 1
        t["cost"] += act
        t["est"] += est
        t["tokens"] += tokens
        day = str(row["t"] or "")[:10]
        d = by_day.setdefault(day, {"day": day, "calls": 0, "cost": 0.0, "est": 0.0, "tags": {}})
        d["calls"] += 1
        d["cost"] += act
        d["est"] += est
        d["tags"][tag] = round(d["tags"].get(tag, 0.0) + act, 6)

    tag_list = []
    total_cost = 0.0
    for tag, item in sorted(by_tag.items(), key=lambda kv: -kv[1]["cost"]):
        total_cost += item["cost"]
        tag_list.append({
            "tag": tag, "name": item["name"], "calls": item["calls"],
            "cost": round(item["cost"], 4),
            "avg_actual": round(item["cost"] / item["calls"], 5) if item["calls"] else 0,
            "avg_estimated": round(item["est"] / item["calls"], 5) if item["calls"] else 0,
            "tokens": item["tokens"],
            "deviation": _dev(item["est"], item["cost"]),
        })
    for item in tag_list:
        item["share"] = round(item["cost"] / total_cost, 4) if total_cost else 0

    day_list = [{"day": d["day"], "calls": d["calls"], "cost": round(d["cost"], 4),
                 "estimated": round(d["est"], 4), "tags": d["tags"]}
                for d in sorted(by_day.values(), key=lambda x: x["day"])]
    est_total = sum(item["est"] for item in by_tag.values())
    return {
        "enabled": bool(config().get("enabled", True)),
        "total": {"cost": round(total_cost, 4), "calls": len(rows),
                  "tokens": sum(int(r["input_tokens"] or 0) + int(r["output_tokens"] or 0)
                                + int(r["cache_read_tokens"] or 0) + int(r["cache_write_tokens"] or 0)
                                for r in rows),
                  "estimated": round(est_total, 4),
                  "deviation": _dev(est_total, total_cost)},
        "by_tag": tag_list,
        "by_day": day_list,
        "unclassified": sum(1 for r in rows if (r["feature_tag"] or "other") == "other"),
        "first_day": day_list[0]["day"] if day_list else "",
        "last_day": day_list[-1]["day"] if day_list else "",
        "warn_threshold": float(config().get("estimate_warn_threshold", 0.2)),
        "fail_threshold": float(config().get("estimate_fail_threshold", 0.5)),
    }


def curiosity_stats(now: datetime | None = None) -> dict:
    """v14：好奇心探索花费的专项数字（总/本月/本周/日均）。"""
    now = now or datetime.now()
    tag = "curiosity_autonomous"
    total = attribute(tags=[tag])
    month_start = now.replace(day=1).strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    month = attribute(start=month_start, end=now.strftime("%Y-%m-%d"), tags=[tag])
    week = attribute(start=week_start, end=now.strftime("%Y-%m-%d"), tags=[tag])
    days = total.get("by_day") or []
    span = len(days) if days else 1
    return {
        "total": total["total"]["cost"],
        "calls": total["total"]["calls"],
        "month": month["total"]["cost"],
        "week": week["total"]["cost"],
        "per_day": round(total["total"]["cost"] / max(1, span), 4),
        "days": span,
    }


def _qq_timeline(now: datetime | None = None) -> dict[str, list[tuple]]:
    """从桥接的会话状态里读消息时间线：conv -> [(时间, 是不是她发的, 文本)]。"""
    import json as _json

    path = BRIDGE_DIR / "state" / "social-v2.json"
    out: dict[str, list[tuple]] = {}
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for key, conv in (data.get("conversations") or {}).items():
        rows = []
        for msg in (conv.get("recentMessages") or []):
            stamp = msg.get("time")
            if not stamp:
                continue
            try:
                at = datetime.fromtimestamp(int(stamp) / 1000)
            except Exception:
                continue
            rows.append((at, bool(msg.get("isSelf")), str(msg.get("text") or "")))
        rows.sort(key=lambda item: item[0])
        out[key] = rows
    return out


def message_split(days: int = 7, now: datetime | None = None) -> dict:
    """按「她主动起话头（发送）/ 她接话（回复）」拆，含单条平均。

    口径（推断，但每一步都有据）：
    - 消息类型：看桥接会话里她那条消息之前 10 分钟有没有老大说话；
      没有 = 主动发送，有 = 回复。
    - 费用归属：碎碎念（chitchat 标签）算主动发送，桥接的 QQ 回合算回复，
      其余后台调用单列。消息是"条"，调用是"次"，两者分开算平均。
    """
    now = now or datetime.now()
    timelines = _qq_timeline(now)
    since = (now - timedelta(days=int(days))).timestamp()
    send_msgs = reply_msgs = 0
    for rows in timelines.values():
        for index, (stamp, is_self, _text) in enumerate(rows):
            if not is_self:
                continue
            if stamp.timestamp() < since:
                continue
            replied = False
            for back in range(index - 1, max(-1, index - 12), -1):
                prev_at, prev_self, _t = rows[back]
                if prev_self:
                    continue
                if 0 <= (stamp - prev_at).total_seconds() <= 600:
                    replied = True
                break
            if replied:
                reply_msgs += 1
            else:
                send_msgs += 1

    events = _rows(days=days, start="", end="", tags=None)
    # 消息只覆盖"最近 100 条/会话"的窗口，费用也只在同一窗口里算，才不会被拉高
    stamps = [stamp for rows in timelines.values() for stamp, _s, _t in rows]
    window_start = min(stamps) if stamps else None
    window_end = max(stamps) if stamps else None
    bridge_cost = 0.0
    bridge_calls = 0
    chitchat_cost = 0.0
    chitchat_calls = 0
    backend_cost = 0.0
    backend_calls = 0
    window_bridge_cost = 0.0
    window_bridge_calls = 0
    for row in events:
        value = float(row.get("actual_cost") or 0)
        source = str(row.get("source") or "")
        tag = str(row.get("feature_tag") or "")
        in_window = True
        try:
            at = datetime.fromisoformat(str(row.get("t")))
            if window_start and window_end:
                in_window = window_start <= at <= window_end
        except Exception:
            in_window = False
        if tag == "chitchat":
            chitchat_cost += value
            chitchat_calls += 1
        elif source == "bridge":
            bridge_cost += value
            bridge_calls += 1
            if in_window:
                window_bridge_cost += value
                window_bridge_calls += 1
        else:
            backend_cost += value
            backend_calls += 1

    total = bridge_cost + chitchat_cost + backend_cost
    out = {
        "send": {"calls": chitchat_calls, "messages": send_msgs, "cost": round(chitchat_cost, 4),
                 "avg_per_call": round(chitchat_cost / chitchat_calls, 5) if chitchat_calls else 0,
                 "avg_per_message": round(chitchat_cost / send_msgs, 5) if send_msgs else 0},
        "reply": {"calls": bridge_calls, "messages": reply_msgs, "cost": round(bridge_cost, 4),
                  "avg_per_call": round(bridge_cost / bridge_calls, 5) if bridge_calls else 0,
                  # 单条平均只算"有消息可对照"的那段窗口，避免 2 天花费 ÷ 2 小时消息
                  "avg_per_message": round(window_bridge_cost / reply_msgs, 4) if reply_msgs else 0,
                  "window_cost": round(window_bridge_cost, 4),
                  "window_calls": window_bridge_calls,
                  "window_start": window_start.isoformat(timespec="minutes") if window_start else "",
                  "window_end": window_end.isoformat(timespec="minutes") if window_end else ""},
        "backend": {"calls": backend_calls, "messages": 0, "cost": round(backend_cost, 4),
                    "avg_per_call": round(backend_cost / backend_calls, 5) if backend_calls else 0,
                    "avg_per_message": 0},
        "total": {"calls": len(events), "cost": round(total, 4), "days": days,
                  "note": "发送=没有老大触发的那条；回复=接老大话的；后台=学习/压缩/自检等"},
    }
    for key in ("send", "reply", "backend"):
        out[key]["share"] = round(out[key]["cost"] / total, 4) if total else 0
    return out


def context_simulation(days_list=(10, 30, 100), now: datetime | None = None) -> dict:
    """如果每次调用都把 N 天的原文上下文一起读进去，要花多少。

    口径：用桥接账本的实际跨度算出「每天多少轮」，一轮≈她一条 + 老大一条，
    平均字数取会话里的真实值；token 按未命中价算（复用上下文会便宜很多，单列高峰×2）。
    """
    now = now or datetime.now()
    timelines = _qq_timeline(now)
    stamps = [stamp for rows in timelines.values() for stamp, _s, _t in rows]
    chars = [len(row[2]) for rows in timelines.values() for row in rows if row[2]]
    avg_chars = sum(chars) / len(chars) if chars else 40
    tokens_per_msg = max(8.0, avg_chars / 1.6 + 8)

    if stamps:
        span_days = max(0.2, (max(stamps) - min(stamps)).total_seconds() / 86400.0)
        msgs_per_day = len(stamps) / span_days
    else:
        span_days, msgs_per_day = 1.0, 0.0

    # 今天实际发生了多少次调用（用来算"如果每次都要读 N 天"的日增量）
    today = now.strftime("%Y-%m-%d")
    try:
        from . import db

        row = db.q1("SELECT COUNT(*) n FROM cost_event WHERE t LIKE ?", (today + "%",))
        calls_today = int(row["n"] or 0) if row else 0
    except Exception:
        calls_today = 0

    price = PRICE["cache_miss"] / 1_000_000
    table = []
    for days in days_list:
        tokens = msgs_per_day * days * tokens_per_msg
        per_call = tokens * price
        table.append({
            "days": days,
            "messages": int(msgs_per_day * days),
            "tokens": int(tokens),
            "cost_per_call": round(per_call, 4),
            "cost_per_call_peak": round(per_call * 2, 4),
            "daily_extra": round(per_call * calls_today, 4),
        })
    return {"msgs_per_day": round(msgs_per_day, 1), "avg_chars": round(avg_chars, 1),
            "tokens_per_msg": round(tokens_per_msg, 1), "span_days": round(span_days, 1),
            "calls_today": calls_today, "table": table,
            "note": "只看文本；按未命中价 ¥1/M，高峰 ×2 单列；daily_extra=今天调用量 × 每次多读的上下文"}


def detail(day: str = "", tag: str = "", limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM cost_event WHERE 1=1"
    args: list = []
    if day:
        sql += " AND t LIKE ?"
        args.append(f"{day}%")
    if tag:
        sql += " AND feature_tag=?"
        args.append(tag)
    sql += " ORDER BY t DESC LIMIT ?"
    args.append(int(limit))
    from . import db

    return [dict(r) for r in db.q(sql, args)]


def export_csv(path: str, days: int | None = None, start: str = "", end: str = "") -> str:
    data = attribute(days=days, start=start, end=end)
    lines = ["日期,功能,调用次数,实际花费,预估花费,偏差百分比"]
    for item in data["by_tag"]:
        pct = item["deviation"]["pct"]
        lines.append(f"{data['first_day'] or '-'}~{data['last_day'] or '-'},{item['name']},"
                     f"{item['calls']},{item['cost']:.6f},{item['avg_estimated'] * item['calls']:.6f},"
                     f"{'' if pct is None else round(pct * 100, 1)}")
    lines.append("")
    lines.append("日期,调用次数,实际花费,预估花费")
    for day in data["by_day"]:
        lines.append(f"{day['day']},{day['calls']},{day['cost']:.6f},{day['estimated']:.6f}")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return str(out)


def import_ledgers(engine: bool = True, bridge: bool = True) -> dict:
    """把两个 jsonl 账本的增量导入 cost_event（幂等，只补没进过的）。"""
    from . import db

    added = {"engine": 0, "bridge": 0, "skipped": 0}
    if engine and ENGINE_LEDGER.exists():
        for line in ENGINE_LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            at = str(row.get("at") or "")
            task = _norm_task(str(row.get("task") or ""))
            tag = tag_of_task(task)
            key = f"engine|{at}|{task}|{row.get('cost')}"
            if db.q1("SELECT 1 FROM cost_event WHERE message_id=?", (key,)):
                added["skipped"] += 1
                continue
            usage = row.get("usage") or {}
            record_event(tag, task=task, model=str(row.get("provider") or ""), usage={
                "cache_miss": usage.get("cache_miss", 0), "cache_hit": usage.get("cache_hit", 0),
                "cache_write": usage.get("cache_write", 0), "output": usage.get("output", 0),
            }, estimated=0.0, actual=float(row.get("cost") or 0), source="engine",
                 message_id=key, at=at, meta={"imported": True, "peak": row.get("peak")})
            added["engine"] += 1

    if bridge and BRIDGE_LEDGER.exists():
        for line in BRIDGE_LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("k") != "turn":
                continue
            b = row.get("b") or {}
            at = datetime.fromtimestamp(float(row.get("t", 0)) / 1000).isoformat(timespec="seconds")
            key = f"bridge|{at}|{row.get('sid')}|{row.get('seq')}"
            raw_tag = str(row.get("tag") or "").strip()
            tag = raw_tag if raw_tag in FEATURE_TAGS else "other"
            existing = db.q1("SELECT id, feature_tag FROM cost_event WHERE message_id=?", (key,))
            if existing:
                # 账本后来补上了 tag → 把之前记成 other 的那条升级掉（不重算钱）
                if tag != "other" and existing["feature_tag"] == "other":
                    db.ex("UPDATE cost_event SET feature_tag=? WHERE id=?", (tag, existing["id"]))
                    added["upgraded"] = added.get("upgraded", 0) + 1
                else:
                    added["skipped"] += 1
                continue
            usage = {"cache_miss": b.get("cacheMiss", 0), "cache_hit": b.get("cacheHit", 0),
                     "cache_write": b.get("cacheWrite", 0), "output": b.get("output", 0)}
            # 桥接从 v14 起会给每轮打 tag（chat / chitchat）；老账本没有就记 other
            record_event(tag, task="bridge-turn", model=str(row.get("model") or ""),
                         usage=usage, estimated=0.0, actual=estimate_cost(usage, _parse_at(at)),
                         source="bridge", session_id=str(row.get("sid") or ""),
                         message_id=key, at=at,
                         meta={"key": row.get("key"), "imported": True, "raw_tag": raw_tag})
            added["bridge"] += 1
    return added


def _parse_at(value: str):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
