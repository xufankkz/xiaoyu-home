# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""记忆系统 ·「记忆星图」面板（v13 新增）。

她脑子里的东西画成一片星座：一颗星 = 一条记忆 / 一条知识，一条线 = 共同的概念。
左边是可以缩放平移的星图，右边是点开那颗星的全文。

数据（只读，`mode=ro` 打开 data/db/dafeiyu.db）：
  · memory_item    → 记忆星（long1/long2/instant/… 按 layer 分色）
  · knowledge_item → 知识星（琥珀色，标题用 name）
  · 「含思考链」开关 → thought_node（默认关，紫色星，线会变密）
  · graph_edge 真实库里只有 thought→thought 的 ref 边，**不能当记忆关系用**；
    所以主边是 concepts 交集（同概念即连、权重=交集数、只连出现 ≥2 次的概念，
    单概念挂 >40 个节点的「大词」跳过），graph_edge 里 kind != ref 的再叠加成强边。
    边上限 1500，节点上限 400（按显著度/时间采样；现在库里 154 个，全画）。

布局：Fruchterman-Reingold 简化版（斥力 + 边引力 + 微向心力 + 温度退火），
200 次迭代、固定种子 → 同数据同布局，刷新时星星不会乱跳。
读库 + 算布局都在 QThread，主线程只负责画；30 秒自刷一次（可见时才跑），
外壳 15 秒一次的 refresh_all 只做节流后的空转。
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from core import settings
from ui.widgets import hint_label, title_label

MAX_NODES = 400
MAX_EDGES = 1500
EDGE_K = 3               # 每个节点只连概念重合最多的 K 个伙伴（防毛线球）
LAYOUT_ITERATIONS = 300
LAYOUT_SEED = 7
BIG_CONCEPT = 40          # 一个概念挂在 >40 个节点上就没有区分度了，跳过
CORE_EDGE = 0.92          # 主星座铺到画布的 92%
CORE_TAIL = 0.10          # 远端指数尾巴，最大 1.02
RING_INNER = 1.08         # 孤立星外圈半径
RING_SPREAD = 0.10
REFRESH_MS = 30_000       # 自动刷新
LOAD_THROTTLE = 25.0      # 外壳定时刷新太密时，这层挡住重复计算

COLORS: dict[tuple[str, str], str] = {
    ("memory", "long1"): "#7cc7ff",
    ("memory", "long2"): "#a9b6ff",
    ("memory", "instant"): "#5ee9a4",
    ("memory", "short"): "#f5c451",
    ("memory", "language"): "#8fd8ff",
    ("memory", "emotion"): "#f7a6c8",
    ("knowledge", ""): "#ffd166",
    ("thought", ""): "#c3a8ff",
}
KIND_CN = {"memory": "记忆", "knowledge": "知识", "thought": "思考"}
LAYER_CN = {"long1": "1号长期记忆", "long2": "2号长期记忆", "short": "短期记忆",
            "instant": "瞬时记忆", "language": "语言记忆", "emotion": "情绪记忆"}
STATUS_CN = {"open": "未整理", "resolved": "已整理"}


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _db_path() -> Path:
    try:
        path = Path(settings.DB_DIR) / "dafeiyu.db"
    except Exception:
        path = Path("")
    if path.exists():
        return path
    try:
        from core import db as _db  # 兜底：以核心自己的 DB_PATH 为准

        other = Path(str(getattr(_db, "DB_PATH", "")))
        if other.exists():
            return other
    except Exception:
        pass
    return path


def _columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in cur.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _has_table(cur: sqlite3.Cursor, table: str) -> bool:
    try:
        row = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _concepts(raw) -> list[str]:
    """concepts 是 JSON 数组字符串；解析失败就当没有。"""
    if isinstance(raw, (list, tuple)):
        data = list(raw)
    else:
        try:
            data = json.loads(raw or "[]")
        except Exception:
            return []
    if not isinstance(data, (list, tuple)):
        return []
    out: list[str] = []
    for item in data:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _first_line(text, limit: int = 18) -> str:
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:limit] + ("…" if len(line) > limit else "")
    return ""


def _short_time(raw) -> str:
    text = str(raw or "").strip()
    return (text[:10] + " " + text[11:16]) if len(text) >= 16 else text


def node_color(node: dict) -> str:
    if node["kind"] == "memory":
        return COLORS.get(("memory", str(node.get("layer") or "")), "#7cc7ff")
    return COLORS.get((node["kind"], ""), "#8fd8ff")


def node_kind_cn(node: dict) -> str:
    if node["kind"] == "memory":
        return LAYER_CN.get(str(node.get("layer") or ""), "记忆")
    if node["kind"] == "knowledge":
        return "知识 · " + str(node.get("domain") or "未分类")
    return "思考 · " + str(node.get("type") or "")


