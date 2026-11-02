# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""语言系统 / 情绪系统面板：字数、上限、冷却倒计时、停止学习、询问更新入口。"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.entity import Layer
from core.language import memory as langmem
from ui.widgets import hint_label


class BookPanel(QWidget):
    def __init__(self, layer: str, parent=None):
        super().__init__(parent)
        self.layer = layer
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.head = QLabel("—")
        self.head.setObjectName("Title")
        layout.addWidget(self.head)

        self.detail = hint_label("")
        layout.addWidget(self.detail)

        self.ask_label = hint_label("")
        layout.addWidget(self.ask_label)

        row = QHBoxLayout()
        self.yes = QPushButton("要更新")
        self.yes.setObjectName("Primary")
        self.yes.clicked.connect(lambda: self._answer(True))
        self.no = QPushButton("再等等")
        self.no.clicked.connect(lambda: self._answer(False))
        row.addWidget(self.yes)
        row.addWidget(self.no)
        row.addStretch(1)
        layout.addLayout(row)

        self.hint = hint_label("")
        layout.addWidget(self.hint)

    def _answer(self, yes: bool) -> None:
        out = langmem.answer(self.layer, yes)
        self.hint.setText(str(out.get("message") or ""))
        self.refresh()

    def refresh(self) -> None:
        st = langmem.status(self.layer)
        self.head.setText(f"{st['title']}：{st['chars']} / {st['trigger']} 字")
        state = "冷却中" if st["locked"] else "可以更新"
        self.detail.setText(
            f"单次上限 {st['record_max']} 字｜目标压缩 {st['target']}±{st['tolerance']}｜{st['count']} 条\n"
            f"状态：{state}（剩 {st['days_left']} 天）｜停止学习：{'是' if st['locked'] else '否'}"
        )
        due = st.get("ask") or {}
        if due.get("due"):
            self.ask_label.setText(f"⏰ {due.get('question')}\n{due.get('detail')}")
            self.yes.setVisible(True)
            self.no.setVisible(True)
        else:
            self.ask_label.setText("")
            self.yes.setVisible(False)
            self.no.setVisible(False)
