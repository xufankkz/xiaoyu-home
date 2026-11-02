# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""情绪动力学的纯函数（可单测）。

规格 6.6：
- 自然回归：每周期向 baseline 缓慢回归，负面（自卑/创伤）更慢；
- 不得把「高/低」判定瞬间抹平 → 阈值保护窗口（在 vector.py 里用，纯函数只管算）。
"""

from __future__ import annotations

from ..entity import DIMS, dominant_emotion


def decay_toward(value: float, baseline: float, elapsed_min: float, half_life_min: float) -> float:
    """指数回归：k = 1 - 0.5^(Δt/τ)。τ<=0 视为不衰减。"""
    if half_life_min <= 0 or elapsed_min <= 0:
        return float(value)
    k = 1.0 - 0.5 ** (float(elapsed_min) / float(half_life_min))
    return float(value) + (float(baseline) - float(value)) * k


def step(values: dict, baseline: dict, half_lives: dict, elapsed_min: float,
         protected: set[str] | None = None) -> dict:
    """一次 tick 的回归。protected 里的维度**完全不回归**（阈值保护窗口）。"""
    out = {}
    protected = protected or set()
    for dim in DIMS:
        if dim in protected:
            out[dim] = float(values.get(dim, 0.0))
            continue
        out[dim] = round(decay_toward(values.get(dim, 0.0), baseline.get(dim, 0.0),
                                      elapsed_min, half_lives.get(dim, 0.0)), 3)
    return out


def dominant_of(values: dict, baseline: dict) -> str:
    # Keep the historical signature for callers; distance no longer labels mood.
    return dominant_emotion(values)


def crossed(values: dict, thresholds: dict) -> list[str]:
    """哪些维度已经越过行为阈值（用于设置保护窗口）。"""
    out = []
    for dim, limit in (thresholds or {}).items():
        if dim in DIMS and float(values.get(dim, 0.0)) >= float(limit):
            out.append(dim)
    return out


def shift(values: dict, delta: dict, weight: float = 1.0, lo: float = 0.0, hi: float = 100.0) -> dict:
    """按 delta×weight 修改，返回新值（不修改入参）。"""
    out = {d: float(values.get(d, 0.0)) for d in DIMS}
    for dim, dv in (delta or {}).items():
        if dim in DIMS:
            out[dim] = max(lo, min(hi, out[dim] + float(dv) * float(weight)))
    return out


def rpe_weight(count: int, decay: float = 0.85, floor: float = 0.2) -> float:
    """同一触发重复出现时递减（多巴胺适应性）。count=0 → 1.0。"""
    return round(max(floor, decay ** max(0, int(count))), 3)