# ── 读库 ────────────────────────────────────────────────────────────────────
def load_graph(include_thought: bool = False) -> dict:
    """只读打开库，凑出 nodes / edges / positions。任何异常都退化成空图，不抛给界面。"""
    out: dict = {"nodes": [], "edges": [], "positions": [], "stats": {},
                 "error": "", "signature": ()}
    path = _db_path()
    if not path or not path.exists():
        out["error"] = f"找不到数据库：{path}"
        return out
    con = None
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3.0)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        nodes: list[dict] = []
        sig: list[str] = []

        # ── 记忆 ──
        if _has_table(cur, "memory_item"):
            cols = _columns(cur, "memory_item")
            if "id" in cols:
                wanted = [c for c in ("id", "layer", "subtype", "content", "summary",
                                      "concepts", "salience", "status", "t_created")
                          if c in cols]
                sql = f"SELECT {', '.join(wanted)} FROM memory_item"
                if "tombstone" in cols:
                    sql += " WHERE COALESCE(tombstone, 0) = 0"
                try:
                    rows = cur.execute(sql).fetchall()
                except Exception:
                    rows = []
                newest = ""
                for row in rows:
                    data = dict(row)
                    label = _first_line(data.get("summary") or "", 16) \
                        or _first_line(data.get("content"), 16)
                    stamp = str(data.get("t_created") or "")
                    newest = max(newest, stamp)
                    nodes.append({
                        "id": str(data.get("id") or ""),
                        "kind": "memory",
                        "layer": str(data.get("layer") or ""),
                        "subtype": str(data.get("subtype") or ""),
                        "label": label or "（无标题记忆）",
                        "content": str(data.get("content") or data.get("summary") or ""),
                        "concepts": _concepts(data.get("concepts")),
                        "salience": float(data.get("salience") or 0.0),
                        "t": stamp,
                        "status": str(data.get("status") or ""),
                    })
                sig.append(f"m{len(rows)}:{newest}")

        # ── 知识 ──
        if _has_table(cur, "knowledge_item"):
            cols = _columns(cur, "knowledge_item")
            if "id" in cols:
                wanted = [c for c in ("id", "name", "domain", "concepts", "content",
                                      "source", "learned_at", "created_at") if c in cols]
                try:
                    rows = cur.execute(
                        f"SELECT {', '.join(wanted)} FROM knowledge_item").fetchall()
                except Exception:
                    rows = []
                for row in rows:
                    data = dict(row)
                    nodes.append({
                        "id": str(data.get("id") or ""),
                        "kind": "knowledge",
                        "layer": "",
                        "domain": str(data.get("domain") or ""),
                        "source": str(data.get("source") or ""),
                        "label": _first_line(data.get("name") or data.get("content"), 18)
                        or "（无标题知识）",
                        "content": str(data.get("content") or ""),
                        "concepts": _concepts(data.get("concepts")),
                        "salience": 0.85,
                        "t": str(data.get("learned_at") or data.get("created_at") or ""),
                        "status": "",
                    })
                sig.append(f"k{len(rows)}")

        # ── 思考链（可选，默认关）──
        if include_thought and _has_table(cur, "thought_node"):
            cols = _columns(cur, "thought_node")
            if "id" in cols:
                wanted = [c for c in ("id", "type", "content", "summary", "concepts",
                                      "last_active_at") if c in cols]
                try:
                    rows = cur.execute(
                        f"SELECT {', '.join(wanted)} FROM thought_node").fetchall()
                except Exception:
                    rows = []
                for row in rows:
                    data = dict(row)
                    nodes.append({
                        "id": str(data.get("id") or ""),
                        "kind": "thought",
                        "layer": "",
                        "type": str(data.get("type") or ""),
                        "label": _first_line(data.get("summary") or data.get("content"), 14)
                        or "（无标题想法）",
                        "content": str(data.get("content") or ""),
                        "concepts": _concepts(data.get("concepts")),
                        "salience": 0.6,
                        "t": str(data.get("last_active_at") or ""),
                        "status": "",
                    })
                sig.append(f"t{len(rows)}")

        # ── 上限 400：知识优先，再按显著度 / 时间 ──
        total = len(nodes)
        if total > MAX_NODES:
            nodes.sort(key=lambda n: (1 if n["kind"] == "knowledge" else 0,
                                      float(n.get("salience") or 0.0),
                                      str(n.get("t") or "")), reverse=True)
            nodes = nodes[:MAX_NODES]

        index = {node["id"]: i for i, node in enumerate(nodes)}
        edges = _build_edges(cur, nodes, index)
        positions = force_layout(len(nodes), edges)

        out.update({
            "nodes": nodes,
            "edges": edges,
            "positions": positions,
            "stats": {
                "nodes": len(nodes),
                "total": total,
                "edges": len(edges),
                "memory": sum(1 for n in nodes if n["kind"] == "memory"),
                "knowledge": sum(1 for n in nodes if n["kind"] == "knowledge"),
                "thought": sum(1 for n in nodes if n["kind"] == "thought"),
                "isolated": _isolated_count(len(nodes), edges),
            },
            "signature": tuple(sig) + (len(nodes), len(edges)),
        })
        return out
    except Exception as exc:  # noqa: BLE001 - 字段缺失 / 库损坏都只退化
        out["error"] = f"读记忆库失败：{exc}"
        return out
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def _isolated_count(count: int, edges: list[tuple[int, int, float]]) -> int:
    seen: set[int] = set()
    for a, b, _w in edges:
        seen.add(a)
        seen.add(b)
    return max(0, count - len(seen))


