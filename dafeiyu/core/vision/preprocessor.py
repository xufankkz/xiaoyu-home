# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""视觉预处理：把任意图片变成「带坐标与标签的切块 + 全局缩略图 + manifest」。

为什么要有这一层：视觉模型会把输入压到 ~800×800，4000×3000 的图细节全丢。
所以先按 800×800（overlap 40）切块，逐块识别，再按坐标拼回结构化答案。
小图（最长边 ≤ 800）不切，省一次折腾。

依赖只用到 PySide6 的 QImage（已随界面装好，headless 下不依赖 QApplication），
不额外引 Pillow —— 少一个依赖，少一次装不上的风险。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader

from .. import settings

# Qt 单张图片解码有 256MB 上限；提到 512MB，再大的就按解码尺寸缩放，别整条链路崩掉。
# 注意：这个静态方法在 QImageReader 上（QImage 没有），名字一样容易写错。
MAX_DECODE_BYTES = 512 * 1024 * 1024
try:
    QImageReader.setAllocationLimit(512)
except Exception:
    pass

POS = ("左", "中", "右")
ROWS = ("上", "中", "下")


def cfg() -> dict:
    return settings.load("vision")


def _label(col: int, row: int, cols: int, rows: int) -> str:
    cx = 1 if cols <= 1 else round(col * 2 / (cols - 1))
    cy = 1 if rows <= 1 else round(row * 2 / (rows - 1))
    base = f"{POS[min(2, max(0, cx))]}{ROWS[min(2, max(0, cy))]}"
    if cols * rows <= 1:
        return "整体"
    return f"{base}({row + 1}/{rows}-{col + 1}/{cols})"


@dataclass
class Tile:
    index: int
    path: str
    x: int
    y: int
    w: int
    h: int
    label: str
    col: int
    row: int
    sampled: bool = False

    def as_dict(self) -> dict:
        return {"i": self.index, "file": self.path, "x": self.x, "y": self.y,
                "w": self.w, "h": self.h, "label": self.label,
                "col": self.col, "row": self.row, "sampled": self.sampled}


@dataclass
class VisionPlan:
    image_path: str
    width: int
    height: int
    tiled: bool
    tile_size: int
    overlap: int
    tiles: list[Tile] = field(default_factory=list)
    thumb: str = ""
    work_dir: str = ""
    manifest: dict = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.tiles)


