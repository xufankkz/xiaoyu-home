# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""关于 / 开源协议：许可证、版本、数据边界说明。

放这里而不是首页：它是「需要时才看」的信息，不该占首页的注意力。
（也满足补丁要求：软件里能明确看到「严禁商用收费」。）
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from core import db, settings
VERSION = "0.9.1"          # 开源首发版本号；发版时改这里
from ui.dashboard import ACCENT, SectionTitle, StatCard
from ui.widgets import hint_label


class AboutPanel(QWidget):
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

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        for col, (title, value, color) in enumerate([
            ("版本", VERSION, ACCENT[1]),
            ("许可", "AGPL-3.0 + 附加条款", ACCENT[0]),
            ("数据", "只在本机", ACCENT[2]),
        ]):
            card = StatCard(title, color=color)
            card.set(value, "")
            grid.addWidget(card, 0, col)
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        layout.addWidget(SectionTitle("许可与使用限制"))
        self.license = hint_label(
            "本项目以 GNU AGPL-3.0 发布，并在其之上附加《附加条款》：\n"
            "· 严禁闭源分发 —— 任何修改与衍生作品必须继续开源；\n"
            "· 严禁商业收费 —— 不得用于付费订阅、付费陪伴、付费托管、广告变现；\n"
            "· 商用须事先取得作者书面授权。\n"
            "完整条款见仓库根目录的 LICENSE（顶部附加条款）与 ADDITIONAL_TERMS.md。"
        )
        layout.addWidget(self.license)

        layout.addWidget(SectionTitle("你的数据在哪"))
        self.where = hint_label("")
        layout.addWidget(self.where)

        layout.addWidget(SectionTitle("运行环境"))
        self.env = hint_label("")
        layout.addWidget(self.env)

        layout.addWidget(SectionTitle("致谢"))
        layout.addWidget(hint_label(
            "框架：PySide6 / APScheduler。开源协议与第三方组件许可见 README 的「致谢」一节。"
        ))

    def refresh(self) -> None:
        try:
            size = f"{db.DB_PATH.stat().st_size / 1024 / 1024:.1f} MB" if db.DB_PATH.exists() else "未创建"
        except Exception:
            size = "未知"
        self.where.setText(
            f"程序目录：{settings.ROOT}\n"
            f"记忆库：{db.DB_PATH}（{size}）\n"
            f"状态/日志：{settings.STATE}／{settings.LOGS}\n"
            "所有数据只存在这台机器上；除了你自己配置的模型接口，程序不会外发任何内容。"
        )
        self.env.setText(
            f"Python {platform.python_version()}（{sys.executable}）\n"
            f"系统：{platform.platform()}"
        )
