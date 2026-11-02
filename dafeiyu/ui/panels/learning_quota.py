# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""学习系统 ·「上网学习配额」面板（v13 新增）。

为什么单独开一页：她自学多久、学多少，是这个软件里唯一直接烧钱的节奏旋钮，
藏在 learning.json / webstudy.json 里太隐蔽。这里给三个数字 + 一个总开关。

读写规则（重要）：
  · 配置源还是 `data/config/webstudy.json`（和 core/learning/webstudy.py 完全同一个文件）；
  · 读：utf-8-sig（老版本记事本存过带 BOM 的文件）；
  · 写：只改 `enabled` 与 `quota.rounds_per_day / items_per_round /
    min_interval_minutes`，**其它键（sites / tavily / topics / filter …）原样保留**，
    先写 `.tmp` 再 os.replace，避免核心正在读时读到半个文件；
  · 状态：`core.learning.webstudy.status()` 是纯读（配置 + 状态文件 + 词表），
    仍然放 QThread 里取，主线程只负责显示，面板打开/按刷新都不会卡。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from core import settings
from ui.widgets import hint_label, title_label

CONFIG_PATH = Path(settings.CONFIG) / "webstudy.json"
STATUS_EVERY_MS = 12_000

DEFAULTS = {"rounds_per_day": 3, "items_per_round": 5, "min_interval_minutes": 120}
RANGES = {
    "rounds_per_day": (0, 20, "每天最多自学"),
    "items_per_round": (1, 10, "每轮最多记"),
    "min_interval_minutes": (30, 1440, "两轮最少间隔"),
}
UNITS = {
    "rounds_per_day": ("轮 / 天", "0 = 今天不许她自己上网学，只能我让她学"),
    "items_per_round": ("条知识 / 轮", "每条 = 一个知识点写进知识库"),
    "min_interval_minutes": ("分钟", "两轮之间至少隔这么久，避免连着烧钱"),
}


# ── 配置文件读写（只碰 enabled 与 quota 三个字段）───────────────────────────
def read_config(path: Path | None = None) -> dict:
    path = Path(path or CONFIG_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_quota(*, enabled: bool, rounds: int, items: int, interval: int,
                path: Path | None = None) -> Path:
    path = Path(path or CONFIG_PATH)
    data = read_config(path)
    if not data:
        data = {"enabled": True, "quota": {}}
    data["enabled"] = bool(enabled)
    quota = data.get("quota")
    if not isinstance(quota, dict):
        quota = {}
        data["quota"] = quota
    quota["rounds_per_day"] = int(rounds)
    quota["items_per_round"] = int(items)
    quota["min_interval_minutes"] = int(interval)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _quota_of(data: dict) -> dict:
    quota = data.get("quota") if isinstance(data, dict) else {}
    if not isinstance(quota, dict):
        quota = {}
    out = {}
    for key, (_lo, _hi, _label) in RANGES.items():
        try:
            out[key] = int(quota.get(key, DEFAULTS[key]))
        except Exception:
            out[key] = DEFAULTS[key]
    return out


class _StatusWorker(QThread):
    """后台取 webstudy.status()：纯读文件 + 读库，很快，但不占主线程。"""

    done = Signal(dict)

    def run(self) -> None:  # noqa: D401
        try:
            from core.learning import webstudy

            self.done.emit(dict(webstudy.status() or {}))
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"error": str(exc)})


