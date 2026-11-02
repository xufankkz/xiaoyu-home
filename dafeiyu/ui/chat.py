# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""聊天窗口：跟大肥鱼直接在桌面上说话。

所有回复都走 cognition.dialogue（同一套情绪/记忆/策略），不是另开一条逻辑。
主动消息（碎碎念）也在这里以气泡出现，不弹窗打断。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from core.repo import system as sr
from core.text import clip


class _ReplyWorker(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, text: str, conv: str, parent=None):
        super().__init__(parent)
        self.text = text
        self.conv = conv

    def run(self) -> None:
        try:
            from core.cognition import dialogue

            out = dialogue.handle_owner_message(self.text, conv=self.conv)
            self.done.emit(out or {})
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class Bubble(QFrame):
    def __init__(self, text: str, role: str, meta: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("BubbleMe" if role == "me" else "BubbleHer")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        label = QLabel(text)
        label.setObjectName("BubbleText")
        label.setWordWrap(True)
        label.setMaximumWidth(520)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)
        if meta:
            foot = QLabel(meta)
            foot.setObjectName("BubbleMeta")
            layout.addWidget(foot)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)


class ChatPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._shown_chitchat: set[str] = set()
        self._worker: _ReplyWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("跟大肥鱼说话")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch(1)
        self.status = QLabel("—")
        self.status.setObjectName("Hint")
        head.addWidget(self.status)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(160)
        holder = QWidget()
        self.list_layout = QVBoxLayout(holder)
        self.list_layout.setContentsMargins(4, 4, 4, 4)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("跟她说点什么…（Enter 发送）")
        self.input.returnPressed.connect(self.send)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("Primary")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        root.addLayout(row)

        self.load_history()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_proactive)
        self.timer.start(4000)

    # ── 气泡 ──
    def add_bubble(self, text: str, role: str, meta: str = "") -> None:
        bubble = Bubble(text, role, meta)
        align = Qt.AlignRight if role == "me" else Qt.AlignLeft
        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        if role == "me":
            wrapper.addStretch(1)
            wrapper.addWidget(bubble)
        else:
            wrapper.addWidget(bubble)
            wrapper.addStretch(1)
        holder = QWidget()
        holder.setLayout(wrapper)
        self.list_layout.insertWidget(self.list_layout.count() - 1, holder)
        QTimer.singleShot(30, self._scroll_bottom)

    def _scroll_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def load_history(self) -> None:
        for row in sr.dialogue_recent(20):
            role = "me" if row.get("role") == "owner" else "her"
            stamp = str(row.get("t") or "")[11:16]
            self.add_bubble(row.get("text", ""), role, stamp)

    # ── 发送 ──
    def send(self) -> None:
        text = self.input.text().strip()
        if not text or self._worker is not None:
            return
        self.input.clear()
        self.add_bubble(text, "me", datetime.now().strftime("%H:%M"))
        self.status.setText("她正在想…")
        self.send_btn.setEnabled(False)
        self._worker = _ReplyWorker(text, "private:owner", self)
        self._worker.done.connect(self._on_reply)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_reply(self, out: dict) -> None:
        self.send_btn.setEnabled(True)
        self._worker = None
        reply = (out or {}).get("reply") or ""
        policy = (out or {}).get("policy") or {}
        if reply:
            meta = f"{datetime.now():%H:%M}"
            if policy.get("reason"):
                meta += f" · 语气：{policy.get('tone')}"
            self.add_bubble(reply, "her", meta)
            self.status.setText(f"语气：{policy.get('tone', '—')}")
        else:
            self.status.setText(f"没回你（{policy.get('reason', '不想说话')}）")
        intents = (out or {}).get("new_intents") or []
        if intents:
            self.status.setText(self.status.text() + f" · 记下待学：{'、'.join(intents[:2])}")

    def _on_failed(self, message: str) -> None:
        self.send_btn.setEnabled(True)
        self._worker = None
        self.status.setText(f"出错：{clip(message, 60)}")

    # ── 主动消息 ──
    def poll_proactive(self) -> None:
        try:
            rows = sr.chitchat_recent(3)
        except Exception:
            return
        for row in reversed(rows):
            cid = str(row.get("id"))
            if cid in self._shown_chitchat:
                continue
            self._shown_chitchat.add(cid)
            t = str(row.get("t") or "")
            if t and t < datetime.now().strftime("%Y-%m-%d %H:%M:%S")[:13]:
                # 太老的不用冒出来
                if t[:13] < datetime.now().strftime("%Y-%m-%d %H"):
                    continue
            self.add_bubble(row.get("content", ""), "her", "自己冒出来的一句")
