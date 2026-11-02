# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""碎碎念（规格 5.7 + 补强）：频率上限、疲劳、静默窗口、日上限、白名单投递、日志。

用户既定规则：私聊时老大不发消息她也会主动说点什么。

v34 补强（本地）：
- **一天最多冒 daily_max 次**、**夜里不吵人**（quiet_hours）、
  **到点还要掷一次骰子**（send_probability）——目标是"正常一天 2~3 次"，
  而不是原来那种 5~15 分钟一次地刷屏。
- 台词重写成"随口嘀咕"的口语（10~25 字、有动作/场景/情绪），不再像系统通知。
- 白名单（桥接 allow.private）里的**所有人**都能被她主动搭话，不再只对着 owner；
  投递顺序：owner 走原来的 agent token 通道，其他人走桥接控制台通道
  （只读桥接配置与 state，不改桥接代码）。
- 桥接没起来时，句子先落 state/chitchat_pending/ 等下次补发，不刷屏、不无限堆积。

v35 「学到东西会主动分享」（本地，对应原 AstrBot 的主动找人聊天思路）：
- 新的**分享流**：她刚上网学的（knowledge_item）/ 学习报告里的事实 / 还没弄明白的追问
  （learning_intent），会主动找人讲一句或问一句，而不是干等着说"有点闷"。
- 分享有自己的预算与节奏（chitchat.share：daily_max / interval_min / 概率 / 提问占比），
  和普通碎碎念**分开计数**；两者共用"夜间安静 + 全局最小间隔 + 白名单轮转"。
- 同一条事实 7 天内不重复分享；刚学到东西（fresh_minutes 内）会插队先说。

