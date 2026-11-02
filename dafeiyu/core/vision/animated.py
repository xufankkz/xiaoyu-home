# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""动态表情（GIF / WebP / APNG）解码：均匀抽帧 + 帧号时间戳 + 可选拼图。

为什么要单独一条路：QImage 只会读第一帧，静态切块链路拿到动图等于只看见第一秒。
这里按 QImageReader.imageCount() 逐帧读，均匀抽 N 帧（默认 5），再：
- 有 Qt 应用（桌面在跑）→ 拼成一张带帧号/时间戳的标注图；
- 无 Qt 应用（headless 夜间任务）→ 不拼图，直接把逐帧文件交给看图链路，帧号与时间写在提示词里。
两种都能让模型看到"整段动作"，不依赖任何外部插件。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QImageReader, QPainter

from .. import db, settings
from ..entity import loads, now_iso
from ..text import clip

ANIM_SUFFIXES = (".gif", ".webp", ".apng")


def cfg() -> dict:
    return settings.load("animation")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class FrameInfo:
    index: int
    path: str
    t_ms: int
    label: str


@dataclass
class AnimationPlan:
    source: str
    sha256: str
    width: int
    height: int
    frames: list[FrameInfo] = field(default_factory=list)
    duration_ms: int = 0
    loop: int = 0
    sheet_path: str = ""
    work_dir: str = ""

    @property
    def count(self) -> int:
        return len(self.frames)

    def manifest(self) -> dict:
        return {
            "source": self.source, "sha256": self.sha256,
            "width": self.width, "height": self.height,
            "frames": self.count, "duration_ms": self.duration_ms, "loop": self.loop,
            "sheet": self.sheet_path,
            "frame_indices": [f.index for f in self.frames],
            "frame_files": [f.path for f in self.frames],
            "frame_times_ms": [f.t_ms for f in self.frames],
        }


