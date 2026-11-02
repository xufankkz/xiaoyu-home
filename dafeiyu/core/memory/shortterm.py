# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""短期记忆：日记 → 待总结 → 3 天升级 / 150 字压缩进 2 号（用户既定规则）。

- 日记原文永久保留（md 只读底稿，DB 只存索引条目）；
- 同一件事在 ≥3 个不同日期出现 → 升级为长期记忆；
- 未满 3 天：与近 3 天内容一起压缩，约 300 字 → 150±20 字，存入 2 号。
补强并行触发（不替代 3 天主规则）：salience≥0.8 / open 且等待≥3 天 / 被检索命中≥3 次。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import settings
from ..entity import Layer, MemoryItem, Status
from ..repo import memory as mr
from ..repo import system as sr
from ..text import bigrams, char_count, clip, compress_mechanical, days_since, keywords, today_str

USE_LLM = True


# ── 日记 ────────────────────────────────────────────────────────────────────
def diary_list() -> list[dict]:
    items = mr.list_layer(layer=Layer.SHORT.value, subtype="diary", limit=2000,
                          alive_only=True, order="t_created DESC")
    by_day: dict[str, dict] = {}
    for item in items:
        day = (item.ext or {}).get("date") or item.t_created[:10]
        slot = by_day.setdefault(day, {"date": day, "entries": 0, "chars": 0})
        slot["entries"] += 1
        slot["chars"] += char_count(item.content)
    return [by_day[d] for d in sorted(by_day, reverse=True)]


def diary_get(day: str) -> dict | None:
    path = settings.LEGACY_DIARY / f"{day}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"date": day, "content": text, "chars": char_count(text)}


def sync_diaries() -> dict:
    """把旧系统/桥接新写的日记增量收进 DB（幂等）。"""
    from ..bridge.legacy_import import import_diary

    before = mr.count(Layer.SHORT.value)
    n = import_diary()
    return {"imported": n, "short_total": mr.count(Layer.SHORT.value), "before": before}


# ── 3 天升级 ────────────────────────────────────────────────────────────────
def _cluster_key(item: MemoryItem) -> str:
    return (item.cluster or (item.ext or {}).get("topic") or item.skeleton or "")[:16].strip()


def find_clusters() -> list[dict]:
    min_days = int(settings.get("short.promote_min_days", 3))
    lookback = int(settings.get("short.promote_lookback_days", 14))
    cutoff = (datetime.now() - timedelta(days=lookback)).isoformat(timespec="seconds")
    groups: dict[str, list[MemoryItem]] = {}
    for item in mr.list_layer(layer=Layer.SHORT.value, limit=4000, alive_only=True):
        if item.t_created < cutoff:
            continue
        if item.status != Status.OPEN.value:
            continue
        key = _cluster_key(item)
        if not key:
            continue
        groups.setdefault(key, []).append(item)
    out = []
    for key, members in groups.items():
        days = sorted({(m.ext or {}).get("date") or m.t_created[:10] for m in members})
        salience = max((m.salience for m in members), default=0.0)
        hits = sum(m.access_count for m in members)
        waiting = max((days_since(m.t_created) for m in members), default=0.0)
        parallel = (salience >= float(settings.get("short.parallel_salience", 0.8))
                    or (waiting >= float(settings.get("short.parallel_open_days", 3)))
                    or hits >= int(settings.get("short.parallel_hits", 3)))
        if len(days) >= min_days or (parallel and len(days) >= 1 and salience >= 0.8):
            out.append({"cluster": key, "days": days, "members": members,
                        "salience": round(salience, 2), "parallel": parallel})
    out.sort(key=lambda c: (-len(c["days"]), -c["salience"]))
    return out


