# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""大肥鱼 · 领域实体。

规格要求「必须实现为带显式字段的实体，不得只用纯文本」。这里就是那层：
所有记忆 / 思维链 / 学习意图 / 情绪 / 奖罚 / 碎碎念 / 注意力 / 审计，
一律是带类型注解的 dataclass，落库时转成行，读库时还原。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ── 基础工具 ────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


_JSON_FIELDS = {"concepts", "refs", "context", "delta", "slots", "detail", "stats", "ext", "memory_hits", "values", "baseline"}


def _row(obj: Any) -> dict:
    row: dict[str, Any] = {}
    for key, value in asdict(obj).items():
        if key in _JSON_FIELDS:
            row[key] = dumps(value)
        elif isinstance(value, bool):
            row[key] = int(value)
        elif isinstance(value, Enum):
            row[key] = value.value
        else:
            row[key] = value
    return row


# ── 枚举与常量 ──────────────────────────────────────────────────────────────
class Layer(str, Enum):
    INSTANT = "instant"
    SHORT = "short"
    LONG1 = "long1"
    LONG2 = "long2"
    LANGUAGE = "language"
    EMOTION = "emotion"


class ThoughtType(str, Enum):
    START = "start"
    STEP = "step"
    BRANCH = "branch"
    DECISION = "decision"
    CONCLUSION = "conclusion"
    OPEN_QUESTION = "open_question"
    REFLECTION = "reflection"


