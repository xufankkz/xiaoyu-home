# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""主界面外壳（v13）：左侧固定导航 + 右侧内容区。

布局
  ┌──────────┬──────────────────────────────────────────────┐
  │ 导航栏    │  页头（标题 / 刷新 / 返回）                    │
  │ 品牌      │  ┌────────────────────┬──────────────────┐   │
  │ 分区列表  │  │ 主页：大立绘        │ 状态 / 花费 / 桌宠 │   │
  │ （选中高亮）│  └────────────────────┴──────────────────┘   │
  │ 底部自检   │  详情页：左侧小桌宠 + 子页列表 + 内容          │
  └──────────┴──────────────────────────────────────────────┘

分区与子页沿用 build_panels() 的返回结构（记忆/学习/语言/情绪/花费/碎碎念/
拒绝权/审计/QQ 接入/思考/语音/联系人/空间/人设），一个都没少。
"""

from __future__ import annotations
import release_paths

import json
import urllib.request
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from core import cost, db, settings
from core.entity import Layer
from ui.avatar import STATES, HeroAvatar, PetWidget, StateAvatar
from ui.panels.audit import AuditPanel
from ui.panels.chitchat import ChitchatPanel
from ui.panels.cost import CostPanel
from ui.panels.curiosity_engine import CuriosityEnginePanel
from ui.panels.emotion import EmotionPanel
from ui.panels.language import BookPanel
from ui.panels.learning import LearningPanel
from ui.panels.learning_quota import LearningQuotaPanel
from ui.panels.memory import (BookView, DiaryView, InstantView, Lt1View, Lt2View, PendingView)
from ui.panels.memory_map import MemoryMapPanel
from ui.panels.qqbridge import QQBridgePanel
from ui.panels.refusal import RefusalPanel
from ui.panels.relationship import RelationshipPanel
from ui.panels.residual import ResidualView
from ui.panels.stickers import StickerPanel
from ui.panels.xiaoyu import ThinkingPanel
from ui.panels.xiaoyu_features import VoicePanel, ContactsPanel, PersonaPanel, QzonePanel
from ui.panels.memewiki import MemeWikiPanel
from ui.panels.about import AboutPanel
from ui.panels.webstudy import WebStudyPanel
from ui.widgets import hint_label

SIDEBAR_W = 216     # 左侧导航固定宽度
RIGHT_W = 340       # 主页右列卡片宽度
SIDE_CARD_W = 208   # 详情页左侧桌宠卡宽度

FUNCTIONS = [
    ("thinking", "思考"),
    ("persona", "人设"),
    ("voice", "语音"),
    ("contacts", "联系人"),
    ("qzone", "空间"),
    ("memory", "记忆"),
    ("learning", "学习"),
    ("language", "语言"),
    ("emotion", "情绪"),
    ("cost", "花费"),
    ("chitchat", "碎碎念"),
    ("refusal", "拒绝权"),
    ("audit", "审计"),
    ("qq", "QQ 接入"),
]

# 导航分组（分区顺序即显示顺序；键必须与 build_panels() 一致）
NAV_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("概览", [("home", "主页")]),
    ("陪伴", [("thinking", "思考"), ("persona", "人设"), ("voice", "语音"),
              ("contacts", "联系人"), ("qzone", "空间")]),
    ("记忆与学习", [("memory", "记忆"), ("learning", "学习"),
                    ("language", "语言"), ("emotion", "情绪")]),
    ("运行与治理", [("cost", "花费"), ("chitchat", "碎碎念"), ("refusal", "拒绝权"),
                    ("audit", "审计"), ("qq", "QQ 接入")]),
]


def _money(v: float) -> str:
    return f"¥{v:.2f}" if abs(v) >= 0.01 else f"¥{v:.3f}"


def _asset_pixmap(name: str, size: int) -> QPixmap | None:
    from ui.avatar import find_asset

    path = find_asset(name)
    if path is None:
        return None
    pix = QPixmap(str(path))
    if pix.isNull():
        return None
    return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    """统一卡片：圆角 + 1px 描边 + 内边距（样式在 theme.qss 的 QFrame#Card）。"""
    card = QFrame()
    card.setObjectName("Card")
    card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(8)
    if title:
        label = QLabel(title)
        label.setObjectName("CardTitle")
        layout.addWidget(label)
    return card, layout


