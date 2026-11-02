# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""主窗口（v13）：左侧导航 + 主页/详情内容区 + 底部一行状态。

界面结构全部交给 Shell（左导航 216px + 内容区），这里只负责：
  · 窗口尺寸（按 1080p 屏给默认 1360×880，最小 1120×720）
  · 应用图标（data/ui/assets/小鱼.ico）
  · 每 2 秒的情绪/活动刷新，每 15 秒的面板刷新
  · 调试对话（Ctrl+Shift+D，默认隐藏；正式对话在 QQ）
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QVBoxLayout, QWidget,
)

from core import db, settings
from core.cognition import loop
from core.emotion import vector
from ui.chat import ChatPanel
from ui.shell import Shell


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("小鱼")
        for name in ("小鱼.ico", "大肥鱼.ico"):
            icon_path = settings.ASSETS / name
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
                break

        size = settings.app().get("window", {})
        want_w = int(size.get("width", 1360))
        want_h = int(size.get("height", 880))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            width = min(want_w, screen.width() - 100)
            height = min(want_h, screen.height() - 90)
            self.setGeometry(screen.left() + (screen.width() - width) // 2,
                             screen.top() + (screen.height() - height) // 2,
                             width, height)
            min_w = min(1120, max(900, screen.width() - 200))
            min_h = min(720, max(620, screen.height() - 220))
        else:
            self.resize(want_w, want_h)
            min_w, min_h = 1120, 720
        self.setMinimumSize(int(min_w), int(min_h))

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 左导航 + 内容区 ──
        self.shell = Shell()
        self.shell.on_repair = self.repair
        self.shell.on_update = self.check_update
        outer.addWidget(self.shell, 1)

        # ── 调试对话（默认收起） ──
        self.chat = ChatPanel()
        self.chat.hide()
        outer.addWidget(self.chat)
        try:
            from PySide6.QtGui import QKeySequence, QShortcut

            self._chat_shortcut = QShortcut(QKeySequence("Ctrl+Shift+D"), self)
            self._chat_shortcut.activated.connect(self.toggle_chat)
        except Exception:
            self._chat_shortcut = None

        # ── 底部状态条 ──
        bar = QFrame()
        bar.setObjectName("StatusBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(20, 5, 20, 7)
        bar_layout.setSpacing(10)
        self._dot = QLabel("●")
        self._dot.setObjectName("StatusDot")
        bar_layout.addWidget(self._dot)
        self._status = QLabel("正在读取她的状态…")
        self._status.setObjectName("Hint")
        bar_layout.addWidget(self._status, 1)
        self._chat_hint = QLabel("Ctrl+Shift+D 调试对话")
        self._chat_hint.setObjectName("StatusHint")
        bar_layout.addWidget(self._chat_hint)
        outer.addWidget(bar)

        self.fast = QTimer(self)
        self.fast.timeout.connect(self._refresh_live)
        self.fast.start(2000)
        self.slow = QTimer(self)
        self.slow.timeout.connect(self._refresh_slow)
        self.slow.start(15000)
        self._refresh_live()
        self._refresh_slow()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_centered", False):
            self._centered = True
            screen = self.screen().availableGeometry() if self.screen() else None
            if screen is not None:
                geo = self.frameGeometry()
                geo.moveCenter(screen.center())
                self.move(max(screen.left(), geo.left()), max(screen.top(), geo.top()))

    # ── 调试对话 ──
    def toggle_chat(self) -> bool:
        visible = not self.chat.isVisible()
        self.chat.setVisible(visible)
        if visible:
            self.chat.setMinimumHeight(240)
        self._status.setText("调试对话已打开（仅本地，不影响 QQ）" if visible
                             else "调试对话已收起")
        return visible

    # ── 修复 / 更新 ──
    def repair(self) -> None:
        text = self.shell.repair()
        box = QMessageBox(self)
        box.setWindowTitle("修复自检")
        box.setText("自检结果")
        box.setDetailedText(text)
        box.setIcon(QMessageBox.Information if "需要看" not in text else QMessageBox.Warning)
        box.exec()

    def check_update(self) -> None:
        text = self.shell.check_update()
        box = QMessageBox(self)
        box.setWindowTitle("检查更新")
        box.setText(text)
        box.exec()

    # ── 刷新 ──
    def _refresh_live(self) -> None:
        try:
            st = vector.status()
        except Exception as exc:  # noqa: BLE001
            self._status.setText(f"情绪读取失败：{exc}")
            return
        activity = loop.status()
        state, caption = self._avatar_state(st, activity)
        hint = (f"主导情绪：{st['dominant_cn']}｜正面 {st['positive']:.0f} 负面 {st['negative']:.0f} "
                f"害羞 {st['values']['shyness']:.0f}｜保护中：{', '.join(st['protected']) or '无'}")
        self.shell.set_avatar(state, caption, hint)
        self._status.setText(
            f"她在：{activity.get('note') or activity.get('kind', 'idle')}｜"
            f"DB {db.stats().get('memory_item', 0)} 条记忆｜{settings.ROOT}")

    def _refresh_slow(self) -> None:
        try:
            self.shell.refresh_all()
        except Exception:
            pass

    def _avatar_state(self, st: dict, activity: dict) -> tuple[str, str]:
        kind = str(activity.get("kind") or "")
        if kind in ("learn", "study"):
            return "study", "在学东西"
        if kind in ("open_thought", "task"):
            return "think", "在想事"
        try:
            from core.refusal import store as refusal_store
            from core.text import parse_iso

            rows = refusal_store.recent(1)
            if rows:
                when = parse_iso(rows[0].get("t") or "")
                fresh = when and (datetime.now() - when).total_seconds() < 180
                if fresh and rows[0]["decision"] in ("REFUSE", "DEFLECT"):
                    return "refuse", "刚拒绝了"
                if fresh and rows[0]["decision"] == "RELUCTANT_ACCEPT":
                    return "reluctant", "嘴上不情愿"
        except Exception:
            pass
        if st["behavior"]["negative_high"]:
            return "sad", "不想说话"
        if st["behavior"]["shy_high"]:
            return "shy", "有点不好意思"
        if st["behavior"]["positive_high"]:
            return "lively", "心情不错"
        return "calm", "平静"
