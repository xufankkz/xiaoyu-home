# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""语言记忆 / 情绪记忆的「账本」：字数上限、整体压缩、两周冷却、到期询问。

用户既定规则：
- 语言：单次总结 ≤1000 字；总字数超 3000 → 压到 3000（±500），两周不学。
- 情绪：单次记录 ≤2000 字；总字数超 6000 → 压到 6000（±1000），两周不学。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import settings
from ..entity import CooldownState, Layer, MemoryItem, Status
from ..repo import memory as mr
from ..text import bigrams, char_count, clip, shrink_markdown

TITLES = {
    Layer.LANGUAGE.value: ("语言记忆", "language"),
    Layer.EMOTION.value: ("情绪记忆", "emotion"),
}


def cooldowns() -> CooldownState:
    return CooldownState.from_json(settings.read_state("cooldown.json", {}))


def save_cooldowns(state: CooldownState) -> CooldownState:
    settings.write_state("cooldown.json", state.to_json())
    return state


def _md_path(layer: str):
    name = TITLES[layer][0]
    return settings.MEMORY / ("language" if layer == Layer.LANGUAGE.value else "emotion") / f"{name}.md"


def items(layer: str) -> list[MemoryItem]:
    return mr.list_layer(layer=layer, limit=2000, alive_only=True, order="t_created")


def total_chars(layer: str) -> int:
    return sum(char_count(i.content) for i in items(layer))


def render(layer: str) -> str:
    title = TITLES[layer][0]
    lines = [f"# {title}", "", f"> 由大肥鱼自己维护。当前共 {len(items(layer))} 条 / {total_chars(layer)} 字。", ""]
    for item in items(layer):
        head = (item.ext or {}).get("head") or f"{item.t_created[:10]} · 总结"
        lines += [f"## {head}", "", item.content.strip(), "",
                  f"> 来源：{(item.ext or {}).get('source') or item.origin or '—'}"
                  f"｜字数：{char_count(item.content)}", "", "---", ""]
    return "\n".join(lines).rstrip() + "\n"


def write_projection(layer: str) -> str:
    path = _md_path(layer)
    settings.write_text_atomic(path, render(layer))
    return str(path)


def _limits(layer: str) -> dict:
    key = "language" if layer == Layer.LANGUAGE.value else "emotion_memory"
    cfg = settings.thr().get(key, {})
    return {
        "record_max": int(cfg.get("record_max", 1000 if layer == Layer.LANGUAGE.value else 2000)),
        "trigger": int(cfg.get("memory_trigger", 3000 if layer == Layer.LANGUAGE.value else 6000)),
        "target": int(cfg.get("memory_target", 3000 if layer == Layer.LANGUAGE.value else 6000)),
        "tolerance": int(cfg.get("tolerance", 500 if layer == Layer.LANGUAGE.value else 1000)),
        "lock_days": int(cfg.get("lock_days", 14)),
    }


def append(layer: str, text: str, *, head: str = "", source: str = "") -> dict:
    """追加一条总结；超单次上限先截断，追加后超总量再整体压缩。"""
    limits = _limits(layer)
    body = (text or "").strip()
    if not body:
        return {"stored": False, "reason": "没有内容"}
    truncated = False
    if char_count(body) > limits["record_max"]:
        body = clip(body, limits["record_max"], "…")
        truncated = True
    item = MemoryItem(
        layer=layer, subtype="summary",
        content=body, summary=clip(body, 60), skeleton=head or clip(body, 40),
        concepts=bigrams(body)[:10], emotion_tag="平静",
        salience=0.75, status=Status.RESOLVED.value, origin="learning",
        t_created=datetime.now().isoformat(timespec="seconds"),
        ext={"head": head or f"{datetime.now():%Y-%m-%d} · 总结", "source": source,
             "chars": char_count(body), "truncated": truncated},
    )
    mr.insert(item)
    mr.set_index(item.id, "memory", bigrams(body), item.concepts)
    total = total_chars(layer)
    result = {"stored": True, "chars": char_count(body), "total": total, "truncated": truncated}
    if total > limits["trigger"]:
        result.update(compress(layer))
    write_projection(layer)
    return result


