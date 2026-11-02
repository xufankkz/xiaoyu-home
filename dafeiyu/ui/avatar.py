# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""形象层：大鲸鱼桌宠（DS 娘）立绘 + 情绪状态表达。

三块：
  HeroAvatar      —— 首页大立绘（清晰半身像 + 呼吸/光晕/状态叠加 + 状态胶囊）
  StateAvatar     —— 详情页左侧小桌宠（六个表情的原生尺寸，缩到 ~150px 不糊）
  PetWidget       —— 摸摸头 gif（QMovie 播放；gif 缺失时退回静态 logo）

资源位置：`data/ui/assets`（settings.ASSETS）优先，其次 `ui/assets`。
丢一张新图进去就会自动替换：
  xiaoyu_portrait.png   主立绘（透明 PNG，建议 ≥600px）
  xiaoyu_thought.png    「思考」时用的那张（可无）
  states/<状态>.png     六/八种表情（calm/lively/sad/shy/think/study/reluctant/refuse）
  pet_pat.gif           桌宠小动画（可无）
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QMovie, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

try:  # 资源目录以 settings 为准（data/ui/assets）；独立跑时退回同目录 assets
    from core import settings as _settings

    ASSETS = Path(_settings.ASSETS)
except Exception:  # pragma: no cover - 仅在没有 core 时用
    _settings = None  # type: ignore[assignment]
    ASSETS = Path(__file__).resolve().parent / "assets"

LEGACY_ASSETS = Path(__file__).resolve().parent / "assets"

STATE_ORDER = ["lively", "calm", "sad", "shy", "think", "study", "reluctant", "refuse"]
STATES: dict[str, tuple[str, str]] = {
    "lively": ("活泼", "#5ee9a4"),
    "calm": ("平静", "#7cc7ff"),
    "sad": ("失落", "#9aa9c2"),
    "shy": ("害羞", "#f7a6c8"),
    "think": ("思考", "#a9b6ff"),
    "study": ("学习", "#f5c451"),
    "reluctant": ("不情愿", "#ffab6b"),
    "refuse": ("拒绝", "#ff8181"),
}

# 立绘/表情的候选文件名（新名优先，兼容老名字）
PORTRAIT_NAMES = ("xiaoyu_portrait.png", "大肥鱼-big.png", "大肥鱼.png", "portrait.png")
THOUGHT_NAMES = ("xiaoyu_thought.png", "thought.png")
PET_NAMES = ("pet.gif", "pet.webp", "大肥鱼.gif")


def _asset_dirs() -> list[Path]:
    dirs = [ASSETS, LEGACY_ASSETS]
    out: list[Path] = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def find_asset(relative: str) -> Path | None:
    for base in _asset_dirs():
        path = base / relative
        if path.exists():
            return path
    return None


def load_pixmap(relative: str) -> QPixmap | None:
    path = find_asset(relative)
    if path is None:
        return None
    pix = QPixmap(str(path))
    return pix if not pix.isNull() else None


def _first_pixmap(names) -> QPixmap | None:
    for name in names:
        pix = load_pixmap(name)
        if pix is not None:
            return pix
    return None


def _draw_caption(painter: QPainter, rect: QRectF, text: str, color: str,
                  *, font: QFont, opacity: float = 1.0) -> None:
    """底部状态胶囊：圆角底 + 状态色描边 + 圆点。"""
    painter.save()
    painter.setOpacity(opacity)
    f = QFont(font)
    f.setPointSizeF(max(9.0, font.pointSizeF()))
    f.setBold(True)
    painter.setFont(f)
    metrics = painter.fontMetrics()
    pad_x = 16.0
    text_w = metrics.horizontalAdvance(text)
    box_w = min(rect.width(), text_w + pad_x * 2 + 14)
    box = QRectF(rect.center().x() - box_w / 2, rect.center().y() - 15, box_w, 30)

    c = QColor(color)
    pill = QPainterPath()
    pill.addRoundedRect(box, 15, 15)
    painter.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 130), 1.0))
    painter.setBrush(QColor(11, 18, 32, 190))
    painter.drawPath(pill)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(c.red(), c.green(), c.blue(), 210))
    painter.drawEllipse(QPointF(box.left() + 14, box.center().y()), 3.4, 3.4)

    painter.setPen(QPen(QColor(color)))
    painter.drawText(QRectF(box.left() + 22, box.top(), box.width() - 26, box.height()),
                     Qt.AlignCenter, text)
    painter.restore()


