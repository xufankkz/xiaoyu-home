# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""学习面板（v35 重构）：卡片 + 队列表格 + 缺口表。

旧版把「队列、权重、疲劳、五信号、缺口」全塞进两三段长文本，看不出重点。
新版：上面四张卡回答「开没开、排了多少、最缺什么、预算够不够」，
下面两张表放可核对的数据（谁在队列里、差哪些知识）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)
from PySide6.QtCore import QThread, Signal

from core import settings
from core.cognition import curiosity, metacognition
from core.learning import executor
from ui.dashboard import ACCENT, SectionTitle, StatCard, fill_table, make_table
from ui.widgets import hint_label

FLAG = settings.STATE / "learning_switch.json"
SIGNAL_CN = {"gap": "缺口", "pred_err": "预测误差", "progress": "进度",
             "competence": "能力", "social": "社会"}


class _LearnWorker(QThread):
    done = Signal(dict)

    def run(self) -> None:
        try:
            out = executor.run_one()
        except Exception as exc:  # noqa: BLE001
            out = {"ok": False, "error": str(exc)}
        self.done.emit(out or {})


class LearningPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)

        # ── 操作行 ──
        row = QHBoxLayout()
        win = settings.learning()
        self.switch = QCheckBox(f"定时学习（{win['window_start']}–{win['window_end']}）")
        self.switch.setChecked(True)
        self.switch.toggled.connect(self._toggle)
        row.addWidget(self.switch)
        row.addStretch(1)
        self.btn = QPushButton("现在学一个")
        self.btn.setObjectName("Primary")
        self.btn.clicked.connect(self._learn)
        row.addWidget(self.btn)
        layout.addLayout(row)

        self.status = hint_label("")
        layout.addWidget(self.status)

        # ── 卡片 ──
        self.cards: dict[str, StatCard] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for col, (key, title, color) in enumerate([
            ("queue", "待学队列", ACCENT[5]),
            ("signal", "好奇心主信号", ACCENT[0]),
            ("gap", "知识缺口", ACCENT[1]),
            ("budget", "联网预算 / 疲劳", ACCENT[2]),
        ]):
            card = StatCard(title, color=color)
            self.cards[key] = card
            grid.addWidget(card, 0, col)
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        # ── 队列表 ──
        layout.addWidget(SectionTitle("学习意图队列（按好奇心分排序）"))
        self.queue = make_table(["目标", "来源", "分数", "可学性", "状态"], stretch_col=0)
        self.queue.setMinimumHeight(190)
        layout.addWidget(self.queue)

        # ── 缺口表 ──
        layout.addWidget(SectionTitle("知识库缺口（元认知）"))
        self.gaps = make_table(["领域", "主题", "缺什么"], stretch_col=2)
        self.gaps.setMinimumHeight(150)
        layout.addWidget(self.gaps)

        self.detail = hint_label("")
        layout.addWidget(self.detail)

        self._worker: _LearnWorker | None = None
        self.refresh()

    # ── 动作 ────────────────────────────────────────────────────────────
    def _toggle(self, flag: bool) -> None:
        try:
            FLAG.parent.mkdir(parents=True, exist_ok=True)
            FLAG.write_text('{"enabled": %s}' % ("true" if flag else "false"), encoding="utf-8")
        except Exception:
            pass

    def _learn(self) -> None:
        if self._worker is not None:
            return
        self.btn.setEnabled(False)
        self.status.setText("正在学…（联网可能要一会儿）")
        self._worker = _LearnWorker(self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, out: dict) -> None:
        self.btn.setEnabled(True)
        self._worker = None
        if out.get("ok"):
            self.status.setText(f"学会了：{str(out.get('knowledge'))[:40]}"
                                f"｜关掉 {out.get('closed_open', 0)} 个未决节点"
                                f"｜花销 ¥{float(out.get('cost') or 0):.4f}")
        else:
            self.status.setText(f"没学成：{str(out.get('error') or out.get('reason') or out)[:80]}")
        self.refresh()

    # ── 刷新 ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        view = curiosity.view()
        sig = view["signals"]
        queue = view["queue"]

        top = max(queue, key=lambda item: item.get("score", 0)) if queue else None
        self.cards["queue"].set(
            f"{len(queue)} 条",
            (f"最急：{str(top['target'])[:12]}（分 {top['score']}）" if top else "队列是空的")
            + "\n自动学习的候选，点右上「现在学一个」可立刻跑一次",
        )
        name = max(sig, key=lambda key: sig[key])
        self.cards["signal"].set(
            f"{SIGNAL_CN.get(name, name)} {sig[name]:.2f}",
            "｜".join(f"{SIGNAL_CN.get(k, k)} {v:.2f}" for k, v in sig.items())
            + "\n最高的那个信号决定优先学什么",
        )
        gaps = metacognition.gaps(6)
        self.cards["gap"].set(
            f"{len(gaps)} 处",
            (f"{gaps[0]['name']} 缺 {len(gaps[0]['missing'])} 项" if gaps else "知识库还没建起来"),
        )
        self.cards["budget"].set(
            "够" if view["budget_ok"] else "用完了",
            f"疲劳：{'｜'.join(f'{k} {v:.2f}' for k, v in view['fatigue'].items()) or '无'}\n"
            f"预算用完时好奇心引擎会跳过本轮",
        )

        fill_table(self.queue, [
            [str(item["target"]), str(item["source"]), str(item["score"]),
             str(item["learnability"]), str(item["status"])]
            for item in queue
        ])
        fill_table(self.gaps, [
            [str(g["domain"]), str(g["name"]), "、".join(g["missing"]) or "—"]
            for g in gaps
        ])

        weights = view.get("domain_weights") or {}
        self.detail.setText(
            "领域权重：" + ("｜".join(f"{k} {v:.2f}" for k, v in weights.items()) or "（暂无）"))