def _build_edges(cur: sqlite3.Cursor, nodes: list[dict],
                 index: dict[str, int]) -> list[tuple[int, int, float]]:
    """连边策略：concept 交集的「每个节点取最近 K 个」+ graph_edge(非 ref) 强边。

    为什么不是「同概念两两连」：instant 记忆动辄共享 8~9 个概念，
    两两连会变成 600+ 条边的毛线球（实测中位最近邻 0.01，星星全叠在一起）。
    改成每个节点只连概念重合最多的 K 个伙伴：边数 ≈ 1.5×节点数，几乎人人都连得上，
    布局也不会糊成一团。上限 MAX_EDGES。
    """
    if len(nodes) < 2:
        return []
    inverted: dict[str, list[int]] = defaultdict(list)
    for i, node in enumerate(nodes):
        for concept in node["concepts"]:
            inverted[concept].append(i)

    # 每个节点对其它节点的概念重合计数
    overlap: list[dict[int, float]] = [defaultdict(float) for _ in range(len(nodes))]
    for members in inverted.values():
        if len(members) < 2 or len(members) > BIG_CONCEPT:
            continue
        for a in members:
            row = overlap[a]
            for b in members:
                if a != b:
                    row[b] += 1.0

    weights: dict[tuple[int, int], float] = {}
    for i, row in enumerate(overlap):
        ranked = sorted(row.items(), key=lambda item: (-item[1], item[0]))[:EDGE_K]
        for j, weight in ranked:
            key = (i, j) if i < j else (j, i)
            if weight > weights.get(key, 0.0):
                weights[key] = float(weight)

    # graph_edge：真实库里 14 条全是 thought→thought 的 ref，跳过；只吃非 ref 边
    if _has_table(cur, "graph_edge"):
        cols = _columns(cur, "graph_edge")
        src_col = next((c for c in ("src", "source_node_id", "source") if c in cols), "")
        dst_col = next((c for c in ("dst", "target_node_id", "target") if c in cols), "")
        kind_col = next((c for c in ("kind", "relation_type", "relation") if c in cols), "")
        if src_col and dst_col:
            select = f"SELECT {src_col} AS s, {dst_col} AS d"
            if kind_col:
                select += f", {kind_col} AS k"
            try:
                rows = cur.execute(select + " FROM graph_edge").fetchall()
            except Exception:
                rows = []
            for row in rows:
                relation = str(row["k"] if kind_col else "").lower()
                if relation == "ref":
                    continue
                a = index.get(str(row["s"] or ""))
                b = index.get(str(row["d"] or ""))
                if a is None or b is None or a == b:
                    continue
                key = (a, b) if a < b else (b, a)
                weights[key] = weights.get(key, 0.0) + 2.0

    edges = [(a, b, w) for (a, b), w in weights.items()]
    if len(edges) > MAX_EDGES:
        edges.sort(key=lambda item: item[2], reverse=True)
        edges = edges[:MAX_EDGES]
    return edges