class StateChip(QLabel):
    """状态胶囊：颜色跟着情绪走。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StateChip")
        self.setAlignment(Qt.AlignCenter)
        self.set_state("calm")

    def set_state(self, state: str, text: str = "") -> None:
        label, color = STATES.get(state, STATES["calm"])
        self.setText(text or label)
        self.setStyleSheet(f"color:{color};")


class NavRail(QFrame):
    """左侧导航：品牌 + 分组分区列表 + 底部自检 / 更新。"""

    home_requested = Signal()
    selected = Signal(str)
    repair_clicked = Signal()
    update_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(SIDEBAR_W)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 16, 12, 14)
        root.setSpacing(12)

        # ── 品牌 ──
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setObjectName("BrandLogo")
        logo.setFixedSize(QSize(32, 32))
        pix = _asset_pixmap("logo.png", 32) or _asset_pixmap("xiaoyu_portrait.png", 32)
        if pix is not None:
            logo.setPixmap(pix)
        brand.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        name = QLabel("小鱼")
        name.setObjectName("BrandTitle")
        sub = QLabel("本地 AI 伴侣")
        sub.setObjectName("BrandSub")
        titles.addWidget(name)
        titles.addWidget(sub)
        brand.addLayout(titles)
        brand.addStretch(1)
        root.addLayout(brand)

        # ── 分区列表（可滚动） ──
        scroll = QScrollArea()
        scroll.setObjectName("NavScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("NavHolder")
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(2)

        self.buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for group_title, items in NAV_GROUPS:
            caption = QLabel(group_title)
            caption.setObjectName("NavGroup")
            holder_layout.addSpacing(6)
            holder_layout.addWidget(caption)
            for key, label in items:
                btn = QPushButton(label)
                btn.setObjectName("NavItem")
                btn.setCheckable(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                btn.clicked.connect(lambda _=False, k=key: self._on_item(k))
                self._group.addButton(btn)
                holder_layout.addWidget(btn)
                self.buttons[key] = btn
        holder_layout.addStretch(1)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        # ── 底部 ──
        self.repair_btn = QPushButton("体检 · 修复")
        self.repair_btn.setObjectName("NavAction")
        self.repair_btn.setCursor(Qt.PointingHandCursor)
        self.repair_btn.clicked.connect(self.repair_clicked.emit)
        root.addWidget(self.repair_btn)

        self.update_btn = QPushButton("检查更新")
        self.update_btn.setObjectName("NavAction")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(self.update_clicked.emit)
        root.addWidget(self.update_btn)

        foot = QLabel("本地运行 · 数据不出机器")
        foot.setObjectName("NavFoot")
        foot.setWordWrap(True)
        root.addWidget(foot)

    # ── 交互 ──
    def _on_item(self, key: str) -> None:
        if key == "home":
            self.set_active("home")
            self.home_requested.emit()
            return
        self.set_active(key)
        self.selected.emit(key)

    def set_active(self, key: str) -> None:
        for name, btn in self.buttons.items():
            active = name == key
            if btn.isChecked() != active:
                btn.setChecked(active)
            btn.setProperty("active", "true" if active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def add_extra(self, key: str, label: str) -> None:
        """build_panels() 里多出来的分区兜底加进导航，保证不丢功能。"""
        if key in self.buttons:
            return
        btn = QPushButton(label)
        btn.setObjectName("NavItem")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _=False, k=key: self._on_item(k))
        self._group.addButton(btn)
        holder = self.findChild(QWidget, "NavHolder")
        layout = holder.layout() if holder is not None else None
        if layout is not None:
            layout.insertWidget(max(0, layout.count() - 1), btn)
        self.buttons[key] = btn

    def current(self) -> str:
        for key, btn in self.buttons.items():
            if btn.isChecked():
                return key
        return "home"


class HomePage(QWidget):
    """主页：左边大立绘，右边她现在的状态 / 今日花费 / 摸摸头桌宠。"""

    def __init__(self, shell: "Shell", parent=None):
        super().__init__(parent)
        self.shell = shell
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(16)

        # ── 页头 ──
        head = QHBoxLayout()
        head.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("主页")
        title.setObjectName("PageTitle")
        sub = QLabel("她的状态、花费和入口，一屏看完")
        sub.setObjectName("PageSub")
        titles.addWidget(title)
        titles.addWidget(sub)
        head.addLayout(titles)
        head.addStretch(1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("Primary")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(shell.refresh_all)
        head.addWidget(self.refresh_btn)
        self.repair_btn = QPushButton("体检")
        self.repair_btn.setObjectName("Ghost")
        self.repair_btn.setCursor(Qt.PointingHandCursor)
        self.repair_btn.clicked.connect(shell.run_repair)
        head.addWidget(self.repair_btn)
        self.update_btn = QPushButton("检查更新")
        self.update_btn.setObjectName("Ghost")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(shell.run_update)
        head.addWidget(self.update_btn)
        root.addLayout(head)

        # ── 主体：立绘 + 右列 ──
        body = QHBoxLayout()
        body.setSpacing(16)

        hero_card = QFrame()
        hero_card.setObjectName("HeroCard")
        hero_layout = QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(16, 14, 16, 14)
        hero_layout.setSpacing(2)
        hero_top = QHBoxLayout()
        hero_top.setSpacing(8)
        hero_name = QLabel("小鱼")
        hero_name.setObjectName("HeroName")
        hero_tag = QLabel("大鲸鱼桌宠")
        hero_tag.setObjectName("HeroTag")
        hero_top.addWidget(hero_name)
        hero_top.addWidget(hero_tag)
        hero_top.addStretch(1)
        hero_layout.addLayout(hero_top)

        self.hero = HeroAvatar()
        self.avatar = self.hero  # 兼容旧字段名
        hero_layout.addWidget(self.hero, 1)

        hero_foot = QHBoxLayout()
        hero_foot.setSpacing(10)
        self.hero_meta = QLabel("记忆读取中…")
        self.hero_meta.setObjectName("Hint")
        hero_foot.addWidget(self.hero_meta)
        hero_foot.addStretch(1)
        self.hero_stamp = QLabel("")
        self.hero_stamp.setObjectName("CardLabel")
        hero_foot.addWidget(self.hero_stamp)
        hero_layout.addLayout(hero_foot)
        body.addWidget(hero_card, 1)

        right_holder = QWidget()
        right_holder.setFixedWidth(RIGHT_W)
        right = QVBoxLayout(right_holder)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(14)

        # 她现在
        status_card, status_layout = _card("她现在")
        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        self.state_chip = StateChip()
        chip_row.addWidget(self.state_chip, 0, Qt.AlignLeft)
        self.activity = QLabel("—")
        self.activity.setObjectName("CardValue")
        chip_row.addWidget(self.activity)
        chip_row.addStretch(1)
        status_layout.addLayout(chip_row)
        self.avatar_hint = hint_label("情绪与保护状态在这里刷新")
        status_layout.addWidget(self.avatar_hint)
        right.addWidget(status_card)

        # 今日花费
        cost_card, cost_layout = _card("今日花费")
        cost_row = QHBoxLayout()
        cost_row.setSpacing(10)
        self.total = QLabel("¥0.00")
        self.total.setObjectName("BigValue")
        cost_row.addWidget(self.total)
        cost_row.addStretch(1)
        self.calls = QLabel("0 轮聊天")
        self.calls.setObjectName("CardValue")
        cost_row.addWidget(self.calls, 0, Qt.AlignBottom)
        cost_layout.addLayout(cost_row)
        self.note = QLabel("")
        self.note.setObjectName("Hint")
        self.note.setWordWrap(True)
        cost_layout.addWidget(self.note)
        self.day = QLabel("今天")
        self.day.setObjectName("CardLabel")
        cost_layout.addWidget(self.day)
        right.addWidget(cost_card)

        # 摸摸头
        pet_card, pet_layout = _card("摸摸头")
        self.pet = PetWidget(size=104)
        self.pet.setToolTip("点一下，去看她现在的情绪")
        self.pet.clicked.connect(lambda: shell.open_detail("emotion"))
        pet_layout.addWidget(self.pet, 0, Qt.AlignHCenter)
        self.pet_caption = QLabel("蹭蹭")
        self.pet_caption.setObjectName("PetCaption")
        self.pet_caption.setAlignment(Qt.AlignCenter)
        pet_layout.addWidget(self.pet_caption)
        pet_hint = hint_label("和 QQ 那套同一只鲸鱼：表情跟着情绪换")
        pet_hint.setAlignment(Qt.AlignCenter)
        pet_layout.addWidget(pet_hint)
        right.addWidget(pet_card)
        right.addStretch(1)

        body.addWidget(right_holder, 0)
        root.addLayout(body, 1)

    # ── 状态 ──
    def set_status(self, state: str, caption: str) -> None:
        label = STATES.get(state, STATES["calm"])[0]
        self.state_chip.set_state(state, label)
        self.activity.setText(caption or "平静")


class DetailPage(QWidget):
    """详情页：左侧小桌宠 + 中间（返回 / 子页列表 / 内容）。"""

    def __init__(self, shell: "Shell", parent=None):
        super().__init__(parent)
        self.shell = shell
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(14)

        # ── 页头 ──
        head = QHBoxLayout()
        head.setSpacing(12)
        self.back = QPushButton("← 返回")
        self.back.setObjectName("Ghost")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(shell.go_home)
        head.addWidget(self.back)
        self.title = QLabel("")
        self.title.setObjectName("PageTitle")
        head.addWidget(self.title)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("PageSub")
        head.addWidget(self.subtitle)
        head.addStretch(1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh_current)
        head.addWidget(self.refresh_btn)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)

        # ── 左列：她（桌宠表情）+ 子页导航 ──
        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        side_card = QFrame()
        side_card.setObjectName("SideCard")
        side_card.setFixedWidth(SIDE_CARD_W)
        side_layout = QVBoxLayout(side_card)
        side_layout.setContentsMargins(14, 16, 14, 14)
        side_layout.setSpacing(8)
        self.state_avatar = StateAvatar(size=136)
        self.avatar = self.state_avatar  # 兼容旧字段名
        side_layout.addWidget(self.state_avatar, 0, Qt.AlignHCenter)
        self.state_chip = StateChip()
        side_layout.addWidget(self.state_chip, 0, Qt.AlignHCenter)
        self.avatar_hint = hint_label("")
        side_layout.addWidget(self.avatar_hint)
        side_layout.addStretch(1)
        self.side_note = QLabel("")
        self.side_note.setObjectName("CardLabel")
        side_layout.addWidget(self.side_note)
        left_col.addWidget(side_card, 0)

        # 子页导航（只有一页时整块收起）
        self.sub_card = QFrame()
        self.sub_card.setObjectName("SideCard")
        self.sub_card.setFixedWidth(SIDE_CARD_W)
        sub_layout = QVBoxLayout(self.sub_card)
        sub_layout.setContentsMargins(12, 12, 12, 12)
        sub_layout.setSpacing(6)
        self.sub_title = QLabel("子页")
        self.sub_title.setObjectName("CardTitle")
        sub_layout.addWidget(self.sub_title)
        self.items = QListWidget()
        self.items.setObjectName("SubList")
        self.items.setMinimumHeight(180)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.items.currentRowChanged.connect(self._pick)
        sub_layout.addWidget(self.items, 1)
        self.sub_card.setMaximumHeight(520)
        left_col.addWidget(self.sub_card, 0)
        left_col.addStretch(1)

        left_holder = QWidget()
        left_holder.setFixedWidth(SIDE_CARD_W)
        left_holder.setLayout(left_col)
        body.addWidget(left_holder, 0)

        # ── 右：面板内容 ──
        self.stack = QStackedWidget()
        self.stack.setObjectName("PanelStack")
        body.addWidget(self.stack, 1)

        root.addLayout(body, 1)

        self._views: list[QWidget] = []
        self._module = ""

    # ── 子页 ──
    def show_module(self, key: str, title: str, views: list[tuple[str, QWidget]]) -> None:
        self._module = key
        self.title.setText(title)
        self.subtitle.setText(f"{len(views)} 个子页" if views else "")
        self.items.clear()
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
        self._views = []
        for label, widget in views:
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(0, 32))
            self.items.addItem(item)
            self.stack.addWidget(widget)
            self._views.append(widget)
        # 一页的分区就不显示子页列表（否则左边永远挂一块空卡片）
        multi = len(views) > 1
        self.sub_card.setVisible(multi)
        self.sub_title.setText(f"子页 · {len(views)}")
        if views:
            self.items.setCurrentRow(0)
        self.side_note.setText(f"分区：{title}")

    def set_status(self, state: str, caption: str) -> None:
        label = STATES.get(state, STATES["calm"])[0]
        self.state_chip.set_state(state, label)
        self.state_avatar.set_state(state, caption)

    def _pick(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            self.refresh_current()

    def refresh_current(self) -> None:
        index = self.stack.currentIndex()
        if 0 <= index < len(self._views):
            try:
                self._views[index].refresh()
            except Exception:
                pass

    def current_view(self) -> QWidget | None:
        index = self.stack.currentIndex()
        return self._views[index] if 0 <= index < len(self._views) else None


class Shell(QWidget):
    """左侧导航 + 内容区（主页 / 详情）。对外接口与 v12 保持一致。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Shell")
        self.panels = build_panels()
        self.on_repair = None      # MainWindow 可以挂自己的弹窗
        self.on_update = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = NavRail()
        self.nav.home_requested.connect(self.go_home)
        self.nav.selected.connect(self.open_detail)
        self.nav.repair_clicked.connect(self.run_repair)
        self.nav.update_clicked.connect(self.run_update)
        for key, (title, _views) in self.panels.items():
            if key not in self.nav.buttons:
                self.nav.add_extra(key, title[:4])
        root.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        self.home = HomePage(self)
        self.detail = DetailPage(self)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.detail)
        self.stack.setCurrentWidget(self.home)
        root.addWidget(self.stack, 1)

        self.nav.set_active("home")

    # ── 导航 ──
    def go_home(self) -> None:
        self.stack.setCurrentWidget(self.home)
        self.nav.set_active("home")
        self.refresh_all()

    def open_detail(self, key: str) -> None:
        title, views = self.panels.get(key, ("", []))
        if not views:
            return
        self.detail.show_module(key, title, views)
        self.stack.setCurrentWidget(self.detail)
        self.nav.set_active(key)
        self.detail.refresh_current()

    # ── 刷新 ──
    def refresh_all(self) -> None:
        self.refresh_home()
        if self.stack.currentWidget() is self.detail:
            self.detail.refresh_current()

    def refresh_home(self) -> None:
        try:
            data = cost.summary()
            today = data["today"]
            self.home.total.setText(_money(today["total"]))
            self.home.calls.setText(f"{today['turns']} 轮聊天")
            self.home.note.setText(
                f"私聊 {_money(today['private'])}｜群 {_money(today['group'])}"
                f"｜后台 {_money(today['engine'])}")
        except Exception:
            self.home.note.setText("花费读不到")
        self.home.day.setText(datetime.now().strftime("%m-%d") + " 今天")
        try:
            stats = db.stats()
            self.home.hero_meta.setText(
                f"记忆 {stats.get('memory_item', 0)} 条 · {len(self.panels)} 个分区 · 数据只在本机")
        except Exception:
            self.home.hero_meta.setText(f"{len(self.panels)} 个分区 · 数据只在本机")
        self.home.hero_stamp.setText(datetime.now().strftime("%H:%M 刷新"))

    def set_avatar(self, state: str, caption: str, hint: str) -> None:
        for page in (self.home, self.detail):
            try:
                page.avatar.set_state(state, caption)
            except Exception:
                pass
            try:
                page.avatar_hint.setText(hint)
            except Exception:
                pass
            try:
                page.set_status(state, caption)
            except Exception:
                pass
        if getattr(self.home, "pet", None) is not None:
            try:
                self.home.pet.set_state(state)
                self.home.pet_caption.setText(f"摸摸头 · {caption or '发呆'}")
            except Exception:
                pass

    # ── 维护 ──
    def run_repair(self) -> None:
        if callable(self.on_repair):
            self.on_repair()
            return
        from PySide6.QtWidgets import QMessageBox

        text = self.repair()
        box = QMessageBox(self)
        box.setWindowTitle("修复自检")
        box.setText("自检结果")
        box.setDetailedText(text)
        box.setIcon(QMessageBox.Information if "需要看" not in text else QMessageBox.Warning)
        box.exec()

    def run_update(self) -> None:
        if callable(self.on_update):
            self.on_update()
            return
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("检查更新")
        box.setText(self.check_update())
        box.exec()

    def repair(self) -> str:
        lines = []
        checks = []

        try:
            db.q1("SELECT 1")
            checks.append(("数据库连接", True, f"{db.stats().get('memory_item', 0)} 条记忆"))
        except Exception as exc:  # noqa: BLE001
            checks.append(("数据库连接", False, str(exc)))

        try:
            settings.write_state("repair_probe.json", {"at": datetime.now().isoformat()})
            settings.read_state("repair_probe.json", {})
            checks.append(("状态文件读写", True, "state/ 可写可读"))
        except Exception as exc:  # noqa: BLE001
            checks.append(("状态文件读写", False, str(exc)))

        port = int((settings.app().get("core_api") or {}).get("port", 18766))
        checks.append(_probe_http(f"本机 API :{port}", f"http://127.0.0.1:{port}/api/status"))
        checks.append(_probe_http("QQ 桥接 :3100", release_paths.url('bridge', '/api/status'),
                                  bridge_dir() / "state" / "console-token"))

        try:
            import core_ethos

            ok = core_ethos.verify()["ok"]
            checks.append(("灵魂锚点", ok, "完好" if ok else "被修改（已回退内置副本）"))
        except Exception as exc:  # noqa: BLE001
            checks.append(("灵魂锚点", False, str(exc)))

        for name, ok, detail in checks:
            lines.append(f"{'✓' if ok else '✗'} {name}：{detail}")
        bad = sum(1 for _n, ok, _d in checks if not ok)
        lines.insert(0, "全部通过。" if bad == 0 else f"{bad} 项需要看：")
        return "\n".join(lines)

    def check_update(self) -> str:
        local = str((settings.app().get("version") or "0.1.0"))
        url = str((settings.app().get("update_url") or "")).strip()
        if not url:
            return (f"当前版本 {local}\n没有配置更新地址（config/app.json → update_url），"
                    f"按本地版本算已是最新。")
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            latest = str(data.get("version") or "")
            if latest and latest != local:
                return f"发现新版本 {latest}（当前 {local}）\n{data.get('url') or ''}"
            return f"当前已是最新版本（{local}）"
        except Exception as exc:  # noqa: BLE001
            return f"检查更新失败：{exc}\n当前版本 {local}"


