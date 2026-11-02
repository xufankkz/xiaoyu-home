# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""大肥鱼 · 配置与路径。

所有阈值都在 config/*.json 里。用户既定规则里的数字（6h/18h/3天/两周/150字/3000字/6000字…）
只允许在 thresholds.json 里调，代码里不写死。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 默认数据目录固定为程序目录下的 data\（原版 `~/.dafeiyu` 没做 expanduser，
# 会随当前工作目录漂移，导致数据目录乱建）
ROOT = Path(
    os.environ.get("DAFEIYU_HOME") or (Path(__file__).resolve().parents[1] / "data")
).resolve()

CONFIG = ROOT / "config"
DB_DIR = ROOT / "db"
MEMORY = ROOT / "memory"
LEARNING = ROOT / "learning"
STATE = ROOT / "state"
LOGS = ROOT / "logs"
UI = ROOT / "ui"
ASSETS = UI / "assets"

# 旧（活体）目录：核心只写投影，不搬走
LEGACY = ROOT
LEGACY_MEM = ROOT / "记忆系统"
LEGACY_INSTANT = LEGACY_MEM / "瞬时记忆"
LEGACY_LT2 = LEGACY_MEM / "2号长期记忆"
LEGACY_EMOTION_STATE = LEGACY_MEM / "情绪阈值" / "状态.json"
LEGACY_EMOTION_MD = LEGACY_MEM / "情绪阈值" / "状态.md"
LEGACY_DIARY = LEGACY_MEM / "短期记忆" / "日记本"
LEGACY_PENDING = LEGACY_MEM / "短期记忆" / "待总结"
LEGACY_LANG_MEM = LEGACY_MEM / "语言记忆"
LEGACY_EMO_MEM = LEGACY_MEM / "情绪记忆"
LEGACY_RUN = ROOT / "运行"

FILES = {
    "app": CONFIG / "app.json",
    "emotion": CONFIG / "emotion.json",
    "thresholds": CONFIG / "thresholds.json",
    "llm": CONFIG / "llm.json",
    "vision": CONFIG / "vision.json",
    "refusal": CONFIG / "refusal.json",
    "residual": CONFIG / "residual.json",
    "animation": CONFIG / "animation.json",
    "cost_tracking": CONFIG / "cost_tracking.json",
    "pricing_override": CONFIG / "pricing.override.json",
    "curiosity_engine": CONFIG / "curiosity_engine.json",
    "circadian": CONFIG / "circadian.json",
}

_cache: dict[str, tuple[float, dict]] = {}


def load(name: str) -> dict:
    """按 mtime 缓存的 JSON 读取；文件坏了就返回空字典，不让程序崩。"""
    path = FILES[name]
    try:
        mtime = path.stat().st_mtime
    except Exception:
        return {}
    cached = _cache.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _cache[name] = (mtime, data)
    return data


def save(name: str, data: dict) -> None:
    path = FILES[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    _cache.pop(name, None)


def app() -> dict:
    return load("app")


def _hhmm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minutes(text: str, fallback: int = 0) -> int:
    try:
        hour, minute = str(text).split(":")
        return int(hour) * 60 + int(minute)
    except Exception:
        return fallback


def learning() -> dict:
    """学习窗口的唯一配置源（v12 1.1）。所有时间都从这里算，别再写死。"""
    cfg = app().get("learning") or {}
    start = _minutes(cfg.get("window_start", "00:00"), 0)
    end = _minutes(cfg.get("window_end", "01:00"), 60)
    offset = int(cfg.get("self_dialogue_offset_min", 30))
    span = int(cfg.get("self_dialogue_minutes", 30))
    consolidate = int(cfg.get("consolidate_min_after_end", 10))
    archive = int(cfg.get("archive_min_after_end", 180))
    return {
        "timezone": str(cfg.get("timezone") or "Asia/Shanghai"),
        "window_start": _hhmm(start), "window_end": _hhmm(end),
        "start_min": start, "end_min": end,
        "self_dialogue_start": _hhmm(end + offset),
        "self_dialogue_end": _hhmm(end + offset + span),
        "consolidate_at": _hhmm(end + consolidate),
        "archive_at": _hhmm(end + archive),
        "self_dialogue_offset_min": offset,
        "raw": cfg,
    }


def in_window(now, start_min: int, end_min: int) -> bool:
    minutes = now.hour * 60 + now.minute
    if start_min <= end_min:
        return start_min <= minutes < end_min
    return minutes >= start_min or minutes < end_min      # 跨零点


def emotion_cfg() -> dict:
    return load("emotion")


def thr() -> dict:
    return load("thresholds")


def llm_cfg() -> dict:
    return load("llm")


def get(path: str, default: Any = None) -> Any:
    """点号取配置：settings.get("instant.purge_hours", 18)。"""
    node: Any = load("thresholds")
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def bootstrap_config() -> dict:
    """首次运行：把 `config.default/` 复制成 `config/`（开源版开箱即用）。

    只补**缺失**的文件，绝不覆盖已有配置 —— 老用户升级时不会被改设置。
    没有 config.default（开发机直接跑）就什么都不做。
    """
    import shutil

    src = ROOT / "config.default"
    copied = []
    if not src.is_dir():
        return {"copied": copied}
    CONFIG.mkdir(parents=True, exist_ok=True)
    for item in src.glob("*.json"):
        target = CONFIG / item.name
        if target.exists():
            continue
        try:
            shutil.copy2(item, target)
            copied.append(item.name)
        except Exception:
            continue
    return {"copied": copied}


def ensure_dirs() -> None:
    bootstrap_config()
    for d in (
        CONFIG, DB_DIR, DB_DIR / "backup", MEMORY, LEARNING, STATE, LOGS, UI / "assets",
        MEMORY / "instant", MEMORY / "short_term", MEMORY / "long_term_1",
        MEMORY / "long_term_2", MEMORY / "language", MEMORY / "emotion",
        MEMORY / "thought_chain", LEARNING / "intents", LEARNING / "reports",
        STATE / "reward_log", STATE / "outbox",
    ):
        d.mkdir(parents=True, exist_ok=True)


def state_file(name: str) -> Path:
    STATE.mkdir(parents=True, exist_ok=True)
    return STATE / name


def read_state(name: str, default: dict | None = None) -> dict:
    path = state_file(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else (default or {})
    except Exception:
        return default or {}


def write_state(name: str, data: dict) -> Path:
    path = state_file(name)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
