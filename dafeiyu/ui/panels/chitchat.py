# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""碎碎念设置面板：开关、频率、疲劳、静默窗口、手动触发。"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from core.cognition import chitchat
from core.emotion import vector
from ui.widgets import hint_label


class _ChitchatWorker(QThread):
    done = Signal(dict)

    def run(self) -> None:
        try:
            out = chitchat.manual(vector.current())
        except Exception as exc:  # noqa: BLE001
            out = {"ok": False, "error": str(exc)}
        self.done.emit(out or {})


class ChitchatPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        self.toggle = QCheckBox("允许她自己冒出来说话")
        self.toggle.setChecked(True)
        self.toggle.toggled.connect(self._toggle)
        row.addWidget(self.toggle)
        row.addStretch(1)
        self.btn = QPushButton("现在说一句")
        self.btn.clicked.connect(self._trigger)
        row.addWidget(self.btn)
        layout.addLayout(row)

        self.status = hint_label("")
        layout.addWidget(self.status)

        self.detail = hint_label("")
        layout.addWidget(self.detail)

        layout.addWidget(QLabel("最近主动说过的话"))
        self.log = QTextBrowser()
        self.log.setMinimumHeight(120)
        layout.addWidget(self.log)

        self._worker: _ChitchatWorker | None = None

    def _toggle(self, flag: bool) -> None:
        chitchat.set_enabled(flag)
        self.refresh()

    def _trigger(self) -> None:
        if self._worker is not None:
            return
        self.btn.setEnabled(False)
        self.status.setText("正在想说什么…")
        self._worker = _ChitchatWorker(self)
        self._worker.done.connect(self._done)
        self._worker.start()

    def _done(self, out: dict) -> None:
        self.btn.setEnabled(True)
        self._worker = None
        if out.get("ok"):
            self.status.setText(f"说了：{out.get('text')}（通道 {out.get('via')}）")
        elif out.get("skipped"):
            self.status.setText(f"没说：{out.get('skipped')}")
        else:
            self.status.setText(f"没送出去：{out.get('error') or out.get('outbox') or '未知'}")
        self.refresh()

    def refresh(self) -> None:
        view = chitchat.view(vector.current())
        self.toggle.blockSignals(True)
        self.toggle.setChecked(bool(view["enabled"]))
        self.toggle.blockSignals(False)

        gate = view["gate"]
        self.detail.setText(
            f"下次到点：{str(view['next_due_at'])[11:16] or '—'}｜间隔 {view['interval'][0]:.0f}~{view['interval'][1]:.0f} 分钟\n"
            f"连续无回应 {view['consecutive_no_reply']} 次｜疲劳档 {view['fatigue_level']}｜"
            f"累计说过 {view['total_sent']} 句\n"
            f"静默到：{view['muted_until'] or '—'}｜现在：{gate.get('reason', '可以')}\n"
            f"推送通道：{view['push']}"
        )
        rows = []
        for row in view["recent"]:
            flag = "已回" if row.get("replied") else "没回"
            rows.append(f"<div><span style='color:#7b8ea8'>{row['t'][5:16]} · {flag}</span><br>"
                        f"{row['content']}</div><br>")
        self.log.setHtml("<style>body{color:#cfe0f5;font-size:12px;line-height:1.6}</style>"
                         + ("".join(rows) or "<div style='color:#7b8ea8'>（还没主动说过话）</div>"))
