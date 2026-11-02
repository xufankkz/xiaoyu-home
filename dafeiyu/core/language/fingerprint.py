# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v12 2.6 语言指纹：她自己的说话习惯。

记四种东西：口头禅、语气词偏好、标点习惯、句子长度。
生成回复时把指纹写进提示词（强约束），让她前后像同一个人。
"""

from __future__ import annotations

import re
from collections import Counter

from .. import db, settings
from ..entity import now_iso
from ..text import clip, parse_iso

FILE = "language_fingerprint.json"
PARTICLES = "啊呀呢吧嘛诶哦嗯哼啦咯喔噢呗"
PUNCT = {"ellipsis": "……", "exclaim": "！", "question": "？", "tilde": "～", "comma": "，"}


def _state() -> dict:
    raw = settings.read_state(FILE, {})
    raw.setdefault("phrases", {})
    raw.setdefault("particles", {})
    raw.setdefault("punct", {})
    raw.setdefault("lengths", [])
    raw.setdefault("samples", 0)
    raw.setdefault("updated_at", "")
    return raw


def _save(data: dict) -> dict:
    data["updated_at"] = now_iso()
    settings.write_state(FILE, data)
    return data


def observe(text: str) -> dict:
    """吃进一条她说过的话，更新指纹。"""
    body = str(text or "").strip()
    if not body:
        return _state()
    data = _state()
    phrases = Counter(data.get("phrases") or {})
    particles = Counter(data.get("particles") or {})
    punct = Counter(data.get("punct") or {})

    # 口头禅：3~5 字的短句片段（去除标点后按标点切）
    for chunk in re.split(r"[。！？!?，,、\s…]+", body):
        piece = chunk.strip()
        if 3 <= len(piece) <= 6 and not piece.isdigit():
            phrases[piece] += 1
    for char in body:
        if char in PARTICLES:
            particles[char] += 1
    for key, mark in PUNCT.items():
        if mark in body:
            punct[key] += 1

    lengths = list(data.get("lengths") or [])[-99:] + [len(body)]
    data.update({
        "phrases": dict(phrases.most_common(12)),
        "particles": dict(particles.most_common(8)),
        "punct": dict(punct.most_common(6)),
        "lengths": lengths[-100:],
        "samples": int(data.get("samples", 0)) + 1,
    })
    return _save(data)


def learn_from_history(limit: int = 200) -> dict:
    """第一次启动时，把历史里她说过的话吃进来。"""
    rows = db.q("SELECT content FROM dialogue_message WHERE role='assistant' "
                "ORDER BY t DESC LIMIT ?", (int(limit),))
    if not rows:
        rows = db.q("SELECT content FROM instant_memory WHERE role='assistant' "
                    "ORDER BY ts DESC LIMIT ?", (int(limit),))
    data = _state()
    if int(data.get("samples", 0)) >= 20:
        return {"skipped": "已有样本", "samples": data.get("samples")}
    count = 0
    for row in rows:
        observe(row["content"] or "")
        count += 1
    return {"learned": count, "samples": _state().get("samples", 0)}


def hint() -> str:
    data = _state()
    if int(data.get("samples", 0)) < 5:
        return ""
    parts = []
    phrases = [k for k, v in (data.get("phrases") or {}).items() if v >= 2][:3]
    if phrases:
        parts.append("你习惯用这些口头禅：" + "、".join(f"「{p}」" for p in phrases))
    particles = [k for k, v in (data.get("particles") or {}).items() if v >= 3][:3]
    if particles:
        parts.append("句尾常带：" + "".join(particles))
    punct = data.get("punct") or {}
    if punct.get("ellipsis", 0) > punct.get("exclaim", 0):
        parts.append("停顿用「……」多于感叹号")
    elif punct.get("exclaim", 0) >= 3:
        parts.append("情绪上来会用感叹号")
    lengths = data.get("lengths") or []
    if lengths:
        avg = sum(lengths) / len(lengths)
        if avg < 20:
            parts.append(f"句子偏短（平均 {avg:.0f} 字），能一句不写两句")
        elif avg > 45:
            parts.append(f"说话偏长（平均 {avg:.0f} 字），会展开讲")
    if not parts:
        return ""
    return "【你的语言指纹】" + "；".join(parts) + "。保持这个习惯，别突然变成别人。"


def status() -> dict:
    data = _state()
    lengths = data.get("lengths") or []
    return {
        "samples": int(data.get("samples", 0)),
        "phrases": list((data.get("phrases") or {}).items())[:6],
        "particles": list((data.get("particles") or {}).items())[:6],
        "punct": data.get("punct") or {},
        "avg_length": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        "updated_at": data.get("updated_at", ""),
    }
