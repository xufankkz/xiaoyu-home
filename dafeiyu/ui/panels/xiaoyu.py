"""小鱼控制台 + 思考分区（2026-10-30 新增）

两个面板：
  ConsolePanel —— 一键启动/停止 QQ 那套（SnowLuma + DSH Web + 大肥鱼 + 桥接），并显示四个服务的端口状态
  ThinkingPanel —— 阅读她在 QQ 会话里的「思考」（reasoning）与工具动作，不再让思考流到 QQ

数据来源：
  - 端口状态：本机 TCP 监听
  - 思考流：~/.dsh/sessions/*/<session>/session.v4.jsonl.zstd（DSH 的会话日志，zstd 压缩）
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import hint_label, title_label



class ThinkingPanel(QWidget):
    """读 DSH 会话日志，把她的 reasoning 与发送动作列出来。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        outer.addWidget(title_label("思考分区"))
        outer.addWidget(hint_label("这里是她没发出去的那部分：推演、犹豫、决定。QQ 里只出现最终消息。"))

        bar = QHBoxLayout()
        self.cb_auto = QCheckBox("自动刷新（3 秒）")
        self.cb_auto.setChecked(True)
        self.btn_refresh = QPushButton("立即刷新")
        bar.addWidget(self.cb_auto)
        bar.addWidget(self.btn_refresh)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        outer.addWidget(self.view, 1)

        self.btn_refresh.clicked.connect(self.refresh)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(3000)
        self.refresh()

    def _tick(self) -> None:
        if self.cb_auto.isChecked():
            self.refresh()

    # ── 读取 DSH 会话 ───────────────────────────────────────────────
    @staticmethod
    def _sessions_dir() -> Path | None:
        home = Path(os.environ.get("USERPROFILE", "")) / ".dsh" / "sessions"
        return home if home.exists() else None

    _cache: dict = {}

    def _read_text(self, f: Path) -> str:
        key = (str(f), f.stat().st_mtime)
        if self._cache.get("key") == key:
            return self._cache.get("text", "")
        import zstandard as zstd

        dctx = zstd.ZstdDecompressor()
        with open(f, "rb") as fh, dctx.stream_reader(fh) as reader:
            raw = reader.read()
        text = raw.decode("utf-8", errors="replace")
        self._cache = {"key": key, "text": text}
        return text

    def _latest_qq_session(self) -> Path | None:
        """找她（QQ 会话）的日志：按 mcp__snowluma__ 工具调用次数挑，次数相同取最新。"""
        base = self._sessions_dir()
        if not base:
            return None
        cands = []
        for d in base.iterdir():
            if not d.is_dir():
                continue
            for sub in d.iterdir():
                f = sub / "session.v4.jsonl.zstd"
                if f.exists():
                    cands.append((f.stat().st_mtime, f))
        cands.sort(reverse=True)
        best = None
        for mtime, f in cands[:12]:
            try:
                text = self._read_text(f)
            except Exception:
                continue
            hits = text.count("mcp__snowluma__")
            if hits > 0 and (best is None or hits > best[0]):
                best = (hits, f)
        if best:
            return best[1]
        return cands[0][1] if cands else None

    def refresh(self) -> None:
        f = self._latest_qq_session()
        if not f:
            self.view.setPlainText("没找到 DSH 会话日志（~/.dsh/sessions）。")
            return
        try:
            text = self._read_text(f)
        except Exception as e:  # noqa: BLE001
            self.view.setPlainText(f"读取会话日志失败：{e}")
            return

        events = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue

        out: list[str] = []
        for e in events[-160:]:
            t = e.get("type")
            data = e.get("data") or {}
            ts = e.get("time")
            stamp = datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S") if ts else "--:--:--"
            if t == "assistant/message":
                for block in (data.get("message") or {}).get("content") or []:
                    bt = block.get("type")
                    if bt == "reasoning" and block.get("text"):
                        out.append(f"[{stamp}] 💭 {block['text'].strip()[:800]}")
                    elif bt == "text" and block.get("text"):
                        out.append(f"[{stamp}] 🗒 {block['text'].strip()[:400]}")
            elif t == "tool/call":
                name = data.get("name") or data.get("toolName") or "?"
                args = data.get("arguments") or ""
                out.append(f"[{stamp}] 🔧 {name} {str(args)[:220]}")

        header = f"会话：{f.parent.name}\n" + "-" * 40 + "\n"
        self.view.setPlainText(header + ("\n\n".join(out[-120:]) if out else "（还没有记录）"))
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())