# ── 力导向布局（Fruchterman-Reingold 简化版）────────────────────────────────
def force_layout(count: int, edges: list[tuple[int, int, float]],
                 iterations: int = LAYOUT_ITERATIONS,
                 seed: int = LAYOUT_SEED) -> list[tuple[float, float]]:
    """斥力 + 弹簧 + 碰撞松弛 + 半径压缩归一化；177 点 300 次 ≈ 0.6 秒（放线程里跑）。

    调参依据（拿真实库量过）：
      · 纯 Fruchterman-Reingold 的 d²/k 引力会把共享概念多的 instant 记忆捏成一坨
        （中位最近邻 0.011，星星直接叠在一起），所以引力改成弹簧（向 rest 收敛）
        再加一条硬碰撞下限；
      · 结尾不用 min/max 硬裁剪（孤立点会被糊到边框上，出现坐标重合的星），
        改用 r → r/(1+0.55·r/ref) 的单调压缩：远星还在外面，但不会压成一条边。
    最终坐标归一到 ~[-1,1]，画布按自身尺寸映射。
    """
    if count <= 0:
        return []
    rnd = random.Random(seed)
    xs = [rnd.uniform(-1.0, 1.0) for _ in range(count)]
    ys = [rnd.uniform(-1.0, 1.0) for _ in range(count)]
    k = math.sqrt(4.0 / count)
    k2 = k * k
    rest = k * 1.3            # 弹簧自然长度
    collision = rest          # 近于此距离额外排斥，防止叠点

    for step in range(iterations):
        temperature = 0.10 * (1.0 - step / iterations) + 0.002
        dxs = [0.0] * count
        dys = [0.0] * count

        for i in range(count):
            xi = xs[i]
            yi = ys[i]
            ax = 0.0
            ay = 0.0
            for j in range(i + 1, count):
                dx = xi - xs[j]
                dy = yi - ys[j]
                dist2 = dx * dx + dy * dy
                if dist2 < 1e-6:
                    dist2 = 1e-6
                force = k2 / dist2
                dist = math.sqrt(dist2)
                if dist < collision:
                    force += (collision - dist) * 3.0 / dist
                ax += dx * force
                ay += dy * force
                dxs[j] -= dx * force
                dys[j] -= dy * force
            dxs[i] += ax
            dys[i] += ay

        # 弹簧：把有边相连的两颗星拉到 rest 附近（权重只影响画线粗细，不参与力学，免得抱团）
        for a, b, _weight in edges:
            dx = xs[a] - xs[b]
            dy = ys[a] - ys[b]
            dist = math.hypot(dx, dy) or 1e-4
            force = (dist - rest) * 0.35
            ux = dx / dist * force
            uy = dy / dist * force
            dxs[a] -= ux
            dys[a] -= uy
            dxs[b] += ux
            dys[b] += uy

        # 微向心力：孤立星不会被推到天边
        for i in range(count):
            dxs[i] -= xs[i] * 0.02
            dys[i] -= ys[i] * 0.02

        for i in range(count):
            length = math.hypot(dxs[i], dys[i]) or 1e-6
            move = min(length, temperature)
            xs[i] += dxs[i] / length * move
            ys[i] += dys[i] / length * move

    # ── 归一化：连成一片的「主星座」铺满中间，孤立星（没边可连的）单独排外圈 ──
    connected: set[int] = set()
    for a, b, _weight in edges:
        connected.add(a)
        connected.add(b)
    core = [i for i in range(count) if i in connected]
    lonely = [i for i in range(count) if i not in connected]

    if core:
        cx = sum(xs[i] for i in core) / len(core)
        cy = sum(ys[i] for i in core) / len(core)
        radii = sorted(math.hypot(xs[i] - cx, ys[i] - cy) for i in core) or [1.0]
        ref = radii[int(len(radii) * 0.90)]
    else:
        cx = sum(xs) / count
        cy = sum(ys) / count
        ref = 1.0
    if ref <= 1e-6:
        ref = 1.0

    out: list[tuple[float, float]] = [(0.0, 0.0)] * count
    for i in core:
        dx = xs[i] - cx
        dy = ys[i] - cy
        r = math.hypot(dx, dy)
        if r < 1e-6:
            continue
        u = r / ref
        # u<=1 线性铺到 0.85；再远的用指数尾巴收在 0.97 以内，避免糊在边框上
        r_out = CORE_EDGE * u if u <= 1.0 else CORE_EDGE + CORE_TAIL * (1.0 - math.exp(-(u - 1.0)))
        out[i] = (dx / r * r_out, dy / r * r_out)

    # 孤立星：黄金角排在外圈，既不挤中心，又一眼看出「它还没连上谁」
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for order, i in enumerate(lonely):
        angle = golden * order + 0.6
        radius = RING_INNER + RING_SPREAD * ((order * 7) % 5) / 4.0
        out[i] = (math.cos(angle) * radius, math.sin(angle) * radius)
    return out


class _GraphWorker(QThread):
    done = Signal(dict)

    def __init__(self, include_thought: bool, parent=None):
        super().__init__(parent)
        self.include_thought = include_thought

    def run(self) -> None:  # noqa: D401
        try:
            self.done.emit(load_graph(self.include_thought))
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"nodes": [], "edges": [], "positions": [], "stats": {},
                            "error": f"星图加载失败：{exc}", "signature": ()})