class VisionPreprocessor:
    def __init__(self, *, tile: int | None = None, overlap: int | None = None,
                 max_tiles: int | None = None, small_skip: int | None = None,
                 thumb_edge: int | None = None, quality: int | None = None,
                 work_dir: str | Path | None = None):
        c = cfg()
        self.tile = int(tile or c.get("tile", 800))
        self.overlap = int(overlap if overlap is not None else c.get("overlap", 40))
        self.max_tiles = int(max_tiles or c.get("max_tiles", 64))
        self.small_skip = int(small_skip or c.get("small_skip", 800))
        self.thumb_edge = int(thumb_edge or c.get("thumb_edge", 1024))
        self.quality = int(quality or c.get("jpeg_quality", 88))
        self.work_root = Path(work_dir) if work_dir else (settings.ROOT / str(c.get("work_dir", "memory\\instant\\vision")))

    def _load(self, src: Path) -> tuple[QImage, dict]:
        """读图；超过解码上限的超大图先按比例缩小再读。"""
        reader = QImageReader(str(src))
        reader.setAutoTransform(True)
        size = reader.size()
        note: dict = {}
        if size.isValid():
            pixels = size.width() * size.height()
            if pixels * 4 > MAX_DECODE_BYTES:
                scale = (MAX_DECODE_BYTES / (pixels * 4)) ** 0.5
                scaled = QSize(max(1, int(size.width() * scale)), max(1, int(size.height() * scale)))
                reader.setScaledSize(scaled)
                note = {"downscaled_from": [size.width(), size.height()],
                        "downscaled_to": [scaled.width(), scaled.height()]}
        image = reader.read()
        if image.isNull():
            raise ValueError(f"读不出这张图（可能超过解码上限）：{src}")
        return image, note

    # ── 切块 ──
    def prepare(self, image_path: str | Path, *, label: str = "") -> VisionPlan:
        src = Path(image_path)
        if not src.exists():
            raise FileNotFoundError(str(src))
        image, note = self._load(src)
        if image.format() != QImage.Format_RGB32:
            image = image.convertToFormat(QImage.Format_RGB32)
        width, height = image.width(), image.height()
        stem = src.stem[:40]
        out_dir = self.work_root / f"{stem}-{abs(hash(str(src))) % 100000}"
        out_dir.mkdir(parents=True, exist_ok=True)

        tiled = max(width, height) > self.small_skip
        step = max(1, self.tile - max(0, self.overlap))

        if not tiled:
            boxes = [(0, 0, width, height)]
            cols = rows = 1
        else:
            xs = list(range(0, max(1, width - self.tile + 1), step))
            ys = list(range(0, max(1, height - self.tile + 1), step))
            if not xs or xs[-1] + self.tile < width:
                xs.append(max(0, width - self.tile))
            if not ys or ys[-1] + self.tile < height:
                ys.append(max(0, height - self.tile))
            boxes = [(x, y, x + self.tile, y + self.tile) for y in ys for x in xs]
            cols, rows = len(xs), len(ys)

        # 太多就等距抽样（保留首尾），并在 manifest 里标 sampled
        sampled = False
        if len(boxes) > self.max_tiles:
            keep = [round(i * (len(boxes) - 1) / (self.max_tiles - 1)) for i in range(self.max_tiles)]
            boxes = [boxes[i] for i in dict.fromkeys(keep)]
            sampled = True

        tiles: list[Tile] = []
        for index, (x0, y0, x1, y1) in enumerate(boxes):
            crop = image.copy(x0, y0, min(x1, width) - x0, min(y1, height) - y0)
            file = out_dir / f"tile_{index:03d}.jpg"
            crop.save(str(file), "JPG", self.quality)
            col = index % cols
            row = index // cols if cols else 0
            tiles.append(Tile(index=index, path=str(file), x=x0, y=y0,
                              w=crop.width(), h=crop.height(),
                              label=_label(col, row, cols, rows),
                              col=col, row=row, sampled=sampled))

        # 全局缩略图：给模型一个"整张长什么样"的锚
        thumb_path = ""
        if tiled:
            thumb = image.scaled(self.thumb_edge, self.thumb_edge,
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
            thumb_file = out_dir / "thumb.jpg"
            thumb.save(str(thumb_file), "JPG", self.quality)
            thumb_path = str(thumb_file)

        plan = VisionPlan(
            image_path=str(src), width=width, height=height, tiled=tiled,
            tile_size=self.tile, overlap=self.overlap, tiles=tiles,
            thumb=thumb_path, work_dir=str(out_dir),
        )
        plan.manifest = {
            "image": str(src), "label": label, "width": width, "height": height,
            "tiled": tiled, "tile": self.tile, "overlap": self.overlap,
            "cols": cols, "rows": rows, "count": len(tiles), "sampled": sampled,
            "thumb": thumb_path,
            "tiles": [t.as_dict() for t in tiles],
        }
        plan.manifest.update(note)
        (out_dir / "manifest.json").write_text(
            json.dumps(plan.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan

    # ── 聚合 ──
    def aggregate(self, plan: VisionPlan, results: list[dict]) -> dict:
        """把每块的识别结果按坐标拼成结构化答案。"""
        by_index = {int(r.get("i", r.get("index", -1))): r for r in results or []}
        ordered = sorted(plan.tiles, key=lambda t: (t.row, t.col))
        blocks = []
        for tile in ordered:
            hit = by_index.get(tile.index) or {}
            text = str(hit.get("text") or "").strip()
            if not text:
                continue
            blocks.append({"i": tile.index, "label": tile.label, "rect": [tile.x, tile.y, tile.w, tile.h],
                           "text": text, "confidence": float(hit.get("confidence") or 0.5)})
        summary_parts = []
        for block in blocks:
            tag = f"{block['label']}"
            if block["label"] not in summary_parts[-1:]:
                pass
            summary_parts.append(f"【{tag}】{block['text']}")
        return {
            "tiled": plan.tiled,
            "width": plan.width,
            "height": plan.height,
            "tiles": plan.count,
            "blocks": blocks,
            "summary": "\n".join(summary_parts),
            "coverage": round(len(blocks) / max(1, plan.count), 3),
        }


def describe_positions(plan: VisionPlan) -> str:
    """给提示词用的位置清单（让模型知道自己在看哪一块）。"""
    lines = []
    for tile in plan.tiles:
        lines.append(f"- 第{tile.index}块【{tile.label}】{Path(tile.path).name} "
                     f"({tile.x},{tile.y}) {tile.w}×{tile.h}"
                     + ("（抽样）" if tile.sampled else ""))
    return "\n".join(lines)
