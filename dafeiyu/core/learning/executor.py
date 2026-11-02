# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""学习执行器：联网检索 → 解析 → 存入技能栏/知识库 → 关闭 open 节点 → 回写好奇心。

规格 5.2：定时学习（每天 00:00-01:00）+ 并行自习通道（好奇心驱动）。
行动权限：对话中只能「记下待学」或「问老大」；自主/空闲时才走完整流程。
"""

from __future__ import annotations

import json
from datetime import datetime

from .. import llm, settings
from ..entity import IntentStatus, Layer, now_iso
from ..language import memory as langmem
from ..llm import prompts
from ..repo import learning as lr
from ..repo import thought as tr
from ..text import char_count, clip, keywords
from ..cognition import curiosity, metacognition


def report_path(topic: str) -> "object":
    folder = settings.LEARNING / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    safe = clip(topic, 24).replace("\\", "").replace("/", "").replace(":", "")
    return folder / f"{datetime.now():%Y-%m-%d}-{safe}.md"


# 老名字留着：外部（含 v36 自主上网学习）都按公开名字调
_report_path = report_path


def execute(intent, *, allow_web: bool = True, visual_url: str | None = None) -> dict:
    """执行一个学习意图。allow_web=False 时只记不学（对话中）。"""
    if not allow_web:
        return {"intent": intent.id, "deferred": True,
                "reason": "对话中只能记下待学，等自主/空闲时再学"}
    # v14：好奇心引擎生成的意图，标签与开关都单独处理
    autonomous = str(getattr(intent, "source", "")) == "autonomous"
    if autonomous:
        try:
            from .. import curiosity_engine

            conf = curiosity_engine.cfg()
            if not bool(conf.get("auto_learn", True)):
                return {"intent": intent.id, "deferred": True,
                        "reason": "自主学习开关关着，意图留在队列里等手动确认"}
            blocked, why = curiosity_engine.budget_blocked()
            if blocked:
                return {"intent": intent.id, "deferred": True, "reason": why}
        except Exception:
            pass
    if not curiosity.budget_ok():
        return {"intent": intent.id, "deferred": True, "reason": "联网预算用完了"}

    feature_tag = "curiosity_autonomous" if autonomous else "learning"
    lr.intent_update(intent.id, status=IntentStatus.LEARNING.value, attempts=int(intent.attempts) + 1)
    domain = (intent.context or {}).get("domain") or curiosity.domain_of(intent.target)
    before = metacognition.uncertainty_of(intent.target)

    # 视觉证据（可选）：需要"看一眼网页"的意图，先截图/抽图并识别
    visual: dict | None = None
    if visual_url:
        try:
            from . import browse

            visual = browse.explore(visual_url, question=intent.proposed_query)
        except Exception as exc:  # noqa: BLE001
            visual = {"ok": False, "error": str(exc)}

    prompt = prompts.LEARNING.format(target=intent.target)
    if visual and visual.get("summary"):
        prompt += ("\n\n【我刚刚打开网页看到的内容（截图识别，可能有误差）】\n"
                   + clip(visual["summary"], 2500))
    ok, text, meta = llm.provider.run(prompt, task="learning", feature_tag=feature_tag, timeout=600)
    curiosity.record_web()

    if not ok or not text.strip():
        ev = metacognition.record_failure(intent.target, text or "学习失败", kind="failure", uncertainty=0.8)
        attempts = int(intent.attempts) + 1
        if attempts >= 2:
            lr.intent_update(intent.id, status=IntentStatus.ABANDONED.value,
                             last_error=clip(text, 200), attempts=attempts)
            outcome = "abandoned"
        else:
            lr.intent_update(intent.id, status=IntentStatus.QUEUED.value,
                             last_error=clip(text, 200), attempts=attempts)
            outcome = "retry"
        _write_report(intent, ok=False, body=text, meta=meta, outcome=outcome)
        return {"intent": intent.id, "ok": False, "outcome": outcome,
                "error": clip(text, 200), "evidence": ev.id, "cost": meta.get("cost")}

    kn = metacognition.learn(
        intent.target, text, domain=domain, source="web",
        intent_id=intent.id, concepts=keywords(f"{intent.target} {text}", 8),
    )
    closed = tr.close_open(intent.target, kn.id, note=clip(kn.name, 40))
    after = metacognition.uncertainty_of(intent.target)
    fb = curiosity.feedback(domain, before, after)

    lr.intent_update(intent.id, status=IntentStatus.DONE.value, result_ref=kn.id,
                     last_error="", curiosity_score=float(intent.curiosity_score))

    # v13：把这次的结果回写给好奇心引擎（误差、进步率、探索次数）
    if autonomous:
        try:
            from .. import curiosity_engine

            curiosity_engine.record_outcome(
                intent.target, error_before=before, error_after=after,
                note=clip(kn.name, 60))
        except Exception:
            pass

    _write_report(intent, ok=True, body=text, meta=meta, outcome="done",
                  extra={"knowledge": kn.id, "closed_open": closed, "feedback": fb,
                         "uncertainty": [round(before, 2), round(after, 2)],
                         "visual": (visual or {}).get("summary", "")[:400] if visual else ""})
    return {"intent": intent.id, "ok": True, "outcome": "done", "knowledge": kn.id,
            "closed_open": closed, "feedback": fb, "chars": char_count(text),
            "visual": bool((visual or {}).get("ok")), "cost": meta.get("cost", 0.0)}


def write_report(intent, *, ok: bool, body: str, meta: dict, outcome: str,
                 extra: dict | None = None) -> None:
    path = report_path(intent.target)
    lines = [
        f"# 学习报告 · {intent.target}",
        "",
        f"- 时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 结果：{'成功' if ok else '失败'}（{outcome}）",
        f"- 来源：{intent.source}｜好奇心分：{intent.curiosity_score:.2f}｜可学习性：{intent.expected_learnability:.2f}",
        f"- 花销：¥{float(meta.get('cost') or 0):.4f}",
        "",
    ]
    if extra:
        try:
            block = json.dumps(extra, ensure_ascii=False, indent=2, default=str)
        except Exception:
            block = str(extra)
        lines += ["```json", block, "```", ""]
    lines += ["## 原始输出", "", body or "（空）", ""]
    settings.write_text_atomic(path, "\n".join(lines))


# 老名字留着（历史调用点不改）
_write_report = write_report


def run_one(intent_id: str | None = None, *, allow_web: bool = True,
            visual_url: str | None = None) -> dict:
    """跑一个意图（队列里分最高的）。"""
    if intent_id:
        intent = lr.intent_get(intent_id)
    else:
        queue = lr.intent_queue(limit=1)
        intent = queue[0] if queue else None
    if intent is None:
        return {"ok": False, "reason": "队列是空的"}
    return execute(intent, allow_web=allow_web, visual_url=visual_url)


def window_open(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    cfg = settings.learning()
    return settings.in_window(now, cfg["start_min"], cfg["end_min"])


def system_locked() -> dict:
    """语言与情绪都在冷却里 → 学习系统整体停（用户既定规则）。"""
    lang_locked = langmem.locked(Layer.LANGUAGE.value)
    emo_locked = langmem.locked(Layer.EMOTION.value)
    return {"language": lang_locked, "emotion": emo_locked, "all_locked": lang_locked and emo_locked}


def scheduled_run(max_items: int = 2) -> dict:
    """定时学习窗口里跑几条（意图队列 + v36 自主上网学习）。"""
    if system_locked()["all_locked"]:
        return {"skipped": "语言与情绪都在冷却里，学习系统暂停"}
    # 配了 study_urls 就让第一条意图去看一眼网页（截图 → 切块 → 识别 → 写进学习素材）
    visual_url = None
    try:
        urls = settings.load("vision").get("study_urls") or []
        if urls:
            visual_url = str(urls[0])
    except Exception:
        visual_url = None
    results = []
    for index in range(max(1, int(max_items))):
        out = run_one(visual_url=visual_url if index == 0 else None)
        results.append(out)
        if not out.get("ok"):
            break
    # v36：自主上网学习（自己找白名单站点看 → 负面过滤 → 总结 → 写知识库）。
    # 开关/配额/间隔/疲劳都在 webstudy.tick() 里把关，这里只负责「到点叫它一声」。
    web = _webstudy_tick()
    return {"results": results,
            "ok": sum(1 for r in results if r.get("ok")),
            "web": web,
            "cost": round(sum(float(r.get("cost") or 0) for r in results), 6)
            + float((web or {}).get("cost") or 0)}


def _webstudy_tick() -> dict:
    try:
        from . import webstudy

        return webstudy.tick()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": clip(str(exc), 200)}


def queue_view(limit: int = 30) -> list[dict]:
    rows = lr.intent_queue(limit=limit)
    return [{"id": i.id, "target": i.target, "gap_type": i.gap_type,
             "score": round(i.curiosity_score, 3), "learnability": round(i.expected_learnability, 2),
             "source": i.source, "status": i.status, "attempts": i.attempts,
             "created": i.t_created, "error": i.last_error} for i in rows]
