# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v31 内稳态：能量 / 社交连接 / 认知新颖性 —— 让行为从「需求误差」里长出来。

和情绪系统的分工（这是整个补丁的关键）：
  · **内稳态**是「身体」：三个 0~1 的慢变量，按分钟涨落，永远朝着设定点回家；
  · **情绪**是「脸色」：由内稳态的误差**染色**（孤独→难过↑，疲劳→愉悦↓），
    但情绪还有自己的快回路（被夸、被怼、害羞），不归内稳态管；
  · **好奇心**是「手脚」：无聊到 0.3 以上时把它推去学习，困到 0.2 以下时一刀切断。

为什么不用「刺激-反应」：老大要的是「她一个人待着也会难受」。
刺激-反应只在有人说话时成立；内稳态在**没人说话**时也一直在算，
所以她会在下午三点因为「好久没人理」自己跑来找你，而不是等一个定时器。

三条动机（= 设定点 − 当前值，取正）：
    fatigue    = 0.8 − energy           → 困：回复变短、不想学
    loneliness = 0.6 − social_connection→ 空：想找人说话，主动率高
    boredom    = 0.5 − cognitive_novelty→ 闲：推给好奇心引擎去学点什么

所有状态只在 `state/homeostasis.json`，不碰记忆表、不改情绪存储格式。
"""

from __future__ import annotations

from datetime import datetime

from . import settings

FILE = "homeostasis.json"

SETPOINTS = {"energy": 0.8, "social_connection": 0.6, "cognitive_novelty": 0.5}

# 每分钟的速率（可被 config/homeostasis.json 覆盖，测试里也会改）
RATES = {
    "energy_gain_idle": 0.012,     # 闲着回血
    "energy_cost_active": 0.020,   # 有互动时消耗
    "social_decay": 0.010,         # 没人理，慢慢空
    "social_gain_message": 0.28,   # 收到一条消息
    "novelty_decay": 0.008,        # 日常停滞
    "novelty_gain_learn": 0.35,    # 学完一批（或刷完一阵视频）
    "energy_cost_learn": 0.06,
    "energy_cost_refusal": 0.06,   # 拒绝/生气更费神
}

# 动机超过这个值才算「有话说」，否则算平静
MOTIVE_FLOOR = 0.05
# 多久没互动算「不活跃」（这段时间能量回血）
IDLE_MINUTES = 10.0

LABEL = {"fatigue": "疲劳", "loneliness": "孤独", "boredom": "无聊"}


def cfg() -> dict:
    data = settings.read_json(settings.CONFIG / "homeostasis.json", {}) or {}
    out = {"enabled": True, "coloring": True, "setpoints": dict(SETPOINTS), **RATES}
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "setpoints" and isinstance(value, dict):
                out["setpoints"].update({k: float(v) for k, v in value.items() if k in SETPOINTS})
            elif key in out:
                out[key] = value
    return out


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _now() -> datetime:
    return datetime.now()


def _blank(now: datetime) -> dict:
    c = cfg()
    return {
        "values": {k: float(c["setpoints"][k]) for k in SETPOINTS},
        "motives": {"fatigue": 0.0, "loneliness": 0.0, "boredom": 0.0},
        "dominant": "",
        "strength": 0.0,
        "last_activity_at": now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "tick_count": 0,
    }


def load() -> dict:
    now = _now()
    raw = settings.read_state(FILE, {})
    if not isinstance(raw, dict) or not isinstance(raw.get("values"), dict):
        return _blank(now)
    base = _blank(now)
    base["values"].update({k: _clamp01(v) for k, v in raw["values"].items() if k in SETPOINTS})
    for key in ("motives", "dominant", "strength", "last_activity_at", "updated_at", "tick_count"):
        if key in raw:
            base[key] = raw[key]
    return base


def save(data: dict) -> dict:
    settings.write_state(FILE, data)
    return data


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp))
    except Exception:
        return None


# ── 动机 ────────────────────────────────────────────────────────────────────
def motives_of(values: dict, setpoints: dict | None = None) -> dict:
    """误差信号：设定点 − 当前值（只取正数）。差多少就是多难受。"""
    sp = setpoints or cfg()["setpoints"]
    return {
        "fatigue": max(0.0, float(sp["energy"]) - float(values.get("energy", 0))),
        "loneliness": max(0.0, float(sp["social_connection"]) - float(values.get("social_connection", 0))),
        "boredom": max(0.0, float(sp["cognitive_novelty"]) - float(values.get("cognitive_novelty", 0))),
    }


def dominant_of(motives: dict) -> tuple[str, float]:
    """全局工作区仲裁：谁最强谁上台；都不够强就算平静。"""
    if not motives:
        return "", 0.0
    name = max(motives, key=lambda k: motives[k])
    strength = float(motives[name])
    if strength < MOTIVE_FLOOR:
        return "", 0.0
    return name, strength


# ── 心跳 ────────────────────────────────────────────────────────────────────
def tick(now: datetime | None = None, *, apply_coloring: bool | None = None) -> dict:
    """每 60 秒一次：变量演进 → 动机 → 仲裁 → 给情绪上色。

    纯函数式演进（只吃 elapsed 和 last_activity_at），所以补跑 12 小时也不会算错。
    """
    now = now or _now()
    c = cfg()
    data = load()
    last = _parse(data.get("updated_at")) or now
    minutes = max(0.0, (now - last).total_seconds() / 60.0)
    minutes = min(minutes, 24 * 60)          # 睡一天最多回血到设定点，别越界
    active_at = _parse(data.get("last_activity_at")) or last
    idle = (now - active_at).total_seconds() / 60.0 >= float(IDLE_MINUTES)

    values = dict(data["values"])
    energy_rate = float(c["energy_gain_idle"]) if idle else -float(c["energy_cost_active"])
    values["energy"] = _clamp01(values["energy"] + energy_rate * minutes)
    values["social_connection"] = _clamp01(values["social_connection"] - float(c["social_decay"]) * minutes)
    values["cognitive_novelty"] = _clamp01(values["cognitive_novelty"] - float(c["novelty_decay"]) * minutes)

    motives = motives_of(values)
    dominant, strength = dominant_of(motives)
    data.update({"values": values, "motives": motives, "dominant": dominant,
                 "strength": round(strength, 4), "updated_at": now.isoformat(timespec="seconds"),
                 "tick_count": int(data.get("tick_count") or 0) + 1})
    save(data)

    do_coloring = bool(c.get("coloring", True)) if apply_coloring is None else bool(apply_coloring)
    if do_coloring and dominant:
        color(dominant, strength)
    return {"values": values, "motives": {k: round(v, 3) for k, v in motives.items()},
            "dominant": dominant, "strength": round(strength, 3), "idle": idle,
            "minutes": round(minutes, 1)}


def note(kind: str, now: datetime | None = None) -> dict:
    """记一次「事件」，直接改变需求变量（不跑整轮 tick）。

    user_message  收到消息：社交连接回升，能量略降
    learning      学完一批：新颖性回升，能量消耗
    video         刷完一阵视频：新颖性回升（比学习省力）
    refusal       拒绝/生气：能量额外消耗，社交连接小降
    """
    now = now or _now()
    c = cfg()
    data = load()
    values = dict(data["values"])
    kind = str(kind or "").strip()
    if kind == "user_message":
        values["social_connection"] = _clamp01(values["social_connection"] + float(c["social_gain_message"]))
        values["energy"] = _clamp01(values["energy"] - float(c["energy_cost_active"]) * 1.0)
        data["last_activity_at"] = now.isoformat(timespec="seconds")
    elif kind in ("learning", "video"):
        gain = float(c["novelty_gain_learn"]) * (1.0 if kind == "learning" else 0.8)
        values["cognitive_novelty"] = _clamp01(values["cognitive_novelty"] + gain)
        values["energy"] = _clamp01(values["energy"] - float(c["energy_cost_learn"]))
        data["last_activity_at"] = now.isoformat(timespec="seconds")
    elif kind == "refusal":
        values["energy"] = _clamp01(values["energy"] - float(c["energy_cost_refusal"]))
        values["social_connection"] = _clamp01(values["social_connection"] - 0.02)
    else:
        return {"ok": False, "error": f"未知事件 {kind}"}

    data["values"] = values
    data["motives"] = motives_of(values)
    data["dominant"], data["strength"] = dominant_of(data["motives"])
    data["updated_at"] = now.isoformat(timespec="seconds")
    save(data)
    return {"ok": True, "kind": kind, "values": values, "dominant": data["dominant"]}


# ── 情感着色 ────────────────────────────────────────────────────────────────
# 键必须是 EmotionVector 里真实存在的维度（中文名），否则 vector.apply 会忽略。
def _coloring_delta(dominant: str, strength: float) -> dict:
    s = max(0.0, min(1.0, float(strength)))
    if dominant == "loneliness":
        return {"难过": 0.05 * s + 0.01, "幸福": -0.02 * s}
    if dominant == "fatigue":
        return {"愉悦": -0.03 * s, "害羞": -0.02 * s}
    if dominant == "boredom":
        return {"愉悦": -0.015 * s}
    return {}


def color(dominant: str, strength: float) -> dict:
    """把主导动机刷到情绪向量上。失败绝不影响 tick（情绪坏了也不能停心跳）。"""
    delta = _coloring_delta(dominant, strength)
    if not delta:
        return {}
    try:
        from .emotion import vector

        return vector.apply(delta, weight=1.0) or {}
    except Exception:
        return {}


# ── 给别的模块用的接口 ──────────────────────────────────────────────────────
def fatigue_fuse(threshold: float = 0.2) -> tuple[bool, str]:
    """v32 熔断：能量低于阈值时，禁止自主好奇心。返回 (是否熔断, 原因)。"""
    energy = float(load()["values"].get("energy", 1.0))
    if energy < float(threshold):
        return True, f"太困了（能量 {energy:.2f} < {threshold:.2f}），这一轮不学了"
    return False, ""


def boredom_signal() -> tuple[float, str]:
    """v32 无聊信号：返回 (强度, 主导)。只有主导是 boredom 时才 >0。"""
    data = load()
    if data.get("dominant") != "boredom":
        return 0.0, ""
    return float(data.get("strength") or 0.0), "boredom"


def social_bias() -> float:
    """v32 孤独加权：0~1，给好奇心/主动搭话用的倍率偏置。"""
    data = load()
    if data.get("dominant") != "loneliness":
        return 0.0
    return float(data.get("strength") or 0.0)


def expressiveness() -> float:
    """表达欲 0~1：疲劳越高越低（语言层/提示词层可以直接用）。"""
    values = load()["values"]
    energy = float(values.get("energy", 0.8))
    return _clamp01(0.4 + 0.6 * energy)


def prompt_block() -> str:
    """给提示词的两三句：她现在的「身体感觉」。没有主导动机就返回空串。"""
    data = load()
    dominant = str(data.get("dominant") or "")
    if not dominant:
        return ""
    values = data.get("values") or {}
    strength = float(data.get("strength") or 0)
    lines = [
        f"【身体感觉】能量 {float(values.get('energy', 0)) * 100:.0f}%、"
        f"社交 {float(values.get('social_connection', 0)) * 100:.0f}%、"
        f"新鲜感 {float(values.get('cognitive_novelty', 0)) * 100:.0f}%",
    ]
    if dominant == "loneliness":
        lines.append(f"主导是「{LABEL['loneliness']}」（{strength:.2f}）：心里空，想找人说话；"
                     "如果老大很久没来，可以主动找他，别硬装没事。")
    elif dominant == "fatigue":
        lines.append(f"主导是「{LABEL['fatigue']}」（{strength:.2f}）：犯困、脑子转不动，"
                     "回短句、别硬撑长篇；不想学东西就直接说困。")
    elif dominant == "boredom":
        lines.append(f"主导是「{LABEL['boredom']}」（{strength:.2f}）：闲得发慌，"
                     "想学点新东西/翻翻热梗，可以从自己感兴趣的地方起个头。")
    return "\n".join(lines) + "\n\n"


def status() -> dict:
    data = load()
    values = data.get("values") or {}
    return {
        "values": {k: round(float(v), 3) for k, v in values.items()},
        "setpoints": {k: round(float(v), 3) for k, v in cfg()["setpoints"].items()},
        "motives": {k: round(float(v), 3) for k, v in (data.get("motives") or {}).items()},
        "dominant": data.get("dominant") or "",
        "strength": round(float(data.get("strength") or 0), 3),
        "label": LABEL.get(str(data.get("dominant") or ""), "平静"),
        "expressiveness": round(expressiveness(), 3),
        "last_activity_at": data.get("last_activity_at") or "",
        "updated_at": data.get("updated_at") or "",
        "ticks": int(data.get("tick_count") or 0),
        "fuse": fatigue_fuse()[0],
    }
