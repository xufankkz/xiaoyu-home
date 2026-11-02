# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""表情字典：她认过的表情、用在什么场合、人工批注。"""

from __future__ import annotations

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from core import sticker
from ui.widgets import hint_label


class StickerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status = hint_label("")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.addWidget(QLabel("sha 前 12 位"))
        self.sha = QLineEdit()
        self.sha.setPlaceholderText("点击下面某一行自动填，或手动粘贴")
        row.addWidget(self.sha, 1)
        row.addWidget(QLabel("叫它"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("例如「好耶」")
        row.addWidget(self.note, 1)
        self.save = QPushButton("记下")
        self.save.clicked.connect(self._annotate)
        row.addWidget(self.save)
        layout.addLayout(row)

        self.result = hint_label("")
        layout.addWidget(self.result)

        self.list = QTextBrowser()
        self.list.setMinimumHeight(200)
        layout.addWidget(self.list)
        layout.addWidget(hint_label(
            "第一次看到某张图要花一次识图钱，之后每次查表 0 元；"
            "她发出去一次，这里就会多一条「这么用」的语境。"))
        self.refresh()

    def _annotate(self) -> None:
        prefix = (self.sha.text() or "").strip()
        note = (self.note.text() or "").strip()
        if not prefix or not note:
            self.result.setText("要填 sha 前缀和称呼")
            return
        rows = sticker.all_entries(limit=500)
        hit = next((r for r in rows if r["sha256"].startswith(prefix)), None)
        if hit is None:
            self.result.setText("没找到这张（前缀对不上）")
            return
        sticker.annotate(hit["sha256"], note)
        self.result.setText(f"已记下：这张叫「{note}」")
        self.refresh()

    def refresh(self) -> None:
        st = sticker.status()
        self.status.setText(
            f"字典里 {st['total']} 张｜被复用 {st['reused']} 张｜累计发出 {st['uses']} 次")
        rows = ["<style>body{color:#cfe0f5;font-size:12px}td{padding:3px 6px}</style>",
                "<table width='100%'><tr style='color:#8fa6c4'><td>sha</td><td>含义</td>"
                "<td align='right'>用</td><td align='right'>看</td><td>这么用</td></tr>"]
        for item in sticker.all_entries(limit=60):
            contexts = item.get("contexts") or []
            rows.append(
                f"<tr><td style='font-family:Consolas,monospace;color:#7b8ea8'>{item['sha256'][:10]}</td>"
                f"<td>{item['summary'][:40]}</td>"
                f"<td align='right'>{item['usages']}</td>"
                f"<td align='right'>{item['hits']}</td>"
                f"<td style='color:#9db4d0'>{'；'.join(contexts[-2:])[:36]}</td></tr>")
        rows.append("</table>")
        if not sticker.all_entries(limit=1):
            rows.append("<div style='color:#7b8ea8'>还是空的：她收到表情包、或你用工具识过图之后就会长出来。</div>")
        self.list.setHtml("".join(rows))
