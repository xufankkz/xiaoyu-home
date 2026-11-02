# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""自我对话练习（规格 5.3）：每天学习后 300 次，分 3 批，成果 ≤1000 字存语言记忆。

补强：过程生成思维链节点，其中的未决问题喂好奇心评估器。
"""

from __future__ import annotations

import re
from datetime import datetime

from .. import llm, settings
from ..entity import Layer, ThoughtType
from ..language import memory as langmem
from ..llm import prompts
from ..text import char_count, clip
from ..thought import chain as thought_chain
from ..cognition import curiosity


def _cfg() -> dict:
    return settings.thr().get("practice", {})


def window_open(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    cfg = settings.learning()
    start = settings._minutes(cfg["self_dialogue_start"])
    end = settings._minutes(cfg["self_dialogue_end"])
    return settings.in_window(now, start, end)


def _emotion_line() -> str:
    from ..emotion.vector import current

    vec = current()
    return (f"{vec.dominant_cn()}（愉悦{int(vec.get('joy'))} 幸福{int(vec.get('happiness'))} "
            f"难过{int(vec.get('sadness'))} 自卑{int(vec.get('inferiority'))} 害羞{int(vec.get('shyness'))}）")


def practice_batch(n: int, topic: str, memory_hint: str = "", start_round: int = 1) -> tuple[bool, list[str], dict]:
    prompt = (
        f"你要练习说话，主题「{topic}」。请一口气写 {n} 句话，每句一行，不要编号、不要解释。\n"
        f"每句不超过 40 字，口语、像人、有情绪，可以自问自答。\n"
        f"情绪：{_emotion_line()}\n"
        f"记忆素材（可用可不用）：{memory_hint or '（无）'}\n"
        f"从第 {start_round} 句开始写。"
    )
    ok, text, meta = llm.provider.run(prompt, task="self_dialogue", timeout=600)
    if not ok:
        return False, [], meta
    lines = [re.sub(r"^\s*(\d+[.、)]|[-*•])\s*", "", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and len(ln) <= 80]
    return True, lines[:n], meta


def run(rounds: int | None = None, batch: int | None = None, topic: str = "说话方式") -> dict:
    cfg = _cfg()
    rounds = int(rounds or cfg.get("rounds", 300))
    batch = max(1, int(batch or cfg.get("batch", 100)))
    if langmem.locked(Layer.LANGUAGE.value):
        return {"skipped": "语言记忆冷却中", "locked_until": langmem.status(Layer.LANGUAGE.value)["locked_until"]}

    chain_id = thought_chain.chain_for(f"self-dialogue-{datetime.now():%Y-%m-%d}")
    all_lines: list[str] = []
    open_lines: list[str] = []
    cost = 0.0
    done = 0
    while done < rounds:
        n = min(batch, rounds - done)
        ok, lines, meta = practice_batch(n, topic, _hint(), start_round=done + 1)
        cost += float(meta.get("cost") or 0)
        if not ok:
            break
        all_lines.extend(lines)
        done += len(lines)
        # 未决问题 → 思维链 open_question
        for line in lines:
            if any(mark in line for mark in ("?", "？", "为什么", "是不是")):
                thought_chain.new_node(
                    chain_id=chain_id, type=ThoughtType.OPEN_QUESTION.value, content=line,
                    task_ref="self_dialogue", salience=0.55, confidence=0.4,
                )
                open_lines.append(line)
        if len(lines) < n // 2:
            break

    summary = _summarize(all_lines, topic)
    stored = langmem.append(Layer.LANGUAGE.value, summary,
                            head=f"{datetime.now():%Y-%m-%d} · 第{len(all_lines)}句自我对话",
                            source=f"自我对话 {len(all_lines)} 句 / 主题 {topic}")

    # 补强：未决问题喂好奇心
    intents = []
    if open_lines:
        intents = curiosity.assess(text="\n".join(open_lines[:5]), source="autonomous")

    return {"rounds": len(all_lines), "batches": (done + batch - 1) // batch,
            "summary_chars": char_count(summary), "stored": stored,
            "open_questions": len(open_lines), "new_intents": [i.target for i in intents],
            "cost": round(cost, 6), "chain_id": chain_id}


def _hint() -> str:
    from ..memory import retrieval
    from ..emotion.vector import current

    try:
        result = retrieval.recall("说话 语气 情绪", vec=current(), top_k=3, touch=False)
        return "；".join(i["summary"] for i in result.get("items", [])[:3])
    except Exception:
        return ""


def _summarize(lines: list[str], topic: str) -> str:
    body = "\n".join(lines)
    if not body.strip():
        return f"今天没练成（主题 {topic}）。"
    limit = int(settings.get("language.summary_max", 1000))
    ok, text, _meta = llm.provider.run(
        f"把下面这 {len(lines)} 句自我对话总结成 ≤{limit} 字的心得，第一人称，"
        f"写「我今天练会了什么、还有什么不顺手」。只输出正文。\n\n{body}",
        task="summary", timeout=300,
    )
    if ok and text.strip():
        return clip(text.strip(), limit, "…")
    # 兜底：按句序抽样
    step = max(1, len(lines) // 12)
    picked = lines[::step][:12]
    return clip(f"今天练了 {len(lines)} 句，主题「{topic}」。" + "；".join(picked), limit, "…")
