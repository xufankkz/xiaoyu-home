# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""启动自检：把"这台机器现在能干什么"写成 state/capabilities.json。

为什么需要：动图、浏览、语音、模型调用都依赖外部能力（ffmpeg / Pillow / Playwright / dsh）。
以前是"用到才发现缺"，现在启动就先摸一遍，UI 与日志都能看到缺什么、该怎么补。
只读检测，不改任何配置。
"""

from __future__ import annotations
import release_paths

import importlib.util
import shutil
import sys
from pathlib import Path

from . import settings
from .entity import now_iso

FILE = "capabilities.json"


def _module(name: str) -> str | None:
    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        return None
    if spec is None:
        return None
    try:
        mod = importlib.import_module(name)
    except Exception:
        return None
    return str(getattr(mod, "__version__", "?"))


def _ffmpeg() -> dict:
    """Check the explicitly configured media tools; never download a fallback."""
    from home_runtime.files import read_json
    config = read_json(release_paths.path('release/settings.json'))
    directory = config.get('integrations', {}).get('media', {}).get('executable_dir', '')
    root = Path(directory) if directory else Path(release_paths.path()).parent / 'components/media/ffmpeg/bin'
    target = root / 'ffmpeg.exe'
    return {'available': target.is_file(), 'path': str(target) if target.is_file() else '',
            'via': 'explicit', 'hint': '在接入与组件中填写自己的FFmpeg bin目录。'}


def _pillow() -> dict:
    version = _module("PIL.ImageSequence") or _module("PIL")
    return {"available": bool(version), "version": version or "",
            "hint": "" if version else "Pillow 未安装（可选）；动图用 QImage 顺序抽帧即可"}


def _playwright() -> dict:
    version = _module("playwright")
    return {"available": bool(version), "version": version or "",
            "hint": "" if version else "自主浏览不可用：pip install playwright"}


def _dsh(path: str) -> dict:
    target = Path(str(path or ""))
    return {"available": target.exists(), "path": str(target),
            "hint": "" if target.exists() else "找不到 dsh 命令行，模型调用会失败（config/llm.json → providers.dsh.cmd）"}


def _qimage() -> dict:
    try:
        from PySide6.QtGui import QImage  # noqa: F401

        from PySide6 import __version__ as qt_version

        return {"available": True, "qt": qt_version}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}


def _sqlite() -> dict:
    import sqlite3

    return {"available": True, "version": sqlite3.sqlite_version}


def capabilities(now: dict | None = None) -> dict:
    app = settings.app()
    llm_cfg = settings.load("llm").get("providers", {}).get("dsh", {})
    core_api = app.get("core_api") or {}
    report = {
        "at": now_iso(),
        "python": sys.version.split()[0],
        "root": str(settings.ROOT),
        "ffmpeg_available": False,
        "pillow_animation_available": False,
        "ffmpeg": _ffmpeg(),
        "pillow": _pillow(),
        "playwright": _playwright(),
        "dsh": _dsh(llm_cfg.get("cmd") or ""),
        "qimage": _qimage(),
        "sqlite": _sqlite(),
        "core_api": {"url": f"http://127.0.0.1:{int(core_api.get('port', 18766))}"},
        "bridge_console": {"url": release_paths.url('bridge', ''), "readonly": True},
    }
    report["ffmpeg_available"] = bool(report["ffmpeg"]["available"])
    report["pillow_animation_available"] = bool(report["pillow"]["available"])
    report["ready"] = {
        "animation": True,          # QImage 路线永远可用
        "static_vision": bool(report["qimage"]["available"]),
        "browse": bool(report["playwright"]["available"]),
        "llm": bool(report["dsh"]["available"]),
    }
    report["missing"] = [name for name, ok in report["ready"].items() if not ok]
    return report


def write(now: dict | None = None) -> dict:
    report = capabilities(now)
    try:
        settings.write_state(FILE, report)
    except Exception:
        pass
    return report


def read() -> dict:
    return settings.read_state(FILE, {})


def summary() -> str:
    data = read() or capabilities()
    ready = data.get("ready") or {}
    parts = []
    for key, label in (("animation", "动图"), ("static_vision", "静态看图"),
                       ("browse", "自主浏览"), ("llm", "模型")):
        parts.append(f"{label}{'✓' if ready.get(key) else '✗'}")
    extra = "｜ffmpeg ✓" if data.get("ffmpeg_available") else "｜ffmpeg ✗（不需要）"
    return "自检：" + " ".join(parts) + extra
