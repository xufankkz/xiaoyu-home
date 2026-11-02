# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""情绪面板（v35 重构）：四张卡片 + 左右分栏刻度 + 历史曲线 + 事件表。

旧版是 12 条刻度 + 几段长文本从上往下堆，一屏放不下、也看不出「现在到底怎么了」。
新版按看板思路分四段：

    ① 卡片：当前情绪 / 内稳态 / 激素 / 动态性格 —— 一眼四句结论；
    ② 左栏：五维情绪刻度 + 策略（回不回/延迟/上限/碎碎念）；
    ③ 右栏：内稳态三条 + 激素四条 + 性格偏移；
    ④ 底部：历史曲线（按天）+ 最近奖罚事件表。

数据仍只走 vector / homeostasis / hormones / policy，口径不变。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget)

from core import homeostasis, hormones
from core.emotion import policy as policy_mod, reward as reward_mod, vector
from core.memory import longterm
from core.repo import emotion as emotion_repo
from ui.dashboard import ACCENT, SectionTitle, StatCard, fill_table, make_table
from ui.widgets import MiniBar, Sparkline, hint_label

DIMS = [("joy", "愉悦", "#4ade80"), ("happiness", "幸福", "#38bdf8"),
        ("sadness", "难过", "#f87171"), ("inferiority", "自卑", "#a78bfa"),
        ("shyness", "害羞", "#f472b6")]
NEEDS = [("energy", "能量", "#fbbf24"),
         ("social_connection", "社交连接", "#60a5fa"),
         ("cognitive_novelty", "新鲜感", "#a3e635")]
HORMONES = [("dopamine", "多巴胺", "#f472b6"), ("cortisol", "皮质醇", "#fb7185"),
            ("oxytocin", "催产素", "#38bdf8"), ("serotonin", "血清素", "#34d399")]
MOTIVE_CN = {"fatigue": "疲劳", "loneliness": "孤独", "boredom": "无聊"}
HORMONE_CN = {key: name for key, name, _ in HORMONES}