def _probe_http(name: str, url: str, token_file: Path | None = None) -> tuple[str, bool, str]:
    req = urllib.request.Request(url)
    if token_file is not None:
        try:
            req.add_header("x-console-token", token_file.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            resp.read(64)
        return (name, True, "通")
    except Exception as exc:  # noqa: BLE001
        return (name, False, f"不通（{exc}）")


def bridge_dir() -> Path:
    return Path(str((settings.app().get("bridge") or {}).get("dir") or ""))


def build_panels() -> dict[str, tuple[str, list[tuple[str, QWidget]]]]:
    """每个功能 = (标题, [(附栏条目, 内容控件)])。"""
    lt1 = Lt1View()
    lt2 = Lt2View()
    diary = DiaryView()
    pending = PendingView()
    instant = InstantView()
    lang_mem = BookView(Layer.LANGUAGE.value)
    emo_mem = BookView(Layer.EMOTION.value)
    residual = ResidualView()
    stickers = StickerPanel()
    meme_wiki = MemeWikiPanel()
    about = AboutPanel()
    web_study = WebStudyPanel()
    learning = LearningPanel()
    engine = CuriosityEnginePanel()
    learning_quota = LearningQuotaPanel()
    memory_map = MemoryMapPanel()
    lang_book = BookPanel(Layer.LANGUAGE.value)
    emotion = EmotionPanel()
    relation = RelationshipPanel()
    cost_panel = CostPanel()
    chitchat = ChitchatPanel()
    refusal = RefusalPanel()
    audit = AuditPanel()
    qq = QQBridgePanel()
    thinking = ThinkingPanel()
    voice_panel = VoicePanel()
    contacts = ContactsPanel()
    persona = PersonaPanel()
    qzone_panel = QzonePanel()

    return {
        "memory": ("记忆系统", [
            ("1号长期记忆", lt1), ("2号长期记忆", lt2), ("短期 · 日记本", diary),
            ("短期 · 待总结", pending), ("瞬时记忆", instant),
            ("语言记忆", lang_mem), ("情绪记忆", emo_mem), ("思维链 · 最近想法", residual), ("表情字典", stickers), ("梗百科", meme_wiki), ("刷抖音学梗", web_study), ("记忆星图", memory_map),
        ]),
        "learning": ("学习系统", [("意图队列与报告", learning), ("好奇心引擎", engine),
                                  ("上网学习配额", learning_quota)]),
        "language": ("语言系统", [("语言账本", lang_book)]),
        "emotion": ("情绪系统", [("当前情绪", emotion), ("关系与索取度", relation),
                                ("情绪记忆（账本）", emo_mem)]),
        "cost": ("花费", [("总览与按功能归因", cost_panel)]),
        "chitchat": ("碎碎念", [("碎碎念", chitchat)]),
        "refusal": ("拒绝权", [("拒绝记录与设置", refusal)]),
        "audit": ("审计视图", [("审计事件", audit), ("关于与许可", about)]),
        "qq": ("QQ 接入", [("接入向导与状态", qq)]),
        "thinking": ("思考分区", [("她没发出去的那部分", thinking)]),
        "voice": ("语音", [("克隆音色 · 试听与发送", voice_panel)]),
        "contacts": ("联系人", [("称呼与白名单", contacts)]),
        "qzone": ("空间", [("她的说说与评论", qzone_panel)]),
        "persona": ("人设", [("查看与编辑", persona)]),
    }