class Status(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class GapType(str, Enum):
    ATTRIBUTE_MISSING = "attribute_missing"
    RELATION_MISSING = "relation_missing"
    PREDICTION_FAILURE = "prediction_failure"
    SKILL_FAILURE = "skill_failure"
    CONTRADICTION = "contradiction"


class IntentStatus(str, Enum):
    QUEUED = "queued"
    LEARNING = "learning"
    DONE = "done"
    ABANDONED = "abandoned"


class CandidateKind(str, Enum):
    DIALOGUE = "dialogue"
    TASK = "task"
    LEARN = "learn"
    CURIOSITY = "curiosity"
    OPEN_THOUGHT = "open_thought"
    CHITCHAT = "chitchat"
    CONSOLIDATE = "consolidate"


INTENT_SOURCES = ("dialog", "autonomous", "idle", "scheduled")
EMOTION_TAGS = ("开心", "难过", "生气", "创伤", "平静", "害羞")

# 2 号长期记忆的分类，与桥接 dafeiyu-memory.js 的 EMOTION_CATEGORIES 保持一致
LT2_CATEGORIES = ("开心", "难过", "生气", "创伤", "平静", "害羞")


# ── 记忆条目 ────────────────────────────────────────────────────────────────
@dataclass
class MemoryItem:
    id: str = field(default_factory=new_id)
    layer: str = Layer.INSTANT.value
    subtype: str = "event"
    content: str = ""
    summary: str = ""
    skeleton: str = ""
    concepts: list[str] = field(default_factory=list)
    emotion_tag: str = "平静"
    salience: float = 0.5
    strength: float = 1.0
    level: int = 0
    status: str = Status.OPEN.value
    refs: list[str] = field(default_factory=list)
    t_created: str = field(default_factory=now_iso)
    last_access: str = ""
    access_count: int = 0
    tombstone: int = 0
    origin: str = ""
    cluster: str = ""
    ext: dict = field(default_factory=dict)
    confidence: float = 1.0
    valid_from: str = ""
    valid_until: str = ""
    supersedes: str = ""
    superseded_by: str = ""
    emotional_valence: float = 0.0
    memory_type: str = "legacy_record"
    arousal: float = 0.0
    evidence_weight: float = 1.0

    @property
    def text(self) -> str:
        return self.content or self.summary or self.skeleton

    @property
    def alive(self) -> bool:
        return not self.tombstone and self.level < 3

    def to_row(self) -> dict:
        return _row(self)

    @classmethod
    def from_row(cls, r: Any) -> "MemoryItem":
        d = dict(r)
        for k in ("concepts", "refs", "ext"):
            d[k] = loads(d.get(k), [] if k != "ext" else {})
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# ── 思维链节点 ──────────────────────────────────────────────────────────────
@dataclass
class ThoughtNode:
    id: str = field(default_factory=new_id)
    chain_id: str = field(default_factory=new_id)
    type: str = ThoughtType.STEP.value
    content: str = ""
    summary: str = ""
    skeleton: str = ""
    concepts: list[str] = field(default_factory=list)
    task_ref: str = ""
    salience: float = 0.5
    confidence: float = 0.5
    status: str = Status.OPEN.value
    refs: list[str] = field(default_factory=list)
    strength: float = 1.0
    level: int = 0
    t_created: str = field(default_factory=now_iso)
    last_access: str = ""
    access_count: int = 0
    tombstone: int = 0

    def to_row(self) -> dict:
        return _row(self)

    @classmethod
    def from_row(cls, r: Any) -> "ThoughtNode":
        d = dict(r)
        for k in ("concepts", "refs"):
            d[k] = loads(d.get(k), [])
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# ── 学习意图 ────────────────────────────────────────────────────────────────
@dataclass
class LearningIntent:
    id: str = field(default_factory=new_id)
    target: str = ""
    gap_type: str = GapType.ATTRIBUTE_MISSING.value
    curiosity_score: float = 0.0
    expected_learnability: float = 0.0
    source: str = "dialog"
    context: dict = field(default_factory=dict)
    proposed_query: str = ""
    status: str = IntentStatus.QUEUED.value
    result_ref: str = ""
    attempts: int = 0
    last_error: str = ""
    t_created: str = field(default_factory=now_iso)

    def to_row(self) -> dict:
        return _row(self)

    @classmethod
    def from_row(cls, r: Any) -> "LearningIntent":
        d = dict(r)
        d["context"] = loads(d.get("context"), {})
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# ── 奖罚事件 ────────────────────────────────────────────────────────────────
@dataclass
class RewardEvent:
    id: str = field(default_factory=new_id)
    type: str = "reward"
    trigger: str = "praise"
    source_text: str = ""
    delta: dict = field(default_factory=dict)
    rpe_weight: float = 1.0
    t: str = field(default_factory=now_iso)

    def to_row(self) -> dict:
        return _row(self)

    @classmethod
    def from_row(cls, r: Any) -> "RewardEvent":
        d = dict(r)
        d["delta"] = loads(d.get("delta"), {})
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


# ── 情绪向量 ────────────────────────────────────────────────────────────────
DIMS = ("joy", "happiness", "sadness", "inferiority", "shyness")
CN = {"joy": "愉悦", "happiness": "幸福", "sadness": "难过", "inferiority": "自卑", "shyness": "害羞"}
GROUP = {"joy": "positive", "happiness": "positive", "sadness": "negative",
         "inferiority": "negative", "shyness": "other"}
# 情绪标签 → 维度（2 号长期记忆分类用）
TAG_DIM = {"开心": "joy", "难过": "sadness", "害羞": "shyness",
           "生气": "sadness", "创伤": "inferiority", "平静": ""}


def clamp01(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(v)))


def dominant_emotion(values: dict) -> str:
    """The strongest current dimension; baseline distance is a separate signal."""
    return max(DIMS, key=lambda dim: float(values.get(dim, 0.0)))


