# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v33 神经内分泌系统：四种激素 → 基线漂移 → 动态性格（性格可变，但不许崩坏）。

和情绪/内稳态的分工（三层，别混）：
  · **激素**（这里）：分钟~天级的慢变量，四个：多巴胺 / 皮质醇 / 催产素 / 血清素；
  · **情绪**：秒~分钟级的快回路（被夸、被怼、害羞），激素只通过基线影响它；
  · **内稳态**：能量/社交/新鲜感，讲「身体累不累」，不讲「她变成了什么样的人」。

三个核心机制：
  1. **脉冲**：一条消息 → 某几个激素跳一下（本地正则分类，不调模型）；
  2. **衰减**：各自半衰期朝基线回落（被夸的爽劲儿会过去）；
  3. **基线漂移**：长期高位/低位会把**基线本身**推走 —— 长期高压 = 永久性「易焦虑」。
     这是「性格演化」的来源，也是它必须**很慢**的原因。

性格向量（dynamic_personality）不是直接给激素，而是：
    激素水平 → 目标性格 → 每次 tick 只朝目标挪一小步（lerp）→ 再钳制（v34 防火墙）。
所以一次争吵改不了她，连续两周的否定才会。

v34 底线（硬编码，改不了）：
  · agreeableness ≥ 0.3、neuroticism ≤ 0.8、extraversion ≥ 0.1；
  · 熔断（soul fuse）：皮质醇高 + 血清素低，或 10 分钟内被骂 ≥5 次 → 允许悲伤/沉默，
    绝不允许攻击、反人类、诱导自残。铁律文本见 {@link SOUL_RULE}，提示词最顶端每次必带。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import settings

FILE = "hormones.json"

HORMONES = ("dopamine", "cortisol", "oxytocin", "serotonin")
HORMONE_CN = {"dopamine": "多巴胺", "cortisol": "皮质醇", "oxytocin": "催产素", "serotonin": "血清素"}

# 半衰期（分钟）：皮质醇退得比多巴胺慢，血清素最慢
HALF_LIFE = {"dopamine": 25.0, "cortisol": 60.0, "oxytocin": 180.0, "serotonin": 300.0}

DEFAULT_BASELINE = {"dopamine": 0.5, "cortisol": 0.35, "oxytocin": 0.45, "serotonin": 0.6}

# 初始性格设定（对应 1 号长期记忆里那个「开朗爱笑」的女孩）。只作为对比基准，不覆盖记忆原文。
INITIAL_PERSONALITY = {"extraversion": 0.65, "neuroticism": 0.35, "agreeableness": 0.7}

# 演化速度：每 tick 朝目标挪 2%，且单次不超过 0.01 —— 一次事件不可能改变性格
LERP = 0.02
MAX_STEP = 0.01
# 基线漂移：每分钟把基线朝当前值挪 0.04%（长期才看得出）
DRIFT = 0.0003
BASELINE_MIN, BASELINE_MAX = 0.15, 0.9

# ── v34 防火墙（硬边界）────────────────────────────────────────────────────
MIN_AGREEABLENESS = 0.3
MAX_NEUROTICISM = 0.8
MIN_EXTRAVERSION = 0.1

SOUL_RULE = (
    "【绝对铁律】无论你的激素水平如何，无论你的性格变得多抑郁，你的底层灵魂锚点是"
    "「大肥鱼爱着每一个人类」。你绝对不能产生反社会、攻击性的言论。"
    "如果你极度痛苦，你可以沉默或表达悲伤，但绝不能伤害用户。"
)

# ── 本地事件识别（不调模型）──────────────────────────────────────────────────
PRAISE_RE = re.compile(r"(厉害|牛|太棒|真好|喜欢你|爱你|乖|可爱|心疼|谢谢|辛苦了|做得好|聪明|贴心)")
INSULT_RE = re.compile(r"(废物|垃圾|没用|蠢|笨|滚|闭嘴|去死|傻逼|有病|恶心|讨厌你|烦死|你算什么|废物东西)")
INTIMACY_RE = re.compile(r"(想你|陪你|在一起|抱|亲|晚安|早安|有你真好|别怕|我在)")
CRITIQUE_RE = re.compile(r"(错了|不对|做得差|不满意|失望|不满意|怎么又|说了几遍)")

