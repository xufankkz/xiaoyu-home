"""Fictional character state and animation cues, evaluated entirely locally.

The numeric rules are bounded game-like presentation mechanics. They do not
model, measure, diagnose or validate real biological processes. Existing
homeostasis/hormones remain compatible inputs and keep their own settings.
There is one transparent output: the same state is shown to the user and model.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import hashlib
import math
import re
import threading

from . import settings

FILE = "affective.json"
_lock = threading.RLock()
DIMS = ("joy", "happiness", "sadness", "inferiority", "shyness")


def bound(v, low=0.0, high=1.0):
    return max(low, min(high, float(v)))


def parse(stamp: str, fallback: datetime) -> datetime:
    try:
        out = datetime.fromisoformat(str(stamp))
        return out.astimezone().replace(tzinfo=None) if out.tzinfo else out
    except (ValueError, TypeError):
        return fallback


def appraise(text: str) -> dict:
    text = str(text or "")
    # A quoted word-definition question is not an insult directed at her.
    if re.search(r"什么意思|是什么梗|这个词", text):
        text = re.sub(r'[“"「].*?[”"」]', '', text)
    if re.search(r"不是故意|不小心|误点|手滑|断网|网络.*卡|有事.*忙", text):
        return {"tag": "accidental", "agency": "circumstance", "valence": -.12, "arousal": .15, "responsibility": "uncertain"}
    if re.search(r"你.*(?:废物|垃圾|蠢|没用)|滚|闭嘴|讨厌你", text):
        return {"tag": "hostile", "agency": "peer", "valence": -.8, "arousal": .85, "responsibility": "local-rule-tag"}
    if re.search(r"太慢|错了|不对|不满意|没做好", text):
        return {"tag": "feedback", "agency": "uncertain", "valence": -.25, "arousal": .3, "responsibility": "uncertain"}
    shy = social_shyness(text)
    if shy or re.search(r"谢谢|辛苦了|喜欢你|爱你|很棒|真好|抱抱|我在", text):
        return {"tag": "supportive", "agency": "peer", "valence": .65, "arousal": .45,
                "responsibility": "local-rule-tag", "shyness": shy}
    return {"tag": "neutral", "agency": "uncertain", "valence": 0.0, "arousal": .05, "responsibility": "uncertain"}


def social_shyness(text: str) -> float:
    """Nonsexual, fictional reactions to direct compliments and friendly affection."""
    text = re.sub(r'[“"「].*?[”"」]', '', str(text or ""))
    if re.search(r"什么意思|是什么梗|这个词|不是夸你|没有夸你|不(?:可爱|漂亮|乖|聪明)|别害羞|不用害羞|不害羞", text):
        return 0.0
    praise = r"可爱|漂亮|真棒|好棒|很棒|真乖|好乖|厉害|优秀|贴心|聪明|温柔"
    if re.search(r"(?:你|小鱼|大肥鱼)[^。！？!?\n]{0,12}(?:" + praise + r")", text) or \
            re.search(r"^(?:真|好|超|太|很)(?:可爱|漂亮|棒|乖|厉害|贴心|聪明)", text):
        return .8
    if re.search(r"摸摸(?:你的)?头|抱抱(?:你|小鱼)|给你一个拥抱|(?:你|小鱼)[^。！？!?\n]{0,6}(?:脸红|害羞)", text):
        return .55
    return 0.0


@dataclass
class RuminationSeed:
    seed_id: str
    trigger_event: str
    query: str
    valence: float
    created_at: str
    decay_rate: float
    expires_at: str
    last_sample: str = ""


@dataclass
class CentralAffectiveState:
    created_at: str = ""
    updated_at: str = ""
    fast_emotion: dict = field(default_factory=lambda: {d: 0.0 for d in DIMS})
    slow_mood: float = 0.0
    social_strain: float = 0.0
    social_pain_threshold: float = .8
    attachment_anchor: float = 0.0
    attachment_by_scope: dict = field(default_factory=dict)
    energy_cycle: float = .5
    rest_anticipation: bool = False
    virtual_heart_rate: float = 70.0
    virtual_temperature: float = 0.0
    seeds: list = field(default_factory=list)
    activations: dict = field(default_factory=dict)
    seen_events: list = field(default_factory=list)
    seen_pulses: dict = field(default_factory=dict)
    shyness_reactions: dict = field(default_factory=dict)
    last_shyness_reaction: dict = field(default_factory=dict)
    last_appraisal: dict = field(default_factory=dict)
    last_scope: str = ""
    expression: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "CentralAffectiveState":
        return cls(**{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__})

    def observe(self, text: str, *, event_id: str, scope: str = "", memory_id: str = "",
                now: datetime | None = None, stress: float = .35, sensitivity: float = .35, energy: float = .8,
                resting: bool | None = None) -> dict:
        now = now or datetime.now()
        self.tick(now=now, stress=stress, energy=energy, resting=resting)
        if event_id in self.seen_events:
            return {"duplicate": True, "delta": {}}
        self.seen_events = (self.seen_events + [event_id])[-200:]
        event = appraise(text)
        value = event["valence"]
        self.last_appraisal = event
        self.last_scope = scope
        # Explicit scope prevents one contact's attachment from replacing another.
        if scope:
            old = self.attachment_by_scope.get(scope) or {"anchor": 0.0, "count": 0}
            age = max(0.0, (now - parse(self.created_at, now)).total_seconds() / 86400)
            rate = .1 if age < 7 else .015
            old["anchor"] = bound(old["anchor"] * (1-rate) + value*rate, -1, 1)
            old["count"] += 1
            self.attachment_by_scope[scope] = old
            self.attachment_by_scope = dict(list(self.attachment_by_scope.items())[-200:])
            self.attachment_anchor = old["anchor"]
        self.social_strain = bound(self.social_strain + max(0, -value)*.12 - max(0, value)*.06)
        self.social_pain_threshold = bound(.8 - .5*self.social_strain, .3, .8)
        gain = 1 + ((1-self.energy_cycle)*.3 + self.social_strain*.2 if value < 0 else self.energy_cycle*.2)
        target = {d: 0.0 for d in DIMS}
        if value > 0:
            target.update(joy=value, happiness=value*.6)
        elif value < 0:
            target.update(sadness=-value, inferiority=-value*(.4 if event["tag"] == "hostile" else .1))
        delta = {}
        for dim in DIMS:
            alpha = .2 if dim in {"sadness", "inferiority"} else .5
            previous = self.fast_emotion.get(dim, 0.0)
            self.fast_emotion[dim] = bound((1-alpha)*previous + alpha*target[dim])
            delta[dim] = round((self.fast_emotion[dim] - previous)*12*gain, 4)
        # Production context preparation does not call the legacy reward detector.
        # Use a bounded social pulse here; ordinary follow-up messages must not
        # immediately cancel it. Natural return still belongs to vector.tick().
        shy_signal = float(event.get("shyness", 0.0))
        delta["shyness"] = 0.0
        if shy_signal:
            signature = hashlib.sha256(re.sub(r"[\s，。！？!?~～]", "", text).encode()).hexdigest()[:24]
            previous = self.shyness_reactions.get(signature) or {"count": 0}
            count = int(previous.get("count", 0))
            delta["shyness"] = round(min(10.0, shy_signal*10)*max(.25, .65**count), 2)
            self.fast_emotion["shyness"] = bound(self.fast_emotion.get("shyness", 0)+shy_signal*.2)
            self.shyness_reactions[signature] = {"count": count+1,
                "expires_at": (now+timedelta(minutes=30)).isoformat(timespec="seconds")}
            self.shyness_reactions = dict(list(self.shyness_reactions.items())[-100:])
            self.last_shyness_reaction = {"t": now.isoformat(timespec="seconds"),
                                          "delta": delta["shyness"], "kind": "友好互动"}
        if event["arousal"] >= .4:
            self.slow_mood = bound(self.slow_mood + value*.035, -.6, .6)
        if value <= -.6:
            stamp = now.isoformat(timespec="seconds")
            self.seeds.append(asdict(RuminationSeed(
                seed_id=hashlib.sha256(event_id.encode()).hexdigest()[:16], trigger_event=memory_id,
                query=text[:160], valence=-value, created_at=stamp,
                decay_rate=.8 - .3*bound(sensitivity), expires_at=(now+timedelta(hours=2)).isoformat(timespec="seconds"))))
            self.seeds = self.seeds[-4:]
        self._cues(event["arousal"])
        self.updated_at = now.isoformat(timespec="seconds")
        return {"duplicate": False, "appraisal": event, "delta": delta}

    def _cues(self, arousal: float = 0.0):
        # Arbitrary animation scales, never medical measurements.
        self.virtual_heart_rate = round(65 + 45*bound(arousal) + 5*self.energy_cycle, 1)
        self.virtual_temperature = round(bound(arousal)*.5, 3)

    def tick(self, *, now: datetime | None = None, stress: float = .35, energy: float = .8,
             resting: bool | None = None, sample=None) -> dict:
        now = now or datetime.now()
        if not self.created_at:
            self.created_at = now.isoformat(timespec="seconds")
        minutes = max(0.0, (now - parse(self.updated_at, now)).total_seconds()/60)
        phase = .5 + .5*math.sin(2*math.pi*(now.timestamp()/60)/90)
        self.energy_cycle = bound(.6*energy + .4*phase)
        self.rest_anticipation = (now.hour >= 23 or now.hour < 6) if resting is None else resting
        for dim in DIMS:
            half_life = (18 + 12*bound(stress)) if dim in {"sadness", "inferiority"} else 6
            self.fast_emotion[dim] = bound(self.fast_emotion.get(dim, 0)*2**(-minutes/half_life))
        self.slow_mood *= 2**(-minutes/(3*24*60))
        self.social_strain *= 2**(-minutes/(12*60))
        self.social_pain_threshold = bound(.8-.5*self.social_strain, .3, .8)
        self.activations = {k: v for k, v in self.activations.items() if parse(v, now) > now}
        self.seen_pulses = {k: v for k, v in self.seen_pulses.items() if parse(v, now) > now}
        self.shyness_reactions = {k: v for k, v in self.shyness_reactions.items()
                                 if parse(v.get("expires_at"), now) > now}
        delta, kept, sampled = {}, [], []
        for raw in self.seeds:
            seed = RuminationSeed(**raw)
            age = max(0.0, (now-parse(seed.created_at, now)).total_seconds()/3600)
            strength = seed.valence*math.exp(-seed.decay_rate*age)
            if now >= parse(seed.expires_at, now) or strength < .05:
                continue
            due = (now-parse(seed.last_sample, now-timedelta(minutes=5))).total_seconds() >= 300
            if sample is not None and due:
                related = sample(seed.query, seed.trigger_event)[:3]
                if related:
                    echo = min(.8, strength*.5)
                    delta["sadness"] = min(2.0, delta.get("sadness", 0)+echo)
                    for mid in related:
                        self.activations[mid] = (now+timedelta(minutes=10)).isoformat(timespec="seconds")
                    sampled.extend(related)
                seed.last_sample = now.isoformat(timespec="seconds")
            kept.append(asdict(seed))
        self.seeds = kept
        self._cues(max(self.fast_emotion.values(), default=0))
        self.updated_at = now.isoformat(timespec="seconds")
        return {"delta": delta, "sampled": sampled, "seed_count": len(kept), "cloud_api_calls": 0}

    def memory_pulse(self, memory: dict, *, now: datetime | None = None) -> dict:
        now = now or datetime.now()
        mid = memory.get("id") or ""
        if not mid or float(memory.get("salience", 0)) < .85 or mid in self.seen_pulses:
            return {}
        tag = memory.get("emotion_tag")
        dim = "sadness" if tag in {"难过", "创伤", "生气"} else ("joy" if tag == "开心" else "")
        if not dim:
            return {}
        self.seen_pulses[mid] = (now+timedelta(minutes=5)).isoformat(timespec="seconds")
        self.fast_emotion[dim] = bound(self.fast_emotion.get(dim, 0)+.08)
        self._cues(.5)
        return {dim: 1.5}

    def prompt_block(self, dominant: str = "平静") -> str:
        mood = "有些低落" if self.slow_mood < -.08 else ("比较轻松" if self.slow_mood > .08 else "平稳")
        cue = "节奏偏快" if self.virtual_heart_rate > 90 else "节奏平缓"
        if self.rest_anticipation:
            cue += "，到了休息时段，有些困倦"
        elif self.energy_cycle < .2:
            cue += "，此刻精力偏低"
        lines = [f"【角色当前状态】心情{dominant}；慢心境{mood}；动作{cue}。"]
        if self.social_strain > .25:
            lines.append("【互动余波】最近不愉快的互动让你有些敏感，可以直接说明感受。")
        if self.seeds:
            lines.append("【反刍余波】刚才的不愉快还未完全散去；如被问起就坦诚表达，不用反话掩饰。")
        if self.last_scope:
            relation = "熟悉而信任" if self.attachment_anchor > .25 else ("还在建立信任" if self.attachment_anchor >= -.25 else "有些谨慎")
            lines.append(f"【相处底色】对当前对话对象{relation}；当前明确的边界与更正仍需尊重。")
        return "\n".join(lines)


def _load() -> CentralAffectiveState:
    return CentralAffectiveState.from_dict(settings.read_state(FILE, {}) or {})


def _inputs():
    from . import circadian, hormones, homeostasis
    data = hormones.status()
    return {"stress": (data.get("hormones", {}).get("cortisol") or {}).get("value", .35),
            "sensitivity": data.get("personality", {}).get("neuroticism", .35),
            "energy": homeostasis.load().get("values", {}).get("energy", .8),
            "resting": circadian.in_sleep_window()}


def observe(text: str, *, event_id: str, scope: str = "", memory_id: str = "") -> dict:
    from .emotion import vector
    with _lock:
        state = _load()
        out = state.observe(text, event_id=event_id, scope=scope, memory_id=memory_id, **_inputs())
        if not out.get("duplicate"):
            applied = vector.apply(out["delta"])
            out["applied"] = applied
            if out["delta"].get("shyness"):
                state.last_shyness_reaction["delta"] = applied.get("shyness", 0.0)
        from .character_expression import plan
        state.expression = plan(vector.read_snapshot(), state)
        settings.write_state(FILE, asdict(state))
        if not out.get("duplicate"):
            from .emotion import daily
            daily.record()
        return out


def tick() -> dict:
    from .emotion import vector
    from .repo import hybrid, memory as mr, system as sr
    from .memory import reliability
    def sample(query, trigger):
        candidates = hybrid.search(query, "memory", 12)["ranked"]
        scores = {i["id"]: i for i in candidates}
        return [i.id for i in mr.many(scores) if i.id != trigger and reliability.current(i)
                and (i.emotional_valence < 0 or i.emotion_tag in {"难过", "生气", "创伤"})
                and (scores[i.id]["semantic"] >= .3 or scores[i.id]["keyword_hit"])]
    with _lock:
        state = _load()
        inputs = _inputs()
        out = state.tick(stress=inputs["stress"], energy=inputs["energy"], resting=inputs["resting"], sample=sample)
        if out["delta"]:
            vector.apply(out["delta"])
        from .character_expression import plan
        state.expression = plan(vector.read_snapshot(), state)
        settings.write_state(FILE, asdict(state))
        if out["sampled"]:
            sr.audit_add("emotion", "affective", "rumination_sample", out)
        from .emotion import daily
        daily.record()
        return out


def memory_pulses(items: list[dict]) -> None:
    from .emotion import vector
    with _lock:
        state = _load()
        for item in items[:3]:
            delta = state.memory_pulse(item)
            if delta:
                vector.apply(delta)
        settings.write_state(FILE, asdict(state))


def activation_bonus(mid: str) -> float:
    stamp = _load().activations.get(mid)
    return 1.1 if stamp and parse(stamp, datetime.min) > datetime.now() else 1.0


def active_memory_ids() -> set[str]:
    now = datetime.now()
    return {mid for mid, stamp in _load().activations.items() if parse(stamp, datetime.min) > now}


def status(vec=None) -> dict:
    from .emotion import vector
    from .entity import CN
    state = _load()
    # Viewing the state does not mutate it or sample memories.
    inputs = _inputs()
    state.tick(stress=inputs["stress"], energy=inputs["energy"], resting=inputs["resting"])
    vec = vec or vector.read_snapshot()
    from .character_expression import plan
    expression = plan(vec, state)
    return {"fictional_character_state": True, "state": asdict(state),
            "expression": expression,
            "block": state.prompt_block(vec.dominant_cn()) + "\n" + expression['block'], "cloud_api_calls": 0,
            "emotion": {"t": vec.updated_at, "dominant": vec.dominant,
                        "dominant_label": vec.dominant_cn(), "reason": "实时状态",
                        "values": [{"key": key, "label": CN[key], "value": round(vec.get(key), 2)} for key in DIMS]}}


def prompt_block() -> str:
    return status()["block"]
