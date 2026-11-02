# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""契约文件投影：DB → 旧文件。

QQ 桥接只认这三处，所以它们必须**逐字节兼容**：
1. 记忆系统\\2号长期记忆\\<分类>.md   （dafeiyu-memory.js 每轮唤醒检索）
2. 记忆系统\\瞬时记忆\\热区.jsonl / 温区.jsonl（stateless.instantDir）
3. 记忆系统\\情绪阈值\\状态.json       （emotion-engine.js）

方向是单向的：DB 是事实源，文件是嘴。人在文件里手改的内容会被下一次投影覆盖
（README 里写清楚：要改记忆请改 DB 或走界面，别改投影文件）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .. import settings
from ..entity import CN, DIMS, LT2_CATEGORIES, EmotionVector, Layer
from ..repo import emotion as er, memory as mr
from ..repo import system as sr
from ..text import char_count

CN2DIM = {v: k for k, v in CN.items()}
DIM2CN = dict(CN)


# ── 2 号长期记忆 ────────────────────────────────────────────────────────────
def _intensity(salience: float) -> int:
    return max(1, min(5, int(round(float(salience or 0.5) * 5))))


def _heading(item) -> str:
    ext = item.ext or {}
    if ext.get("heading"):
        return str(ext["heading"])
    date = str(ext.get("date") or (item.t_created or "")[:10])
    dots = "●" * _intensity(item.salience) + "○" * (5 - _intensity(item.salience))
    return f"{date} · 强度 {dots}"


def _preamble(cat: str, existing: str) -> str:
    if existing:
        head = existing.split("\n---", 1)[0].rstrip()
        if head.strip().startswith("#"):
            return head + "\n\n---\n"
    return f"# 2号长期记忆 · {cat}\n\n> 由短期记忆压缩而来。\n\n---\n"


def render_lt2(cat: str, items: list) -> str:
    path = settings.LEGACY_LT2 / f"{cat}.md"
    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            existing = ""
    # 保留原文件里的先后顺序（导入时记了 seq），没有 seq 的按时间排
    rows = sorted(items, key=lambda i: (
        str((i.ext or {}).get("date") or (i.t_created or "")[:10]),
        int((i.ext or {}).get("seq", 9999)),
        i.t_created or "",
        i.id,
    ))
    if not rows:
        return _preamble(cat, existing)
    out = [_preamble(cat, existing).rstrip(), ""]
    for item in rows:
        ext = item.ext or {}
        out.append(f"### {_heading(item)}")
        out.append("")
        out.append((item.content or item.summary or "").strip())
        out.append("")
        out.append(f"- 影响：{ext.get('impact') or '（待补）'}")
        out.append(f"- 标签：{ext.get('labels') or (' / '.join(item.concepts[:3]) or '—')}")
        out.append(f"- 来源：{ext.get('source') or item.origin or '—'}")
        out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def project_lt2(dry_run: bool = False) -> dict:
    """把 DB 里的 2 号记忆投影回 markdown。

    防呆：DB 里某分类是 0 条、而磁盘文件里还有条目时，**跳过不写**。
    （教训：DB 被误清空过一次，投影忠实地把线上 2 号记忆覆盖成了空壳。
    宁可让文件旧一点，也不能让它凭空消失。）
    """
    counts = {}
    skipped = []
    for cat in LT2_CATEGORIES:
        items = mr.list_layer(layer=Layer.LONG2.value, emotion_tag=cat, limit=2000,
                              alive_only=True, order="t_created")
        target = settings.LEGACY_LT2 / f"{cat}.md"
        existing = ""
        if target.exists():
            try:
                existing = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                existing = ""
        existing_blocks = existing.count("### ")
        if not items and existing_blocks:
            counts[cat] = -1
            skipped.append(cat)
            sr.audit_add("memory", f"lt2:{cat}", "projection_skip",
                         {"reason": "DB 里 0 条但文件有内容，拒绝清空", "file_blocks": existing_blocks})
            continue
        if items and existing_blocks and len(items) < existing_blocks * 0.5:
            sr.audit_add("memory", f"lt2:{cat}", "projection_shrink",
                         {"db": len(items), "file": existing_blocks,
                          "warn": "条目数掉了一半以上，确认是压缩/蒸馏造成的"})
        text = render_lt2(cat, items)
        counts[cat] = len(items)
        if dry_run:
            continue
        settings.write_text_atomic(target, text)
        # 规格目录里留一份（人看）
        mirror = settings.MEMORY / "long_term_2" / f"{cat}.md"
        settings.write_text_atomic(mirror, text)
    return {"by_category": counts, "total": sum(v for v in counts.values() if v > 0),
            "skipped": skipped}


