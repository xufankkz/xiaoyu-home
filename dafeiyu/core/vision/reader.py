# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""视觉识别适配器：把切块交给「会看图的 agent」，拿回结构化结果。

核心是 Python，拿不到 DSH 的视觉通道，所以这里走 `dsh headless`：
prompt 里给出切块路径，agent 用它自带的图片读取工具逐张看，回一段 JSON。
换实现只要满足 VisionReader 协议（比如以后接本地视觉模型）。
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from .. import llm, settings
from ..text import clip

PROMPT = """你要看 {n} 张图片，它们是同一张大图（{width}×{height}）按 800×800 切出来的块。
请用你的图片读取工具逐张打开，然后**只输出一个 JSON 数组**，不要输出别的文字、不要代码块围栏。

每一块的清单（路径、在原图里的坐标）：
{listing}

输出格式（严格）：
[{{"i":0,"text":"这块里有什么（中文，60 字内，写具体：物体、文字、数字、布局）","confidence":0.8}}]

要求：
- 逐块描述，不要合并、不要跳过。
- 认不出的地方写「看不清」，不要瞎编。
- 如果块里有文字，把文字尽量抄下来。
{question}"""


FRAME_PROMPT = """这是一张动图按时间顺序抽出来的 {n} 帧（不是 {n} 张不同的图，是同一段动作的连续画面）。
请用你的图片读取工具逐帧打开，然后**只输出一个 JSON 数组**，不要别的文字、不要代码块围栏。

帧清单（帧号、时间戳、路径）：
{listing}

输出格式（严格）：
[{{"i":0,"text":"这一帧的动作/表情/文字（中文，40 字内）","confidence":0.8}}]

要求：
- 逐帧描述"这一帧在做什么"，特别注意帧与帧之间的变化（动作、表情、出现/消失的元素）。
- 最后在心里把整段动作总结成一句（在 i=-1 那条里给整段总结）。
{question}"""


class VisionReader(Protocol):
    name: str

    def available(self) -> bool: ...

    def read_tiles(self, plan, question: str = "") -> list[dict]: ...


class NullReader:
    """兜底：不识别，只留 manifest。学习流程照常往下走。"""

    name = "null"

    def available(self) -> bool:
        return True

    def read_tiles(self, plan, question: str = "", feature_tag: str = "") -> list[dict]:
        return []

    def read_frames(self, plan, question: str = "") -> list[dict]:
        return []

class DshAgentReader:
    name = "dsh-agent"

    def __init__(self, max_per_call: int | None = None):
        cfg = settings.load("vision")
        self.max_per_call = int(max_per_call or cfg.get("max_tiles_per_call", 8))

    def available(self) -> bool:
        return llm.provider.available("vision")

    def read_tiles(self, plan, question: str = "", feature_tag: str = "") -> list[dict]:
        results: list[dict] = []
        tiles = list(plan.tiles)
        for start in range(0, len(tiles), self.max_per_call):
            chunk = tiles[start:start + self.max_per_call]
            listing = "\n".join(
                f"- 第{t.index}块【{t.label}】{t.path}（{t.x},{t.y}）{t.w}×{t.h}" for t in chunk)
            prompt = PROMPT.format(
                n=len(chunk), width=plan.width, height=plan.height,
                listing=listing,
                question=(f"\n额外问题：{question}" if question else ""),
            )
            if plan.thumb and start == 0:
                prompt += f"\n（另有一张整图缩略图，可先看它找重点：{plan.thumb}）"
            ok, text, _meta = llm.provider.run(
                prompt, task="vision", feature_tag=feature_tag or "vision_static", timeout=420)
            if not ok or not text.strip():
                continue
            results.extend(_parse(text, [t.index for t in chunk]))
        return results

    def read_frames(self, plan, question: str = "") -> list[dict]:
        """动图：逐帧看，最后要一句整段总结。"""
        listing = "\n".join(f"- 第{f.index}帧（{f.t_ms}ms）{f.path}" for f in plan.frames)
        if plan.sheet_path:
            listing += f"\n- 另有一张拼好的标注图：{plan.sheet_path}"
        prompt = FRAME_PROMPT.format(
            n=plan.count, listing=listing,
            question=(f"\n额外问题：{question}" if question else ""),
        )
        ok, text, _meta = llm.provider.run(prompt, task="vision",
                                           feature_tag="vision_animation", timeout=420)
        if not ok or not text.strip():
            return []
        allowed = [f.index for f in plan.frames] + [-1]
        rows = _parse(text, allowed)
        return rows


def _parse(text: str, allowed: list[int]) -> list[dict]:
    body = (text or "").strip()
    body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
    body = re.sub(r"\s*```$", "", body)
    start, end = body.find("["), body.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(body[start:end + 1])
    except Exception:
        return []
    out = []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("i", row.get("index", -1)))
        except Exception:
            continue
        if index not in allowed:
            continue
        text_value = str(row.get("text") or "").strip()
        if not text_value:
            continue
        out.append({"i": index, "text": clip(text_value, 300),
                    "confidence": float(row.get("confidence") or 0.5)})
    return out


def default_reader() -> VisionReader:
    reader = DshAgentReader()
    return reader if reader.available() else NullReader()