def compress(layer: str) -> dict:
    """整体压缩到目标字数，然后进入两周冷却。"""
    limits = _limits(layer)
    target, tol = limits["target"], limits["tolerance"]
    current = items(layer)
    before = total_chars(layer)
    merged = "\n\n".join(i.content.strip() for i in current)
    if char_count(merged) <= target + tol:
        new_text, method = merged, "无需压缩"
    else:
        new_text, method = merged, "机械压缩（模型不可用）"
        from .. import llm
        from ..llm import prompts

        ok, out, _meta = llm.provider.run(
            prompts.COMPRESS.format(target=target, low=target - tol, high=target + tol, body=merged),
            task="compress", timeout=600,
        )
        if ok and out.strip():
            new_text, method = out.strip(), "模型压缩"
        else:
            new_text = shrink_markdown(merged, target)
    if char_count(new_text) > target + tol:
        new_text = clip(new_text, target + tol, "…")

    for old in current:
        mr.update_fields(old.id, tombstone=1, level=3, content="",
                         ext={**(old.ext or {}), "compressed_into": "book"})
        mr.drop_index(old.id, "memory")
    head = f"{datetime.now():%Y-%m-%d} · 整体压缩（{before} 字 → {char_count(new_text)} 字）"
    item = MemoryItem(
        layer=layer, subtype="compressed", content=new_text, summary=clip(new_text, 60),
        skeleton=head, concepts=bigrams(new_text)[:12], salience=0.9,
        status=Status.RESOLVED.value, origin="compress", t_created=datetime.now().isoformat(timespec="seconds"),
        ext={"head": head, "source": f"压缩（{method}）", "chars": char_count(new_text)},
    )
    mr.insert(item)
    mr.set_index(item.id, "memory", bigrams(new_text), item.concepts)
    until = lock(layer, limits["lock_days"])
    write_projection(layer)
    return {"compressed": True, "before": before, "after": char_count(new_text),
            "method": method, "locked_until": until}


def lock(layer: str, days: int | None = None) -> str:
    limits = _limits(layer)
    days = int(days if days is not None else limits["lock_days"])
    state = cooldowns()
    slot = getattr(state, "language" if layer == Layer.LANGUAGE.value else "emotion")
    until = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
    slot.update({"cooldown_until": until, "lock_started": datetime.now().isoformat(timespec="seconds"),
                 "awaiting_user": False, "memory_chars": total_chars(layer)})
    save_cooldowns(state)
    return until


def locked(layer: str) -> bool:
    slot = getattr(cooldowns(), "language" if layer == Layer.LANGUAGE.value else "emotion")
    until = slot.get("cooldown_until") or ""
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now()
    except Exception:
        return False


def due(layer: str) -> bool:
    slot = getattr(cooldowns(), "language" if layer == Layer.LANGUAGE.value else "emotion")
    until = slot.get("cooldown_until") or ""
    if not until:
        return False
    try:
        if datetime.fromisoformat(until) > datetime.now():
            return False
    except Exception:
        return False
    return not slot.get("awaiting_user")


def ask(layer: str) -> dict:
    if not due(layer):
        return {"due": False}
    state = cooldowns()
    slot = getattr(state, "language" if layer == Layer.LANGUAGE.value else "emotion")
    name = TITLES[layer][0]
    slot["awaiting_user"] = True
    save_cooldowns(state)
    return {"due": True, "layer": layer,
            "question": f"{name}冷了两周了，要不要更新？",
            "detail": f"上次更新：{str(slot.get('lock_started') or '')[:10]}"
                      f"｜冷却到：{str(slot.get('cooldown_until') or '')[:10]}"}


def answer(layer: str, yes: bool) -> dict:
    state = cooldowns()
    slot = getattr(state, "language" if layer == Layer.LANGUAGE.value else "emotion")
    history = list(slot.get("ask_history") or [])
    history.append({"at": datetime.now().isoformat(timespec="seconds"), "answer": "yes" if yes else "no"})
    slot["ask_history"] = history[-20:]
    slot["awaiting_user"] = False
    if yes:
        slot["cooldown_until"] = ""
        save_cooldowns(state)
        return {"ok": True, "action": "unlocked", "message": "解锁了，下次学习会写进来。"}
    until = (datetime.now() + timedelta(days=7)).isoformat(timespec="seconds")
    slot["cooldown_until"] = until
    save_cooldowns(state)
    return {"ok": True, "action": "postponed", "message": f"行，再缓一周（到 {until[:10]}）。"}


def status(layer: str) -> dict:
    limits = _limits(layer)
    state = cooldowns()
    slot = getattr(state, "language" if layer == Layer.LANGUAGE.value else "emotion")
    chars = total_chars(layer)
    until = slot.get("cooldown_until") or ""
    days_left = 0.0
    if until:
        try:
            days_left = max(0.0, round((datetime.fromisoformat(until) - datetime.now()).total_seconds() / 86400, 1))
        except Exception:
            days_left = 0.0
    return {
        "layer": layer, "title": TITLES[layer][0], "chars": chars,
        "trigger": limits["trigger"], "target": limits["target"], "tolerance": limits["tolerance"],
        "record_max": limits["record_max"], "count": len(items(layer)),
        "locked": locked(layer), "locked_until": until, "days_left": days_left,
        "awaiting_user": bool(slot.get("awaiting_user")), "ask_due": due(layer),
        "last_learned_at": slot.get("last_learned_at") or "",
        "ask": ask(layer) if due(layer) else {"due": False},
        "recent": [{"head": (i.ext or {}).get("head", ""), "chars": char_count(i.content),
                    "preview": clip(i.content, 120), "t": i.t_created} for i in items(layer)[-6:][::-1]],
    }