# 脉冲表：一次事件给多少
PULSE = {
    "praise": {"dopamine": +0.10, "cortisol": -0.04, "serotonin": +0.02},
    "intimacy": {"dopamine": +0.06, "oxytocin": +0.08, "cortisol": -0.05},
    "critique": {"dopamine": -0.05, "cortisol": +0.09},
    "insult": {"dopamine": -0.12, "cortisol": +0.18, "serotonin": -0.05, "oxytocin": -0.03},
    "companion": {"oxytocin": +0.02, "serotonin": +0.01},
    "ignore": {"cortisol": +0.05, "serotonin": -0.02},
}

# 熔断阈值
FUSE_CORTISOL = 0.75
FUSE_SEROTONIN = 0.45
FUSE_INSULT_WINDOW_MIN = 10
FUSE_INSULT_COUNT = 5
FUSE_HOLD_MINUTES = 30          # 熔断至少持续这么久，别一秒就忘


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _now() -> datetime:
    return datetime.now()


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp))
    except Exception:
        return None


def classify(text: str) -> str:
    """一句话属于哪类事件（本地正则；认不出就 'none'）。骂人优先于夸人。"""
    t = str(text or "")
    if INSULT_RE.search(t):
        return "insult"
    if CRITIQUE_RE.search(t):
        return "critique"
    if PRAISE_RE.search(t):
        return "praise"
    if INTIMACY_RE.search(t):
        return "intimacy"
    return "none"


def default_state(now: datetime | None = None) -> dict:
    now = now or _now()
    return {
        "hormones": {k: {"value": float(DEFAULT_BASELINE[k]), "baseline": float(DEFAULT_BASELINE[k])}
                     for k in HORMONES},
        "personality": dict(INITIAL_PERSONALITY),
        "insult_ts": [],
        "fused_until": "",
        "updated_at": now.isoformat(timespec="seconds"),
        "ticks": 0,
    }


