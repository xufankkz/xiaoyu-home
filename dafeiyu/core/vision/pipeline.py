# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""视觉流水线：任何入口（上传 / 网页截图 / 学习素材）都走这一条。

prepare（切块） → read（识别） → aggregate（按坐标聚合） → attach（落库 + 挂附件）
未决问题 → 思维链 open 节点 → 喂好奇心评估器。
"""

from __future__ import annotations

from pathlib import Path

from .. import llm, settings
from ..entity import ThoughtType
from ..text import clip, keywords
from ..thought import chain as thought_chain
from . import preprocessor, reader as reader_mod, store

UNCLEAR = ("看不清", "不确定", "无法辨认", "认不出", "模糊")

# 表情/动作 → 轻微情绪色彩（社会性线索，不当强奖罚）
EMOJI_EMOTION = {
    "大笑": ("joy", 2), "笑": ("joy", 1), "开心": ("joy", 2), "鼓掌": ("joy", 1),
    "开心地": ("joy", 2), "哭": ("sadness", 2), "难过": ("sadness", 2), "委屈": ("sadness", 2),
    "无语": ("inferiority", 1), "生气": ("sadness", 1), "害羞": ("shyness", 2), "脸红": ("shyness", 2),
    "撒娇": ("shyness", 1), "抱": ("joy", 1),
}


def analyze(image_path: str | Path, *, question: str = "", memory_item_id: str = "",
            kind: str = "upload", source: str = "", created_from: str = "manual",
            reader=None, label: str = "", feature_tag: str = "") -> dict:
    """跑完整条视觉链路。动图走帧解码，静态图走切块。"""
    pre = preprocessor.VisionPreprocessor()
    reader = reader or reader_mod.default_reader()
    from . import animated as animated_mod

    decoder = animated_mod.AnimatedDecoder()
    try:
        is_anim = decoder.supports(image_path)
    except Exception:
        is_anim = False
    if is_anim:
        return _analyze_animation(image_path, decoder, reader, question=question,
                                  memory_item_id=memory_item_id, kind=kind or "sticker",
                                  source=source, created_from=created_from, label=label)
    try:
        plan = pre.prepare(image_path, label=label)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"切块失败：{exc}"}

    results = []
    if reader.available() and not isinstance(reader, reader_mod.NullReader):
        try:
            results = reader.read_tiles(plan, question, feature_tag=feature_tag) if reader else []
        except Exception as exc:  # noqa: BLE001
            results = []
            _note_failure(image_path, f"识别失败：{exc}")

    report = pre.aggregate(plan, results)
    summary = report.get("summary") or (f"{plan.width}×{plan.height} 的图（"
                                        + ("已切 " + str(plan.count) + " 块" if plan.tiled else "未切块")
                                        + "），暂未识别")
    report["summary"] = clip(summary, 4000)

    concepts = keywords(f"{label} {report['summary']}", 8)
    attach = store.attach(plan, report, memory_item_id=memory_item_id, kind=kind,
                          source=source or plan.image_path, created_from=created_from,
                          concepts=concepts)

    # v15：静态图也进表情字典（同一张图下次查表 0 花费）
    try:
        from .. import sticker

        sha = sticker.sha256_file(str(image_path))
        if sha and kind in ("sticker", "upload", "page_image"):
            sticker.remember(sha, summary=report["summary"], concepts=concepts,
                             kind=kind, note=str(getattr(plan, "label", "") or ""))
    except Exception:
        pass

    question_nodes = _open_questions(report, plan)
    return {"ok": True, "asset_id": attach["asset_id"], "tiles": plan.count,
            "tiled": plan.tiled, "summary": report["summary"],
            "coverage": report.get("coverage"), "open_questions": question_nodes,
            "reader": getattr(reader, "name", "?")}


def _analyze_animation(image_path, decoder, reader, *, question="", memory_item_id="",
                       kind="sticker", source="", created_from="manual", label="") -> dict:
    """动图：抽帧 → 逐帧识别（或缓存命中）→ 落库 + animation_meta。"""
    try:
        plan = decoder.decode(image_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"抽帧失败：{exc}"}

    cached = decoder.cache_get(plan.sha256)
    cache_hit = bool(cached and cached.get("summary"))
    results: list[dict] = []
    if cache_hit:
        summary = str(cached["summary"])
        concepts = list(cached.get("concepts") or [])
        blocks: list[dict] = []
    else:
        results = reader.read_frames(plan, question) if reader else []
        overall = ""
        blocks = []
        for frame in plan.frames:
            hit = next((r for r in results if int(r.get("i", -1)) == frame.index), None)
            if hit:
                blocks.append({"i": frame.index, "t_ms": frame.t_ms, "text": hit["text"]})
        overall = next((r["text"] for r in results if int(r.get("i", -1)) == -1), "")
        summary = (overall or "").strip() or "；".join(b["text"] for b in blocks)
        if not summary:
            summary = f"{plan.count} 帧动图（{plan.duration_ms}ms），暂未识别"
        concepts = keywords(summary, 8) if results else ["动态表情"]
        if not concepts:
            concepts = ["动态表情"]
        decoder.cache_put(plan.sha256, plan, summary, concepts)

    emotion_tag, gain, dim = _emoji_emotion(summary)
    meta = plan.manifest()
    meta.update({
        "cache_hit": cache_hit, "summary": summary[:800], "emotion_tag": emotion_tag,
        "blocks": blocks, "label": label, "reader": getattr(reader, "name", "?"),
    })
    attach = store.attach(plan, {"summary": summary, "coverage": len(blocks) / max(1, plan.count)},
                          memory_item_id=memory_item_id, kind=kind or "sticker",
                          source=source or plan.source, created_from=created_from,
                          concepts=concepts, animation_meta=meta)
    if dim and gain:
        _nudge_emotion(dim, gain)
    return {"ok": True, "animated": True, "asset_id": attach["asset_id"],
            "tiles": plan.count, "tiled": False, "frames": plan.count,
            "duration_ms": plan.duration_ms, "cache_hit": cache_hit,
            "summary": summary, "emotion_tag": emotion_tag,
            "coverage": round(len(blocks) / max(1, plan.count), 3),
            "reader": getattr(reader, "name", "?")}


def _emoji_emotion(summary: str) -> tuple[str, int, str]:
    body = summary or ""
    for word, (dim, gain) in EMOJI_EMOTION.items():
        if word in body:
            tag = {"joy": "开心", "sadness": "难过", "inferiority": "自卑", "shyness": "害羞"}.get(dim, "平静")
            return tag, gain, dim
    return "平静", 0, ""


def _nudge_emotion(dim: str, gain: int) -> None:
    """表情包带来的轻微情绪波动（不是奖罚事件，不写 reward_event）。"""
    try:
        from ..emotion import vector

        vector.apply({dim: float(gain)}, 1.0)
    except Exception:
        pass


def _open_questions(report: dict, plan) -> list[str]:
    made = []
    text = report.get("summary") or ""
    if not any(mark in text for mark in UNCLEAR):
        return made
    chain = thought_chain.chain_for(f"vision-{Path(plan.image_path).stem}")
    node = thought_chain.new_node(
        chain_id=chain, type=ThoughtType.OPEN_QUESTION.value,
        content=f"图里有些地方看不清：{clip(text, 120)}", task_ref="vision",
        salience=0.6, confidence=0.35,
        concepts=keywords(text, 6),
    )
    made.append(node.id)
    try:
        from ..cognition import curiosity

        curiosity.assess(text=clip(text, 200), targets=[f"看清{clip(text, 20)}"],
                         source="autonomous")
    except Exception:
        pass
    return made


def _note_failure(image_path, detail: str) -> None:
    try:
        from ..memory import distill

        distill.record_failure(f"vision:{Path(image_path).name}", detail, kind="failure", uncertainty=0.7)
    except Exception:
        pass
