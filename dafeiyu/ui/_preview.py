"""离屏预览：只搭 Shell 的骨架（导航 + 主页 + 详情占位），不实例化任何真实面板。

不启动软件、不碰数据库，只做像素级自查：
  python ui/_preview.py  →  %TEMP%/dafeiyu_preview/*.png
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# 用真实 Windows 平台渲染（取系统字体），但窗口不上屏：WA_DontShowOnScreen

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

OUT = Path(tempfile.gettempdir()) / "dafeiyu_preview"
OUT.mkdir(parents=True, exist_ok=True)


class Stub:
    def refresh_all(self):
        pass

    def repair(self):
        pass

    def check_update(self):
        pass

    def open_detail(self, key):
        pass

    def go_home(self):
        pass

    def run_repair(self):
        pass

    def run_update(self):
        pass


def main() -> int:
    app = QApplication([])
    qss = ROOT / "data" / "ui" / "theme.qss"
    if not qss.exists():
        qss = ROOT / "ui" / "theme.qss"
    app.setStyleSheet(qss.read_text(encoding="utf-8"))

    from ui.shell import DetailPage, HomePage, NavRail

    stub = Stub()

    # ── 主页 ──
    host = QWidget()
    host.setObjectName("Root")
    host.resize(1360, 880)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    nav = NavRail()
    layout.addWidget(nav)
    home = HomePage(stub)
    layout.addWidget(home, 1)
    nav.set_active("home")
    home.total.setText("¥1.284")
    home.calls.setText("6 轮聊天")
    home.note.setText("私聊 ¥0.42｜群 ¥0.31｜后台 ¥0.55")
    home.day.setText("10-30 今天")
    home.set_status("lively", "心情不错")
    home.avatar_hint.setText("主导情绪：开心｜正面 62 负面 8 害羞 12｜保护中：无")
    host.setAttribute(Qt.WA_DontShowOnScreen, True)
    host.show()
    for _ in range(6):
        app.processEvents()
    host.grab().save(str(OUT / "home.png"))

    # ── 详情页（子页列表 + 占位内容） ──
    host2 = QWidget()
    host2.setObjectName("Root")
    host2.resize(1360, 880)
    layout2 = QHBoxLayout(host2)
    layout2.setContentsMargins(0, 0, 0, 0)
    layout2.setSpacing(0)
    nav2 = NavRail()
    layout2.addWidget(nav2)
    detail = DetailPage(stub)
    layout2.addWidget(detail, 1)
    nav2.set_active("memory")
    views = []
    for name in ("1号长期记忆", "2号长期记忆", "短期 · 日记本", "瞬时记忆", "表情字典"):
        placeholder = QLabel(f"{name} · 面板内容占位")
        placeholder.setAlignment(Qt.AlignCenter)
        views.append((name, placeholder))
    detail.show_module("memory", "记忆系统", views)
    detail.set_status("shy", "有点不好意思")
    detail.avatar_hint.setText("主导情绪：害羞｜正面 41 负面 6 害羞 63｜保护中：隐私")
    host2.setAttribute(Qt.WA_DontShowOnScreen, True)
    host2.show()
    for _ in range(6):
        app.processEvents()
    host2.grab().save(str(OUT / "detail.png"))

    # ── 立绘特写（看呼吸/光晕/叠加是否好看） ──
    from ui.avatar import HeroAvatar

    solo = QWidget()
    solo.setObjectName("Root")
    solo.resize(520, 640)
    solo_layout = QVBoxLayout(solo)
    solo_layout.setContentsMargins(16, 16, 16, 16)
    hero = HeroAvatar()
    solo_layout.addWidget(hero)
    solo.setAttribute(Qt.WA_DontShowOnScreen, True)
    solo.show()
    for state in ("calm", "lively", "think", "shy", "sad", "study"):
        hero.set_state(state, "测试")
        for _ in range(4):
            app.processEvents()
        solo.grab().save(str(OUT / f"hero_{state}.png"))

    # ── 新增面板：学习 · 上网学习配额 ──
    from ui.panels.learning_quota import LearningQuotaPanel

    def _page(nav_key: str, title: str, views):
        host = QWidget()
        host.setObjectName("Root")
        host.resize(1360, 880)
        box = QHBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        rail = NavRail()
        box.addWidget(rail)
        page = DetailPage(stub)
        box.addWidget(page, 1)
        rail.set_active(nav_key)
        page.show_module(nav_key, title, views)
        page.set_status("calm", "平静")
        page.avatar_hint.setText("主导情绪：平静｜正面 38 负面 4 害羞 9｜保护中：无")
        page.items.setCurrentRow(len(views) - 1)
        host.setAttribute(Qt.WA_DontShowOnScreen, True)
        host.show()
        return host

    def _placeholder(text: str) -> QLabel:
        label = QLabel(f"{text} · 面板内容占位")
        label.setAlignment(Qt.AlignCenter)
        return label

    quota = LearningQuotaPanel()
    host_quota = _page("learning", "学习系统", [
        ("意图队列与报告", _placeholder("学习面板")),
        ("好奇心引擎", _placeholder("好奇心引擎")),
        ("上网学习配额", quota),
    ])
    wait_until = time.time() + 6.0
    while time.time() < wait_until and "—" in quota.line_rounds.text():
        app.processEvents()
        time.sleep(0.02)
    for _ in range(30):
        app.processEvents()
    host_quota.grab().save(str(OUT / "panel_learning_quota.png"))

    # ── 新增面板：记忆 · 记忆星图 ──
    from ui.panels.memory_map import MemoryMapPanel

    memory = MemoryMapPanel()
    memory_views = [(name, _placeholder(name)) for name in (
        "1号长期记忆", "2号长期记忆", "短期 · 日记本", "短期 · 待总结", "瞬时记忆",
        "语言记忆", "情绪记忆", "思维链 · 最近想法", "表情字典", "梗百科", "刷抖音学梗")]
    memory_views.append(("记忆星图", memory))
    host_map = _page("memory", "记忆系统", memory_views)
    deadline = time.time() + 12.0
    while time.time() < deadline and not memory.canvas.nodes:
        app.processEvents()
        time.sleep(0.03)
    if memory.canvas.nodes:
        best = max(range(len(memory.canvas.nodes)),
                   key=lambda i: len(memory.canvas.nodes[i].get("concepts") or []))
        memory._on_node_clicked(best)
    for _ in range(30):
        app.processEvents()
    host_map.grab().save(str(OUT / "panel_memory_map.png"))

    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
