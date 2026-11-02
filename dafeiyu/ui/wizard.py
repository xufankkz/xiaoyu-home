# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""首次启动向导：给自己捏一个「她」（名字 / 性别 / 身高 / 性格）。

只在**第一次**启动出现（`core.firstrun.detect()`）；已经在跑的实例永远不会看到这个窗口。
直接点「用默认设定开始」也能进 —— 默认就是「大肥鱼 + 傲娇壳 + 温柔心」。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTextEdit, QVBoxLayout)

from core import firstrun

WELCOME = (
    "第一次见面，先给「她」定个样子吧。\n"
    "这些设定会写进她的 1 号长期记忆（自我认知 + 性格设定），以后她自己也知道自己是谁。\n"
    "不填也能用：直接点下面「用默认设定开始」。"
)


class SetupWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("大肥鱼 · 初次见面")
        self.setMinimumWidth(560)
        # 别用系统默认白底：主题是深色，白底会让标题白字看不见（实测截出来就是一片白）
        self.setStyleSheet(
            "QDialog{background:#0b1220;}"
            "QLabel{color:#d9e6f8;font-size:13px;}"
            "QLabel#Title{font-size:18px;font-weight:700;color:#f2f7ff;}"
            "QLabel#Hint{color:#859cbd;font-size:12px;}"
            "QLineEdit,QComboBox,QTextEdit{background:#101a2a;color:#e9f1ff;border:1px solid #24354f;"
            "border-radius:8px;padding:6px 8px;font-size:13px;}"
            "QComboBox QAbstractItemView{background:#121d2e;color:#e9f1ff;}"
            "QPushButton{background:#16233a;color:#d9e6f8;border:1px solid #24354f;border-radius:9px;"
            "padding:7px 15px;font-size:13px;}"
            "QPushButton#Primary{background:#4f8dff;border-color:#5a9bff;color:#ffffff;font-weight:600;}"
            "QPushButton:hover{background:#1b2b45;}"
        )
        self._result: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        head = QLabel("给大肥鱼起个名字、定个性格")
        head.setObjectName("Title")
        layout.addWidget(head)

        intro = QLabel(WELCOME)
        intro.setWordWrap(True)
        intro.setObjectName("Hint")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)
        self.name = QLineEdit(firstrun.DEFAULTS["name"])
        self.gender = QComboBox()
        self.gender.addItems(["暂未设置", "女", "男", "其他"])
        self.height = QLineEdit(firstrun.DEFAULTS["height"])
        self.height.setPlaceholderText("例如 168cm；不填就是「暂未设置」")
        form.addRow("名字", self.name)
        form.addRow("性别", self.gender)
        form.addRow("身高", self.height)
        layout.addLayout(form)

        layout.addWidget(QLabel("性格"))
        self.template = QComboBox()
        for title, _text in firstrun.TEMPLATES:
            self.template.addItem(title)
        self.template.currentIndexChanged.connect(self._fill_template)
        layout.addWidget(self.template)

        self.personality = QTextEdit()
        self.personality.setPlainText(firstrun.TEMPLATES[0][1])
        self.personality.setMinimumHeight(90)
        layout.addWidget(self.personality)

        tip = QLabel("提示：性格写得越具体，她越像「那个人」；以后想改，直接编辑 config/personality.md。")
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        row = QHBoxLayout()
        row.addStretch(1)
        self.skip = QPushButton("用默认设定开始")
        self.skip.clicked.connect(self._accept_defaults)
        row.addWidget(self.skip)
        self.ok = QPushButton("就这样开始")
        self.ok.setObjectName("Primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._accept)
        row.addWidget(self.ok)
        layout.addLayout(row)

    def _fill_template(self, index: int) -> None:
        if 0 <= index < len(firstrun.TEMPLATES):
            self.personality.setPlainText(firstrun.TEMPLATES[index][1])

    def _collect(self) -> dict:
        return {
            "name": (self.name.text() or "").strip() or firstrun.DEFAULTS["name"],
            "gender": self.gender.currentText() or firstrun.DEFAULTS["gender"],
            "height": (self.height.text() or "").strip() or firstrun.DEFAULTS["height"],
            "personality": (self.personality.toPlainText() or "").strip()
            or firstrun.DEFAULTS["personality"],
        }

    def _accept(self) -> None:
        self._result = self._collect()
        self.accept()

    def _accept_defaults(self) -> None:
        self._result = dict(firstrun.DEFAULTS)
        self.accept()

    def result_profile(self) -> dict:
        return self._result or dict(firstrun.DEFAULTS)


def run_if_needed(app) -> dict | None:
    """需要时弹向导并落盘；不需要就返回 None。

    失败绝不影响启动：宁可给人设兜底，也不能让程序起不来。
    """
    try:
        if not firstrun.detect():
            return None
    except Exception:
        return None
    try:
        wizard = SetupWizard()
        wizard.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        wizard.show()
        app.processEvents()
        accepted = wizard.exec() == QDialog.Accepted
        prof = wizard.result_profile() if accepted else dict(firstrun.DEFAULTS)
        return firstrun.apply(prof)
    except Exception as error:  # noqa: BLE001
        try:
            return firstrun.apply(dict(firstrun.DEFAULTS))
        except Exception:
            return {"ok": False, "error": str(error)}
