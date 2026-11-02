# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""记忆系统面板：长期（1/2 号）/ 短期（日记、待总结）/ 瞬时 / 语言 / 情绪。"""

from __future__ import annotations

import html

from PySide6.QtWidgets import QComboBox, QLabel, QTextBrowser, QVBoxLayout, QWidget

from core.entity import Layer
from core.memory import instant as instant_mod
from core.memory import longterm, shortterm
from core.language import memory as langmem
from core.text import char_count, clip
from ui.widgets import Collapsible, hint_label


class HtmlBox(QTextBrowser):
    def __init__(self, height: int = 180, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setOpenExternalLinks(False)

    def set_html(self, body: str) -> None:
        self.setHtml(f"<style>body{{color:#cfe0f5;font-size:12px;line-height:1.65}}"
                     f"h3{{color:#e8f2ff;font-size:13px}} .meta{{color:#7b8ea8}}</style>{body}")


def _esc(text: str) -> str:
    return html.escape(text or "").replace("\n", "<br>")


class Lt1View(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.box = HtmlBox(200)
        layout.addWidget(self.box)

    def refresh(self) -> None:
        parts = []
        for group in longterm.lt1_groups():
            parts.append(f"<h3>{group['title']}（{group['count']}）</h3>")
            if not group["items"]:
                parts.append('<div class="meta">（空）</div>')
            for item in group["items"][:20]:
                parts.append(f"<div>· <b>{_esc(item['summary'])}</b><br>"
                             f"<span class='meta'>{item['t_created'][:10]} · {item['chars']} 字 · "
                             f"显著 {item['salience']}</span></div><br>")
        self.box.set_html("".join(parts))


class Lt2View(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.box = HtmlBox(240)
        layout.addWidget(self.box)

    def refresh(self) -> None:
        parts = []
        for group in longterm.lt2_groups():
            parts.append(f"<h3>{group['category']}（{group['count']} 条 / {group['chars']} 字）</h3>")
            if not group["items"]:
                parts.append('<div class="meta">（空）</div>')
            for item in group["items"][:12]:
                parts.append(
                    f"<div>· <span class='meta'>{_esc(item['date'])}｜{_esc(item['labels'])}</span><br>"
                    f"{_esc(clip(item['text'], 160))}</div><br>")
        self.box.set_html("".join(parts))


class DiaryView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._show)
        self.box = HtmlBox(220)
        layout.addWidget(self.combo)
        layout.addWidget(self.box)
        self._days: list[dict] = []

    def refresh(self) -> None:
        self._days = shortterm.diary_list()
        current = self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        for day in self._days:
            self.combo.addItem(f"{day['date']}（{day['entries']} 段 / {day['chars']} 字）", day["date"])
        if current:
            idx = self.combo.findData(current)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        self.combo.blockSignals(False)
        self._show()

    def _show(self) -> None:
        day = self.combo.currentData()
        if not day:
            self.box.set_html('<div class="meta">还没有日记。</div>')
            return
        data = shortterm.diary_get(day)
        body = _esc((data or {}).get("content", ""))
        self.box.setHtml(f"<style>body{{color:#cfe0f5;font-size:12px;line-height:1.7}}</style>"
                         f"<div class='meta'>只读，不参与主动读取；她想看会自己翻。</div>{body}")


class PendingView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.head = hint_label("")
        self.box = HtmlBox(200)
        layout.addWidget(self.head)
        layout.addWidget(self.box)

    def refresh(self) -> None:
        view = shortterm.pending_view(30)
        stats = view["stats"]
        self.head.setText(f"等待中 {stats['pending']} 条 · 可升级 {stats['ready']} 条 · "
                          f"满 {stats['min_days']} 天压成 {stats['compress_target']}±20 字")
        rows = []
        for row in view["pending"][:20]:
            rows.append(
                f"<div>· <span class='meta'>{_esc(row['date'])}｜{row['chars']} 字｜{_esc(row['emotion'])}"
                f"｜还有 {row['ready_in']:g} 天</span><br>{_esc(clip(row['text'], 120))}</div><br>")
        self.box.set_html("".join(rows) or '<div class="meta">（空）</div>')


class InstantView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.head = hint_label("")
        self.box = HtmlBox(220)
        layout.addWidget(self.head)
        layout.addWidget(self.box)

    def refresh(self) -> None:
        view = instant_mod.view(80)
        self.head.setText(f"≤{view['hot_hours']:g} 小时原文 {len(view['hot'])} 条 · "
                          f"{view['hot_hours']:g}~{view['purge_hours']:g} 小时可摘要 {len(view['warm'])} 条 · "
                          f"已清空留墓碑 {view['tombstones']} 条")
        rows = ["<h3>热区（原文）</h3>"]
        for item in view["hot"][:25]:
            sender = "老大" if item["is_owner"] else item["sender"]
            rows.append(f"<div><span class='meta'>{_esc(item['t_created'][5:16])} {_esc(sender)}</span><br>"
                        f"{_esc(item['text'])}</div><br>")
        rows.append("<h3>温区（摘要/降级）</h3>")
        for item in view["warm"][:15]:
            rows.append(f"<div><span class='meta'>{_esc(item['t_created'][5:16])} · L{item['level']}"
                        f" · {item['age_hours']:g}h</span><br>{_esc(item['text'])}</div><br>")
        self.box.set_html("".join(rows))


class BookView(QWidget):
    """语言记忆 / 情绪记忆共用。"""

    def __init__(self, layer: str, parent=None):
        super().__init__(parent)
        self.layer = layer
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.head = hint_label("")
        self.box = HtmlBox(200)
        layout.addWidget(self.head)
        layout.addWidget(self.box)

    def refresh(self) -> None:
        st = langmem.status(self.layer)
        self.head.setText(
            f"{st['chars']} / {st['trigger']} 字 · {st['count']} 条 · "
            f"{'冷却中（剩 %.1f 天）' % st['days_left'] if st['locked'] else '可更新'}"
        )
        rows = [f"<h3>{st['title']}</h3>"]
        for rec in st["recent"]:
            rows.append(f"<div>· <span class='meta'>{_esc(rec['head'])}（{rec['chars']} 字）</span><br>"
                        f"{_esc(rec['preview'])}</div><br>")
        self.box.set_html(""".join(rows) or '<div class="meta">（空）</div>'""")


class MemoryTree(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._children: list[QWidget] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.long_term = Collapsible("长期记忆", "mem.long")
        self.long_term.set_builder(self._build_long)
        layout.addWidget(self.long_term)

        self.short_term = Collapsible("短期记忆", "mem.short")
        self.short_term.set_builder(self._build_short)
        layout.addWidget(self.short_term)

        self.instant = Collapsible("瞬时记忆", "mem.instant")
        instant_view = InstantView()
        self.instant.set_builder(lambda parent: self._wrap(parent, instant_view))
        layout.addWidget(self.instant)

        self.language = Collapsible("语言记忆", "mem.language")
        lang_view = BookView(Layer.LANGUAGE.value)
        self.language.set_builder(lambda parent: self._wrap(parent, lang_view))
        layout.addWidget(self.language)

        self.emotion_mem = Collapsible("情绪记忆", "mem.emotion")
        emo_view = BookView(Layer.EMOTION.value)
        self.emotion_mem.set_builder(lambda parent: self._wrap(parent, emo_view))
        layout.addWidget(self.emotion_mem)

        from ui.panels.residual import ResidualView

        self.residual = Collapsible("思维链 · 最近想法", "mem.residual")
        residual_view = ResidualView()
        self.residual.set_builder(lambda parent: self._wrap(parent, residual_view))
        layout.addWidget(self.residual)

        self._children.extend([instant_view, lang_view, emo_view, residual_view])

    def _wrap(self, parent, widget: QWidget) -> QWidget:
        widget.refresh()
        widget.setParent(parent)
        return widget

    def _build_long(self, parent) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        lt1 = Collapsible("1号长期记忆（性格/朋友/老大）", "mem.lt1", depth=1)
        view1 = Lt1View()
        lt1.set_builder(lambda p: self._wrap(p, view1))
        layout.addWidget(lt1)

        lt2 = Collapsible("2号长期记忆（按情绪分类）", "mem.lt2", depth=1)
        view2 = Lt2View()
        lt2.set_builder(lambda p: self._wrap(p, view2))
        layout.addWidget(lt2)

        self._children += [view1, view2]
        return holder

    def _build_short(self, parent) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        diary = Collapsible("大肥鱼的日记本", "mem.diary", depth=1)
        diary_view = DiaryView()
        diary.set_builder(lambda p: self._wrap(p, diary_view))
        layout.addWidget(diary)

        pending = Collapsible("等待总结升为 2 号的内容", "mem.pending", depth=1)
        pending_view = PendingView()
        pending.set_builder(lambda p: self._wrap(p, pending_view))
        layout.addWidget(pending)

        self._children += [diary_view, pending_view]
        return holder

    def refresh(self) -> None:
        for child in self._children:
            try:
                if child.isVisible():
                    child.refresh()
            except Exception:
                pass
        for section in (self.long_term, self.short_term, self.instant, self.language,
                        self.emotion_mem, self.residual):
            section.refresh()
