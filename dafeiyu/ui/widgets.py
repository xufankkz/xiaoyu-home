# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""通用小部件：可折叠段落、卡片、迷你条、情绪曲线。"""

from __future__ import annotations

import json
import math

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core import settings

STATE_FILE = "sidebar.json"


def _load_state() -> dict:
    return settings.read_state(STATE_FILE, {})


def _save_state(data: dict) -> None:
    settings.write_state(STATE_FILE, data)


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)


def title_label(text: str, size: int = 20) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Title")
    font = label.font()
    font.setPointSize(size)
    font.setBold(True)
    label.setFont(font)
    return label


def hint_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


class Collapsible(QWidget):
    """可折叠段落：点标题展开/收起，状态持久化（重启后保持上次展开）。"""

    toggled = Signal(bool)

    def __init__(self, title: str, key: str, *, depth: int = 0, parent=None):
        super().__init__(parent)
        self.key = key
        self.depth = depth
        self._built = False
        self._builder = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = QPushButton(self)
        self.header.setObjectName("Section" if depth == 0 else "Sub")
        self.header.setCheckable(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setText(f"{'▪' if depth == 0 else '·'}  {title}")
        self.header.clicked.connect(self._on_click)
        layout.addWidget(self.header)

        self.body = QWidget(self)
        self.body.setObjectName("SectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(10 if depth == 0 else 22, 2, 6, 8)
        self.body_layout.setSpacing(6)
        self.body.setVisible(False)
        layout.addWidget(self.body)

        state = _load_state()
        expanded = bool(state.get(key, depth == 0 and False))
        self.header.setChecked(expanded)
        self.body.setVisible(expanded)
        self._sync_arrow(expanded)

    def set_builder(self, builder) -> "Collapsible":
        """懒构建内容（展开时才真正查库）。"""
        self._builder = builder
        if self.header.isChecked():
            self._ensure_built()
        return self

    def _ensure_built(self) -> None:
        if self._built or self._builder is None:
            return
        widget = self._builder(self.body)
        if widget is not None:
            self.body_layout.addWidget(widget)
        self._built = True

    def refresh(self) -> None:
        if not self._built:
            return
        for i in range(self.body_layout.count()):
            item = self.body_layout.itemAt(i).widget()
            if item is not None and hasattr(item, "refresh"):
                item.refresh()

    def _on_click(self) -> None:
        expanded = self.header.isChecked()
        self.body.setVisible(expanded)
        self._sync_arrow(expanded)
        if expanded:
            self._ensure_built()
            self.refresh()
        state = _load_state()
        state[self.key] = expanded
        _save_state(state)
        self.toggled.emit(expanded)

    def _sync_arrow(self, expanded: bool) -> None:
        text = self.header.text()
        base = text[3:] if text[:3] in ("▸  ", "▾  ") else text
        self.header.setText(("▾  " if expanded else "▸  ") + base.lstrip("▪· "))

    def expand(self, flag: bool = True) -> None:
        self.header.setChecked(flag)
        self._on_click()


class MiniBar(QWidget):
    """一条 0~100 的情绪刻度。"""

    def __init__(self, label: str, color: str = "#3b82f6", parent=None):
        super().__init__(parent)
        self.label = label
        self.value = 0.0
        self.color = QColor(color)
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, value: float) -> None:
        self.value = max(0.0, min(100.0, float(value)))
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        painter.setPen(QPen(QColor("#859cbd")))
        font = QFont(self.font())
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(0, 0, 46, height - 8, Qt.AlignVCenter | Qt.AlignLeft, self.label)

        track = QPainterPath()
        x0, y0, w, h = 52, height / 2 - 4, width - 52 - 44, 8
        track.addRoundedRect(x0, y0, w, h, 4, 4)
        painter.fillPath(track, QColor("#17243a"))

        filled = QPainterPath()
        filled.addRoundedRect(x0, y0, max(0.0, w * self.value / 100.0), h, 4, 4)
        painter.fillPath(filled, self.color)

        painter.setPen(QPen(QColor("#d9e6f8")))
        painter.drawText(x0 + w + 4, 0, 40, height - 8, Qt.AlignVCenter | Qt.AlignRight,
                         f"{int(round(self.value))}")


class Sparkline(QWidget):
    """情绪历史曲线（按天）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.series: list[dict] = []
        self.setMinimumHeight(120)

    def set_series(self, rows: list[dict]) -> None:
        self.series = rows or []
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), QColor("#101a2a"))

        dims = [("joy", "#4ade80"), ("happiness", "#38bdf8"), ("sadness", "#f87171"),
                ("inferiority", "#a78bfa"), ("shyness", "#f472b6")]
        if not self.series:
            painter.setPen(QPen(QColor("#5b6b7d")))
            painter.drawText(self.rect(), Qt.AlignCenter, "还没有情绪历史")
            return

        pad = 14
        rows = self.series[-30:]
        n = len(rows)
        for dim, color in dims:
            path = QPainterPath()
            for i, row in enumerate(rows):
                value = float(row.get(dim) or 0.0)
                x = pad + (width - 2 * pad) * (i / max(1, n - 1))
                y = height - pad - (height - 2 * pad) * (value / 100.0)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(QColor(color), 2))
            painter.drawPath(path)

        painter.setPen(QPen(QColor("#5b6b7d")))
        font = QFont(self.font())
        font.setPointSize(8)
        painter.setFont(font)
        if rows:
            painter.drawText(pad, height - 2, str(rows[0].get("day", "")))
            painter.drawText(width - pad - 70, height - 2, str(rows[-1].get("day", "")))


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color:#1e2c43; background:#1e2c43; max-height:1px;")
    return line


class Stat(QWidget):
    """一行「名称：值」。"""

    def __init__(self, name: str, value: str = "—", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        self.name = QLabel(name)
        self.name.setStyleSheet("color:#859cbd; font-size:12px;")
        self.value = QLabel(value)
        self.value.setStyleSheet("color:#d9e6f8; font-size:12px;")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.name)
        layout.addStretch(1)
        layout.addWidget(self.value)

    def set(self, value: str) -> None:
        self.value.setText(value)


def smooth_pulse(t: float, period: float = 4.0) -> float:
    return 0.5 + 0.5 * math.sin(2 * math.pi * t / period)
