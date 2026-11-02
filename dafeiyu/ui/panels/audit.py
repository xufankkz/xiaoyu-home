# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""审计视图：按时间线查看一条记忆/思维链的创建、降级、蒸馏、检索、学习关联全过程。

还可查看任意时刻的情绪向量快照与奖罚事件（规格第九节）。
"""

from __future__ import annotations

import html

from PySide6.QtWidgets import QComboBox, QLabel, QTextBrowser, QVBoxLayout, QWidget

from core.repo import memory as mr
from core.repo import system as sr
from core.repo import emotion as emotion_repo
from core.text import clip
from ui.widgets import hint_label

ACTION_CN = {
    "created": "创建", "accessed": "被访问", "retrieved": "被检索命中", "level_down": "降级",
    "distilled": "蒸馏", "tombstoned": "清空正文物墓碑", "linked": "建立关联",
    "learned": "学到", "reward": "奖罚", "ignored": "不理人", "step": "认知步骤", "done": "周期完成",
}


class AuditPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(QLabel("选一条记忆，看她这一路的经历"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._show)
        layout.addWidget(self.combo)

        self.timeline = QTextBrowser()
        self.timeline.setMinimumHeight(180)
        layout.addWidget(self.timeline)

        layout.addWidget(QLabel("最近的系统动作"))
        self.recent = QTextBrowser()
        self.recent.setMinimumHeight(140)
        layout.addWidget(self.recent)

        self.snap = hint_label("")
        layout.addWidget(self.snap)

    def refresh(self) -> None:
        current = self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        items = []
        for layer, label in (("instant", "瞬时"), ("short", "短期"), ("long1", "1号"), ("long2", "2号")):
            for item in mr.list_layer(layer=layer, limit=15, alive_only=True, order="t_created DESC"):
                items.append((item, label))
        for item, label in items:
            self.combo.addItem(f"[{label}] {clip(item.summary or item.content, 30)}", item.id)
        if current:
            idx = self.combo.findData(current)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)
        self._show()

        rows = []
        for ev in sr.audit_recent(40):
            detail = ev.get("detail") or {}
            brief = clip(", ".join(f"{k}={v}" for k, v in list(detail.items())[:3]), 90)
            rows.append(
                f"<div><span style='color:#7b8ea8'>{ev['t'][5:19]}</span> "
                f"<b>{ACTION_CN.get(ev['action'], ev['action'])}</b> "
                f"<span style='color:#8fa6c4'>{ev['entity_kind']}</span> {brief}</div>")
        self.recent.setHtml("<style>body{color:#cfe0f5;font-size:12px;line-height:1.7}</style>"
                            + ("".join(rows) or "<div style='color:#7b8ea8'>（空）</div>"))

        last = emotion_repo.snapshot_last()
        if last:
            self.snap.setText(f"最新情绪快照 {last['t'][5:19]}：愉悦{last['joy']:.0f} 幸福{last['happiness']:.0f} "
                              f"难过{last['sadness']:.0f} 自卑{last['inferiority']:.0f} 害羞{last['shyness']:.0f}"
                              f"（主导 {last['dominant']}）")
        else:
            self.snap.setText("还没有情绪快照")

    def _show(self) -> None:
        mid = self.combo.currentData()
        if not mid:
            self.timeline.setHtml("<div style='color:#7b8ea8'>还没有记忆条目。</div>")
            return
        rows = []
        for ev in sr.audit_timeline(mid, 80):
            detail = ev.get("detail") or {}
            brief = clip(", ".join(f"{k}={v}" for k, v in list(detail.items())[:4]), 160)
            rows.append(f"<div><span style='color:#7b8ea8'>{ev['t'][5:19]}</span> "
                        f"<b>{ACTION_CN.get(ev['action'], ev['action'])}</b> {html.escape(brief)}</div>")
        item = mr.get(mid)
        head = ""
        if item is not None:
            head = (f"<h3>{html.escape(clip(item.summary or item.content, 60))}</h3>"
                    f"<div style='color:#8fa6c4'>层：{item.layer}｜强度 {item.strength:.2f}｜"
                    f"显著 {item.salience:.2f}｜level {item.level}｜"
                    f"{'墓碑' if item.tombstone else '存活'}｜被访问 {item.access_count} 次</div><br>")
        self.timeline.setHtml("<style>body{color:#cfe0f5;font-size:12px;line-height:1.7}</style>"
                              + head + ("".join(rows) or "<div style='color:#7b8ea8'>（这条还没什么经历）</div>"))