# ── 瞬时记忆 ────────────────────────────────────────────────────────────────
def _ts_of(item) -> int:
    ext = item.ext or {}
    if ext.get("ts"):
        try:
            return int(ext["ts"])
        except Exception:
            pass
    try:
        return int(datetime.fromisoformat(item.t_created).timestamp() * 1000)
    except Exception:
        return int(datetime.now().timestamp() * 1000)


def _instant_line(item) -> dict:
    ext = item.ext or {}
    return {
        "conv": ext.get("conv") or "private:owner",
        "label": ext.get("label") or "老大私聊",
        "sender": ext.get("sender") or "老大",
        "is_self": bool(ext.get("is_self", False)),
        "is_owner": bool(ext.get("is_owner", True)),
        "text": item.content or item.summary,
        "ts": _ts_of(item),
        "kind": ext.get("kind") or "chat",
    }


def project_instant(dry_run: bool = False) -> dict:
    from ..text import hours_since

    hot_h = float(settings.get("instant.hot_hours", 6))
    purge_h = float(settings.get("instant.purge_hours", 18))
    items = mr.list_layer(layer=Layer.INSTANT.value, limit=5000, alive_only=True, order="t_created")
    hot, warm = [], []
    for item in items:
        age = hours_since(item.t_created)
        if age > purge_h:
            continue
        rec = _instant_line(item)
        (hot if age <= hot_h else warm).append(rec)
    hot.sort(key=lambda r: r["ts"])
    warm.sort(key=lambda r: r["ts"])
    if not dry_run:
        hot_path = settings.LEGACY_INSTANT / "热区.jsonl"
        if not hot and not warm and hot_path.exists() and hot_path.stat().st_size > 2:
            # 同样的防呆：DB 空、文件有货 → 不清空
            sr.audit_add("memory", "instant", "projection_skip",
                         {"reason": "DB 里 0 条但热区文件有内容，拒绝清空"})
            return {"hot": 0, "warm": 0, "skipped": True}
        for name, rows in (("热区.jsonl", hot), ("温区.jsonl", warm)):
            body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
            settings.write_text_atomic(settings.LEGACY_INSTANT / name, body + ("\n" if body else ""))
            settings.write_text_atomic(settings.MEMORY / "instant" / name, body + ("\n" if body else ""))
        md = (
            "# 瞬时记忆 · 状态\n\n"
            f"- 更新时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"- 热区（≤{hot_h:g}h，原文）：{len(hot)} 条\n"
            f"- 温区（{hot_h:g}~{purge_h:g}h，可摘要）：{len(warm)} 条\n"
            f"- 超过 {purge_h:g}h 的非日记内容：已蒸馏清空（墓碑保留）\n"
        )
        settings.write_text_atomic(settings.MEMORY / "instant" / "状态.md", md)
    return {"hot": len(hot), "warm": len(warm)}


