# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""拒绝权面板：阈值/预算/傲娇设置 + 最近决策 + 现场试一句。"""

from __future__ import annotations

import html

from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QTextBrowser, QVBoxLayout, QWidget,
)

from core import settings
from core.emotion import vector
from core.refusal import arbiter as refusal_arbiter
from core.refusal import store as refusal_store
from core.text import clip
from ui.widgets import hint_label


class RefusalPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status = hint_label("")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.addWidget(QLabel("阈值"))
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.2, 1.0)
        self.threshold.setSingleStep(0.02)
        self.threshold.setDecimals(2)
        row.addWidget(self.threshold)

        row.addWidget(QLabel("每小时拒绝上限"))
        self.cap = QSpinBox()
        self.cap.setRange(0, 20)
        row.addWidget(self.cap)

        row.addWidget(QLabel("傲娇上限"))
        self.tsundere_cap = QSpinBox()
        self.tsundere_cap.setRange(0, 20)
        row.addWidget(self.tsundere_cap)

        row.addWidget(QLabel("傲娇程度"))
        self.tsundere_level = QDoubleSpinBox()
        self.tsundere_level.setRange(0.0, 1.0)
        self.tsundere_level.setSingleStep(0.05)
        self.tsundere_level.setDecimals(2)
        row.addWidget(self.tsundere_level)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        self.allow_deflect = QCheckBox("允许 DEFLECT（先不接这个话）")
        row2.addWidget(self.allow_deflect)
        row2.addStretch(1)
        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self._save)
        row2.addWidget(self.save_btn)
        layout.addLayout(row2)

        layout.addWidget(QLabel("试一句（会真的记一条决策，可在审计里看到）"))
        row3 = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("例如：你必须马上给我做，不许拒绝")
        self.input.returnPressed.connect(self._try)
        row3.addWidget(self.input, 1)
        self.try_btn = QPushButton("判定")
        self.try_btn.clicked.connect(self._try)
        row3.addWidget(self.try_btn)
        layout.addLayout(row3)

        self.result = hint_label("")
        layout.addWidget(self.result)

        layout.addWidget(QLabel("最近决策"))
        self.log = QTextBrowser()
        self.log.setMinimumHeight(160)
        layout.addWidget(self.log)

    # ── 动作 ──
    def _save(self) -> None:
        cfg = settings.load("refusal")
        cfg.update({
            "threshold": round(float(self.threshold.value()), 3),
            "refuse_per_hour": int(self.cap.value()),
            "tsundere_per_hour": int(self.tsundere_cap.value()),
            "tsundere_level": round(float(self.tsundere_level.value()), 3),
            "allow_deflect": bool(self.allow_deflect.isChecked()),
        })
        settings.save("refusal", cfg)
        refusal_arbiter._default = None  # 让新配置立刻生效
        self.status.setText(self.status.text() + "\n（已保存，下一句生效）")
        self.refresh()

    def _try(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        arb = refusal_arbiter.default()
        decision = arb.decide(text, vector.current())
        extra = f"\n理由：{decision.reason_text}" if decision.reason_text else ""
        alts = f"\n替代：{'、'.join(decision.alternatives)}" if decision.alternatives else ""
        self.result.setText(
            f"→ {decision.decision}（倾向 {decision.propensity} / 阈值 {decision.threshold}）"
            f"{extra}{alts}")
        self.refresh()

    # ── 刷新 ──
    def refresh(self) -> None:
        cfg = settings.load("refusal")
        st = refusal_arbiter.default().status()
        self.threshold.setValue(float(cfg.get("threshold", 0.62)))
        self.cap.setValue(int(cfg.get("refuse_per_hour", 3)))
        self.tsundere_cap.setValue(int(cfg.get("tsundere_per_hour", 2)))
        self.tsundere_level.setValue(float(cfg.get("tsundere_level", 0.4)))
        self.allow_deflect.setChecked(bool(cfg.get("allow_deflect", True)))

        self.status.setText(
            f"累计决策 {st['counts']['total']}｜拒绝 {st['counts']['refused']}｜"
            f"傲娇 {st['counts']['tsundere']}｜本小时已用 拒绝 {st['used']['refuse']}/"
            f"{st['refuse_per_hour']}、傲娇 {st['used']['tsundere']}/{st['tsundere_per_hour']}"
            f"｜连拒 {st['consecutive_refusals']}（{st['repair_after']} 次后会自动软化）"
        )

        rows = []
        for d in refusal_store.recent(12):
            sig = d.get("signals") or {}
            sig_text = " ".join(f"{k}{v:.2f}" for k, v in sig.items())
            rows.append(
                f"<div><b>{html.escape(d['decision'])}</b> "
                f"<span style='color:#7b8ea8'>{d['t'][5:16]} 倾向 {d['propensity']:.2f}/"
                f"阈值 {d['threshold']:.2f} 风格 {d['style']}</span><br>"
                f"<span style='color:#a9bed8'>{html.escape(clip(d['request_summary'], 40))}</span><br>"
                f"<span style='color:#8fa6c4'>{html.escape(d['reason_text'] or '（接受，不解释）')}</span><br>"
                f"<span style='color:#5f7290;font-size:11px'>{html.escape(sig_text)}</span></div><br>")
        self.log.setHtml("<style>body{color:#cfe0f5;font-size:12px;line-height:1.6}</style>"
                         + ("".join(rows) or "<div style='color:#7b8ea8'>（还没有拒绝记录）</div>"))