@dataclass
class EmotionVector:
    values: dict = field(default_factory=dict)
    baseline: dict = field(default_factory=dict)
    dominant: str = "joy"
    updated_at: str = field(default_factory=now_iso)

    # —— 构造 ——
    @classmethod
    def from_config(cls, cfg: dict) -> "EmotionVector":
        initial = {k: float(cfg.get("initial", {}).get(k, 0)) for k in DIMS}
        baseline = {k: float(cfg.get("baseline", {}).get(k, 5)) for k in DIMS}
        vec = cls(values=initial, baseline=baseline)
        vec.recompute_dominant()
        return vec

    @classmethod
    def from_json(cls, data: dict) -> "EmotionVector":
        values = {}
        for group in ("positive", "negative", "other"):
            for k, v in (data.get(group) or {}).items():
                if k in DIMS:
                    values[k] = float(v)
        for k in DIMS:
            values.setdefault(k, 0.0)
        baseline = {k: float((data.get("baseline") or {}).get(k, values.get(k, 0))) for k in DIMS}
        vec = cls(values=values, baseline=baseline,
                  updated_at=str(data.get("updated_at") or now_iso()))
        vec.recompute_dominant()
        return vec

    def to_json(self) -> dict:
        return {
            "positive": {k: round(self.values.get(k, 0.0), 1) for k in DIMS if GROUP[k] == "positive"},
            "negative": {k: round(self.values.get(k, 0.0), 1) for k in DIMS if GROUP[k] == "negative"},
            "other": {k: round(self.values.get(k, 0.0), 1) for k in DIMS if GROUP[k] == "other"},
            "dominant": self.dominant,
            "baseline": {k: round(self.baseline.get(k, 0.0), 1) for k in DIMS},
            "updated_at": self.updated_at,
        }

    # —— 读写 ——
    def get(self, dim: str) -> float:
        return float(self.values.get(dim, 0.0))

    def set(self, dim: str, value: float) -> None:
        self.values[dim] = round(clamp01(value), 2)

    def apply(self, delta: dict, weight: float = 1.0) -> dict:
        """按 delta × rpe_weight 修改情绪，返回实际生效的增量。"""
        applied = {}
        for dim, dv in (delta or {}).items():
            if dim not in DIMS:
                continue
            step = float(dv) * float(weight)
            before = self.get(dim)
            self.set(dim, before + step)
            applied[dim] = round(self.values[dim] - before, 2)
        self.touch()
        return applied

    def touch(self) -> None:
        self.updated_at = now_iso()
        self.recompute_dominant()

    def recompute_dominant(self) -> str:
        self.dominant = dominant_emotion(self.values)
        return self.dominant

    # —— 派生量 ——
    def positive(self) -> float:
        vals = [self.get(d) for d in DIMS if GROUP[d] == "positive"]
        return sum(vals) / len(vals) if vals else 0.0

    def negative(self) -> float:
        vals = [self.get(d) for d in DIMS if GROUP[d] == "negative"]
        return sum(vals) / len(vals) if vals else 0.0

    def shy(self) -> float:
        return self.get("shyness")

    def dominant_cn(self) -> str:
        return CN.get(self.dominant, self.dominant)

    def mood_factor(self, pos_gain: float = 0.004, neg_gain: float = 0.003) -> float:
        """情绪 → 好奇心强度（正面升、负面降）。"""
        raw = 0.6 + pos_gain * (self.get("joy") + self.get("happiness")) - neg_gain * (self.get("sadness") + self.get("inferiority"))
        return round(max(0.2, min(1.2, raw)), 3)


# ── 碎碎念 / 好奇心 / 冷却 ─────────────────────────────────────────────────
@dataclass
class ChitchatState:
    last_chitchat_at: str = ""
    consecutive_no_reply: int = 0
    next_due_at: str = ""
    muted_until: str | None = None
    enabled: bool = True
    fatigue_level: int = 0
    total_sent: int = 0
    last_ignored_at: str = ""

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ChitchatState":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass
class CuriosityWeights:
    domain_weights: dict = field(default_factory=lambda: {"语言": 1.0, "情绪": 1.0, "害羞": 1.0})
    fatigue: dict = field(default_factory=dict)
    last_explored: dict = field(default_factory=dict)
    budget: dict = field(default_factory=lambda: {"hourly_web": 6, "daily_web": 30})
    feedback: dict = field(default_factory=dict)
    web_log: list = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "CuriosityWeights":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def weight(self, domain: str) -> float:
        return float(self.domain_weights.get(domain, 1.0))

    def tired(self, domain: str) -> float:
        return float(self.fatigue.get(domain, 0.0))


