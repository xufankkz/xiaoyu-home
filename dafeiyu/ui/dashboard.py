# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""UI 积木：卡片、趋势柱、环形图、说明栏、斑马线表格。

为什么单独抽一层：
  老大说「每个功能栏打开都眼花」，问题是**每个面板各写一套排版**，于是有的用大段
  QLabel 堆字、有的用 HTML 表格、有的把说明压到表格上。这里把"看板"的三种零件固化成
  一套（卡片 / 图表 / 表格 + 说明栏），以后每个面板只要填数据，排版就自动一致。

约定（改面板时请守住）：
  · **不用绝对定位**：全部 QVBoxLayout / QHBoxLayout / QGridLayout，拉伸不重叠；
  · 数字大、标签小：卡片主数值 22~28pt，说明 9~10pt；
  · 长说明不压在内容上：一律进 {@link InfoBar}（(i) 悬停出 QToolTip），或进表格下方的单行；
  · 图表只在**有数据**时显示，空数据直接说「暂无」而不是画一张空轴。
"""

from __future__ import annotations

from PySide6.QtCharts import (QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView,
                              QPieSeries, QValueAxis)
from PySide6.QtCore import QMargins, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

# 主题色：深海蓝白（和 theme.qss 的底色配套）
ACCENT = ["#4f8dff", "#63c6ff", "#5ee9a4", "#f5c451", "#f7a6c8", "#a9b6ff",
          "#7ee0d0", "#ff9a9a", "#8fd8ff", "#ffd166"]
TEXT = "#d9e6f8"
MUTED = "#93a8c6"
BG = "#101a2a"
GRID = "#1e2c43"
CARD = "#121d2e"


def money(value: float) -> str:
    v = float(value or 0)
    if abs(v) >= 0.01:
        return f"¥{v:,.2f}"
    return f"¥{v:.3f}"


class StatCard(QFrame):
    """一张核心指标卡：标题小字 + 数字大字 + 一到两行说明小字。"""

    def __init__(self, title: str, *, color: str = ACCENT[0], parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet(
            f"#StatCard{{background:{CARD};border:1px solid {GRID};border-radius:14px;}}"
            f"#StatCard QLabel{{background:transparent;}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{color};font-size:11px;")
        head.addWidget(dot)
        self.title = QLabel(title)
        self.title.setStyleSheet(f"color:{MUTED};font-size:11px;")
        head.addWidget(self.title)
        head.addStretch(1)
        layout.addLayout(head)

        self.value = QLabel("—")
        font = QFont(self.font())
        font.setPointSize(22)
        font.setBold(True)
        self.value.setFont(font)
        self.value.setStyleSheet(f"color:{TEXT};")
        layout.addWidget(self.value)

        self.sub = QLabel("")
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet(f"color:{MUTED};font-size:10px;line-height:1.4;")
        layout.addWidget(self.sub)

    def set(self, value: str, sub: str = "", *, color: str | None = None) -> None:
        self.value.setText(value)
        if color:
            self.value.setStyleSheet(f"color:{color};")
        self.sub.setText(sub)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(f"color:{TEXT};font-size:12px;font-weight:600;padding:2px 0;")


def _style_chart(chart: QChart) -> None:
    chart.setBackgroundBrush(QColor(BG))
    chart.setBackgroundRoundness(0)
    chart.setMargins(QMargins(6, 6, 6, 6))
    chart.legend().setLabelColor(QColor(MUTED))
    chart.legend().setBorderColor(QColor(0, 0, 0, 0))


class TrendBars(QChartView):
    """近 N 天花费柱状图（悬停显示当天金额）。"""

    def __init__(self, parent=None):
        chart = QChart()
        _style_chart(chart)
        chart.setTitle("")
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._empty = True

    def set_data(self, rows: list[tuple[str, float]], *, unit: str = "¥") -> None:
        """rows: [(日期标签, 金额)]，空列表就显示「暂无」。"""
        chart = self.chart()
        chart.removeAllSeries()
        for axis in list(chart.axes()):
            chart.removeAxis(axis)

        rows = list(rows or [])
        if not rows or max((v for _, v in rows), default=0) <= 0:
            chart.setTitle("暂无花费记录")
            chart.setTitleBrush(QColor(MUTED))
            self._empty = True
            return
        chart.setTitle("")
        self._empty = False

        bar_set = QBarSet("花费")
        bar_set.setColor(QColor(ACCENT[1]))
        bar_set.setBorderColor(QColor(ACCENT[1]))
        for _, value in rows:
            bar_set.append(float(value))
        series = QBarSeries()
        series.append(bar_set)
        series.setLabelsVisible(False)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append([label for label, _ in rows])
        axis_x.setLabelsColor(QColor(MUTED))
        axis_x.setGridLineVisible(False)
        axis_x.setLineVisible(False)
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        top = max(v for _, v in rows)
        axis_y.setRange(0, top * 1.15 or 1)
        axis_y.setLabelsColor(QColor(MUTED))
        axis_y.setGridLineColor(QColor(GRID))
        axis_y.setLineVisible(False)
        axis_y.setLabelFormat("%.2f")
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        series.hovered.connect(
            lambda status, index, _bars: self._hover(status, index, rows, unit))

    def _hover(self, status: bool, index: int, rows, unit: str) -> None:
        from PySide6.QtWidgets import QToolTip

        if not status or index < 0 or index >= len(rows):
            QToolTip.hideText()
            return
        label, value = rows[index]
        QToolTip.showText(self.mapToGlobal(self.rect().center()),
                          f"{label}\n{unit}{value:.2f}")


class Donut(QChartView):
    """按功能占比的环形图（中心显示总额，右侧图例带百分比）。"""

    def __init__(self, parent=None):
        chart = QChart()
        _style_chart(chart)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, rows: list[tuple[str, float]], *, center: str = "", unit: str = "¥") -> None:
        chart = self.chart()
        chart.removeAllSeries()
        chart.setTitle("")

        items = [(name, float(value)) for name, value in (rows or []) if float(value) > 0]
        if not items:
            chart.setTitle("暂无归因数据")
            chart.setTitleBrush(QColor(MUTED))
            return

        total = sum(value for _, value in items) or 1.0
        series = QPieSeries()
        series.setHoleSize(0.55)
        for index, (name, value) in enumerate(items):
            slc = series.append(f"{name} {value / total * 100:.0f}%", value)
            slc.setColor(QColor(ACCENT[index % len(ACCENT)]))
            slc.setLabelColor(QColor(MUTED))
            slc.setLabelVisible(False)
            slc.setBorderColor(QColor(BG))
        chart.addSeries(series)

        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignRight)
        chart.legend().setLabelColor(QColor(MUTED))
        if center:
            title = QFont()
            title.setPointSize(11)
            chart.setTitle(f"{center}")
            chart.setTitleFont(title)
            chart.setTitleBrush(QColor(TEXT))

        series.hovered.connect(
            lambda slc, state: self._hover(slc, state, total, unit))

    def _hover(self, slc, state: bool, total: float, unit: str) -> None:
        from PySide6.QtWidgets import QToolTip

        if not state:
            QToolTip.hideText()
            return
        value = float(slc.value())
        QToolTip.showText(self.mapToGlobal(self.rect().center()),
                          f"{slc.label()}\n{unit}{value:.2f}（{value / total * 100:.1f}%）")


class InfoBar(QWidget):
    """表格下方的单行说明栏：左边 (i) 悬停看完整规则，右边放按钮。

    长文本**必须**放 tooltip 里 —— 以前直接把三段说明塞进 QLabel 压在表格上，
    窗口一缩就互相遮挡（老大反馈的就是这个）。
    """

    def __init__(self, tip: str, text: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)

        self.icon = QLabel("(i)")
        self.icon.setStyleSheet(f"color:{ACCENT[2]};font-weight:700;font-size:11px;")
        self.icon.setCursor(Qt.WhatsThisCursor)
        self.icon.setToolTip(tip)
        layout.addWidget(self.icon)

        self.text = QLabel(text)
        self.text.setStyleSheet(f"color:{MUTED};font-size:10px;")
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.text, 1)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        layout.addLayout(self.actions)

    def set_text(self, text: str) -> None:
        self.text.setText(text)

    def add_action(self, widget) -> None:
        self.actions.addWidget(widget)


def make_table(headers: list[str], *, stretch_col: int = 0) -> QTableWidget:
    """标准明细表：斑马线、只读、行选中、最后一列可排序。"""
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setShowGrid(False)
    table.setSortingEnabled(False)
    table.setWordWrap(False)
    table.setStyleSheet(
        "QTableWidget{background:#101a2a;alternate-background-color:#121d30;"
        "border:1px solid #1e2c43;border-radius:10px;color:#cfe0f5;font-size:11px;}"
        "QHeaderView::section{background:#16233a;color:#93a8c6;border:none;"
        "padding:6px 8px;font-size:11px;}"
        "QTableWidget::item{padding:4px 6px;border:none;}"
        "QTableWidget::item:selected{background:#1f3556;color:#ffffff;}"
    )
    header = table.horizontalHeader()
    header.setSectionResizeMode(stretch_col, QHeaderView.Stretch)
    for i in range(len(headers)):
        if i != stretch_col:
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
    table.verticalHeader().setDefaultSectionSize(26)
    return table


def fill_table(table: QTableWidget, rows: list[list[str]], *, colors: dict | None = None) -> None:
    """填表。colors: {列号: 颜色} 或 {(行,列): 颜色}。"""
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            if c > 0:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            color = None
            if colors:
                color = colors.get((r, c)) or colors.get(c)
            if color:
                item.setForeground(QColor(color))
            table.setItem(r, c, item)