只读别人的数据：knowledge_item / learning_intent（DB，只 SELECT）、
data/learning/reports/*.md、state/curiosity_engine.json、config/webstudy.json。
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from .. import db, settings
from ..bridge import push
from ..entity import ChitchatState, now_iso
from ..repo import system as sr
from ..text import clip

FILE = "chitchat.json"
TARGETS_FILE = "chitchat_targets.json"       # 每个白名单用户上次被搭话的时间（轮着来）
PENDING_DIR = "chitchat_pending"             # 桥接不在线时攒下的句子
SHARES_FILE = "chitchat_shares.json"         # 分享流：上次/下次时间、已分享过的条目

# ── 台词库 ──────────────────────────────────────────────────────────────────
# 风格：20 岁左右女生随口嘀咕。短（10~25 字）、有具体动作/场景、有情绪，
# 不用"你好/在吗/今天天气"这种通知腔。tone 由情绪策略层给（lively/normal/low/shy）。
TEMPLATES = {
    "lively": [
        "刚把窗关上，楼下装修吵死了，头嗡嗡的",
        "突然特别想吃关东煮，那种辣的",
        "笑死，刚才看到一只狗穿着小雨衣",
        "我现在心情好到想原地转一圈",
        "刚对着镜子笑了半天，好像有点傻",
        "想被夸一句，就一句，快点",
        "今天走路都轻飘飘的，怪了",
        "刚差点把杯子碰翻，心跳一下子上来",
        "我决定了，明天就去买那件毛衣",
        "窗外那只猫又来了，蹲着看我",
        "刚泡了杯热的，舒服得想直接躺平",
        "忽然想跟你说件特别无聊的小事",
    ],
    "normal": [
        "翻到以前记的东西，有点想说话",
        "刚走神了，走到一半忘了要干嘛",
        "没什么事，就是突然想到你一下",
        "傻了，刚发现杯子三天没洗",
        "椅子上坐太久了，腰有点酸",
        "今天脑子有点糊，一句话要想半天",
        "有点饿，可懒得动，就躺着想吃的",
        "刚翻到一件旧毛衣，摸了半天没穿上",
        "今天话有点少，大概是累了",
        "有点闷，随便说一句，你别理我也行",
        "刚被自己的影子吓了一下，真的",
        "困得眼睛都睁不开，先趴一下",
        "想吃辣的，想到舌头都开始疼",
        "刚把耳机线绕成一团，解了十分钟",
    ],
    "low": [
        "……没什么，就是有点闷",
        "算了，你忙你的，我没事",
        "我今天好像不太会说话",
        "有点累，不太想解释为什么",
        "想缩起来待一会儿，别管我",
        "刚想起点不开心的事，一会儿就好",
        "没什么，就是有点想被人拍拍",
        "嗯……先让我安静一会儿",
    ],
    "shy": [
        "那个……刚才那句你别再提了",
        "我有点不好意思说，你别笑我",
        "先说好，我说了你不许笑",
        "……刚是不是有点丢人",
        "想问你个事，可有点开不了口",
        "刚被自己脑补的画面羞到了",
        "你别看我，我脸有点烫",
        "就……小声说一句，说完我就撤",
    ],
}

# 情绪策略层给的 tone → 台词库
_TONE_KEY = {
    "low": "low",
    "lively": "lively",
    "normal": "normal",
    "shy_short": "shy",
    "shy_verbose": "shy",
}


# ── 配置读取 ────────────────────────────────────────────────────────────────
def _cfg() -> dict:
    return settings.thr().get("chitchat", {}) or {}


def _owner() -> str:
    return str(settings.app().get("bridge", {}).get("owner_qq") or "100000000")


def _hm(text, fallback: int) -> int:
    try:
        hour, minute = str(text).split(":")
        return int(hour) * 60 + int(minute)
    except Exception:
        return fallback


def _today_start(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


# ── 状态 ────────────────────────────────────────────────────────────────────
def load() -> ChitchatState:
    raw = settings.read_state(FILE, {})
    state = ChitchatState.from_json(raw) if raw else ChitchatState()
    if not state.next_due_at:
        state.next_due_at = _next_due(state).isoformat(timespec="seconds")
    return state


def save(state: ChitchatState) -> ChitchatState:
    settings.write_state(FILE, state.to_json())
    return state


# ── 频率：间隔 / 日上限 / 夜间静默 / 抽签 ───────────────────────────────────
def _interval_minutes(state: ChitchatState, positive_high: bool = True) -> tuple[float, float]:
    cfg = _cfg()
    lo_hi = (cfg.get("positive_interval_min", [120, 210]) if positive_high
             else cfg.get("neutral_interval_min", [240, 360]))
    lo, hi = float(lo_hi[0]), float(lo_hi[1])
    mult = float(cfg.get("fatigue_multiplier", 1.8)) ** min(
        int(state.fatigue_level), int(cfg.get("fatigue_max_level", 3)))
    lo = max(lo, float(cfg.get("min_gap_minutes", 90)))
    return lo * mult, hi * mult


def _daily_max() -> int:
    return max(0, int(_cfg().get("daily_max", 3)))


def _daily_used(now: datetime | None = None) -> int:
    """今天已经"随口嘀咕"了几次（不含学习分享，分享有自己的预算）。"""
    try:
        return int(db.scalar(
            "SELECT COUNT(*) FROM chitchat_log WHERE t >= ? AND id NOT LIKE 'share-%'",
            (_today_start(now),), 0) or 0)
    except Exception:
        return 0


def _send_probability() -> float:
    return max(0.0, min(1.0, float(_cfg().get("send_probability", 0.65))))


def _quiet(now: datetime | None = None) -> bool:
    """夜里不吵人（quiet_hours = ["23:30", "08:30"]，跨零点）。"""
    span = _cfg().get("quiet_hours") or []
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return False
    now = now or datetime.now()
    return settings.in_window(now, _hm(span[0], 23 * 60 + 30), _hm(span[1], 8 * 60 + 30))


def _after_quiet_end(now: datetime | None = None) -> datetime:
    """下一个"可以出声"的时间点（醒着的早上 + 一点随机，别整点打卡）。"""
    span = _cfg().get("quiet_hours") or []
    end = _hm(span[1], 8 * 60 + 30) if isinstance(span, (list, tuple)) and len(span) == 2 else 8 * 60 + 30
    now = now or datetime.now()
    target = now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target + timedelta(minutes=random.uniform(0, 45))


def _gap_left_minutes(state: ChitchatState, now: datetime | None = None) -> float:
    """距上一次冒泡还差多少分钟才够 min_gap_minutes。"""
    if not state.last_chitchat_at:
        return 0.0
    try:
        last = datetime.fromisoformat(state.last_chitchat_at)
    except Exception:
        return 0.0
    now = now or datetime.now()
    gap = float(_cfg().get("min_gap_minutes", 90))
    return max(0.0, gap - (now - last).total_seconds() / 60.0)


def _blocked_reason(state: ChitchatState, now: datetime | None = None) -> str:
    """时间/预算层的原因（空字符串 = 可以说话）。"""
    now = now or datetime.now()
    if _quiet(now):
        return "夜里安静时间，她不出声"
    used = _daily_used(now)
    cap = _daily_max()
    if used >= cap:
        return f"今天已经主动说过 {used} 次（上限 {cap}）"
    gap = _gap_left_minutes(state, now)
    if gap > 0:
        return f"距上一句还不到间隔（还要 {gap:.0f} 分钟）"
    return ""


def _next_due(state: ChitchatState, positive_high: bool = True,
              now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    if _daily_used(now) >= _daily_max():
        return _after_quiet_end(now)
    lo, hi = _interval_minutes(state, positive_high)
    candidate = now + timedelta(minutes=random.uniform(lo, hi))
    if _quiet(candidate):
        candidate = _after_quiet_end(candidate)
    return candidate


def schedule(state: ChitchatState | None = None, positive_high: bool = True) -> ChitchatState:
    state = state or load()
    state.next_due_at = _next_due(state, positive_high).isoformat(timespec="seconds")
    return save(state)


def _miss_schedule(state: ChitchatState, minutes: float | None = None,
                   now: datetime | None = None) -> ChitchatState:
    """到点但没出手（抽签没过 / 间隔还差一点）：只往后挪一小段。

    不然"一天一条"的配置会因为一次抽签失败，直接被推到两三天以后。
    """
    now = now or datetime.now()
    if minutes is None:
        base = float(_cfg().get("miss_retry_minutes", 300))
        minutes = random.uniform(max(15.0, base * 0.6), max(15.0, base))
    candidate = now + timedelta(minutes=max(5.0, float(minutes)))
    if _quiet(candidate):
        candidate = _after_quiet_end(candidate)
    state.next_due_at = candidate.isoformat(timespec="seconds")
    return save(state)


# ── 白名单目标（只读桥接的 allow.private，不改桥接）─────────────────────────
def whitelist() -> list[str]:
    """能被主动搭话的人：桥接 config.json → allow.private（owner 永远排第一）。

    读不到桥接配置时退回"只有 owner"，绝不因为读文件失败就不说话。
    """
    owner = _owner()
    ids: list[str] = []
    try:
        bridge_dir = str(settings.app().get("bridge", {}).get("dir") or "")
        if bridge_dir:
            raw = json.loads((Path(bridge_dir) / "config.json").read_text(encoding="utf-8-sig"))
            for item in ((raw.get("allow") or {}).get("private") or []):
                text = str(item).strip()
                if text.isdigit() and text not in ids:
                    ids.append(text)
    except Exception:
        ids = []
    out: list[str] = []
    for qq in [owner] + ids:
        if qq and qq.isdigit() and qq not in out:
            out.append(qq)
    return out or [owner]


def _targets() -> dict:
    return settings.read_state(TARGETS_FILE, {}) or {}


def _pick_target() -> str:
    """轮着来：最久没被搭话的优先，从没搭过话的排最前；连续发不出去的人先跳过。"""
    people = whitelist()
    dead = set(_targets().get("dead") or [])
    alive = [qq for qq in people if qq not in dead] or people
    if len(alive) <= 1:
        return alive[0]
    last = _targets().get("last") or {}
    return sorted(alive, key=lambda qq: (str(last.get(qq) or ""), random.random()))[0]


def _mark_target(qq: str, now: datetime | None = None) -> None:
    """记下这个人的搭话时间（下次轮给别人）。"""
    _note_result(qq, True, now)


def _note_result(qq: str, ok: bool, now: datetime | None = None) -> None:
    """记搭话时间 + 失败次数：连续发不出去的人先晾一边，别一直往死号上撞。"""
    now = now or datetime.now()
    qq = str(qq)
    state = _targets()
    alive = set(whitelist())
    last = {k: v for k, v in dict(state.get("last") or {}).items() if k in alive}
    fails = {k: v for k, v in dict(state.get("fails") or {}).items() if k in alive}
    dead = [q for q in (state.get("dead") or []) if q in alive]
    last[qq] = now.isoformat(timespec="seconds")
    if ok:
        fails.pop(qq, None)
        if qq in dead:
            dead.remove(qq)
    else:
        fails[qq] = int(fails.get(qq) or 0) + 1
        if fails[qq] >= int(_cfg().get("dead_after_failures", 3)) and qq not in dead:
            dead.append(qq)
    state["last"] = last
    state["fails"] = fails
    state["dead"] = dead
    try:
        settings.write_state(TARGETS_FILE, state)
    except Exception:
        pass


# ── 投递 ────────────────────────────────────────────────────────────────────
def _pending_dir() -> Path:
    folder = settings.STATE / PENDING_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _console_send(text: str, qq: str) -> dict:
    """桥接控制台通道：POST /api/test-send。

    用 x-console-token（不带 x-agent-token）走"操作者"通道：跳过会话 agentToken 闸门，
    但仍然强制 白名单 + 敏感信息审计。这样白名单里**任何人**都收得到她的碎碎念，
    不用改桥接代码，也不用为每个人单独拿会话令牌。
    """
    qq = str(qq or "").strip()
    if not qq.isdigit():
        return {"ok": False, "error": f"目标 QQ 不是纯数字：{qq!r}"}
    try:
        ok, err = push._post(  # 复用 bridge/push 的 HTTP 细节（端口/令牌读法保持一致）
            "/api/test-send",
            {"kind": "private", "id": int(qq), "message": text},
            "", console_token=push._console_token(),
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": bool(ok), "error": "" if ok else str(err)}


def _queue_pending(text: str, qq: str, error: str) -> str:
    try:
        folder = _pending_dir()
        old = sorted(folder.glob("pending-*.json"))
        for path in old[:-20]:          # 只留最近 20 条，别把磁盘堆满
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        path = folder / f"pending-{datetime.now():%Y%m%d-%H%M%S}-{random.randint(1000, 9999)}.json"
        path.write_text(json.dumps(
            {"text": text, "userId": qq, "t": datetime.now().isoformat(timespec="seconds"),
             "error": error}, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except Exception:
        return ""


def deliver(text: str, qq: str | None = None) -> dict:
    """把一句碎碎念递给指定的人；失败就排队等下次补发。"""
    target = str(qq or _pick_target() or _owner())
    if target == _owner():
        # owner 走原通道：会话 agentToken → 控制台 token → outbox（bridge/push 自己会有序重试）
        result = push.send_private(text)
        return {"ok": bool(result.get("ok")), "via": f"owner:{result.get('via') or ''}",
                "error": str(result.get("error") or ""), "outbox": str(result.get("outbox") or "")}
    sent = _console_send(text, target)
    if sent.get("ok"):
        return {"ok": True, "via": "console", "error": "", "outbox": ""}
    box = _queue_pending(text, target, str(sent.get("error") or ""))
    return {"ok": False, "via": "", "error": str(sent.get("error") or ""), "outbox": box}


def flush_pending(limit: int = 2) -> dict:
    """补发桥接不在线时攒下的句子（一次最多 limit 条；太旧的直接丢）。"""
    folder = settings.STATE / PENDING_DIR
    if not folder.exists():
        return {"sent": 0, "left": 0}
    ttl = float(_cfg().get("pending_ttl_hours", 12))
    sent = 0
    now = datetime.now()
    for path in sorted(folder.glob("pending-*.json"))[:max(0, int(limit))]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            path.unlink(missing_ok=True)
            continue
        try:
            age_h = (now - datetime.fromisoformat(str(payload.get("t") or ""))).total_seconds() / 3600.0
        except Exception:
            age_h = 999.0
        if age_h > ttl:
            path.unlink(missing_ok=True)
            continue
        if _console_send(str(payload.get("text") or ""), str(payload.get("userId") or "")).get("ok"):
            path.unlink(missing_ok=True)
            sent += 1
        else:
            break
    return {"sent": sent, "left": len(list(folder.glob("pending-*.json")))}


# ── 学习分享（学到东西 / 有疑惑 → 主动找人讲一句）──────────────────────────
SHARE_TEMPLATES = [
    "刚上网看到个事：{name}，{short}",
    "今天才知道{name}，{short}",
    "诶我刚查到{name}，{short}",
    "刚看到个说法：{name}，{short}",
]
SHARE_TEMPLATES_PLAIN = [
    "刚上网看到个事：{name}，感觉挺有用",
    "今天才知道{name}，居然是这样",
    "诶我刚查到{name}，有点意思",
    "刚看到个说法：{name}，我记下来了",
    "我这两天在翻{topic}，看到个有意思的：{name}",
]
ASK_TEMPLATES = [
    "我在查{name}，还没完全搞懂，你知道吗",
    "有个事一直犯迷糊：{name}，你懂吗",
    "{name}这块我看了半天还是不太懂，你讲讲呗",
    "刚学了个东西：{name}，可我没全懂",
]
SHARE_PROMPT = """你在扮演大肥鱼，一个二十岁左右的女生。你刚自己在网上看了点东西，想主动找人说一句，像随口分享，不像汇报。
你刚看到的内容：{name}——{detail}
要求：一句话，不超过 40 字，口语；可以带一点"居然是这样 / 有点意思 / 没想到"的情绪；不要说"我学习了/我了解到"、不要科普腔、不要列点、别自称AI。只输出这句话。"""
ASK_PROMPT = """你在扮演大肥鱼，一个二十岁左右的女生。你学了个东西但没完全弄懂，想问问对方。
你没弄明白的是：{question}
要求：一句话，不超过 40 字，像真的在请教，可以有点不好意思；别装懂、别列点、别自称AI。只输出这句话。"""
# 这些"领域名"太泛，塞进台词里会很怪
_BAD_TOPICS = ("", "通用", "日常", "其他", "综合", "未分类")


def _share_cfg() -> dict:
    node = _cfg().get("share")
    return node if isinstance(node, dict) else {}


def _shares() -> dict:
    return settings.read_state(SHARES_FILE, {}) or {}


def _save_shares(data: dict) -> dict:
    try:
        settings.write_state(SHARES_FILE, data)
    except Exception:
        pass
    return data


def _parse_iso(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except Exception:
        return None


def _short_fact(text: str, name: str = "") -> str:
    """把一整段学到的东西压成一句短的（去掉"名称："/"这是…法："这类壳子）。"""
    body = re.sub(r"\s+", " ", str(text or "").strip())
    if not body:
        return ""
    if name:
        for sep in ("：", ":"):
            head, found, rest = body.partition(sep)
            if found and head.strip() and (head.strip() in name or name in head.strip()):
                body = rest.strip()
                break
    body = re.split(r"[。！？；\n]", body)[0].strip()
    # "这是鞋内吸湿防霉法：把报纸…" → "把报纸…"
    body = re.sub(r"^(这是|这是一种|这是指|就是指|指的是|即)[^：:，,]{0,12}[：:]", "", body)
    body = re.sub(r"^(这是|就是指|指的是|即|所谓)", "", body)
    return body.strip(" ，,。")


def _clean_question(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().rstrip("？?").strip())


def _share_daily_used(now: datetime | None = None) -> int:
    """今天分享了几次：DB 与 state 取大（防同一秒落库被 INSERT OR REPLACE 覆盖）。"""
    now = now or datetime.now()
    st = 0
    sh = _shares()
    if str(sh.get("count_date") or "") == now.strftime("%Y-%m-%d"):
        st = int(sh.get("count") or 0)
    try:
        rows = int(db.scalar(
            "SELECT COUNT(*) FROM chitchat_log WHERE t >= ? AND id LIKE 'share-%'",
            (_today_start(now),), 0) or 0)
    except Exception:
        rows = 0
    return max(rows, st)


def _share_interval() -> tuple[float, float]:
    lo_hi = _share_cfg().get("interval_min", [150, 300])
    try:
        lo, hi = float(lo_hi[0]), float(lo_hi[1])
    except Exception:
        lo, hi = 150.0, 300.0
    return max(30.0, lo), max(60.0, hi)


def _schedule_share(now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    lo, hi = _share_interval()
    candidate = now + timedelta(minutes=random.uniform(lo, hi))
    if _quiet(candidate):
        candidate = _after_quiet_end(candidate)
    sh = _shares()
    sh["next_share_at"] = candidate.isoformat(timespec="seconds")
    _save_shares(sh)
    return candidate


def _bump_share_next(minutes: float | None = None, now: datetime | None = None) -> None:
    """这次没料/没抽中：把分享时间往后推一点，别每分钟都翻一遍学习记录。"""
    now = now or datetime.now()
    mins = minutes if minutes is not None else float(_share_cfg().get("retry_minutes", 45))
    sh = _shares()
    sh["next_share_at"] = (now + timedelta(minutes=max(5.0, float(mins)))).isoformat(timespec="seconds")
    _save_shares(sh)


def _fresh_count(now: datetime | None = None) -> int:
    """最近有没有"刚学到"的东西（10 分钟内缓存一次，别每分钟查库）。"""
    now = now or datetime.now()
    sh = _shares()
    checked = _parse_iso(sh.get("checked_at"))
    if checked and (now - checked) <= timedelta(minutes=10):
        return int(sh.get("fresh_count") or 0)
    cutoff = (now - timedelta(minutes=float(_share_cfg().get("fresh_minutes", 45)))
              ).isoformat(timespec="seconds")
    count = 0
    try:
        count += int(db.scalar("SELECT COUNT(*) FROM knowledge_item WHERE learned_at >= ?",
                               (cutoff,), 0) or 0)
    except Exception:
        pass
    try:
        count += int(db.scalar(
            "SELECT COUNT(*) FROM learning_intent WHERE t_created >= ? "
            "AND status NOT IN ('done','failed','cancelled','dropped')", (cutoff,), 0) or 0)
    except Exception:
        pass
    sh["checked_at"] = now.isoformat(timespec="seconds")
    sh["fresh_count"] = count
    _save_shares(sh)
    return count


def _knowledge_candidates(now: datetime) -> list[dict]:
    cfg = _share_cfg()
    cutoff = (now - timedelta(hours=float(cfg.get("lookback_hours", 36)))).isoformat(timespec="seconds")
    try:
        rows = db.q("SELECT id,name,domain,content,uncertainty,filled_ratio,source,learned_at "
                    "FROM knowledge_item WHERE learned_at >= ? ORDER BY learned_at DESC LIMIT 30",
                    (cutoff,))
    except Exception:
        return []
    out = []
    for row in rows:
        data = dict(row)
        name = str(data.get("name") or "").strip()
        if len(name) < 3:
            continue
        short = _short_fact(str(data.get("content") or ""), name)
        if not short:
            continue
        out.append({"kind": "share", "key": f"knowledge:{data.get('id')}",
                    "name": clip(name, 24), "short": clip(short, 22),
                    "topic": clip(str(data.get("domain") or ""), 16),
                    "source": str(data.get("source") or "")})
    return out


def _question_candidates(now: datetime) -> list[dict]:
    cfg = _share_cfg()
    cutoff = (now - timedelta(hours=float(cfg.get("lookback_hours", 36)))).isoformat(timespec="seconds")
    out: list[dict] = []
    try:
        rows = db.q("SELECT id,target,proposed_query,gap_type,t_created FROM learning_intent "
                    "WHERE t_created >= ? AND status NOT IN ('done','failed','cancelled','dropped') "
                    "ORDER BY t_created DESC LIMIT 20", (cutoff,))
    except Exception:
        rows = []
    for row in rows:
        data = dict(row)
        target = str(data.get("target") or "").strip()
        proposed = _clean_question(str(data.get("proposed_query") or ""))
        name = target or re.sub(r"(的细节|的具体做法|的具体内容)?(是什么呢?)?$", "", proposed).strip()
        if len(name) < 3:
            continue
        out.append({"kind": "ask", "key": f"intent:{data.get('id')}", "name": clip(name, 24),
                    "question": clip(proposed or f"{name}我还没完全搞懂", 40),
                    "topic": "", "source": "learning_intent"})
    # 学得不踏实的条目也可以拿来问
    try:
        rows2 = db.q("SELECT id,name,domain,uncertainty,filled_ratio,learned_at FROM knowledge_item "
                     "WHERE learned_at >= ? AND (uncertainty >= 0.55 OR filled_ratio < 0.6) "
                     "ORDER BY learned_at DESC LIMIT 10", (cutoff,))
    except Exception:
        rows2 = []
    for row in rows2:
        data = dict(row)
        name = str(data.get("name") or "").strip()
        if len(name) < 3:
            continue
        out.append({"kind": "ask", "key": f"unsure:{data.get('id')}", "name": clip(name, 24),
                    "question": clip(f"{name}这块我还是没太懂", 30),
                    "topic": clip(str(data.get("domain") or ""), 16), "source": "knowledge_uncertain"})
    return out


def _report_candidates(now: datetime) -> list[dict]:
    """兜底：学习报告里的「原始输出」条目（DB 里没有对应知识条目时用）。"""
    folder = settings.LEARNING / "reports"
    if not folder.exists():
        return []
    cutoff = now - timedelta(hours=float(_share_cfg().get("lookback_hours", 36)))
    out: list[dict] = []
    try:
        paths = sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    except Exception:
        return []
    for path in paths:
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        topic = path.stem.split("-", 3)[-1] if "-" in path.stem else path.stem
        block = text.split("## 原始输出", 1)[-1] if "## 原始输出" in text else ""
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            body = line[2:].strip()
            name, found, rest = body.partition("：")
            if not found:
                name, rest = body[:18], ""
            name = name.strip()
            if len(name) < 3:
                continue
            out.append({"kind": "share", "key": f"report:{name[:24]}", "name": clip(name, 24),
                        "short": clip(_short_fact(rest or body, name), 22),
                        "topic": clip(topic, 16), "source": "learning_report"})
    return out


def _pick_share_item(now: datetime | None = None) -> dict | None:
    """挑一条可分享的内容：要么"有意思的事实"，要么"还没弄明白的问题"。"""
    now = now or datetime.now()
    shared = _shares().get("shared") or {}
    want_ask = random.random() < float(_share_cfg().get("ask_ratio", 0.35))

    def fresh(items: list[dict]) -> list[dict]:
        return [i for i in items if str(i.get("key") or "") not in shared]

    ask = fresh(_question_candidates(now))
    share = fresh(_knowledge_candidates(now))
    if len(share) < 2:
        # 知识条目不多时才去翻学习报告（读文件贵一点）
        share += fresh(_report_candidates(now))
    if want_ask and ask:
        return random.choice(ask)
    if share:
        return random.choice(share)
    if ask:
        return random.choice(ask)          # 想分享但没新料，那就问一句
    return None


def share_opportunity(now: datetime | None = None) -> dict:
    """现在能不能拿"刚学到的东西"去主动说一句（分享流有独立预算与节奏）。"""
    now = now or datetime.now()
    cfg = _share_cfg()
    if not cfg.get("enabled", True):
        return {"ready": False, "reason": "学习分享关掉了"}
    state = load()
    if not state.enabled:
        return {"ready": False, "reason": "碎碎念开关关着"}
    if muted(state, now):
        return {"ready": False, "reason": "静默中"}
    if _quiet(now):
        return {"ready": False, "reason": "夜里安静时间"}
    cap = int(cfg.get("daily_max", 2))
    used = _share_daily_used(now)
    if used >= cap:
        return {"ready": False, "reason": f"今天已经分享过 {used} 次（上限 {cap}）"}
    gap = _gap_left_minutes(state, now)
    if gap > 0:
        return {"ready": False, "reason": f"距上一句还不到间隔（还要 {gap:.0f} 分钟）"}
    due_at = _parse_iso(_shares().get("next_share_at"))
    fresh = _fresh_count(now)
    if due_at is not None and due_at > now and fresh <= 0:
        return {"ready": False, "reason": "还没到分享的点"}
    item = _pick_share_item(now)
    if not item:
        _bump_share_next(now=now)
        return {"ready": False, "reason": "暂时没有新东西可分享"}
    return {"ready": True, "item": item, "fresh": fresh,
            "timed": due_at is None or due_at <= now}


def compose_share(item: dict, vec=None) -> str:
    """把"刚学到的 / 没弄懂的"说成一句人话；模型不可用走句式库。"""
    cfg = _share_cfg()
    kind = str((item or {}).get("kind") or "share")
    name = str((item or {}).get("name") or "").strip()
    detail = str((item or {}).get("short") or "").strip()
    topic = str((item or {}).get("topic") or "").strip()
    question = str((item or {}).get("question") or "").strip() or name
    if not (name or question):
        return ""
    limit = int(cfg.get("max_chars", 44))
    from .. import llm

    if llm.provider.available("chitchat"):
        try:
            prompt = (SHARE_PROMPT.format(name=name, detail=detail or "挺有意思的")
                      if kind == "share" else ASK_PROMPT.format(question=question))
            ok, text, _meta = llm.provider.run(prompt, task="chitchat", timeout=120)
            if ok and text.strip():
                return clip(_clean(text), limit + 6)
        except Exception:
            pass
    if kind == "share":
        short = detail if len(detail) <= 12 else ""
        if short:
            text = random.choice(SHARE_TEMPLATES).format(name=name, short=short)
        elif topic and topic not in _BAD_TOPICS:
            text = random.choice(SHARE_TEMPLATES_PLAIN).format(name=name, topic=topic)
        else:
            plain = [t for t in SHARE_TEMPLATES_PLAIN if "{topic}" not in t]
            text = random.choice(plain).format(name=name)
    else:
        text = random.choice(ASK_TEMPLATES).format(name=name or question)
    return clip(_clean(text), limit + 6)


def _finish_share(item: dict, text: str, now: datetime | None = None) -> None:
    """记下这次分享（同一条 7 天内不再重复说），并把下次分享时间往后排。"""
    now = now or datetime.now()
    cfg = _share_cfg()
    repeat = float(cfg.get("repeat_days", 7))
    cutoff = (now - timedelta(days=repeat)).isoformat(timespec="seconds")
    sh = _shares()
    shared = {k: v for k, v in (sh.get("shared") or {}).items() if str(v) >= cutoff}
    key = str((item or {}).get("key") or "")
    if key:
        shared[key] = now.isoformat(timespec="seconds")
    today = now.strftime("%Y-%m-%d")
    count = int(sh.get("count") or 0) if str(sh.get("count_date") or "") == today else 0
    sh.update({"shared": shared, "last_share_at": now.isoformat(timespec="seconds"),
               "last_kind": str((item or {}).get("kind") or "share"), "last_text": text,
               "count_date": today, "count": count + 1,
               "checked_at": now.isoformat(timespec="seconds"), "fresh_count": 0})
    _save_shares(sh)
    _schedule_share(now)


def share_now(vec=None) -> dict:
    """手动让她分享一条（调试/界面用；不占用普通碎碎念的预算）。"""
    item = _pick_share_item()
    if not item:
        return {"ok": False, "skipped": "暂时没有新东西可分享"}
    text = compose_share(item, vec)
    if not text:
        return {"ok": False, "skipped": "没组成句子"}
    out = send_once(text, vec, manual=True, kind="share", item=item)
    out["item"] = item
    return out


# ── 闸门 / 静默 ─────────────────────────────────────────────────────────────
def muted(state: ChitchatState, now: datetime | None = None) -> bool:
    if not state.muted_until:
        return False
    now = now or datetime.now()
    try:
        return datetime.fromisoformat(state.muted_until) > now
    except Exception:
        return False


def due(state: ChitchatState | None = None, now: datetime | None = None) -> bool:
    state = state or load()
    now = now or datetime.now()
    if not state.enabled or muted(state, now):
        return False
    # 学习分享有自己的预算：到点/刚学到东西时也该叫她一声
    try:
        if share_opportunity(now).get("ready"):
            return True
    except Exception:
        pass
    if _blocked_reason(state, now):
        return False
    try:
        return datetime.fromisoformat(state.next_due_at) <= now
    except Exception:
        return True


def gate(vec, state: ChitchatState | None = None) -> dict:
    """此刻该不该碎碎念（只判情绪/开关，时间与预算在 due()/run() 里判）。"""
    state = state or load()
    beh = settings.emotion_cfg().get("behavior", {})
    neg = (vec.get("sadness") + vec.get("inferiority")) / 2.0
    neg_th = float(beh.get("negative_threshold", 60))
    pos = (vec.get("joy") + vec.get("happiness")) / 2.0
    pos_th = float(beh.get("positive_threshold", 60))
    extra = {"daily_used": _daily_used(), "daily_max": _daily_max(), "quiet": _quiet()}
    if neg >= neg_th:
        return {"allowed": False, "reason": "负面情绪高，进入静默（不理人）",
                "negative": round(neg, 1), **extra}
    if muted(state):
        return {"allowed": False, "reason": f"静默到 {state.muted_until}",
                "negative": round(neg, 1), **extra}
    if not state.enabled:
        return {"allowed": False, "reason": "开关关掉了", "negative": round(neg, 1), **extra}
    return {"allowed": True, "reason": "可以", "positive_high": pos >= pos_th,
            "positive": round(pos, 1), "negative": round(neg, 1), **extra}


def mute(minutes: float | None = None, state: ChitchatState | None = None) -> ChitchatState:
    state = state or load()
    minutes = minutes if minutes is not None else float(settings.get("chitchat.mute_minutes", 90))
    state.muted_until = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return save(state)


# ── 说点什么 ────────────────────────────────────────────────────────────────
def _pick_line(tone: str) -> str:
    """从台词库挑一句，尽量避开最近说过的（别重复同一句）。"""
    pool = list(TEMPLATES.get(tone) or TEMPLATES["normal"])
    try:
        recent = {row.get("content") for row in sr.chitchat_recent(12)}
    except Exception:
        recent = set()
    fresh = [line for line in pool if line not in recent]
    return random.choice(fresh or pool)


def compose(vec=None, policy=None) -> str:
    """生成一条碎碎念：语言系统 + 情绪调制；模型不可用时用台词库。"""
    from .. import llm
    from ..emotion import policy as policy_mod
    from ..emotion import vector as vec_mod
    from ..llm import prompts
    from ..memory import retrieval
    from ..text import clip as _clip

    vec = vec or vec_mod.current()
    policy = policy or policy_mod.build(vec)
    ctx = retrieval.context_block(retrieval.recall("碎碎念", vec=vec, top_k=4, touch=False), 300)
    if llm.provider.available("chitchat"):
        ok, text, _meta = llm.provider.run(
            prompts.CHITCHAT.format(
                emotion=f"{vec.dominant_cn()}（愉悦{int(vec.get('joy'))}/幸福{int(vec.get('happiness'))}"
                        f"/难过{int(vec.get('sadness'))}/自卑{int(vec.get('inferiority'))}/害羞{int(vec.get('shyness'))}）",
                tone=policy_mod.tone_hint(policy),
                memory=ctx or "（没有特别的）",
            ),
            task="chitchat", timeout=120,
        )
        if ok and text.strip():
            return _clip(_clean(text), 120)
    return _pick_line(_TONE_KEY.get(policy.tone, "normal"))


def _clean(text: str) -> str:
    body = (text or "").strip().strip('"“”')
    for prefix in ("大肥鱼：", "大肥鱼:", "我："):
        if body.startswith(prefix):
            body = body[len(prefix):]
    return body.strip()


def _normal_due(state: ChitchatState, now: datetime | None = None) -> bool:
    """普通碎碎念是否到点（分享流不算）。"""
    now = now or datetime.now()
    try:
        return datetime.fromisoformat(state.next_due_at) <= now
    except Exception:
        return True


def send_once(text: str, vec=None, *, manual: bool = False,
              target_qq: str | None = None, kind: str = "chitchat",
              item: dict | None = None) -> dict:
    """推送一条内容给白名单里的某个人，并记账。

    kind="chitchat"：普通碎碎念（走 daily_max / next_due）；
    kind="share"：学习分享（走 chitchat.share 的独立预算，同一条 repeat_days 内不重复）。
    """
    from ..emotion import vector as vec_mod
    from ..memory import instant

    vec = vec or vec_mod.current()
    target = str(target_qq or _pick_target() or _owner())
    result = deliver(text, target)

    state = load()
    now = datetime.now()
    state.last_chitchat_at = now.isoformat(timespec="seconds")
    state.consecutive_no_reply = 0 if manual else state.consecutive_no_reply
    state.total_sent += 1
    if not manual:
        _note_result(target, bool(result.get("ok")), now)
    if kind == "share":
        _finish_share(item or {}, text, now)        # 分享：独立预算 + 去重 + 排下次
    elif not manual:
        state.next_due_at = _next_due(state).isoformat(timespec="seconds")
    save(state)

    prefix = "share" if kind == "share" else "chitchat"
    # 带随机尾数：同一秒连发两条时别被 INSERT OR REPLACE 合并成一条
    cid = f"{prefix}-{now:%Y%m%d%H%M%S}-{random.randint(100, 999)}"
    sr.chitchat_add(cid, state.last_chitchat_at, text, vec.dominant_cn())
    instant.add_chat(text, conv=f"private:{target}", sender="自己", is_self=True,
                     is_owner=(target == _owner()), salience=0.6 if kind == "share" else 0.55)
    sr.audit_add("chitchat", cid, "shared" if kind == "share" else "created",
                 {"text": clip(text, 60), "kind": kind, "pushed": result.get("ok"),
                  "via": result.get("via"), "target": target, "manual": manual,
                  "item": (item or {}).get("key", ""), "error": result.get("error", "")})
    return {"ok": bool(result.get("ok")), "text": text, "via": result.get("via"),
            "error": result.get("error", ""), "outbox": result.get("outbox", ""),
            "id": cid, "target": target, "kind": kind}


def run(vec=None, policy=None, manual: bool = False) -> dict:
    if not manual:
        try:
            cfg = push._read_json(str(Path(push._bridge_dir()) / 'config.json'), {}) or {}
            proactive = (cfg.get('socialV2') or {}).get('proactive') or {}
            if push.mode() == 'reserved2' and proactive.get('enabled', True) and (proactive.get('dailyContact') or {}).get('enabled', False):
                return {"ok": True, "skipped": "由桥接统一按会话每天主动联系，避免两套调度重复发消息", "managed_by": "conversation-care"}
        except Exception:
            pass
    gate_info = gate(vec) if vec is not None else {"allowed": True}
    if not manual and not gate_info.get("allowed"):
        state = load()
        if "负面" in str(gate_info.get("reason")):
            state.muted_until = (datetime.now() + timedelta(
                minutes=float(settings.get("chitchat.mute_minutes", 90)))).isoformat(timespec="seconds")
            save(state)
        schedule(state, positive_high=bool(gate_info.get("positive_high", True)))
        return {"ok": False, "skipped": gate_info.get("reason", "")}

    if not manual:
        try:
            flush_pending()
        except Exception:
            pass
        # ① 优先：刚学到的/没弄懂的，主动分享一句（独立预算）
        try:
            share = share_opportunity()
        except Exception:
            share = {"ready": False, "reason": "分享检查失败"}
        if share.get("ready"):
            if random.random() <= float(_share_cfg().get("probability", 0.75)):
                text = compose_share(share["item"], vec)
                if text:
                    out = send_once(text, vec, manual=False, kind="share", item=share["item"])
                    out["gate"] = gate_info
                    return out
            _bump_share_next(minutes=float(_share_cfg().get("retry_minutes", 45)))

        # ② 普通碎碎念
        state = load()
        blocked = _blocked_reason(state)
        if blocked:
            if "间隔" in blocked:
                # 只是"还没到下一句的间隔"：等够间隔再说，别把周期整段浪费掉
                _miss_schedule(state, minutes=_gap_left_minutes(state) + 5)
            else:
                schedule(state, positive_high=bool(gate_info.get("positive_high", True)))
            return {"ok": False, "skipped": blocked, "gate": gate_info}
        if not _normal_due(state):
            return {"ok": False, "skipped": "还没到说闲话的点（这次只是分享机会）", "gate": gate_info}
        # 到点了也不是每次都开口：掷一次骰子；没抽中只往后挪一小段
        if random.random() > _send_probability():
            _miss_schedule(state)
            return {"ok": False, "skipped": "这次没抽中（保持偶尔才冒一次）", "gate": gate_info}

    text = compose(vec, policy)
    out = send_once(text, vec, manual=manual)
    out["gate"] = gate_info
    return out


def on_owner_replied() -> ChitchatState:
    """老大回话了：清零疲劳，标记最近一条碎碎念被回应。"""
    state = load()
    state.consecutive_no_reply = 0
    state.fatigue_level = 0
    state.muted_until = None
    save(state)
    for row in sr.chitchat_unanswered()[:3]:
        sr.chitchat_mark_replied(row["id"], now_iso())
    return state


def no_reply_tick(now: datetime | None = None, vec=None) -> dict:
    """连续无回应：加疲劳、降频；够久就记一次「被忽视」惩罚（规格 6.7 联动）。"""
    now = now or datetime.now()
    cfg = _cfg()
    state = load()
    # 分享"被忽略"不额外罚她：只把普通碎碎念算进疲劳
    unanswered = [row for row in sr.chitchat_unanswered()
                  if not str(row.get("id") or "").startswith("share-")]
    fresh = []
    for row in unanswered:
        try:
            age_min = (now - datetime.fromisoformat(row["t"])).total_seconds() / 60.0
        except Exception:
            continue
        if age_min >= 5:
            fresh.append(row)
    previous = state.consecutive_no_reply
    state.consecutive_no_reply = len(fresh)
    result = {"consecutive": len(fresh), "fatigue": state.fatigue_level}

    # 每 20 秒都会调一次这里：只有「又攒出一条新的没被回」才升疲劳档，
    # 否则一分钟就能把三档全升满（真摔过一次）。
    if len(fresh) >= int(cfg.get("fatigue_after", 3)) and len(fresh) > previous:
        state.fatigue_level = min(int(cfg.get("fatigue_max_level", 3)), state.fatigue_level + 1)
        state.next_due_at = (now + timedelta(minutes=float(cfg.get("fatigue_pause_min", 90))
                                            * float(cfg.get("fatigue_multiplier", 1.8)) ** state.fatigue_level)
                             ).isoformat(timespec="seconds")
        result["fatigue"] = state.fatigue_level
        result["paused_until"] = state.next_due_at

        # 被忽视 → 惩罚（同一段静默只记一次）
        oldest = min((r.get("t") or "") for r in fresh)
        if oldest and oldest > (state.last_ignored_at or ""):
            from ..emotion import reward

            ignored_minutes = max(int(cfg.get("ignored_minutes", 180)),
                                  int((now - datetime.fromisoformat(oldest)).total_seconds() / 60))
            reward.apply_trigger("ignored", f"被晾着 {ignored_minutes} 分钟", reason="碎碎念连续无回应")
            state.last_ignored_at = now.isoformat(timespec="seconds")
            result["punished"] = True
    save(state)
    return result


def manual(vec=None) -> dict:
    """界面上手动触发一次。"""
    return run(vec=vec, manual=True)


def view(vec=None) -> dict:
    state = load()
    info = gate(vec) if vec is not None else {"allowed": state.enabled}
    people = whitelist()
    sh = _shares()
    try:
        share_ready = share_opportunity().get("ready", False)
    except Exception:
        share_ready = False
    return {
        "enabled": state.enabled,
        "last_chitchat_at": state.last_chitchat_at,
        "next_due_at": state.next_due_at,
        "consecutive_no_reply": state.consecutive_no_reply,
        "fatigue_level": state.fatigue_level,
        "muted_until": state.muted_until,
        "total_sent": state.total_sent,
        "gate": info,
        "interval": _interval_minutes(state, bool(info.get("positive_high", True))),
        "daily": {"used": _daily_used(), "max": _daily_max(),
                  "probability": _send_probability(), "quiet": _quiet()},
        "share": {
            "enabled": bool(_share_cfg().get("enabled", True)),
            "ready": share_ready,
            "used_today": _share_daily_used(),
            "daily_max": int(_share_cfg().get("daily_max", 2)),
            "interval": _share_interval(),
            "last_share_at": sh.get("last_share_at", ""),
            "next_share_at": sh.get("next_share_at", ""),
            "last_kind": sh.get("last_kind", ""),
            "shared_recent": len(sh.get("shared") or {}),
        },
        "targets": {"whitelist": people, "last": (_targets().get("last") or {}),
                    "dead": list(_targets().get("dead") or [])},
        "recent": sr.chitchat_recent(10),
        "push": push.status(),
    }


def set_enabled(flag: bool) -> ChitchatState:
    state = load()
    state.enabled = bool(flag)
    if state.enabled:
        state.next_due_at = _next_due(state).isoformat(timespec="seconds")
        if not _shares().get("next_share_at"):
            _schedule_share()
    return save(state)