def promote(dry_run: bool = False) -> list[dict]:
    """反复出现满 3 天 → 固化进长期记忆（2 号标注「固化」；涉及老大的同时进 1 号）。"""
    promoted: list[dict] = []
    for group in find_clusters():
        members: list[MemoryItem] = group["members"]
        texts: list[str] = []
        for m in sorted(members, key=lambda x: x.t_created):
            body = (m.content or "").strip()
            if body and body not in texts:
                texts.append(body)
        merged = " ".join(texts)
        if not merged:
            continue
        summary = _compress(merged, target=170 if char_count(merged) > 220 else max(60, char_count(merged) // 2))
        emo = _majority_emotion(members)
        concepts = list(dict.fromkeys([c for m in members for c in (m.concepts or [])]))[:8]
        anchor = group["days"][0]
        item = MemoryItem(
            layer=Layer.LONG2.value, subtype="consolidated",
            content=summary, summary=clip(summary, 60), skeleton=group["cluster"],
            concepts=concepts, emotion_tag=emo, salience=min(1.0, 0.75 + 0.05 * len(group["days"])),
            status=Status.RESOLVED.value, t_created=f"{anchor}T12:00:00", origin="promote",
            ext={"impact": f"这件事连着 {len(group['days'])} 天出现，已经不只是当天的事了。",
                 "labels": f"固化 / 反复出现{len(group['days'])}天",
                 "source": f"短期记忆·反复出现（{', '.join(group['days'])}）",
                 "date": anchor, "days": group["days"], "cluster": group["cluster"]},
        )
        if not dry_run:
            mr.insert(item)
            mr.set_index(item.id, "memory", bigrams(summary), concepts)
            # 涉及老大 → 同时沉淀进 1 号（对老大的认识）
            if any(k in merged for k in ("老大", "主人")):
                lt1 = MemoryItem(
                    layer=Layer.LONG1.value, subtype="owner",
                    content=clip(summary, 200), summary=clip(summary, 60),
                    concepts=list(dict.fromkeys(concepts + ["老大"])), emotion_tag=emo,
                    salience=0.9, status=Status.RESOLVED.value, t_created=item.t_created,
                    origin="promote_owner",
                    ext={"impact": "关于老大的事，反复出现了。", "source": f"短期记忆·反复出现（{anchor}）",
                         "date": anchor},
                )
                mr.insert(lt1)
                mr.set_index(lt1.id, "memory", bigrams(lt1.content), lt1.concepts)
            for m in members:
                mr.update_fields(m.id, status=Status.RESOLVED.value,
                                 ext={**(m.ext or {}), "promoted": True, "promoted_to": item.id})
            sr.audit_add("memory", item.id, "created",
                         {"kind": "promote", "cluster": group["cluster"], "days": group["days"]})
            _append_promote_log(group, emo, summary)
        promoted.append({"cluster": group["cluster"], "days": group["days"], "emotion": emo,
                         "summary": summary, "id": item.id})
    return promoted


def _append_promote_log(group: dict, emo: str, summary: str) -> None:
    path = settings.MEMORY / "short_term" / "晋升记录.md"
    head = "# 短期记忆 → 长期记忆 · 晋升记录\n\n> 同一件事反复出现满 3 天，就从「今天的事」变成「我的事」。\n\n"
    try:
        prev = path.read_text(encoding="utf-8") if path.exists() else head
    except Exception:
        prev = head
    block = (
        f"\n## {datetime.now():%Y-%m-%d %H:%M} · 固化\n\n"
        f"- 事由：{group['cluster']}\n"
        f"- 出现的日子：{', '.join(group['days'])}（{len(group['days'])} 天）\n"
        f"- 归入：2号长期记忆 / {emo}\n"
        f"- 压缩后：{char_count(summary)} 字\n"
    )
    settings.write_text_atomic(path, prev.rstrip() + "\n" + block)


def _majority_emotion(members: list[MemoryItem]) -> str:
    counts: dict[str, int] = {}
    for m in members:
        counts[m.emotion_tag or "平静"] = counts.get(m.emotion_tag or "平静", 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else "平静"


def _compress(text: str, target: int = 150, tol: int | None = None) -> str:
    tol = int(tol if tol is not None else settings.get("short.compress_tolerance", 20))
    body = (text or "").strip()
    if char_count(body) <= target:
        return body
    if USE_LLM:
        from .. import llm
        from ..llm import prompts

        ok, out, _meta = llm.provider.run(
            prompts.COMPRESS.format(target=target, low=max(20, target - tol), high=target + tol, body=body),
            task="compress", timeout=300,
        )
        if ok and out.strip():
            result = out.strip()
            if char_count(result) > target + tol:
                result = clip(result, target + tol, "。")
            return result
    return compress_mechanical(body, target)


# ── 未满 3 天：批量压缩进 2 号 ─────────────────────────────────────────────
def compress_pending(dry_run: bool = False) -> dict:
    min_days = int(settings.get("short.promote_min_days", 3))
    target = int(settings.get("short.compress_target", 150))
    batch_chars = int(settings.get("short.batch_source_chars", 300))
    items = [i for i in mr.list_layer(layer=Layer.SHORT.value, limit=4000, alive_only=True)
             if i.status == Status.OPEN.value and i.subtype in ("pending", "diary")
             and days_since(i.t_created) >= min_days]
    items.sort(key=lambda i: i.t_created)
    batches: list[list[MemoryItem]] = []
    current: list[MemoryItem] = []
    size = 0
    for item in items:
        length = char_count(item.content)
        if current and size + length > batch_chars:
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        batches.append(current)

    done = []
    for batch in batches:
        merged = " ".join(i.content.strip() for i in batch if i.content.strip())
        if not merged:
            continue
        summary = _compress(merged, target=target)
        emo = _majority_emotion(batch)
        anchor = min((i.ext or {}).get("date") or i.t_created[:10] for i in batch)
        concepts = list(dict.fromkeys([c for i in batch for c in (i.concepts or [])]))[:8]
        item = MemoryItem(
            layer=Layer.LONG2.value, subtype="summary",
            content=summary, summary=clip(summary, 60), skeleton=clip(merged, 40),
            concepts=concepts, emotion_tag=emo, salience=0.55,
            status=Status.RESOLVED.value, t_created=f"{anchor}T12:00:00", origin="compress",
            ext={"impact": "（压缩自短期记忆，原文在日记里）",
                 "labels": f"压缩 / {emo}", "source": f"日记 {anchor} 起 {len(batch)} 条",
                 "date": anchor, "batch": len(batch), "from_chars": char_count(merged)},
        )
        if not dry_run:
            mr.insert(item)
            mr.set_index(item.id, "memory", bigrams(summary), concepts)
            for m in batch:
                mr.update_fields(m.id, status=Status.RESOLVED.value,
                                 ext={**(m.ext or {}), "compressed_to": item.id,
                                      "summary_chars": char_count(summary)})
            sr.audit_add("memory", item.id, "created",
                         {"kind": "compress", "batch": len(batch),
                          "from_chars": char_count(merged), "to_chars": char_count(summary)})
        done.append({"id": item.id, "batch": len(batch), "from": char_count(merged),
                     "to": char_count(summary), "emotion": emo})
    return {"batches": len(done), "items": done,
            "chars_in": sum(d["from"] for d in done), "chars_out": sum(d["to"] for d in done)}


# ── 视图 ────────────────────────────────────────────────────────────────────
def stats() -> dict:
    items = mr.list_layer(layer=Layer.SHORT.value, limit=4000, alive_only=True)
    pending = [i for i in items if i.status == Status.OPEN.value]
    waiting = [round(days_since(i.t_created), 1) for i in pending]
    min_days = int(settings.get("short.promote_min_days", 3))
    return {
        "pending": len(pending),
        "ready": sum(1 for d in waiting if d >= min_days),
        "clusters": len(find_clusters()),
        "diary_days": len(diary_list()),
        "min_days": min_days,
        "compress_target": int(settings.get("short.compress_target", 150)),
    }


def pending_view(limit: int = 60) -> dict:
    items = mr.list_layer(layer=Layer.SHORT.value, limit=4000, alive_only=True,
                          order="t_created DESC")
    min_days = int(settings.get("short.promote_min_days", 3))
    rows = []
    for item in items:
        if item.status != Status.OPEN.value:
            continue
        age = days_since(item.t_created)
        rows.append({
            "id": item.id, "date": (item.ext or {}).get("date") or item.t_created[:10],
            "chars": char_count(item.content), "emotion": item.emotion_tag,
            "topic": item.cluster or item.skeleton, "age_days": round(age, 1),
            "ready_in": max(0, round(min_days - age, 1)), "text": clip(item.content, 300),
        })
        if len(rows) >= limit:
            break
    return {"pending": rows, "stats": stats()}