class LearningQuotaPanel(QWidget):
    """学习系统 → 上网学习配额。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _StatusWorker | None = None
        self._loaded = False
        self._dirty = False

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
        layout.setSpacing(12)

        layout.addWidget(title_label("上网学习配额"))
        layout.addWidget(hint_label(
            "她的自学节奏就这三个数：每天几轮、每轮几条、两轮之间隔多久。"
            "改的是 data/config/webstudy.json，保存后下一轮自学就按新配额走。"))

        # ── 配额卡 ──
        form_card = QFrame()
        form_card.setObjectName("Card")
        form = QVBoxLayout(form_card)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(3, 1)
        self.spins: dict[str, QSpinBox] = {}
        for row, (key, (low, high, label)) in enumerate(RANGES.items()):
            cap = QLabel(label)
            cap.setObjectName("CardValue")
            grid.addWidget(cap, row, 0)
            spin = QSpinBox()
            spin.setRange(int(low), int(high))
            spin.setSingleStep(1)
            spin.setValue(int(DEFAULTS[key]))
            spin.setFixedWidth(110)
            spin.valueChanged.connect(self._mark_dirty)
            grid.addWidget(spin, row, 1)
            unit, tip = UNITS[key]
            unit_label = QLabel(unit)
            unit_label.setObjectName("CardLabel")
            unit_label.setToolTip(tip)
            grid.addWidget(unit_label, row, 2)
            self.spins[key] = spin
            if row == 0:
                note = QLabel(tip)
                note.setObjectName("Hint")
                note.setWordWrap(True)
                grid.addWidget(note, row, 3)
        form.addLayout(grid)

        self.enabled_box = QCheckBox("允许她自己上网学习（关掉 = 只能我让她学）")
        self.enabled_box.toggled.connect(self._mark_dirty)
        form.addWidget(self.enabled_box)

        row_btn = QHBoxLayout()
        row_btn.setSpacing(8)
        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("Primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self.save)
        self.reload_btn = QPushButton("刷新")
        self.reload_btn.setObjectName("Ghost")
        self.reload_btn.setCursor(Qt.PointingHandCursor)
        self.reload_btn.clicked.connect(lambda: self.refresh(force=True))
        row_btn.addWidget(self.save_btn)
        row_btn.addWidget(self.reload_btn)
        row_btn.addStretch(1)
        self.feedback = QLabel("")
        self.feedback.setObjectName("Hint")
        self.feedback.setWordWrap(True)
        row_btn.addWidget(self.feedback, 1)
        form.addLayout(row_btn)
        layout.addWidget(form_card)

        # ── 状态卡 ──
        status_card = QFrame()
        status_card.setObjectName("Card")
        status = QVBoxLayout(status_card)
        status.setContentsMargins(18, 16, 18, 16)
        status.setSpacing(8)
        cap = QLabel("她现在的状态")
        cap.setObjectName("CardTitle")
        status.addWidget(cap)

        self.quota_bar = QProgressBar()
        self.quota_bar.setRange(0, 3)
        self.quota_bar.setValue(0)
        self.quota_bar.setTextVisible(False)
        self.quota_bar.setFixedHeight(10)
        status.addWidget(self.quota_bar)

        self.line_rounds = QLabel("今天已用 — / — 轮")
        self.line_rounds.setObjectName("CardValue")
        status.addWidget(self.line_rounds)

        self.line_today = QLabel("")
        self.line_today.setObjectName("Hint")
        self.line_today.setWordWrap(True)
        status.addWidget(self.line_today)

        self.line_last = QLabel("")
        self.line_last.setObjectName("Hint")
        self.line_last.setWordWrap(True)
        status.addWidget(self.line_last)

        self.line_error = QLabel("")
        self.line_error.setObjectName("Danger")
        self.line_error.setWordWrap(True)
        status.addWidget(self.line_error)
        layout.addWidget(status_card)

        layout.addWidget(hint_label(
            "说明：配额按「天」重置（跨零点自动归零）；最小间隔是两轮之间的硬间隔。"
            "这些都只是闸门，真正学什么由好奇心引擎和话题池决定。"))
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(STATUS_EVERY_MS)

    # ── 生命周期：打开这页才读，不拖慢启动 ──
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh()

    # ── 读 ──
    def refresh(self, force: bool = False) -> None:
        """外壳定时刷新会调这个；用户改了还没保存时不要覆盖他的输入。"""
        if force or not self._dirty:
            self._load_config()
        self._refresh_status()

    def _load_config(self) -> None:
        data = read_config()
        quota = _quota_of(data)
        for key, spin in self.spins.items():
            spin.blockSignals(True)
            spin.setValue(int(quota[key]))
            spin.blockSignals(False)
        enabled = bool(data.get("enabled", True))
        self.enabled_box.blockSignals(True)
        self.enabled_box.setChecked(enabled)
        self.enabled_box.blockSignals(False)
        self._dirty = False

    def _refresh_status(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _StatusWorker(self)
        self._worker.done.connect(self._apply_status)
        self._worker.start()

    def _apply_status(self, data: dict) -> None:
        if not isinstance(data, dict) or data.get("error"):
            reason = (data or {}).get("error") or "读不到 webstudy 状态"
            self.line_rounds.setText("今天已用 — / — 轮")
            self.line_today.setText("")
            self.line_last.setText("")
            self.line_error.setText(f"状态读取失败：{reason}")
            return
        quota = data.get("quota") or {}
        used = int(quota.get("used_rounds") or 0)
        cap = int(quota.get("rounds_per_day") or 0)
        items_per = int(quota.get("items_per_round") or 0)
        used_items = int(quota.get("used_items") or 0)
        self.quota_bar.setRange(0, max(1, cap))
        self.quota_bar.setValue(min(used, max(1, cap)))
        left_rounds = max(0, cap - used)
        self.line_rounds.setText(
            f"今天已用 {used} / {cap} 轮（还剩 {left_rounds} 轮；每轮最多 {items_per} 条）")

        today = data.get("today") or {}
        learned_today = int(today.get("items") or used_items or 0)
        total = int(data.get("learned_total") or 0)
        blocked = int(today.get("blocked") or 0)
        self.line_today.setText(
            f"今天记下 {learned_today} 条知识 · 累计 {total} 条 · "
            f"今天被过滤拦掉 {blocked} 条 · 总开关：{'开' if data.get('enabled') else '关'}")

        last_run = str(data.get("last_run") or "").strip()
        if len(last_run) >= 16:
            last_run = last_run[:10] + " " + last_run[11:16]
        gap = int(data.get("min_interval_minutes") or 0)
        self.line_last.setText(
            f"最小间隔 {gap} 分钟 · 上次自学：{last_run or '还没跑过'}")

        error = str(data.get("last_error") or "").strip()
        self.line_error.setText(f"上次出错：{error}" if error else "")

    # ── 写 ──
    def _mark_dirty(self, *_args) -> None:
        self._dirty = True
        self.feedback.setText("有改动，记得点「保存」")

    def save(self) -> None:
        rounds = int(self.spins["rounds_per_day"].value())
        items = int(self.spins["items_per_round"].value())
        interval = int(self.spins["min_interval_minutes"].value())
        enabled = bool(self.enabled_box.isChecked())
        try:
            path = write_quota(enabled=enabled, rounds=rounds, items=items,
                               interval=interval)
        except Exception as exc:  # noqa: BLE001
            self.feedback.setText(f"保存失败：{exc}")
            return
        self._dirty = False
        switch = "已打开" if enabled else "已关掉"
        self.feedback.setText(
            f"已保存：每天最多 {rounds} 轮、每轮 {items} 条、间隔 {interval} 分钟"
            f"（开关{switch}），下一轮自学起生效")
        self.line_error.setToolTip(f"写入 {path}")
        self._refresh_status()

    # ── 面板关闭：等后台线程收尾，别让 QThread 带着活线程被析构 ──
    def closeEvent(self, event):  # noqa: N802
        self._timer.stop()
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(1500)
        super().closeEvent(event)
