# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""梗百科：本地 6668 条梗词库的查词台 + 两个开关（v24）。

为什么要有这一页：
  「她遇到新词别硬猜」这件事，出了问题只能看日志的话太难查。这里把三件事摆在一起——
  词库在不在、这个词查出来长什么样（和模型看到的是同一段文本）、她最近用过哪些词。

两个开关写的是**桥接**的 config.json（socialV2.meme.*）：
  · 本地预查：每条消息进来先查一次词库，命中就注入（0 花费，查不到不占 token）
  · 玩梗模式：提示词里追加「自然地用网络流行语」，关掉就回到原来的说话风格
  改完要重启桥接才生效（提示词里的块是启动时读的？不是——每轮都读，只是保险起见）。
"""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from core import memewiki
from ui.panels.qqbridge import _read_json, _write_json, bridge_config_path
from ui.widgets import hint_label


class MemeWikiPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status = hint_label("")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        reach = QLabel("查一个词")
        row.addWidget(reach)
        self.query = QLineEdit()
        self.query.setPlaceholderText("例如「奶蛙」「yyds」「绝绝子是什么」")
        self.query.returnPressed.connect(self._search)
        row.addWidget(self.query, 1)
        self.do = QPushButton("查一下")
        self.do.setObjectName("Primary")
        self.do.clicked.connect(self._search)
        row.addWidget(self.do)
        layout.addLayout(row)

        self.result = QTextBrowser()
        self.result.setMinimumHeight(150)
        layout.addWidget(self.result)

        switches = QHBoxLayout()
        self.preload = QCheckBox("本地预查（每条消息先查词库，命中才注入）")
        self.preload.stateChanged.connect(self._save_switches)
        self.mode = QCheckBox("玩梗模式（自然地用网络流行语）")
        self.mode.stateChanged.connect(self._save_switches)
        switches.addWidget(self.preload)
        switches.addWidget(self.mode)
        switches.addStretch(1)
        layout.addLayout(switches)

        self.learned = QTextBrowser()
        self.learned.setMinimumHeight(140)
        layout.addWidget(self.learned)
        layout.addWidget(hint_label(
            "词库本身是只读快照（不联网、不花钱）；她用过的词、你对词条的补充记在 state/memewiki.json。"
            "查不到的词会明确说「查不到」，不会编。"))
        self.refresh()

    # ── 数据 ────────────────────────────────────────────────────────────
    def _switches(self) -> tuple[bool, bool]:
        try:
            bridge = _read_json(bridge_config_path()) or {}
            meme = ((bridge.get("socialV2") or {}).get("meme") or {})
        except Exception:
            meme = {}
        return (meme.get("enabled") is not False, meme.get("modeEnabled") is not False)

    def _save_switches(self) -> None:
        if getattr(self, "_loading", False):
            return
        try:
            path = bridge_config_path()
            bridge = _read_json(path) or {}
            social = bridge.get("socialV2") if isinstance(bridge.get("socialV2"), dict) else {}
            meme = social.get("meme") if isinstance(social.get("meme"), dict) else {}
            meme["enabled"] = bool(self.preload.isChecked())
            meme["modeEnabled"] = bool(self.mode.isChecked())
            social["meme"] = meme
            bridge["socialV2"] = social
            _write_json(path, bridge)
            self._flash("已保存（重启桥接后生效）")
        except Exception as error:
            self._flash(f"保存失败：{error}")

    def _flash(self, text: str) -> None:
        st = memewiki.status()
        self.status.setText(f"{self._head_text(st)}｜{text}")

    def _head_text(self, st: dict) -> str:
        if not st.get("available"):
            return "词库没装（learning/memewiki/memes.db 不存在）"
        return (f"本地词库 {st['count']} 条（快照 {st['built_at'][:10]}）"
                f"｜她用过的词 {st['learned']} 个")

    # ── 交互 ────────────────────────────────────────────────────────────
    def _search(self) -> None:
        raw = (self.query.text() or "").strip()
        if not raw:
            self._flash("先写一个词")
            return
        query = memewiki.extract_query(raw)
        if not query:
            self.result.setPlainText("看不出要查哪个词 —— 换个短点的说法试试（比如直接写一个词）。")
            return
        data = memewiki.search(query, record=False)
        if not data.get("found"):
            self.result.setPlainText(
                f"「{query}」查不到（{data.get('reason') or '词库里没有'}）。\n"
                "查不到时她只会说不知道 —— 这是有意的：编一个梗比不认识更糟。")
            return
        block = memewiki.block(query, record=False)
        head = (f"「{query}」→ 词条「{data['title']}」"
                f"（{'同名' if data['match'] == 'exact' else '最接近'}）\n")
        self.result.setPlainText(head + block)

    def refresh(self) -> None:
        st = memewiki.status()
        self._loading = True
        try:
            enabled, mode = self._switches()
            self.preload.setChecked(enabled)
            self.mode.setChecked(mode)
        finally:
            self._loading = False
        self.status.setText(self._head_text(st))

        rows = ["<style>body{color:#cfe0f5;font-size:12px}td{padding:3px 6px}</style>",
                "<table width='100%'><tr style='color:#8fa6c4'><td>词</td><td align='right'>用</td>"
                "<td>最近</td><td>我的理解 / 用过的语境</td></tr>"]
        learned = memewiki.all_learned()
        for item in learned[:40]:
            note = item.get("note") or "；".join((item.get("contexts") or [])[-2:])
            rows.append(
                f"<tr><td>{item['title'][:14]}</td>"
                f"<td align='right'>{item['hits']}</td>"
                f"<td style='color:#7b8ea8'>{str(item.get('last') or '')[:16]}</td>"
                f"<td style='color:#9db4d0'>{str(note)[:44]}</td></tr>")
        rows.append("</table>")
        if not learned:
            rows.append("<div style='color:#7b8ea8'>还是空的：她查过一次词之后，这里就会记下来。</div>")
        self.learned.setHtml("".join(rows))
