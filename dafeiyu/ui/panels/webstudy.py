# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""刷抖音学梗（0 API 花费）：浏览器看、网页版 DeepSeek 理解、写进本地梗库。

为什么单独一页：
  老大明确不要「刷抖音产生 API 费用」。这条链路上**没有任何模型 API**：
  看的是本机真实 Edge（有头，能看见能接管），理解用的是 chat.deepseek.com 的网页额度，
  学到的东西落到本地词库，以后她聊天时查这个词依然是 0 花费。
  这一页就是这条链路的开关和现场日志 —— 花了多少钱、刷了几条，一眼能看见。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSpinBox, QTextBrowser,
                               QVBoxLayout, QWidget)

from core import webstudy
from ui.widgets import hint_label


class _StudyWorker(QThread):
    line = Signal(str)
    done = Signal(dict)

    def __init__(self, minutes: float, parent=None):
        super().__init__(parent)
        self.minutes = minutes
        self.stop_event = threading.Event()

    def run(self) -> None:
        try:
            out = webstudy.run_session(self.minutes, log=self.line.emit, stop_event=self.stop_event)
        except webstudy.NeedLogin as error:
            out = {"ok": False, "need_login": True, "error": str(error)}
        except webstudy.Stopped:
            out = {"ok": False, "stopped": True}
        except Exception as error:  # noqa: BLE001
            out = {"ok": False, "error": str(error)}
        self.done.emit(out or {})


class WebStudyPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status = hint_label("")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.addWidget(QLabel("刷多久"))
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 60)
        self.minutes.setValue(10)
        self.minutes.setSuffix(" 分钟")
        row.addWidget(self.minutes)
        self.start = QPushButton("去刷会儿抖音")
        self.start.setObjectName("Primary")
        self.start.clicked.connect(self._start)
        row.addWidget(self.start)
        self.stop = QPushButton("停")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self._stop)
        row.addWidget(self.stop)
        self.probe = QPushButton("检查登录")
        self.probe.clicked.connect(self._probe)
        row.addWidget(self.probe)
        row.addStretch(1)
        layout.addLayout(row)

        self.log = QTextBrowser()
        self.log.setMinimumHeight(180)
        layout.addWidget(self.log)

        self.terms = QTextBrowser()
        self.terms.setMinimumHeight(120)
        layout.addWidget(self.terms)
        layout.addWidget(hint_label(
            "这条链路 0 API 花费：看是本机 Edge（有头，你能看见也能接管），"
            "理解走网页版 DeepSeek 的免费额度，学到的东西存本地。"
            "第一次要在弹出的窗口里登录一次，登录态留在 state/browser-profile。"))
        self._worker = None
        self.refresh()

    # ── 动作 ────────────────────────────────────────────────────────────
    def _append(self, line: str) -> None:
        self.log.append(str(line))

    def _probe(self) -> None:
        self.status.setText("检查中：会弹出 Edge，看一下抖音/网页版 DeepSeek 登没登")
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                ctx = webstudy._launch(pw)
                try:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    page.goto(webstudy.DOUYIN_URL, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3500)
                    douyin_out = webstudy._looks_logged_out(page)
                    page.goto(webstudy.DEEPSEEK_URL, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                    ds_out = webstudy._looks_logged_out(page)
                finally:
                    ctx.close()
            self.status.setText(
                f"抖音：{'要登录' if douyin_out else '已登录'}｜"
                f"网页版 DeepSeek：{'要登录' if ds_out else '已登录'}"
                + ("（在弹出的窗口里登一次就好）" if douyin_out or ds_out else ""))
        except Exception as error:  # noqa: BLE001
            self.status.setText(f"检查失败：{error}")

    def _start(self) -> None:
        if self._worker is not None:
            return
        minutes = float(self.minutes.value())
        self.log.clear()
        self._append(f"开始：{minutes:.0f} 分钟，每条约 10 秒，翻页前停 2~5 秒")
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.status.setText("刷抖音中…（窗口可能被别的程序挡住，需要时点一下任务栏）")
        worker = _StudyWorker(minutes, self)
        worker.line.connect(self._append)
        worker.done.connect(self._finished)
        self._worker = worker
        worker.start()

    def _finished(self, out: dict) -> None:
        self._worker = None
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        if out.get("need_login"):
            self.status.setText("要登录：在弹出的 Edge 窗口里登一次抖音 / 网页版 DeepSeek，再点开始")
            self._append(str(out.get("error") or ""))
        elif out.get("stopped"):
            self.status.setText("已手动停止")
        elif out.get("ok") is False:
            self.status.setText(f"出错：{out.get('error')}")
        else:
            self.status.setText(
                f"完成：{out.get('videos', 0)} 条视频，学到 {out.get('learned', 0)} 个词（0 API 花费）")
        self.refresh()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop_event.set()
            self.status.setText("正在停（这一条刷完就停）")

    # ── 数据 ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        rows = webstudy.load_terms()
        lines = ["<style>body{color:#cfe0f5;font-size:12px}td{padding:3px 6px}</style>",
                 f"<div style='color:#8fa6c4'>自己学到的词 {len(rows)} 个（本地，不花钱）</div>",
                 "<table width='100%'>"]
        for row in rows[:25]:
            lines.append(
                f"<tr><td>{str(row.get('title'))[:14]}</td>"
                f"<td style='color:#9db4d0'>{str(row.get('usage'))[:52]}</td>"
                f"<td style='color:#7b8ea8'>{str(row.get('updated_at'))[:10]}</td></tr>")
        lines.append("</table>")
        if not rows:
            lines.append("<div style='color:#7b8ea8'>还没有：刷一次抖音、或者点上面「检查登录」之后就会长出来。</div>")
        self.terms.setHtml("".join(lines))