@dataclass
class CooldownState:
    language: dict = field(default_factory=lambda: {
        "last_learned_at": "", "cooldown_until": "", "memory_chars": 0, "awaiting_user": False})
    emotion: dict = field(default_factory=lambda: {
        "last_learned_at": "", "cooldown_until": "", "memory_chars": 0, "awaiting_user": False})

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "CooldownState":
        state = cls()
        for key in ("language", "emotion"):
            if isinstance(data.get(key), dict):
                state.__dict__[key].update(data[key])
        return state


# ── 补强机制实体 ────────────────────────────────────────────────────────────
@dataclass
class WorkingSlot:
    idx: int = 0
    kind: str = "memory"      # memory|thought|percept|task
    ref_id: str = ""
    weight: float = 1.0
    bound_at: str = field(default_factory=now_iso)
    pinned: bool = False
    inhibit: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "WorkingSlot":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass
class KnowledgeItem:
    id: str = field(default_factory=new_id)
    name: str = ""
    domain: str = ""
    concepts: list[str] = field(default_factory=list)
    content: str = ""
    slots: dict = field(default_factory=dict)   # {"应有": [...], "已填": {...}}
    filled_ratio: float = 0.0
    uncertainty: float = 0.5
    confidence: float = 0.5
    source: str = ""
    intent_id: str = ""
    learned_at: str = field(default_factory=now_iso)

    def to_row(self) -> dict:
        return _row(self)

    @classmethod
    def from_row(cls, r: Any) -> "KnowledgeItem":
        d = dict(r)
        for k in ("concepts", "slots"):
            d[k] = loads(d.get(k), [] if k == "concepts" else {})
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def recompute(self) -> float:
        need = list((self.slots or {}).get("应有") or [])
        filled = dict((self.slots or {}).get("已填") or {})
        if not need:
            self.filled_ratio = 1.0 if self.content.strip() else 0.0
        else:
            hit = sum(1 for k in need if str(filled.get(k, "")).strip())
            self.filled_ratio = round(hit / len(need), 3)
        self.uncertainty = round(1.0 - self.filled_ratio, 3)
        return self.filled_ratio


@dataclass
class Evidence:
    id: str = field(default_factory=new_id)
    kind: str = "failure"     # failure|refusal|simulated|contradiction
    target: str = ""
    detail: str = ""
    uncertainty: float = 0.5
    t: str = field(default_factory=now_iso)

    def to_row(self) -> dict:
        return _row(self)

    @classmethod
    def from_row(cls, r: Any) -> "Evidence":
        d = dict(r)
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class AuditEvent:
    id: str = field(default_factory=new_id)
    t: str = field(default_factory=now_iso)
    entity_kind: str = "memory"
    entity_id: str = ""
    action: str = "created"
    detail: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return _row(self)


@dataclass
class ArbiterCandidate:
    kind: str
    ref_id: str = ""
    value: float = 0.5
    urgency: float = 0.5
    expectancy: float = 0.5
    interrupt_cost: float = 0.0
    fatigue: float = 0.0
    note: str = ""
    payload: dict = field(default_factory=dict)

    def score(self) -> float:
        return (self.value * self.urgency * self.expectancy) - self.interrupt_cost

    def to_json(self) -> dict:
        return {k: v for k, v in asdict(self).items()}


@dataclass
class ResponsePolicy:
    reply: bool = True
    delay_ms: int = 0
    max_chars: int = 200
    tone: str = "normal"       # lively|normal|low|shy|flustered
    chitchat: bool = True
    reason: str = ""


@dataclass
class TickResult:
    at: str = field(default_factory=now_iso)
    activity: str = "idle"
    emotion: dict = field(default_factory=dict)
    new_intents: int = 0
    learned: int = 0
    chitchat: str = ""
    decayed: int = 0
    distilled: int = 0
    notes: list = field(default_factory=list)
