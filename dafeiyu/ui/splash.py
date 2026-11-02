# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""开屏（v12 3.1）：黑屏淡入手写体金句，3 秒后自动消失。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QVBoxLayout, QWidget

import core_ethos


class SoulSplash(QWidget):
    def __init__(self, seconds: float = 3.0, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)

        self.label = QLabel(core_ethos.DISPLAY_QUOTE)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)
        font = QFont("楷体", 20)
        font.setItalic(True)
        self.label.setFont(font)
        self.label.setStyleSheet("color: #d8e8ff;")
        layout.addWidget(self.label)

        self.opacity = QGraphicsOpacityEffect(self.label)
        self.label.setGraphicsEffect(self.opacity)
        self.opacity.setOpacity(0.0)

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            width = min(760, int(geo.width() * 0.6))
            self.setGeometry(geo.center().x() - width // 2, geo.center().y() - 110, width, 220)

        self.anim = QPropertyAnimation(self.opacity, b"opacity", self)
        self.anim.setDuration(int(seconds * 1000))
        self.anim.setStartValue(0.0)
        self.anim.setKeyValueAt(0.35, 1.0)
        self.anim.setKeyValueAt(0.8, 1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.InOutSine)
        self.anim.finished.connect(self.close)

    def play(self) -> None:
        self.show()
        self.raise_()
        self.anim.start(QPropertyAnimation.DeleteWhenStopped)

    def paintEvent(self, event):  # noqa: N802
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(7, 12, 22, 238))
        painter.end()