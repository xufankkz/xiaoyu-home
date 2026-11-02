# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""花费面板（v35 重构）：卡片 → 图表 → 表格 → 说明栏，四段式看板。

为什么重写而不是修补：
  旧排版是「一堆 QLabel 叠在一起 + 一段 HTML 表格」，长说明压在表格上，窗口一缩就重叠。
  新排版把信息按「一眼看懂」的层次拆开：
    ① 顶部 4 张卡片：今天 / 本月累计 / 夜学 / 本地缓存 —— 只看数字就知道花没花多；
    ② 中部两张图：近 7 天走势（柱）+ 功能占比（环）；
    ③ 底部明细表：功能模块 / 次数 / 实际 / 均实际 / 均预估 / 偏差 / 占比；
    ④ 最底一行：状态摘要 + (i) 悬停看完整计费规则 + 导出按钮（长文本永不压在表格上）。
  数据仍然只走 `core.cost`（summary / attribute / message_split / curiosity_stats），口径不变。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from core import cost, settings
from ui.dashboard import (ACCENT, Donut, InfoBar, MUTED, SectionTitle, StatCard, TrendBars,
                          fill_table, make_table, money)
from ui.widgets import hint_label

RULE_TIP = (
    "【计费口径】QQ 聊天取桥接账本，后台取 llm_usage.jsonl：\n"
    "· 缓存命中 ¥0.02/M、未命中 ¥1/M、输出 ¥4/M；工作日高峰（9–12、14–18）单价 ×2。\n"
    "· 预估 = 调用前按字数与历史输出长度算；实际 = 用真实 usage 回算。两者不互相冒充，"
    "偏差列就是「预估准不准」。\n"
    "· 桥接从 v14 起给每轮打标签：对话回合记「普通消息」；「未分类」是早期没有任务的调用。\n"
    "· 语音是本地 VoxCPM 合成，不产生模型费用（只计对话轮次）。\n"
    "· 看图分三档：静态图 / 动图抽帧 / 网页浏览截图；命中本地表情字典时 0 元。"
)

DEVIATION_COLOR = {"fail": "#f87171", "warn": "#fbbf24", "ok": "#9fd6a0", "none": "#7b8ea8"}


class CostPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 内容整体放进滚动区：窗口矮的时候「滚动」而不是把卡片压扁/压重叠
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

        # ── ① 核心指标卡片 ──
        self.cards: dict[str, StatCard] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        specs = [
            ("today", "今日花费", ACCENT[0]),
            ("month", "本月累计", ACCENT[1]),
            ("learn", "夜学（后台）", ACCENT[2]),
            ("cache", "本地缓存命中", ACCENT[6]),
        ]
        for col, (key, title, color) in enumerate(specs):
            card = StatCard(title, color=color)
            self.cards[key] = card
            grid.addWidget(card, 0, col)
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        # ── ② 图表 ──
        charts = QHBoxLayout()
        charts.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(SectionTitle("近 7 天花费"))
        self.trend = TrendBars()
        left.addWidget(self.trend, 1)
        charts.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(4)
        right.addWidget(SectionTitle("按功能归因"))
        self.donut = Donut()
        right.addWidget(self.donut, 1)
        charts.addLayout(right, 2)

        layout.addLayout(charts, 3)

        # ── ③ 明细表 ──
        layout.addWidget(SectionTitle("明细（功能 / 次数 / 花费 / 偏差 / 占比）"))
        self.table = make_table(
            ["功能模块", "调用次数", "实际花费", "均实际", "均预估", "偏差", "占比"], stretch_col=0)
        self.table.setMinimumHeight(240)
        layout.addWidget(self.table, 2)

        # ── ④ 说明栏 + 导出（长文本在 (i) 的 tooltip 里，绝不压在表格上）──
        self.bar = InfoBar(RULE_TIP, "—")
        self.export_btn = QPushButton("导出 CSV")
        self.export_btn.setObjectName("Primary")
        self.export_btn.clicked.connect(self._export)
        self.bar.add_action(self.export_btn)
        layout.addWidget(self.bar)

        self.empty_hint = hint_label("")
        self.empty_hint.setVisible(False)
        layout.addWidget(self.empty_hint)

        self.refresh()

    # ── 导出 ────────────────────────────────────────────────────────────
    def _export(self) -> None:
        try:
            out = cost.export_csv(str(settings.STATE / "cost_export.csv"))
            self.bar.set_text(f"已导出：{out}")
        except Exception as exc:  # noqa: BLE001
            self.bar.set_text(f"导出失败：{exc}")

    # ── 刷新 ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        try:
            self._refresh_summary()
        except Exception as exc:  # noqa: BLE001
            self.cards["today"].set("读不到", f"{exc}")
        try:
            self._refresh_tags()
        except Exception as exc:  # noqa: BLE001
            self.bar.set_text(f"归因读不到：{exc}")

    def _refresh_summary(self) -> None:
        d = cost.summary()
        t, m, a = d["today"], d["month"], d["all"]
        self.cards["today"].set(
            money(t["total"]),
            f"私聊 {money(t['private'])}｜群聊 {money(t['group'])}｜后台 {money(t['engine'])}\n"
            f"今天 {t['turns']} 轮聊天",
        )
        self.cards["month"].set(
            money(m["total"]),
            f"累计 {money(a['total'])}｜共 {a['turns']} 轮聊天\n昨天 {money(d['yesterday']['total'])}",
        )
        le = d.get("learning") or {}
        if le.get("last_run"):
            self.cards["learn"].set(
                money(le["last_run"]),
                f"近 5 次均 {money(le['avg_recent'])}｜单次均价 {money(le['per_call'])}\n"
                f"{le['last_run_at']}，{le['last_run_calls']} 次调用",
            )
        else:
            self.cards["learn"].set("—", f"最近没有夜学记录\n单次均价 {money(le.get('per_call', 0))}")
        self._refresh_cache_card()

    def _refresh_cache_card(self) -> None:
        """本地缓存：表情字典 + 梗百科自学的词 —— 这些命中都不产生模型费用。"""
        parts, hits = [], 0
        try:
            from core import sticker

            st = sticker.status()
            hits += int(st.get("reused") or 0)
            parts.append(f"表情字典 {st.get('total', 0)} 张（复用 {st.get('reused', 0)}）")
        except Exception:
            pass
        try:
            from core import memewiki

            ms = memewiki.status()
            hits += int(ms.get("learned") or 0) + int(ms.get("user_terms") or 0)
            parts.append(f"梗词 本地 {ms.get('count', 0)}／自学 {ms.get('user_terms', 0)}")
        except Exception:
            pass
        if not parts:
            self.cards["cache"].set("无额外花销", "读不到本地缓存")
            return
        self.cards["cache"].set(
            f"{hits} 次命中" if hits else "无额外花销",
            "｜".join(parts) + "\n命中即 0 元：这些地方没有产生模型调用",
        )

    def _refresh_tags(self) -> None:
        data = cost.attribute()
        by_tag = data.get("by_tag") or []

        # 图表：近 7 天（用 by_day，缺的日子补 0）
        days = (data.get("by_day") or [])[-7:]
        self.trend.set_data([(str(day["day"])[5:], float(day["cost"])) for day in days])

        # 图表：功能占比（只画有花费的；0 元的仍留在表格里）
        raw = [(item["name"], float(item["cost"])) for item in by_tag if float(item["cost"]) > 0]
        # 占比 <1% 的合并成「其他」：环形图上 0.0% 的小块只会让人眼花
        own_total = sum(value for _, value in raw) or 1.0
        big = [(name, value) for name, value in raw if value / own_total >= 0.01]
        small = [(name, value) for name, value in raw if value / own_total < 0.01]
        if len(small) > 1:
            big.append(("其他", sum(value for _, value in small)))
            slices = big
        else:
            slices = raw
        total_cost = float(data["total"].get("cost") or 0)
        self.donut.set_data(slices, center=f"合计 {money(total_cost)}")

        # 表格
        rows, colors = [], {}
        for index, item in enumerate(by_tag):
            dev = item.get("deviation") or {}
            pct = dev.get("pct")
            rows.append([
                item["name"],
                f"{item['calls']:,}",
                money(item["cost"]),
                money(item["avg_actual"]),
                money(item["avg_estimated"]),
                "" if pct is None else f"{pct * 100:+.0f}%",
                f"{item['share'] * 100:.1f}%",
            ])
            colors[(index, 5)] = DEVIATION_COLOR.get(dev.get("level", "none"), MUTED)
        if by_tag:
            rows.append([
                "合计", f"{data['total']['calls']:,}", money(total_cost), "", "", "",
                "100%",
            ])
        fill_table(self.table, rows, colors=colors)
        for c in range(self.table.columnCount()):
            item = self.table.item(len(rows) - 1, c) if rows else None
            if item is not None:
                font = item.font()
                font.setBold(True)
                item.setFont(font)

        total = data["total"]
        dev = total.get("deviation") or {}
        pct = dev.get("pct")
        bits = [
            f"共 {total['calls']:,} 次调用",
            f"{total['tokens']:,} token",
            f"实际 {money(total['cost'])}",
        ]
        if pct is not None:
            level = {"ok": "正常", "warn": "偏高", "fail": "偏差过大"}.get(dev.get("level"), "无预估")
            bits.append(f"预估 {money(total['estimated'])}（偏差 {pct * 100:+.0f}%，{level}）")
        if data.get("unclassified"):
            bits.append(f"未分类历史 {data['unclassified']} 条")
        self.bar.set_text("｜".join(bits))

        self.empty_hint.setVisible(not by_tag)
        if not by_tag:
            self.empty_hint.setText(
                "还没有 cost_event 记录：跑一次对话、或点「导出 CSV」会把桥接账本导进来。")