class AnimatedDecoder:
    def __init__(self, *, frames: int | None = None, cell_px: int | None = None,
                 out_dir: str | Path | None = None):
        c = cfg()
        self.frames_wanted = int(frames or c.get("max_frames", 5))
        self.cell_px = int(cell_px or c.get("frame_cell_px", 140))
        self.max_edge = int(c.get("max_edge_single_frame", 800))
        self.out_dir = Path(out_dir) if out_dir else (settings.ROOT / str(c.get("inbox_dir", "learning\\vision_inbox")))

    # ── 判定 ──
    def supports(self, path: str | Path) -> bool:
        p = Path(path)
        if p.suffix.lower() in ANIM_SUFFIXES:
            return True
        reader = QImageReader(str(p))
        try:
            return reader.imageCount() > 1
        except Exception:
            return False

    # ── 解码 ──
    def decode(self, path: str | Path) -> AnimationPlan:
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(str(src))
        digest = _sha256(src)
        reader = QImageReader(str(src))
        reader.setAutoTransform(True)
        total = int(reader.imageCount() or 1)

        # GIF 解码器不支持 jumpToImage（返回 False），只能顺序 read() 逐帧推进。
        # 帧少，顺序读完再挑；顺便把每帧延迟攒出来算总时长。
        wanted = max(1, min(self.frames_wanted, total))
        if total <= wanted:
            picks = list(range(total))
        else:
            picks = sorted({round(i * (total - 1) / (wanted - 1)) for i in range(wanted)})
        pick_set = set(picks)

        out_dir = self.out_dir / f"anim-{digest[:12]}"
        out_dir.mkdir(parents=True, exist_ok=True)

        frames: list[FrameInfo] = []
        elapsed = 0
        delays: list[int] = []
        for index in range(total):
            delay = int(reader.nextImageDelay() or 100)
            image = reader.read()
            if image.isNull():
                break
            delays.append(max(10, delay))
            if index in pick_set:
                if image.format() != QImage.Format_RGB32:
                    image = image.convertToFormat(QImage.Format_RGB32)
                small = image.scaled(self.max_edge, self.max_edge, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)
                file = out_dir / f"frame_{index:03d}.jpg"
                small.save(str(file), "JPG", 88)
                frames.append(FrameInfo(index=index, path=str(file), t_ms=elapsed,
                                        label=f"第{len(frames) + 1}帧(#{index}, {elapsed}ms)"))
            elapsed += max(10, delay)
        duration = sum(delays)

        plan = AnimationPlan(
            source=str(src), sha256=digest, width=small.width() if frames else 0,
            height=small.height() if frames else 0, frames=frames,
            duration_ms=duration, loop=0, work_dir=str(out_dir),
        )
        plan.sheet_path = self._compose_sheet(plan, out_dir)
        return plan

    def _compose_sheet(self, plan: AnimationPlan, out_dir: Path) -> str:
        """只在有 QGuiApplication 时拼标注图（headless 下 QPainter 不可用）。"""
        if not plan.frames or QGuiApplication.instance() is None:
            return ""
        cell = self.cell_px
        cols = min(plan.count, 3)
        rows = (plan.count + cols - 1) // cols
        width, height = cols * cell, rows * (cell + 18)
        sheet = QImage(width, height, QImage.Format_RGB32)
        sheet.fill(QColor("#101820"))
        painter = QPainter(sheet)
        try:
            for i, frame in enumerate(plan.frames):
                image = QImage(frame.path)
                if image.isNull():
                    continue
                scaled = image.scaled(cell, cell, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = (i % cols) * cell + (cell - scaled.width()) // 2
                y = (i // cols) * (cell + 18)
                painter.drawImage(x, y, scaled)
                painter.setPen(QColor("#e6f1ff"))
                painter.drawText(QRect((i % cols) * cell, y + cell, cell, 18),
                                 Qt.AlignCenter, f"#{frame.index} · {frame.t_ms}ms")
        finally:
            painter.end()
        file = out_dir / "sheet.jpg"
        sheet.save(str(file), "JPG", 88)
        return str(file)

    # ── 缓存 ──
    def cache_get(self, sha256: str) -> dict | None:
        if not bool(cfg().get("use_sticker_dict_cache", True)):
            return None
        row = db.q1("SELECT * FROM sticker_cache WHERE sha256=?", (sha256,))
        if row is None:
            return None
        try:
            db.ex("UPDATE sticker_cache SET hits=hits+1, last_seen=? WHERE sha256=?", (now_iso(), sha256))
            row = db.q1("SELECT * FROM sticker_cache WHERE sha256=?", (sha256,)) or row
        except Exception:
            pass
        data = dict(row)
        data["concepts"] = loads(data.get("concepts"), [])
        return data

    def cache_put(self, sha256: str, plan: AnimationPlan, summary: str, concepts: list[str],
                  note: str = "") -> None:
        try:
            db.ex(
                "INSERT OR REPLACE INTO sticker_cache(sha256, t, frames, duration_ms, summary, "
                "concepts, note, hits, last_seen) VALUES(?,?,?,?,?,?,?,COALESCE("
                "(SELECT hits FROM sticker_cache WHERE sha256=?),1),?)",
                (sha256, now_iso(), plan.count, plan.duration_ms, clip(summary, 800),
                 str(concepts), note, sha256, now_iso()),
            )
        except Exception:
            pass

    def annotate(self, sha256: str, note: str) -> bool:
        """人工批注（老大教她认表情的入口）。"""
        if not sha256 or not note:
            return False
        try:
            db.ex("UPDATE sticker_cache SET note=? WHERE sha256=?", (clip(note, 200), sha256))
            return True
        except Exception:
            return False


def describe(plan: AnimationPlan) -> str:
    """给提示词用的帧清单（帧号 + 时间戳 + 文件路径）。"""
    lines = [f"这是同一张动图抽出来的 {plan.count} 帧（原图共 {plan.duration_ms}ms）"]
    for frame in plan.frames:
        lines.append(f"- {frame.label}：{frame.path}")
    if plan.sheet_path:
        lines.append(f"- 也有一张拼好的标注图：{plan.sheet_path}")
    return "\n".join(lines)
