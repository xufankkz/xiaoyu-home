# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""桌面状态：电脑锁没锁、多久没动键鼠（v37 主动请示的前提）。

为什么不用「猜」：
  补丁要求「电脑已解锁 且 近 5 分钟有键鼠活动」才去问。这两件事 Windows 都有确切答案 ——
  `GetLastInputInfo` 给最后输入时刻，`OpenInputDesktop` 在锁屏时打不开输入桌面。
  比读「有没有前台窗口」可靠得多（她自己在后台跑，前台窗口一直有）。

只读，不改任何东西。取不到就当「不活跃」——宁可不问，也别在人家开会时弹浏览器。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from datetime import datetime

USER32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> int:
    """距最后一次键鼠输入多少秒。取不到返回一个很大的数（= 当作没人）。"""
    if USER32 is None or KERNEL32 is None:
        return 10 ** 6
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not USER32.GetLastInputInfo(ctypes.byref(info)):
            return 10 ** 6
        now = KERNEL32.GetTickCount64()
        return max(0, int((now - info.dwTime) / 1000))
    except Exception:
        return 10 ** 6


def is_locked() -> bool:
    """屏幕锁着时 `OpenInputDesktop` 会失败（安全桌面拿不到句柄）。"""
    if USER32 is None:
        return False
    try:
        handle = USER32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not handle:
            return True
        USER32.CloseDesktop(handle)
        return False
    except Exception:
        return False


def state(active_minutes: float = 5.0) -> dict:
    idle = idle_seconds()
    locked = is_locked()
    active = (not locked) and idle <= float(active_minutes) * 60
    return {
        "idle_seconds": idle,
        "idle_minutes": round(idle / 60.0, 1),
        "locked": locked,
        "active": active,
        "active_window_minutes": float(active_minutes),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