# ── 情绪状态（桥接契约）────────────────────────────────────────────────────
def project_emotion_state(vec: EmotionVector, stats: dict | None = None,
                          last_ignore_ms: int = 0, dry_run: bool = False) -> dict:
    events = []
    for ev in er.reward_recent(40):
        delta_cn = {DIM2CN.get(k, k): v for k, v in (ev.delta or {}).items() if k in DIMS}
        if not delta_cn:
            continue
        events.append({
            "t": _ms(ev.t),
            "kind": {"reward": "reward", "punishment": "punish"}.get(ev.type, ev.type),
            "why": _why(ev.trigger),
            "delta": delta_cn,
            "text": (ev.source_text or "")[:200],
        })
    state = {
        "version": 1,
        "updatedAt": _iso_z(vec.updated_at),
        "emotions": {DIM2CN[d]: int(round(vec.get(d))) for d in DIMS},
        "setpoints": {DIM2CN[d]: int(round(vec.baseline.get(d, 0))) for d in DIMS},
        "stats": stats or {"rewards": 0, "punishments": 0, "shame": 0, "ignored": 0, "care": 0},
        "lastIgnoreAt": int(last_ignore_ms or 0),
        "events": events[-60:],
    }
    if not dry_run:
        settings.write_text_atomic(settings.LEGACY_EMOTION_STATE,
                                  json.dumps(state, ensure_ascii=False, indent=2))
        settings.write_text_atomic(settings.LEGACY_EMOTION_MD, render_emotion_md(vec, events))
        settings.write_state("emotion.json", vec.to_json())
    return state


def render_emotion_md(vec: EmotionVector, events: list[dict]) -> str:
    label = _mood_label(vec)
    lines = [
        "# 情绪阈值 · 当前状态",
        "",
        f"- 更新时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        "- " + "｜".join(f"{DIM2CN[d]} {int(round(vec.get(d)))}" for d in DIMS),
        f"- 现在偏向：{label}（主导：{vec.dominant_cn()}）",
        "",
        "## 最近的加减分",
        "",
    ]
    for ev in events[-10:][::-1]:
        delta = " ".join(f"{k}{'+' if v > 0 else ''}{round(v, 1)}" for k, v in (ev.get("delta") or {}).items())
        when = datetime.fromtimestamp((ev.get("t") or 0) / 1000).strftime("%Y-%m-%d %H:%M") if ev.get("t") else "—"
        lines.append(f"- {when} · {ev.get('why') or ev.get('kind')} · {delta}")
    lines += ["", "## 规矩", "",
              "- 0~100，只影响心情、说话方式和翻记忆的倾向，不许把数字说出口。",
              "- 被夸/被抱/被在乎会加分；被骂/被无视/被丢下会扣分；聊到尺度大的内容害羞会窜。",
              "- 安静下来会自己回落，但不会凭空涨。", ""]
    return "\n".join(lines)


def _mood_label(vec: EmotionVector) -> str:
    pos, neg = vec.positive(), vec.negative()
    if vec.get("shyness") >= 70:
        return "害羞"
    if neg >= 60:
        return "低落"
    if pos >= 60:
        return "活泼"
    return "平稳"


def _why(trigger: str) -> str:
    return {
        "praise": "被夸/被亲密对待", "intimacy": "被亲密对待", "request_met": "要求被满足",
        "cared": "被在乎", "ignored": "被晾着", "request_denied": "要求没被满足",
        "insult": "被骂/被伤害", "abandoned": "被丢下", "scale": "聊到尺度大的内容",
    }.get(trigger, trigger)


def _ms(iso_text: str) -> int:
    try:
        return int(datetime.fromisoformat(iso_text).timestamp() * 1000)
    except Exception:
        return int(datetime.now().timestamp() * 1000)


def _iso_z(iso_text: str) -> str:
    try:
        return datetime.fromisoformat(iso_text).isoformat(timespec="milliseconds") + "Z"
    except Exception:
        return datetime.now().isoformat(timespec="milliseconds") + "Z"


# ── 一次全投影 ──────────────────────────────────────────────────────────────
def project_all(vec: EmotionVector | None = None, dry_run: bool = False) -> dict:
    from ..emotion.vector import current as current_vector

    vec = vec or current_vector()
    out = {"lt2": project_lt2(dry_run=dry_run), "instant": project_instant(dry_run=dry_run)}
    if settings.app().get("bridge", {}).get("emotion_mode") == "core":
        last = sr.dialogue_recent(1)
        out["emotion"] = project_emotion_state(vec, dry_run=dry_run)
    else:
        out["emotion"] = {"skipped": "bridge.emotion_mode != core"}
    return out