class EmotionPanel(QWidget):
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

        # ── ① 卡片 ──
        self.cards: dict[str, StatCard] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for col, (key, title, color) in enumerate([
            ("emotion", "当前情绪", ACCENT[4]),
            ("needs", "内稳态", ACCENT[5]),
            ("hormone", "激素", ACCENT[2]),
            ("persona", "动态性格", ACCENT[0]),
        ]):
            card = StatCard(title, color=color)
            self.cards[key] = card
            grid.addWidget(card, 0, col)
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        # ── ②③ 左右分栏 ──
        columns = QHBoxLayout()
        columns.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(SectionTitle("情绪五维（0–100）"))
        self.bars = {}
        for key, name, color in DIMS:
            bar = MiniBar(name, color)
            self.bars[key] = bar
            left.addWidget(bar)
        self.policy = hint_label("")
        left.addWidget(self.policy)
        left.addWidget(SectionTitle("情绪 → 2号记忆 / 说话 / 碎碎念"))
        self.gates = hint_label("")
        left.addWidget(self.gates)
        left.addStretch(1)
        columns.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(4)
        right.addWidget(SectionTitle("内稳态（身体需求，每分钟自己走）"))
        self.need_bars = {}
        for key, name, color in NEEDS:
            bar = MiniBar(name, color)
            self.need_bars[key] = bar
            right.addWidget(bar)
        self.motive = hint_label("")
        right.addWidget(self.motive)

        right.addWidget(SectionTitle("激素（基线会随长期状态漂移）"))
        self.hormone_bars = {}
        for key, name, color in HORMONES:
            bar = MiniBar(name, color)
            self.hormone_bars[key] = bar
            right.addWidget(bar)
        self.hormone_note = hint_label("")
        right.addWidget(self.hormone_note)
        right.addStretch(1)
        columns.addLayout(right, 2)

        layout.addLayout(columns)

        # ── ④ 历史 + 事件表 ──
        layout.addWidget(SectionTitle("情绪历史（按天，五条曲线）"))
        self.spark = Sparkline()
        self.spark.setMinimumHeight(130)
        layout.addWidget(self.spark)

        layout.addWidget(SectionTitle("最近奖罚事件"))
        self.events = make_table(["时间", "触发", "情绪变化", "RPE"], stretch_col=2)
        self.events.setMinimumHeight(150)
        layout.addWidget(self.events)

    # ── 刷新 ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        st = vector.status()
        hs = homeostasis.status()
        hm = hormones.status()
        per = hm["personality"]
        delta = hm["deltas"]

        self.cards["emotion"].set(
            f"{st['dominant_cn']}",
            f"愉悦 {st['values'].get('joy', 0):.0f}｜幸福 {st['values'].get('happiness', 0):.0f}"
            f"｜难过 {st['values'].get('sadness', 0):.0f}\n当前强度最高的一维",
        )
        self.cards["needs"].set(
            MOTIVE_CN.get(hs["dominant"], "平静"),
            f"能量 {hs['values']['energy'] * 100:.0f}｜社交 {hs['values']['social_connection'] * 100:.0f}"
            f"｜新鲜 {hs['values']['cognitive_novelty'] * 100:.0f}\n"
            f"表达欲 {hs['expressiveness']:.2f}"
            f"｜好奇心熔断 {'是' if hs.get('fuse') else '否'}",
        )
        self.cards["hormone"].set(
            f"皮质醇 {hm['hormones']['cortisol']['value']:.2f}",
            f"多巴胺 {hm['hormones']['dopamine']['value']:.2f}"
            f"｜催产素 {hm['hormones']['oxytocin']['value']:.2f}"
            f"｜血清素 {hm['hormones']['serotonin']['value']:.2f}"
            f"\n熔断：{'是（允许悲伤，不许伤人）' if hm['fused'] else '否'}",
        )
        self.cards["persona"].set(
            f"外向 {per['extraversion']:.2f}",
            f"神经质 {per['neuroticism']:.2f}｜宜人 {per['agreeableness']:.2f}\n"
            f"相对初始 {delta['extraversion']:+.2f} / {delta['neuroticism']:+.2f}"
            f" / {delta['agreeableness']:+.2f}",
        )

        for key, value in st["values"].items():
            if key in self.bars:
                self.bars[key].set_value(value)
        for key, bar in self.need_bars.items():
            bar.set_value(float(hs["values"].get(key, 0)) * 100)
        for key, bar in self.hormone_bars.items():
            bar.set_value(float(hm["hormones"].get(key, {}).get("value", 0)) * 100)

        pol = policy_mod.summary()
        self.policy.setText(
            f"回不回：{'回' if pol['reply'] else '不回（不理人）'}｜延迟 {pol['delay_ms'] / 1000:.1f}s｜"
            f"上限 {pol['max_chars']} 字｜语气 {pol['tone']}｜碎碎念 {'开' if pol['chitchat'] else '停'}\n"
            f"{pol['reason']}"
        )
        motives = hs.get("motives") or {}
        self.motive.setText(
            "｜".join(f"{MOTIVE_CN.get(k, k)} {float(v):.2f}" for k, v in motives.items())
            + f"｜设定点 能量 {hs['setpoints']['energy']:.1f}"
            f" / 社交 {hs['setpoints']['social_connection']:.1f}"
            f" / 新鲜 {hs['setpoints']['cognitive_novelty']:.1f}"
        )
        bases = "｜".join(
            f"{HORMONE_CN.get(key, key)}基线 {float(hm['hormones'][key]['baseline']):.2f}"
            for key in hm["hormones"])
        self.hormone_note.setText(f"{bases}｜性格演化 {hm['ticks']} 次心跳")

        self.spark.set_series(emotion_repo.snapshot_daily(30))
        rows = []
        for ev in reward_mod.recent(8):
            rows.append([
                str(ev["t"])[5:16].replace("T", " "),
                str(ev["trigger"]),
                " ".join(f"{k}{v:+.0f}" for k, v in (ev["delta"] or {}).items()),
                f"{ev['rpe_weight']:.2f}",
            ])
        fill_table(self.events, rows)

        gates = longterm.gates(vector.current())
        self.gates.setText("｜".join(
            f"{g['category']}{'✓' if g['open'] else '·'}{g['value']:.0f}/{g['threshold']:.0f}"
            for g in gates
        ) + "\n（✓ = 该分类此刻够格被翻出来）")