# ── 画布 ────────────────────────────────────────────────────────────────────
class StarCanvas(QWidget):
    """星星画布：滚轮缩放、拖拽平移、悬停高亮、点击选中。"""

    node_clicked = Signal(int)     # -1 = 点了空白
    hover_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StarCanvas")
        self.setMinimumSize(420, 380)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)

        self.nodes: list[dict] = []
        self.edges: list[tuple[int, int, float]] = []
        self.pos: list[tuple[float, float]] = []
        self.selected = -1
        self.hover = -1
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self.placeholder = "正在读她的记忆库…"
        self.sub_placeholder = "第一次画要算一次星图，通常不到一秒"

        self._press_pos: QPointF | None = None
        self._press_pan = QPointF()
        self._moved = False

    # ── 坐标 ──
    def _base_scale(self) -> float:
        return max(1.0, min(self.width(), self.height()) * 0.42)

    def world_to_screen(self, x: float, y: float) -> QPointF:
        scale = self._base_scale() * self.zoom
        return QPointF(self.width() / 2.0 + x * scale + self.pan.x(),
                       self.height() / 2.0 + y * scale + self.pan.y())

    def screen_to_world(self, point: QPointF) -> tuple[float, float]:
        scale = self._base_scale() * self.zoom
        if scale <= 1e-6:
            return (0.0, 0.0)
        return ((point.x() - self.width() / 2.0 - self.pan.x()) / scale,
                (point.y() - self.height() / 2.0 - self.pan.y()) / scale)

    def _radius(self, index: int) -> float:
        node = self.nodes[index]
        if node["kind"] == "knowledge":
            base = 6.2
        elif node["kind"] == "thought":
            base = 3.4
        else:
            base = 3.4 + 4.2 * max(0.0, min(1.0, float(node.get("salience") or 0.5)))
        return base * (0.85 + 0.22 * min(2.2, self.zoom))

    def _hit_test(self, point: QPointF) -> int:
        best = -1
        best_dist = 1e9
        for i in range(len(self.nodes) - 1, -1, -1):
            center = self.world_to_screen(*self.pos[i])
            dist = math.hypot(point.x() - center.x(), point.y() - center.y())
            if dist <= self._radius(i) + 6.0 and dist < best_dist:
                best = i
                best_dist = dist
        return best

    # ── 数据 ──
    def set_data(self, nodes, edges, positions, placeholder: str = "",
                 sub_placeholder: str = "") -> None:
        keep = ""
        if 0 <= self.selected < len(self.nodes):
            keep = str(self.nodes[self.selected].get("id") or "")
        self.nodes = list(nodes or [])
        self.edges = list(edges or [])
        self.pos = list(positions or [])
        self.placeholder = placeholder or "正在读她的记忆库…"
        self.sub_placeholder = sub_placeholder
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self.hover = -1
        self.selected = -1
        if keep:
            for i, node in enumerate(self.nodes):
                if str(node.get("id") or "") == keep:
                    self.selected = i
                    break
        self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self.update()

    # ── 交互 ──
    def wheelEvent(self, event):  # noqa: N802
        if not self.nodes:
            return
        before = self.screen_to_world(event.position())
        step = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.zoom = max(0.35, min(7.0, self.zoom * step))
        after = self.screen_to_world(event.position())
        scale = self._base_scale() * self.zoom
        self.pan += QPointF((after[0] - before[0]) * scale, (after[1] - before[1]) * scale)
        self.update()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton or not self.nodes:
            super().mousePressEvent(event)
            return
        self._press_pos = QPointF(event.position())
        self._press_pan = QPointF(self.pan)
        self._moved = False
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self.nodes:
            return
        point = QPointF(event.position())
        if self._press_pos is not None:
            delta = point - self._press_pos
            if abs(delta.x()) + abs(delta.y()) > 3:
                self._moved = True
            if self._moved:
                self.pan = self._press_pan + delta
                self.update()
            return
        hit = self._hit_test(point)
        if hit != self.hover:
            self.hover = hit
            self.setCursor(Qt.PointingHandCursor if hit >= 0 else Qt.OpenHandCursor)
            self.hover_changed.emit(hit)
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._press_pos is not None:
            self._press_pos = None
            self.setCursor(Qt.PointingHandCursor if self.hover >= 0 else Qt.OpenHandCursor)
            if not self._moved:
                self.node_clicked.emit(self._hit_test(QPointF(event.position())))
            self._moved = False
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        if self.hover != -1:
            self.hover = -1
            self.hover_changed.emit(-1)
            self.update()
        super().leaveEvent(event)

    # ── 绘制 ──
    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 14, 14)
        painter.setClipPath(clip)

        self._paint_background(painter, rect)
        if not self.nodes or len(self.pos) != len(self.nodes):
            self._paint_placeholder(painter, rect)
            painter.end()
            return

        connected = self._neighbors()
        self._paint_edges(painter)
        self._paint_nodes(painter, connected)
        self._paint_labels(painter)
        self._paint_tip(painter)
        self._paint_overlay_text(painter, rect)
        painter.end()

    def _neighbors(self) -> set[int]:
        if not (0 <= self.selected < len(self.nodes)):
            return set()
        out: set[int] = set()
        for a, b, _w in self.edges:
            if a == self.selected:
                out.add(b)
            elif b == self.selected:
                out.add(a)
        return out

    def _paint_background(self, painter: QPainter, rect: QRectF) -> None:
        base = QLinearGradient(rect.topLeft(), rect.bottomRight())
        base.setColorAt(0.0, QColor("#091322"))
        base.setColorAt(0.5, QColor("#0a1728"))
        base.setColorAt(1.0, QColor("#08111e"))
        painter.fillRect(rect, base)

        # 两团很淡的星云，给深色底一点层次
        for fx, fy, spread, color, alpha in ((0.26, 0.24, 0.55, "#2b6cb0", 26),
                                             (0.78, 0.72, 0.62, "#5b4bb0", 22)):
            center = QPointF(rect.left() + rect.width() * fx, rect.top() + rect.height() * fy)
            radius = max(rect.width(), rect.height()) * spread
            glow = QRadialGradient(center, radius)
            glow.setColorAt(0.0, QColor(color).lighter(115))
            glow.setColorAt(0.0, QColor(color))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.save()
            painter.setOpacity(alpha / 255.0)
            painter.fillRect(rect, glow)
            painter.restore()

    def _paint_placeholder(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(10.0, font.pointSizeF() + 1.0))
        painter.setFont(font)
        painter.setPen(QPen(QColor("#93a8c6")))
        painter.drawText(rect.adjusted(24, 0, -24, -16), Qt.AlignCenter, self.placeholder)
        small = QFont(self.font())
        small.setPointSizeF(max(8.5, small.pointSizeF() - 1.0))
        painter.setFont(small)
        painter.setPen(QPen(QColor("#5f7697")))
        painter.drawText(rect.adjusted(24, 28, -24, 0), Qt.AlignCenter, self.sub_placeholder)

    def _paint_edges(self, painter: QPainter) -> None:
        selected = self.selected
        for a, b, weight in self.edges:
            start = self.world_to_screen(*self.pos[a])
            end = self.world_to_screen(*self.pos[b])
            highlighted = selected >= 0 and (a == selected or b == selected)
            if selected >= 0 and not highlighted:
                alpha = 22
            else:
                alpha = int(74 + min(96, 24 * weight))
            width = 1.0 + min(1.6, 0.28 * weight)
            if highlighted:
                alpha = 215
                width += 0.9
            painter.setPen(QPen(QColor(124, 176, 236, alpha), width))
            painter.drawLine(start, end)

    def _paint_nodes(self, painter: QPainter, connected: set[int]) -> None:
        selected = self.selected
        for i, node in enumerate(self.nodes):
            center = self.world_to_screen(*self.pos[i])
            radius = self._radius(i)
            core = QColor(node_color(node))
            emphasis = 1.0
            if selected >= 0 and i != selected:
                emphasis = 1.0 if i in connected else 0.3

            glow_radius = radius * (4.2 if i in (selected, self.hover) else 3.2)
            glow = QRadialGradient(center, glow_radius)
            glow.setColorAt(0.0, QColor(core.red(), core.green(), core.blue(),
                                        int(120 * emphasis)))
            glow.setColorAt(0.45, QColor(core.red(), core.green(), core.blue(),
                                         int(46 * emphasis)))
            glow.setColorAt(1.0, QColor(core.red(), core.green(), core.blue(), 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(center, glow_radius, glow_radius)

            body = QRadialGradient(QPointF(center.x() - radius * 0.3,
                                           center.y() - radius * 0.35), radius * 1.7)
            body.setColorAt(0.0, QColor(255, 255, 255, int(245 * emphasis)))
            body.setColorAt(0.45, QColor(core.red(), core.green(), core.blue(),
                                         int(232 * emphasis)))
            dark = core.darker(160)
            body.setColorAt(1.0, QColor(dark.red(), dark.green(), dark.blue(),
                                        int(205 * emphasis)))
            painter.setBrush(body)
            painter.drawEllipse(center, radius, radius)

            if i == selected:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(255, 255, 255, 215), 1.6))
                painter.drawEllipse(center, radius + 4.5, radius + 4.5)
            elif i == self.hover:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(255, 255, 255, 120), 1.1))
                painter.drawEllipse(center, radius + 3.0, radius + 3.0)
        painter.setPen(Qt.NoPen)

    def _paint_labels(self, painter: QPainter) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.5))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        wanted: list[int] = []
        if self.zoom >= 0.8 or self.selected >= 0:
            wanted = sorted(range(len(self.nodes)),
                            key=lambda i: float(self.nodes[i].get("salience") or 0.0),
                            reverse=True)[:6]
        order = list(dict.fromkeys(wanted + [self.hover, self.selected]))
        order = [i for i in order if 0 <= i < len(self.nodes)]
        # 标签避让：跟已画的标签重叠就跳过（不然中间那几颗会糊成一团字）
        drawn: list[QRectF] = []
        for i in order:
            node = self.nodes[i]
            center = self.world_to_screen(*self.pos[i])
            radius = self._radius(i)
            text = metrics.elidedText(str(node.get("label") or ""), Qt.ElideRight, 132)
            box = QRectF(center.x() + radius + 6, center.y() - 11,
                         metrics.horizontalAdvance(text) + 4, 20)
            if not (i == self.selected or i == self.hover):
                if any(box.intersects(other) for other in drawn):
                    continue
            drawn.append(box)
            painter.setPen(QColor(6, 12, 20, 200))
            painter.drawText(QPointF(box.left() + 1, center.y() + 4), text)
            painter.setPen(QColor(214, 230, 250, 255 if i == self.selected else 232))
            painter.drawText(QPointF(box.left(), center.y() + 4), text)

    def _paint_tip(self, painter: QPainter) -> None:
        index = self.hover
        if not (0 <= index < len(self.nodes)) or index == self.selected:
            return
        node = self.nodes[index]
        center = self.world_to_screen(*self.pos[index])
        text = str(node.get("label") or "")
        sub = node_kind_cn(node)
        font = QFont(self.font())
        font.setPointSizeF(max(8.5, font.pointSizeF() - 1.0))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = max(metrics.horizontalAdvance(text), metrics.horizontalAdvance(sub)) + 22
        box = QRectF(center.x() + 14, center.y() - 46, width, 40)
        box.moveLeft(max(8.0, min(box.left(), self.width() - width - 8)))
        box.moveTop(max(8.0, box.top()))
        path = QPainterPath()
        path.addRoundedRect(box, 9, 9)
        painter.setPen(QPen(QColor(58, 92, 140), 1))
        painter.setBrush(QColor(12, 22, 38, 238))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(233, 241, 255)))
        painter.drawText(QRectF(box.left() + 11, box.top() + 5, box.width() - 22, 18),
                         Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.setPen(QPen(QColor(127, 149, 182)))
        painter.drawText(QRectF(box.left() + 11, box.top() + 21, box.width() - 22, 14),
                         Qt.AlignLeft | Qt.AlignVCenter, sub)

    def _paint_overlay_text(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.5))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        painter.setPen(QPen(QColor(90, 110, 138)))
        painter.drawText(QPointF(rect.left() + 16, rect.top() + 18),
                         "滚轮缩放 · 拖拽平移 · 点星星看详情")

        entries = (("长期记忆", "#7cc7ff"), ("瞬时记忆", "#5ee9a4"),
                   ("语言/情绪", "#8fd8ff"), ("知识", "#ffd166"))
        x = rect.left() + 16
        y = rect.bottom() - 14
        for label, color in entries:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(x + 3, y - 4), 3.2, 3.2)
            painter.setPen(QPen(QColor(127, 149, 182)))
            painter.drawText(QPointF(x + 11, y), label)
            x += 11 + metrics.horizontalAdvance(label) + 16


