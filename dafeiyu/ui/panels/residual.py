# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""思维链 · 最近想法：残留池 + 冷启动注入审计。"""

from __future__ import annotations

import html

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from core import settings
from core.emotion import vector
from core.thought import residual as residual_mod
from core.thought import session as session_mod
from ui.widgets import hint_label


class ResidualView(QWidget):
    def __init__(self, conv: str = "private:owner", parent=None):
        super().__init__(parent)
        self.conv = conv
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        self.toggle = QCheckBox("启用残留通道")
        self.toggle.toggled.connect(self._toggle)
        row.addWidget(self.toggle)
        row.addStretch(1)
        self.end_btn = QPushButton("结束本会话")
        self.end_btn.clicked.connect(self._end_session)
        row.addWidget(self.end_btn)
        self.cold_btn = QPushButton("试一次冷启动")
        self.cold_btn.clicked.connect(self._cold_start)
        row.addWidget(self.cold_btn)
        layout.addLayout(row)

        self.status = hint_label("")
        layout.addWidget(self.status)
        self.injected = hint_label("")
        layout.addWidget(self.injected)
        self.result = hint_label("")
        layout.addWidget(self.result)

        self.list = QTextBrowser()
        self.list.setMinimumHeight(190)
        layout.addWidget(self.list)

    # ── 动作 ──
    def _toggle(self, flag: bool) -> None:
        cfg = settings.load("residual")
        cfg["enabled"] = bool(flag)
        settings.save("residual", cfg)
        self.refresh()

    def _end_session(self) -> None:
        out = session_mod.end(self.conv, reason="manual")
        self.result.setText(f"会话已结束：{out.get('nodes', 0)} 个节点转入残留池"
                            if out.get("ended") else "当前没有进行中的会话")
        self.refresh()

    def _cold_start(self) -> None:
        # 先清掉本会话的注入记录，才能重放一次
        data = settings.read_state("residual_state.json", {})
        injected = data.get("injected") or {}
        tag = (session_mod.current(self.conv) or {}).get("session_tag") or ""
        injected.pop(tag, None)
        settings.write_state("residual_state.json", {**data, "injected": injected})
        out = residual_mod.cold_start(self.conv, vec=vector.current())
        n = len(out.get("nodes") or [])
        self.result.setText(f"冷启动注入了 {n} 条" + ("（池子为空或都已超期）" if n == 0 else ""))
        self.refresh()

    # ── 刷新 ──
    def refresh(self) -> None:
        st = residual_mod.status(vector.current())
        self.toggle.blockSignals(True)
        self.toggle.setChecked(bool(st["enabled"]))
        self.toggle.blockSignals(False)

        sess = session_mod.current(self.conv) or {}
        self.status.setText(
            f"池内 {st['pool']} / {st['capacity']} 条｜档位 原文 {st['counts'].get('0', 0)} · "
            f"摘要 {st['counts'].get('1', 0)} · 骨架 {st['counts'].get('2', 0)}\n"
            f"当前会话：{sess.get('session_tag') or '（无）'}｜第 {sess.get('turns', 0)} 轮｜"
            f"链 {str(sess.get('chain_id') or '')[:8]}｜空闲阈值 "
            f"{session_mod.idle_minutes(self.conv):g} 分钟"
        )
        inj = residual_mod.injected_this_session(self.conv)
        nodes = inj.get("nodes") or []
        if nodes:
            self.injected.setText(
                f"本次会话冷启动注入 {len(nodes)} 条（{inj.get('at', '')[:16]}）：" +
                "、".join(f"{n['id'][:6]}·{n['minutes']}分钟前·档{n['tier']}" for n in nodes[:6]))
        else:
            self.injected.setText("本次会话还没有注入过残留节点")

        rows = []
        for e in st["top"]:
            bar_len = max(1, int(round(e["r"] * 20)))
            bar = "█" * bar_len + "·" * (20 - bar_len)
            flag = "未决" if e["is_open"] else ("墓碑" if e["tombstone"] else "已想完")
            rows.append(
                f"<div><b>{e['minutes']} 分钟前</b> "
                f"<span style='color:#7b8ea8'>档 {e['tier']}｜level {e['level']}｜{flag}｜"
                f"{e['emotion_tag']}｜{html.escape(str(e['session_tag'])[-15:])}</span><br>"
                f"<span style='color:#38bdf8;font-family:Consolas,monospace'>{bar}</span> "
                f"<span style='color:#8fa6c4'>{e['r']:.2f}</span><br>"
                f"{html.escape((e['text'] or '')[:110])}</div><br>")
        self.list.setHtml("<style>body{color:#cfe0f5;font-size:12px;line-height:1.6}</style>"
                          + ("".join(rows) or "<div style='color:#7b8ea8'>（残留池是空的："
                                              "先聊两轮，再点「结束本会话」）</div>"))