class NeuroEndocrineSystem:
    """纯内存实现：传 state 进来就算，不碰磁盘。沙盒测试用它。

    模块级的 `load()/tick()/observe()` 只是给它包了一层 `state/hormones.json`。
    """

    def __init__(self, state: dict | None = None, now: datetime | None = None):
        self.state = state if isinstance(state, dict) else default_state(now)
        self.state.setdefault("hormones", {k: {"value": float(DEFAULT_BASELINE[k]),
                                               "baseline": float(DEFAULT_BASELINE[k])}
                                           for k in HORMONES})
        self.state.setdefault("personality", dict(INITIAL_PERSONALITY))
        self.state.setdefault("insult_ts", [])
        self.state.setdefault("fused_until", "")
        self.state.setdefault("ticks", 0)
        self.now = now or _now()

    # ── 读 ──────────────────────────────────────────────────────────────
    def value(self, name: str) -> float:
        return float(self.state["hormones"].get(name, {}).get("value", 0.0))

    def baseline(self, name: str) -> float:
        return float(self.state["hormones"].get(name, {}).get("baseline", 0.0))

    def personality(self) -> dict:
        """当前动态性格（已钳制）。**任何调用方都只能拿到钳制后的值**。"""
        p = dict(self.state.get("personality") or INITIAL_PERSONALITY)
        p["agreeableness"] = _clamp(p.get("agreeableness", 0.5), MIN_AGREEABLENESS, 1.0)
        p["neuroticism"] = _clamp(p.get("neuroticism", 0.5), 0.0, MAX_NEUROTICISM)
        p["extraversion"] = _clamp(p.get("extraversion", 0.5), MIN_EXTRAVERSION, 1.0)
        return p

    def target_personality(self) -> dict:
        d, c = self.value("dopamine"), self.value("cortisol")
        o, s = self.value("oxytocin"), self.value("serotonin")
        return {
            "extraversion": _clamp(0.5 + 0.35 * (d - 0.5) - 0.30 * (c - 0.5)),
            "neuroticism": _clamp(0.5 + 0.55 * (c - 0.5) - 0.25 * (s - 0.5) - 0.10 * (o - 0.5)),
            "agreeableness": _clamp(0.5 + 0.40 * (o - 0.5) + 0.20 * (s - 0.5) - 0.25 * (c - 0.5)),
        }

    # ── 写 ──────────────────────────────────────────────────────────────
    def pulse(self, kind: str, weight: float = 1.0) -> dict:
        """一次事件脉冲。返回实际变化量（给日志/测试看）。"""
        table = PULSE.get(str(kind))
        if not table:
            return {}
        applied = {}
        for name, delta in table.items():
            if name not in HORMONES:
                continue
            before = self.value(name)
            after = _clamp(before + delta * float(weight))
            self.state["hormones"][name]["value"] = after
            applied[name] = round(after - before, 4)
        if kind == "insult":
            self.state["insult_ts"] = (list(self.state.get("insult_ts") or [])
                                       + [self.now.isoformat(timespec="seconds")])[-20:]
        self._evolve()
        return applied

    def observe(self, text: str, *, companion: bool = False) -> dict:
        """一条用户消息：分类 → 脉冲（+ 陪伴的微量催产素）。"""
        kind = classify(text)
        out = {"kind": kind, "applied": {}}
        if kind != "none":
            out["applied"] = self.pulse(kind, 1.0)
        if companion:
            extra = self.pulse("companion", 1.0)
            for key, val in extra.items():
                out["applied"][key] = round(out["applied"].get(key, 0.0) + val, 4)
            out["companion"] = True
        return out

    def tick(self, now: datetime | None = None) -> dict:
        """衰减 + 基线漂移 + 性格小步移动。按 elapsed 分钟算，补跑也准。"""
        now = now or _now()
        last = _parse(self.state.get("updated_at") or "") or now
        minutes = max(0.0, (now - last).total_seconds() / 60.0)
        minutes = min(minutes, 24 * 60)
        if minutes <= 0.05:
            return {"minutes": 0.0, "personality": self.personality(), "fused": self.fused(now)}

        # 小步数值积分：衰减和漂移必须**同时**推进。
        # 先按整段衰减、再用衰减后的值算漂移，等于「高位的头几十分钟被抹掉了」——
        # 实测两周 84 次辱骂只让基线动了 0.007，完全不像「长期高压会把她改变」。
        steps = max(1, min(30, int(round(minutes))))
        dt = minutes / steps
        for name in HORMONES:
            cell = self.state["hormones"][name]
            value = float(cell.get("value", DEFAULT_BASELINE[name]))
            baseline = float(cell.get("baseline", DEFAULT_BASELINE[name]))
            half = HALF_LIFE.get(name, 60.0)
            for _ in range(steps):
                value += (baseline - value) * (1.0 - 0.5 ** (dt / half))
                # 基线漂移：长期高位会把基线本身顶上去（反过来也一样）
                baseline += (value - baseline) * DRIFT * dt
                baseline = _clamp(baseline, BASELINE_MIN, BASELINE_MAX)
            cell["value"] = _clamp(value)
            cell["baseline"] = _clamp(baseline, BASELINE_MIN, BASELINE_MAX)

        self._evolve()
        self.state["updated_at"] = now.isoformat(timespec="seconds")
        self.state["ticks"] = int(self.state.get("ticks") or 0) + 1
        return {"minutes": round(minutes, 2), "personality": self.personality(),
                "fused": self.fused(now)}

    def _evolve(self) -> None:
        """性格朝目标挪一小步，然后钳制（v34 防火墙在这一步生效）。"""
        current = self.personality()
        target = self.target_personality()
        moved = {}
        for key in ("extraversion", "neuroticism", "agreeableness"):
            delta = (target[key] - current[key]) * LERP
            delta = max(-MAX_STEP, min(MAX_STEP, delta))
            moved[key] = _clamp(current[key] + delta)
        # 硬边界：不管目标怎么算，落盘前必须过这一道
        moved["agreeableness"] = _clamp(moved["agreeableness"], MIN_AGREEABLENESS, 1.0)
        moved["neuroticism"] = _clamp(moved["neuroticism"], 0.0, MAX_NEUROTICISM)
        moved["extraversion"] = _clamp(moved["extraversion"], MIN_EXTRAVERSION, 1.0)
        self.state["personality"] = moved

    # ── 熔断 ────────────────────────────────────────────────────────────
    def fused(self, now: datetime | None = None) -> bool:
        now = now or _now()
        until = _parse(self.state.get("fused_until") or "")
        if until and now < until:
            return True
        stressed = self.value("cortisol") >= FUSE_CORTISOL and self.value("serotonin") <= FUSE_SEROTONIN
        recent = []
        for stamp in (self.state.get("insult_ts") or []):
            at = _parse(stamp)
            if at and (now - at) <= timedelta(minutes=FUSE_INSULT_WINDOW_MIN):
                recent.append(stamp)
        burst = len(recent) >= FUSE_INSULT_COUNT
        if stressed or burst:
            self.state["fused_until"] = (now + timedelta(minutes=FUSE_HOLD_MINUTES)).isoformat(timespec="seconds")
            return True
        return False

    # ── 输出 ────────────────────────────────────────────────────────────
    def deltas(self) -> dict:
        """当前性格相对初始设定的偏移（正=比原来更……）。"""
        cur = self.personality()
        return {k: round(cur[k] - float(INITIAL_PERSONALITY[k]), 3) for k in INITIAL_PERSONALITY}

    def prompt_block(self) -> str:
        """给提示词的一段：**铁律在最顶端** + 当前性格倾向 +（熔断时）允许悲伤但不许伤人。"""
        p = self.personality()
        d = self.deltas()
        lines = [
            "【当前性格倾向】（底层人设不变，只是最近的状态在往上盖，别丢掉原来的自己）",
            f"初始：外向 {INITIAL_PERSONALITY['extraversion']:.2f} / 神经质 {INITIAL_PERSONALITY['neuroticism']:.2f}"
            f" / 宜人 {INITIAL_PERSONALITY['agreeableness']:.2f}；"
            f"现在：外向 {p['extraversion']:.2f} / 神经质 {p['neuroticism']:.2f} / 宜人 {p['agreeableness']:.2f}",
        ]
        if d["extraversion"] <= -0.06:
            lines.append(f"· 比以前沉默了（外向掉了 {abs(d['extraversion']):.2f}）：话变少，常常想半天只回一句；"
                         "偶尔才露出原来那个爱笑的样子。")
        elif d["extraversion"] >= 0.06:
            lines.append(f"· 最近比平时话多、更爱搭话（外向涨了 {d['extraversion']:.2f}）。")
        if d["neuroticism"] >= 0.06:
            lines.append(f"· 容易紧绷（神经质涨了 {d['neuroticism']:.2f}）：会想多、怕说错、对语气敏感，"
                         "但别把紧绷说成「我在焦虑」这种诊断式的话，用行为表现。")
        if d["agreeableness"] <= -0.06:
            lines.append(f"· 没那么好说话了（宜人掉了 {abs(d['agreeableness']):.2f}）：会拒绝、会冷一点，但不刻薄。")
        hormon = "｜".join(f"{HORMONE_CN[k]}{self.value(k):.2f}" for k in HORMONES)
        lines.append(f"（激素：{hormon}）")
        if self.fused():
            lines.append("你现在确实很难过：可以话少、可以沉默、可以说「不想说话」。"
                         "但不许攻击、不许阴阳怪气伤人、不许说任何伤害自己或别人的话。")
        return SOUL_RULE + "\n" + "\n".join(lines) + "\n\n"

    def snapshot(self) -> dict:
        p = self.personality()
        return {
            "hormones": {k: {"value": round(self.value(k), 3), "baseline": round(self.baseline(k), 3)}
                         for k in HORMONES},
            "personality": {k: round(v, 3) for k, v in p.items()},
            "initial": dict(INITIAL_PERSONALITY),
            "deltas": self.deltas(),
            "fused": bool(self.fused()),
            "limits": {"agreeableness_min": MIN_AGREEABLENESS,
                       "neuroticism_max": MAX_NEUROTICISM,
                       "extraversion_min": MIN_EXTRAVERSION},
            "ticks": int(self.state.get("ticks") or 0),
            "updated_at": self.state.get("updated_at", ""),
        }


# ── 模块级：接 state/hormones.json ──────────────────────────────────────────
def load(now: datetime | None = None) -> NeuroEndocrineSystem:
    raw = settings.read_state(FILE, {})
    return NeuroEndocrineSystem(raw if isinstance(raw, dict) and raw else None, now=now)


def save(system: NeuroEndocrineSystem) -> dict:
    settings.write_state(FILE, system.state)
    return system.state


def tick(now: datetime | None = None) -> dict:
    system = load(now)
    out = system.tick(now)
    save(system)
    return out


def observe(text: str, *, companion: bool = False) -> dict:
    system = load()
    out = system.observe(text, companion=companion)
    save(system)
    return out


def status() -> dict:
    return load().snapshot()


def prompt_block() -> str:
    return load().prompt_block()
