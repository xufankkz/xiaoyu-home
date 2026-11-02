# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""v13/v14 好奇心引擎面板：领域仪表盘 + 三个开关 + 今日额度。"""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
                               QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

from core import curiosity_engine
from ui.widgets import hint_label


class CuriosityEnginePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status = hint_label("")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        self.enabled = QCheckBox("总开关")
        self.enabled.toggled.connect(self._apply)
        row.addWidget(self.enabled)
        self.auto = QCheckBox("自动执行学习")
        self.auto.toggled.connect(self._apply)
        row.addWidget(self.auto)
        row.addStretch(1)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("调度间隔（分钟）"))
        self.interval = QSpinBox()
        self.interval.setRange(5, 720)
        self.interval.valueChanged.connect(self._apply)
        row2.addWidget(self.interval)
        row2.addWidget(QLabel("单日额度（元，0=不限）"))
        self.budget = QDoubleSpinBox()
        self.budget.setRange(0.0, 100.0)
        self.budget.setDecimals(2)
        self.budget.setSingleStep(0.5)
        self.budget.valueChanged.connect(self._apply)
        row2.addWidget(self.budget)
        self.run_btn = QPushButton("立刻跑一轮")
        self.run_btn.clicked.connect(self._run_once)
        row2.addWidget(self.run_btn)
        row2.addStretch(1)
        layout.addLayout(row2)

        self.result = hint_label("")
        layout.addWidget(self.result)

        self.domains = QTextBrowser()
        self.domains.setMinimumHeight(190)
        layout.addWidget(self.domains)
        layout.addWidget(hint_label(
            "紧迫度 = 0.4×缺口 + 0.3×进步率 + 0.2×新颖 − 0.3×无聊；超过阈值才会自己找东西学。"
            "关掉「自动执行」后，引擎只排队不动手。"))
        self.refresh()

    def _apply(self) -> None:
        curiosity_engine.set_config(
            enabled=self.enabled.isChecked(),
            auto_learn=self.auto.isChecked(),
            interval_minutes=int(self.interval.value()),
            budget_limit_per_day=float(self.budget.value()),
        )
        self.result.setText(f"已保存：间隔 {self.interval.value()} 分钟"
                            f"｜额度 {self.budget.value():.2f} 元/天（重启后按新间隔调度）")
        self.refresh()

    def _run_once(self) -> None:
        out = curiosity_engine.tick()
        if out.get("skipped"):
            self.result.setText(f"本轮跳过：{out['skipped']}")
        elif out.get("target"):
            self.result.setText(f"本轮选中：{out['target']}（紧迫度 {out['urgency']}）")
        else:
            self.result.setText("本轮无自发学习（没有领域超过阈值）")
        self.refresh()

    def refresh(self) -> None:
        st = curiosity_engine.status()
        self.enabled.blockSignals(True)
        self.auto.blockSignals(True)
        self.interval.blockSignals(True)
        self.budget.blockSignals(True)
        self.enabled.setChecked(st["enabled"])
        self.auto.setChecked(st["auto_learn"])
        self.interval.setValue(st["interval_minutes"])
        self.budget.setValue(st["budget_limit"])
        self.enabled.blockSignals(False)
        self.auto.blockSignals(False)
        self.interval.blockSignals(False)
        self.budget.blockSignals(False)

        budget_line = (f"｜今日额度 {st['budget_used']:.2f}/{st['budget_limit']:.2f} 元"
                       if st["budget_limit"] > 0 else f"｜今日已花 {st['budget_used']:.2f} 元（不限）")
        self.status.setText(
            f"上次调度：{st['last_run'][:16] or '还没跑过'}｜上次目标：{st['last_target'] or '无'}"
            f"｜紧迫度 {st['last_urgency']}"
            + (f"\n⚠️ {st['budget_note']}" if st["budget_blocked"] else "")
            + budget_line)

        rows = ["<style>body{color:#cfe0f5;font-size:12px}td{padding:2px 6px}</style>",
                "<table width='100%'><tr style='color:#8fa6c4'><td>领域</td>"
                "<td align='right'>紧迫度</td><td align='right'>进步</td>"
                "<td align='right'>无聊</td><td align='right'>缺口</td>"
                "<td align='right'>探索</td></tr>"]
        for item in st["domains"]:
            color = "#f87171" if item["urgency"] > 0.6 else ("#fbbf24" if item["urgency"] > 0.4 else "#9fd6a0")
            rows.append(
                f"<tr><td>{item['name']}</td>"
                f"<td align='right' style='color:{color}'>{item['urgency']:.2f}</td>"
                f"<td align='right'>{item['progress']:.3f}</td>"
                f"<td align='right'>{item['boredom']:.2f}</td>"
                f"<td align='right'>{item['gap']:.2f}</td>"
                f"<td align='right'>{item['explored']}</td></tr>")
        rows.append("</table>")
        if not st["domains"]:
            rows.append("<div style='color:#7b8ea8'>还没有领域：等第一次对话或跑一轮。</div>")
        self.domains.setHtml("".join(rows))