class HeroAvatar(QWidget):
    """首页主立绘：呼吸、浮动、光晕、水波、状态叠加。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 340)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.state = "calm"
        self.caption = ""
        self.t = 0.0
        self._portrait = _first_pixmap(PORTRAIT_NAMES)
        self._thought = _first_pixmap(THOUGHT_NAMES)
        self._state_pix: dict[str, QPixmap] = {}
        for key in STATES:
            pix = load_pixmap(f"states/{key}.png")
            if pix is not None:
                self._state_pix[key] = pix
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

    # ── 状态 ──
    def set_state(self, state: str, caption: str = "") -> None:
        self.state = state if state in STATES else "calm"
        self.caption = caption or ""
        self.update()

    def _tick(self) -> None:
        self.t += 0.05
        if self.isVisible():
            self.update()

    def _hero_pixmap(self) -> QPixmap | None:
        if self.state == "think" and self._thought is not None:
            return self._thought
        if self._portrait is not None:
            return self._portrait
        return self._state_pix.get(self.state)

    # ── 绘制 ──
    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        width, height = self.width(), self.height()
        rect = QRectF(0, 0, width, height)
        label, color = STATES[self.state]
        base = QColor(color)

        self._background(painter, rect, base)
        self._water_rings(painter, rect, base)
        self._bubbles(painter, rect)

        pix = self._hero_pixmap()
        if pix is None or pix.isNull():
            painter.setPen(QPen(QColor("#8ea6c6")))
            painter.drawText(rect, Qt.AlignCenter,
                             "（找不到立绘：data/ui/assets/xiaoyu_portrait.png）")
            return

        breath = 1.0 + 0.012 * math.sin(self.t * 1.35)
        bob = 4.0 * math.sin(self.t * 0.75)
        side = min(width * 0.80, height * 0.87)
        scale = side / max(pix.width(), pix.height())
        draw_w = pix.width() * scale * breath
        draw_h = pix.height() * scale * breath
        x = (width - draw_w) / 2
        y = (height - draw_h) / 2 + bob - height * 0.03

        if self.state in ("sad", "reluctant"):
            painter.setOpacity(0.88)
        painter.drawPixmap(QRectF(x, y, draw_w, draw_h), pix, QRectF(pix.rect()))
        painter.setOpacity(1.0)

        # 立绘下缘淡入卡片底色：别让「半身像被切一刀」露出来
        fade_top = min(height - 8.0, y + draw_h * 0.80)
        fade = QLinearGradient(QPointF(0, fade_top), QPointF(0, height))
        fade.setColorAt(0.0, QColor(13, 22, 38, 0))
        fade.setColorAt(0.72, QColor(13, 22, 38, 190))
        fade.setColorAt(1.0, QColor(13, 22, 38, 245))
        painter.setPen(Qt.NoPen)
        painter.setBrush(fade)
        painter.drawRect(QRectF(0, fade_top, width, height - fade_top))

        # 「思考」那张立绘自带思考气泡，就别再叠一个程序化的
        using_thought = pix is self._thought
        self._overlay(painter, x, y, draw_w, draw_h, with_bubble=not using_thought)
        text = label
        if self.caption:
            text += f" · {self.caption}"
        font = QFont(self.font())
        font.setPointSizeF(max(10.0, font.pointSizeF()))
        pill_top = min(height - 46.0, y + draw_h + 4.0)
        pill_top = max(pill_top, height - 120.0)
        _draw_caption(painter, QRectF(0, pill_top, width, 30), text, color, font=font)
        painter.end()

    def _background(self, painter: QPainter, rect: QRectF, base: QColor) -> None:
        wash = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        wash.setColorAt(0.0, QColor(90, 160, 255, 26))
        wash.setColorAt(0.55, QColor(90, 160, 255, 8))
        wash.setColorAt(1.0, QColor(90, 160, 255, 0))
        painter.fillRect(rect, wash)

        glow = QRadialGradient(QPointF(rect.center().x(), rect.center().y() * 0.98),
                               max(rect.width(), rect.height()) * 0.58)
        glow.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 54))
        glow.setColorAt(0.62, QColor(base.red(), base.green(), base.blue(), 14))
        glow.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        painter.fillRect(rect, glow)

    def _water_rings(self, painter: QPainter, rect: QRectF, base: QColor) -> None:
        """脚下一圈一圈慢慢扩散的水波（鲸鱼的感觉）。"""
        cx = rect.center().x()
        cy = rect.bottom() - rect.height() * 0.115
        painter.save()
        painter.setBrush(Qt.NoBrush)
        for i in range(3):
            phase = (self.t * 0.28 + i / 3.0) % 1.0
            rx = rect.width() * (0.10 + 0.22 * phase)
            ry = rect.height() * (0.018 + 0.040 * phase)
            alpha = int(46 * (1.0 - phase))
            painter.setPen(QPen(QColor(base.red(), base.green(), base.blue(),
                                       max(0, min(255, alpha))), 1.4))
            painter.drawEllipse(QRectF(cx - rx, cy - ry, rx * 2, ry * 2))
        painter.restore()

    def _bubbles(self, painter: QPainter, rect: QRectF) -> None:
        """背景小气泡：上浮+淡出，营造水下感。"""
        painter.save()
        painter.setPen(Qt.NoPen)
        seeds = ((0.14, 0.55, 3.0), (0.24, 0.30, 2.0), (0.78, 0.42, 2.6),
                 (0.86, 0.66, 3.4), (0.66, 0.18, 1.8), (0.08, 0.82, 2.2),
                 (0.92, 0.30, 1.6))
        for index, (fx, fy, r) in enumerate(seeds):
            drift = (self.t * 0.16 + index * 0.19) % 1.0
            py = rect.height() * (1.0 - drift) * 0.92 + rect.height() * 0.04
            px = rect.width() * (fx + 0.02 * math.sin(self.t * 0.8 + index))
            fade = 1.0 - abs(drift - 0.5) * 1.6
            alpha = int(max(0.0, min(1.0, fade)) * 62)
            painter.setBrush(QColor(150, 205, 255, alpha))
            rr = r * (0.9 + 0.1 * math.sin(self.t + index))
            painter.drawEllipse(QPointF(px, py), rr, rr)
        painter.restore()

    def _overlay(self, painter: QPainter, x: float, y: float, w: float, h: float,
                 with_bubble: bool = True) -> None:
        painter.save()
        if self.state == "shy":
            for cx in (0.385, 0.615):
                center = QPointF(x + w * cx, y + h * 0.615)
                grad = QRadialGradient(center, w * 0.085)
                grad.setColorAt(0.0, QColor(247, 166, 200, 160))
                grad.setColorAt(1.0, QColor(247, 166, 200, 0))
                painter.setBrush(grad)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(center, w * 0.085, h * 0.048)
            painter.setBrush(QColor(160, 210, 255, 205))
            drop = QPainterPath()
            dx, dy = x + w * 0.76, y + h * 0.30
            drop.moveTo(dx, dy)
            drop.cubicTo(dx + 7, dy + 10, dx + 6, dy + 20, dx, dy + 20)
            drop.cubicTo(dx - 6, dy + 20, dx - 7, dy + 10, dx, dy)
            painter.drawPath(drop)

        elif self.state == "sad":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(130, 180, 250, 190))
            for cx in (0.43, 0.57):
                center = QPointF(x + w * cx, y + h * 0.60)
                painter.drawEllipse(center, w * 0.010, h * 0.019)
            cloud = QPainterPath()
            cxx, cyy = x + w * 0.5, y + h * 0.09
            cloud.addEllipse(QPointF(cxx - 18, cyy), 18, 12)
            cloud.addEllipse(QPointF(cxx + 2, cyy - 5), 22, 15)
            cloud.addEllipse(QPointF(cxx + 26, cyy + 1), 15, 11)
            painter.setBrush(QColor(150, 168, 194, 105))
            painter.drawPath(cloud.simplified())

        elif self.state == "lively":
            for i, (cx, cy, r) in enumerate(((0.18, 0.20, 4), (0.82, 0.17, 5), (0.88, 0.44, 3),
                                             (0.14, 0.46, 3), (0.72, 0.10, 3))):
                phase = 0.5 + 0.5 * math.sin(self.t * 2.2 + i * 1.7)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 232, 140, int(70 + 150 * phase)))
                px, py = x + w * cx, y + h * cy
                star = QPainterPath()
                star.moveTo(px, py - r * 2)
                star.lineTo(px + r * 0.6, py - r * 0.6)
                star.lineTo(px + r * 2, py)
                star.lineTo(px + r * 0.6, py + r * 0.6)
                star.lineTo(px, py + r * 2)
                star.lineTo(px - r * 0.6, py + r * 0.6)
                star.lineTo(px - r * 2, py)
                star.lineTo(px - r * 0.6, py - r * 0.6)
                star.closeSubpath()
                painter.drawPath(star)

        elif self.state == "think" and with_bubble:
            bx, by = x + w * 0.62, y + h * 0.10
            bubble = QPainterPath()
            bubble.addRoundedRect(QRectF(bx, by, 88, 42), 18, 18)
            painter.setBrush(QColor(20, 32, 52, 225))
            painter.setPen(QPen(QColor(64, 96, 140), 1))
            painter.drawPath(bubble)
            for i in range(3):
                phase = 0.5 + 0.5 * math.sin(self.t * 3.0 - i * 0.8)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(169, 182, 255, int(90 + 140 * phase)))
                painter.drawEllipse(QPointF(bx + 26 + i * 19, by + 21), 4.5, 4.5)

        elif self.state == "study":
            ring = QRectF(x + w * 0.16, y + h * 0.90, w * 0.68, 8)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(245, 196, 81, 60))
            painter.drawRoundedRect(ring, 4, 4)
            phase = 0.5 + 0.5 * math.sin(self.t * 1.7)
            painter.setBrush(QColor(245, 196, 81, int(110 + 90 * phase)))
            painter.drawRoundedRect(QRectF(ring.x(), ring.y(),
                                           ring.width() * (0.40 + 0.40 * phase), 8), 4, 4)
            painter.setBrush(QColor(245, 196, 81, 200))
            painter.drawEllipse(QPointF(ring.right(), ring.center().y()), 3.5, 3.5)

        elif self.state == "reluctant":
            bx, by = x + w * 0.60, y + h * 0.12
            bubble = QPainterPath()
            bubble.addRoundedRect(QRectF(bx, by, 66, 42), 16, 16)
            painter.setBrush(QColor(40, 30, 22, 225))
            painter.setPen(QPen(QColor(255, 171, 107, 210), 1.2))
            painter.drawPath(bubble)
            font = QFont(self.font())
            font.setPointSize(15)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor("#ffc79a")))
            painter.drawText(QRectF(bx, by, 66, 42), Qt.AlignCenter, "哼")

        elif self.state == "refuse":
            cx, cy = x + w * 0.79, y + h * 0.15
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 129, 129, 215))
            painter.drawEllipse(QPointF(cx, cy), 17, 17)
            painter.setPen(QPen(QColor(255, 255, 255), 3.4, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(cx - 6.5, cy - 6.5), QPointF(cx + 6.5, cy + 6.5))
            painter.drawLine(QPointF(cx + 6.5, cy - 6.5), QPointF(cx - 6.5, cy + 6.5))
        painter.restore()


class StateAvatar(QWidget):
    """详情页左侧小桌宠：直接放原生尺寸的表情图，不放大所以不糊。"""

    def __init__(self, parent=None, *, size: int = 150):
        super().__init__(parent)
        self.state = "calm"
        self._size = size
        self.setFixedHeight(size + 12)
        self.setMinimumWidth(size - 10)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._pix: dict[str, QPixmap] = {}
        for key in STATES:
            pix = load_pixmap(f"states/{key}.png")
            if pix is not None:
                self._pix[key] = pix
        self._portrait = _first_pixmap(PORTRAIT_NAMES)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(60)

    def set_state(self, state: str, caption: str = "") -> None:
        self.state = state if state in STATES else "calm"
        self.update()

    def _tick(self) -> None:
        self._t += 0.06
        if self.isVisible():
            self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(self.rect())
        label, color = STATES[self.state]
        base = QColor(color)

        glow = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.55)
        glow.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 46))
        glow.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        painter.fillRect(rect, glow)

        pix = self._pix.get(self.state) or self._portrait
        if pix is None or pix.isNull():
            painter.setPen(QPen(QColor("#8ea6c6")))
            painter.drawText(rect, Qt.AlignCenter, "桌宠图片缺失")
            return
        bob = 2.0 * math.sin(self._t * 0.9)
        target = min(rect.width(), rect.height()) - 8
        scaled = pix.scaled(int(target), int(target), Qt.KeepAspectRatio,
                            Qt.SmoothTransformation)
        painter.drawPixmap(int((rect.width() - scaled.width()) / 2),
                           int((rect.height() - scaled.height()) / 2 + bob), scaled)


def _heart_path(cx: float, cy: float, r: float) -> QPainterPath:
    """一颗小心心（两个三次贝塞尔拼出来的）。"""
    path = QPainterPath()
    path.moveTo(cx, cy + r)
    path.cubicTo(cx - r * 1.5, cy - r * 0.20, cx - r * 0.75, cy - r * 1.35, cx, cy - r * 0.42)
    path.cubicTo(cx + r * 0.75, cy - r * 1.35, cx + r * 1.5, cy - r * 0.20, cx, cy + r)
    return path


class PetWidget(QWidget):
    """小桌宠（活的）：表情图 + 呼吸/蹦跳 + 飘心，点一下会蹦一下。

    `data/ui/assets/pet.gif` 存在时优先用 QMovie 播用户自己放的动图；
    否则用 states/<状态>.png 做程序化小动画（透明底，不挑深浅色）。
    """

    clicked = Signal()

    def __init__(self, parent=None, *, size: int = 112):
        super().__init__(parent)
        self.setObjectName("PetWidget")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(size + 18, size + 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点一下，去看她现在的情绪")

        self._size = size
        self._state = "lively"
        self._t = 0.0
        self._bounce = 0.0
        self._pix: dict[str, QPixmap] = {}
        for key in STATES:
            pix = load_pixmap(f"states/{key}.png")
            if pix is not None:
                self._pix[key] = pix
        self._portrait = _first_pixmap(PORTRAIT_NAMES)

        self._movie: QMovie | None = None
        gif = None
        for name in PET_NAMES:
            gif = find_asset(name)
            if gif is not None:
                break
        if gif is not None:
            self._movie = QMovie(str(gif), parent=self)
            self._movie.setCacheMode(QMovie.CacheAll)
            self._movie.setScaledSize(QSize(size, size))
            self._movie.frameChanged.connect(self.update)
            self._movie.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(45)

    # ── 接口 ──
    def set_state(self, state: str) -> None:
        self._state = state if state in STATES else "lively"
        self.update()

    def set_caption(self, text: str) -> None:
        """兼容旧调用：把文字放进 tooltip，不占用卡片高度。"""
        self.setToolTip(text or "摸摸头")

    def _tick(self) -> None:
        self._t += 0.05
        if self._bounce > 0:
            self._bounce = max(0.0, self._bounce - 0.075)
        if self.isVisible():
            self.update()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._bounce = 1.0
            self.clicked.emit()
        super().mousePressEvent(event)

    # ── 绘制 ──
    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(self.rect())
        base = QColor(STATES[self._state][1])

        glow = QRadialGradient(rect.center(), rect.width() * 0.62)
        glow.setColorAt(0.0, QColor(base.red(), base.green(), base.blue(), 44))
        glow.setColorAt(1.0, QColor(base.red(), base.green(), base.blue(), 0))
        painter.fillRect(rect, glow)

        self._hearts(painter, rect)

        if self._movie is not None and self._movie.isValid():
            frame = self._movie.currentPixmap()
            if not frame.isNull():
                target = self._size
                scaled = frame.scaled(target, target, Qt.KeepAspectRatio,
                                      Qt.SmoothTransformation)
                painter.drawPixmap(int((rect.width() - scaled.width()) / 2),
                                   int((rect.height() - scaled.height()) / 2), scaled)
                return

        pix = self._pix.get(self._state) or self._portrait
        if pix is None or pix.isNull():
            painter.setPen(QPen(QColor("#9db4d0")))
            painter.drawText(rect, Qt.AlignCenter, "桌宠图片缺失")
            return

        breath = 0.5 + 0.5 * math.sin(self._t * 1.7)
        lift = 3.0 * math.sin(self._t * 1.7) + 11.0 * self._bounce
        target = self._size * (1.0 + 0.06 * self._bounce)
        w = target * (1.0 + 0.012 * breath)
        h = target * (1.0 - 0.012 * breath)
        scaled = pix.scaled(int(w), int(h), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap(int((rect.width() - scaled.width()) / 2),
                           int((rect.height() - scaled.height()) / 2 - lift), scaled)

        # 落地影子：蹦起来的时候缩一点
        shadow_w = self._size * (0.52 - 0.10 * self._bounce)
        shadow = QRectF(rect.center().x() - shadow_w, rect.bottom() - 12,
                        shadow_w * 2, 7)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(70 - 25 * self._bounce)))
        painter.drawEllipse(shadow)

    def _hearts(self, painter: QPainter, rect: QRectF) -> None:
        seeds = ((0.24, 0.00, "#f7a6c8"), (0.70, 0.34, "#8fd8ff"),
                 (0.50, 0.62, "#f7a6c8"), (0.82, 0.16, "#a9b6ff"))
        painter.save()
        painter.setPen(Qt.NoPen)
        for index, (fx, offset, color) in enumerate(seeds):
            phase = (self._t * 0.26 + offset) % 1.0
            alpha = int(180 * math.sin(math.pi * phase))
            if alpha <= 4:
                continue
            px = rect.width() * (fx + 0.03 * math.sin(self._t * 1.2 + index))
            py = rect.height() * (0.97 - phase * 0.82)
            radius = 3.0 + 4.0 * phase
            c = QColor(color)
            painter.setBrush(QColor(c.red(), c.green(), c.blue(), alpha))
            painter.drawPath(_heart_path(px, py, radius))
        painter.restore()


# 旧名兼容（老代码里 from ui.avatar import AvatarWidget）
AvatarWidget = HeroAvatar