# ── 面板 ────────────────────────────────────────────────────────────────────
class MemoryMapPanel(QWidget):
    """记忆系统 →「记忆星图」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _GraphWorker | None = None
        self._loaded_once = False
        self._last_load_at = 0.0
        self._stats: dict = {}
        self._show_thought = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(title_label("记忆星图"))
        titles.addWidget(hint_label("一颗星就是一条记忆/知识，连线是它们共享的概念。"))
        head.addLayout(titles)
        head.addStretch(1)
        self.stats_label = QLabel("正在读取…")
        self.stats_label.setObjectName("Hint")
        head.addWidget(self.stats_label)
        self.thought_btn = QPushButton("含思考链")
        self.thought_btn.setObjectName("Ghost")
        self.thought_btn.setCheckable(True)
        self.thought_btn.setCursor(Qt.PointingHandCursor)
        self.thought_btn.setToolTip("把 thought_node（她没发出去的想法）也画进来；默认关，线会密很多")
        self.thought_btn.toggled.connect(self._toggle_thought)
        head.addWidget(self.thought_btn)
        self.reset_btn = QPushButton("重置视角")
        self.reset_btn.setObjectName("Ghost")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.clicked.connect(self._reset_view)
        head.addWidget(self.reset_btn)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(lambda: self.reload(force=True))
        head.addWidget(self.refresh_btn)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.canvas = StarCanvas()
        self.canvas.node_clicked.connect(self._on_node_clicked)
        body.addWidget(self.canvas, 1)

        detail_card = QFrame()
        detail_card.setObjectName("Card")
        detail_card.setFixedWidth(304)
        detail = QVBoxLayout(detail_card)
        detail.setContentsMargins(16, 14, 16, 14)
        detail.setSpacing(8)

        self.d_title = QLabel("点一颗星看看")
        self.d_title.setWordWrap(True)
        self.d_title.setStyleSheet("font-size:14px;font-weight:700;color:#f2f7ff;")
        detail.addWidget(self.d_title)

        self.d_meta = QLabel("这里显示：所属分区、时间、显著度、连线数和关联概念。")
        self.d_meta.setObjectName("Hint")
        self.d_meta.setWordWrap(True)
        detail.addWidget(self.d_meta)

        self.d_concepts = QLabel("")
        self.d_concepts.setObjectName("CardLabel")
        self.d_concepts.setWordWrap(True)
        detail.addWidget(self.d_concepts)

        self.d_body = QTextBrowser()
        self.d_body.setOpenExternalLinks(False)
        self.d_body.setMinimumHeight(180)
        detail.addWidget(self.d_body, 1)
        body.addWidget(detail_card, 0)
        outer.addLayout(body, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.reload(force=False))
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self._start_load)

    # ── 生命周期 ──
    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(REFRESH_MS)
        if not self._loaded_once:
            self._loaded_once = True
            self._pending.start(40)

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):  # noqa: N802
        self._timer.stop()
        self._pending.stop()
        worker = self._worker
        if worker is not None and worker.isRunning():
            # 布局最长 ~2 秒：宁可等它算完，也别让 QThread 带着还在跑的线程被析构（会崩）
            worker.requestInterruption()
            worker.wait(4000)
        super().closeEvent(event)

    # ── 刷新 ──
    def refresh(self) -> None:
        """外壳每 15 秒会调一次；这里节流，只有「刷新」按钮才强刷。"""
        self.reload(force=False)

    def reload(self, force: bool = False) -> None:
        if not force and time.time() - self._last_load_at < LOAD_THROTTLE:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._start_load()

    def _start_load(self) -> None:
        self._last_load_at = time.time()
        self._worker = _GraphWorker(self._show_thought, self)
        self._worker.done.connect(self._apply_payload)
        self._worker.start()

    def _apply_payload(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        nodes = payload.get("nodes") or []
        positions = payload.get("positions") or []
        error = str(payload.get("error") or "")
        if not nodes or len(positions) != len(nodes):
            self.canvas.set_data([], [], [], "暂无数据",
                                 error or "库里还没有能画的记忆，先跟她聊两句")
            self.stats_label.setText("节点 0")
            self._show_empty(error or "还没有可画的记忆。先跟她聊两句，或等她自己学点东西。")
            return
        self._stats = payload.get("stats") or {}
        self.canvas.set_data(nodes, payload.get("edges") or [], positions)
        extra = ""
        if int(self._stats.get("thought") or 0):
            extra = f" · 思考 {self._stats.get('thought')}"
        self.stats_label.setText(
            f"节点 {self._stats.get('nodes', len(nodes))} · 连线 {self._stats.get('edges', 0)}"
            f" · 知识 {self._stats.get('knowledge', 0)}{extra}"
            f" · 孤立 {self._stats.get('isolated', 0)}")
        if 0 <= self.canvas.selected < len(nodes):
            self._show_node(self.canvas.selected)
        else:
            self._show_empty("点任意一颗星，这里显示它的全文、时间和关联概念。")

    def _toggle_thought(self, checked: bool) -> None:
        self._show_thought = bool(checked)
        self._loaded_once = True
        self.reload(force=True)

    def _reset_view(self) -> None:
        self.canvas.reset_view()

    # ── 详情 ──
    def _on_node_clicked(self, index: int) -> None:
        self.canvas.selected = index if index >= 0 else -1
        self.canvas.update()
        if self.canvas.selected >= 0:
            self._show_node(self.canvas.selected)
        else:
            self._show_empty("点任意一颗星，这里显示它的全文、时间和关联概念。")

    def _show_empty(self, text: str) -> None:
        self.d_title.setText("点一颗星看看")
        self.d_meta.setText(text)
        self.d_concepts.setText("")
        self.d_body.setPlainText("")

    def _show_node(self, index: int) -> None:
        nodes = self.canvas.nodes
        if not (0 <= index < len(nodes)):
            return
        node = nodes[index]
        self.d_title.setText(str(node.get("label") or "（无标题）"))

        bits = [KIND_CN.get(node["kind"], "记忆")]
        if node["kind"] == "memory":
            bits.append(LAYER_CN.get(str(node.get("layer") or ""), "未知层"))
            if node.get("subtype"):
                bits.append(str(node["subtype"]))
            if node.get("status"):
                bits.append(STATUS_CN.get(str(node["status"]), str(node["status"])))
        elif node["kind"] == "knowledge":
            if node.get("domain"):
                bits.append(str(node["domain"]))
            if node.get("source"):
                bits.append(str(node["source"]))
        elif node["kind"] == "thought" and node.get("type"):
            bits.append(str(node["type"]))
        when = _short_time(node.get("t"))
        if when:
            bits.append(when)
        bits.append(f"显著度 {float(node.get('salience') or 0.0):.2f}")
        degree = sum(1 for a, b, _w in self.canvas.edges if a == index or b == index)
        bits.append(f"连 {degree} 条线")
        self.d_meta.setText(" · ".join(bits))

        concepts = node.get("concepts") or []
        if concepts:
            shown = concepts[:14]
            more = f" 等 {len(concepts)} 个" if len(concepts) > len(shown) else ""
            self.d_concepts.setText("关联概念：" + " · ".join(shown) + more)
        else:
            self.d_concepts.setText("关联概念：无")

        content = str(node.get("content") or "").strip()
        self.d_body.setPlainText(content or "（这条只有标题，没有正文）")
        bar = self.d_body.verticalScrollBar()
        if bar is not None:
            bar.setValue(0)
