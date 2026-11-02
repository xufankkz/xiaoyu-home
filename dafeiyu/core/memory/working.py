# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""工作记忆（PFC 式索引槽）。

规格 6.1：容量有限（7±2），槽里存**指针**而不是内容拷贝；
支持临时 Hebbian 绑定（活动依赖衰减）；中央执行决定写谁、保护谁、抑制谁。
"""

from __future__ import annotations

import threading

from .. import settings
from ..entity import WorkingSlot, now_iso

_lock = threading.RLock()
_state: dict | None = None
FILE = "working_memory.json"


def _cfg() -> dict:
    return settings.thr().get("working_memory", {})


def capacity() -> int:
    return int(_cfg().get("slots", 9))


def _load() -> dict:
    global _state
    if _state is None:
        raw = settings.read_state(FILE, {})
        _state = {
            "slots": [WorkingSlot.from_json(s) for s in (raw.get("slots") or [])],
            "links": {k: float(v) for k, v in (raw.get("links") or {}).items()},
        }
    return _state


def _save() -> None:
    st = _load()
    cap = capacity()
    st["slots"] = sorted(st["slots"], key=lambda s: (not s.pinned, -s.weight))[:cap]
    settings.write_state(FILE, {
        "slots": [s.to_json() for s in st["slots"]],
        "links": {k: round(v, 3) for k, v in st["links"].items() if v > 0.02},
        "capacity": cap,
        "updated_at": now_iso(),
    })


def slots() -> list[WorkingSlot]:
    return list(_load()["slots"])


def bind(kind: str, ref_id: str, *, pinned: bool = False, weight: float = 1.0) -> WorkingSlot:
    """把一个指针放进工作记忆（重复绑定则刷新权重）。"""
    with _lock:
        st = _load()
        for slot in st["slots"]:
            if slot.kind == kind and slot.ref_id == ref_id:
                slot.weight = min(2.0, slot.weight + 0.25)
                slot.bound_at = now_iso()
                slot.pinned = slot.pinned or pinned
                _save()
                return slot
        cap = capacity()
        if len(st["slots"]) >= cap:
            victims = [s for s in st["slots"] if not s.pinned]
            if victims:
                victim = min(victims, key=lambda s: s.weight)
                st["slots"].remove(victim)
                for key in [k for k in st["links"] if victim.ref_id in k]:
                    st["links"].pop(key, None)
        used = {s.idx for s in st["slots"]}
        idx = next((i for i in range(cap) if i not in used), len(st["slots"]))
        slot = WorkingSlot(idx=idx, kind=kind, ref_id=ref_id, weight=weight, pinned=pinned)
        st["slots"].append(slot)
        _save()
        return slot


def associate(ref_a: str, ref_b: str, gain: float | None = None) -> None:
    """Hebbian：同时活跃的两个指针之间加权。"""
    gain = gain if gain is not None else float(_cfg().get("hebbian_gain", 0.15))
    key = "|".join(sorted([ref_a, ref_b]))
    with _lock:
        st = _load()
        st["links"][key] = min(3.0, st["links"].get(key, 0.0) + gain)
        _save()


def bind_group(entries: list[tuple[str, str]], *, pinned: bool = False) -> list[WorkingSlot]:
    """一次绑定一组指针，并两两建立临时关联。"""
    bound = [bind(kind, ref_id, pinned=pinned) for kind, ref_id in entries]
    ids = [s.ref_id for s in bound]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            associate(ids[i], ids[j])
    return bound


def decay_tick() -> dict:
    with _lock:
        decay = float(_cfg().get("decay", 0.92))
        floor = float(_cfg().get("unbind_below", 0.1))
        st = _load()
        kept = []
        for slot in st["slots"]:
            if slot.pinned:
                kept.append(slot)
                continue
            slot.weight = round(slot.weight * decay, 3)
            if slot.weight >= floor:
                kept.append(slot)
        st["slots"] = kept
        alive = {s.ref_id for s in kept}
        for key in list(st["links"]):
            a, _, b = key.partition("|")
            if a not in alive or b not in alive:
                st["links"].pop(key, None)
            else:
                st["links"][key] = round(st["links"][key] * decay, 3)
        _save()
        return {"slots": len(st["slots"]), "links": len(st["links"])}


def focus(limit: int = 5) -> list[dict]:
    """给对话/思考用的上下文指针（解析成人能读的一行）。"""
    from ..repo import memory as mr
    from ..repo import thought as tr
    from ..text import clip

    out = []
    for slot in sorted(slots(), key=lambda s: -s.weight)[:limit]:
        text = ""
        if slot.kind == "memory":
            item = mr.get(slot.ref_id)
            text = clip(item.summary or item.content, 60) if item else ""
        elif slot.kind == "thought":
            node = tr.get(slot.ref_id)
            text = clip(node.summary or node.content, 60) if node else ""
        elif slot.kind == "percept":
            text = clip(slot.ref_id, 60)
        if text:
            out.append({"kind": slot.kind, "ref_id": slot.ref_id, "text": text,
                        "weight": round(slot.weight, 2)})
    return out


def snapshot() -> dict:
    st = _load()
    return {
        "capacity": capacity(),
        "slots": [{"idx": s.idx, "kind": s.kind, "ref_id": s.ref_id,
                   "weight": round(s.weight, 2), "pinned": s.pinned} for s in st["slots"]],
        "links": {k: round(v, 2) for k, v in st["links"].items()},
        "focus": focus(),
    }


def clear() -> None:
    global _state
    with _lock:
        _state = {"slots": [], "links": {}}
        _save()
