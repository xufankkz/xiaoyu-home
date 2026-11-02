# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""关系与索取度面板：0–100 的滑杆 + 亲密度 + 里程碑。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider, QTextBrowser,
                               QVBoxLayout, QWidget)

from core import relationship
from ui.widgets import hint_label


class RelationshipPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.intimacy = hint_label("")
        layout.addWidget(self.intimacy)

        row = QHBoxLayout()
        row.addWidget(QLabel("索取度"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setTickInterval(10)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.valueChanged.connect(self._on_change)
        row.addWidget(self.slider, 1)
        self.value = QLabel("65")
        self.value.setObjectName("Title")
        row.addWidget(self.value)
        layout.addLayout(row)

        layout.addWidget(hint_label("0 = 不许索取（只回应）｜65 = 默认（会撒娇、被拒会收）｜100 = 无上限"))
        self.tone = hint_label("")
        layout.addWidget(self.tone)

        btns = QHBoxLayout()
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self._save)
        btns.addWidget(self.save_btn)
        self.repair_btn = QPushButton("和好")
        self.repair_btn.clicked.connect(self._repair)
        btns.addWidget(self.repair_btn)
        btns.addStretch(1)
        layout.addLayout(btns)

        self.result = hint_label("")
        layout.addWidget(self.result)

        self.milestones = QTextBrowser()
        self.milestones.setMinimumHeight(150)
        layout.addWidget(self.milestones)
        self.refresh()

    def _on_change(self, value: int) -> None:
        self.value.setText(str(value))

    def _save(self) -> None:
        data = relationship.set_demand(self.slider.value())
        self.result.setText(f"已保存：索取度 {data['demand_level']}｜{relationship.status()['tone']}")
        self.refresh()

    def _repair(self) -> None:
        relationship.repair()
        relationship.add_intimacy(2, "主动和好")
        self.result.setText("已和好，亲密度 +2")
        self.refresh()

    def refresh(self) -> None:
        st = relationship.status()
        self.slider.blockSignals(True)
        self.slider.setValue(int(st["demand_level"]))
        self.slider.blockSignals(False)
        self.value.setText(str(st["demand_level"]))
        self.intimacy.setText(
            f"亲密度 {st['intimacy']:.0f} / 100｜别扭 {st['conflict_count']} 次｜"
            f"现在的调子：{st['tone']}"
        )
        self.tone.setText("闸门：" + "｜".join(
            f"{name}{'✓' if ok else '✗'}" for name, ok in st["gates"].items()))
        rows = ["<style>body{color:#cfe0f5;font-size:12px}td{padding:2px 6px}</style>",
                "<table width='100%'>"]
        for item in reversed(st["milestones"]):
            rows.append(f"<tr><td style='color:#7b8ea8'>{str(item.get('at',''))[5:16]}</td>"
                        f"<td>{item.get('what','')}</td>"
                        f"<td align='right'>{item.get('delta','')}</td></tr>")
        rows.append("</table>")
        if len(st["milestones"]) == 0:
            rows.append("<div style='color:#7b8ea8'>还没有里程碑。</div>")
        self.milestones.setHtml("".join(rows))
