"""Desktop presentation of the same local application used by the browser.

This window owns no cognition loop, scheduler, model client or database writer.
The existing local services remain responsible for those tasks.
"""

from __future__ import annotations
import release_paths

import os
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QByteArray, QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPixmap
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton, QSizeGrip, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

DEFAULT_URL = release_paths.url('web', '/')


def _window_icon(paths: str, color: str = "#60748f") -> QIcon:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
           f'viewBox="0 0 16 16" fill="none" stroke="{color}" stroke-width="1.2" '
           'stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg>')
    pixmap = QPixmap()
    pixmap.loadFromData(QByteArray(svg.encode("utf-8")), "SVG")
    return QIcon(pixmap)


class RoomTitleBar(QWidget):
    """Application-colored window controls; native system title box is removed."""

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("RoomTitleBar")
        self.setFixedHeight(38)
        self.window_owner = window
        self._drag_offset = None
        self.icon_color = "#60748f"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 6, 0)
        layout.setSpacing(6)
        self.caption = QLabel("小鱼之家", self)
        self.caption.setObjectName("RoomWindowCaption")
        self.caption.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.caption)
        self.connection = QLabel("", self)
        self.connection.setObjectName("RoomWindowConnection")
        self.connection.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.connection)
        layout.addStretch()
        self.menu_button = self._button("界面选项", '<path d="M3 8h.1m4.9 0h.1m4.9 0h.1" stroke-width="2.5"/>')
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.options = QMenu(self.menu_button)
        self.menu_button.setMenu(self.options)
        self.minimize_button = self._button("最小化", '<path d="M3 8h10"/>')
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button = self._button("最大化", '<rect x="3.5" y="3.5" width="9" height="9"/>')
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button = self._button("关闭小鱼之家", '<path d="m4 4 8 8m0-8-8 8"/>')
        self.close_button.setObjectName("RoomWindowClose")
        self.close_button.clicked.connect(window.close)
        for button in (self.menu_button, self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)

    def _button(self, name, paths):
        button = QToolButton(self)
        button.setObjectName("RoomWindowButton")
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setIcon(_window_icon(paths))
        button.setProperty("iconPaths", paths)
        button.setIconSize(QSize(14, 14))
        button.setFixedSize(39, 29)
        button.setFocusPolicy(Qt.StrongFocus)
        return button

    def toggle_maximized(self):
        if self.window_owner.isMaximized():
            self.window_owner.showNormal()
        else:
            self.window_owner.showMaximized()

    def sync_state(self):
        maximized = self.window_owner.isMaximized()
        paths = ('<path d="M5 3h8v8M3 5h8v8H3Z"/>' if maximized else
                 '<rect x="3.5" y="3.5" width="9" height="9"/>')
        self.maximize_button.setProperty("iconPaths", paths)
        self.maximize_button.setIcon(_window_icon(paths, self.icon_color))
        name = "还原窗口" if maximized else "最大化"
        self.maximize_button.setAccessibleName(name)
        self.maximize_button.setToolTip(name)

    def apply_icon_color(self, color):
        self.icon_color = color
        for button in (self.menu_button, self.minimize_button, self.maximize_button, self.close_button):
            button.setIcon(_window_icon(button.property("iconPaths"), color))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window_owner.windowHandle()
            if handle and handle.startSystemMove():
                event.accept()
                return
            self._drag_offset = event.globalPosition().toPoint() - self.window_owner.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.window_owner.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def local_web_url(value: str) -> QUrl:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("小鱼界面地址需要使用本机 HTTP 服务")
    if parsed.username or parsed.password:
        raise ValueError("小鱼界面地址不支持嵌入账号密码")
    return QUrl(value)


class SharedWebWindow(QMainWindow):
    ready_changed = Signal(bool)

    def __init__(self, *, data_dir: Path, url: str | None = None,
                 window_size: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowTitle("小鱼之家")
        self.setObjectName("RoomWindow")
        self.setStyleSheet("""
            QMainWindow#RoomWindow { background:#f6f8fc; }
            QWidget#RoomShell { background:#f6f8fc; border:1px solid #dce5f0; }
            QWidget#RoomTitleBar { background:#f0f4fa; }
            QLabel#RoomWindowCaption { color:#526b8c; font-size:11px; }
            QLabel#RoomWindowConnection { color:#60758f; font-size:10px; padding-left:6px; }
            QToolButton#RoomWindowButton, QToolButton#RoomWindowClose {
                background:transparent; border:0; border-radius:6px; padding:0;
            }
            QToolButton#RoomWindowButton:hover { background:#e1eaf6; }
            QToolButton#RoomWindowClose:hover { background:#f6e3e8; }
            QToolButton#RoomWindowButton:focus, QToolButton#RoomWindowClose:focus { border:1px solid #94b0d2; }
            QToolButton::menu-indicator { image:none; }
            QMenu { background:#ffffff; color:#455e7e; border:1px solid #dce5f0; padding:5px; }
            QMenu::item { padding:7px 22px 7px 12px; border-radius:4px; }
            QMenu::item:selected { background:#e9f0fa; }
            QWidget#ConnectionPanel { background:#f6f8fc; }
            QLabel { color:#344b69; }
            QPushButton { background:#ffffff; color:#456c99; padding:8px 14px;
                          border:1px solid #d1dfef; border-radius:9px; }
            QPushButton:hover { background:#eaf1fa; }
        """)
        self._url = local_web_url(url or os.environ.get("QQBOT_WEB_URL") or DEFAULT_URL)
        self._ready = False
        self._theme_palette = None
        self.data_dir = Path(data_dir)
        icons = [self.data_dir / "ui/assets" / name for name in ("小鱼.ico", "大肥鱼.ico")]
        icons.append(Path(release_paths.path("xiaoyu-web/static/app.ico")))
        for icon in icons:
            if icon.is_file():
                self.setWindowIcon(QIcon(str(icon)))
                break
        size = window_size or {}
        screen = self.screen().availableGeometry() if self.screen() else None
        width = int(size.get("width", 1360))
        height = int(size.get("height", 880))
        if screen:
            width = min(width, max(640, screen.width() - 80))
            height = min(height, max(480, screen.height() - 80))
        self.resize(width, height)
        self.setMinimumSize(min(640, width), min(480, height))

        self.shell = QWidget(self)
        self.shell.setObjectName("RoomShell")
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(1, 1, 1, 1)
        shell_layout.setSpacing(0)
        self.title_bar = RoomTitleBar(self)
        shell_layout.addWidget(self.title_bar)
        self.stack = QStackedWidget(self.shell)
        shell_layout.addWidget(self.stack, 1)
        self.setCentralWidget(self.shell)
        self.view = QWebEngineView(self.stack)
        # Created after the view, so the page is disposed before its profile.
        self.profile = QWebEngineProfile("xiaoyu-desktop", self)
        self.profile.setPersistentStoragePath(str(self.data_dir / "ui/web-profile"))
        self.profile.setCachePath(str(self.data_dir / "ui/web-cache"))
        self.profile.downloadRequested.connect(self.save_download)
        self.page = QWebEnginePage(self.profile, self.view)
        self.page.setBackgroundColor(QColor("#f6f8fc"))
        self.view.setPage(self.page)
        self.stack.addWidget(self.view)

        self.offline = QWidget(self.stack)
        self.offline.setObjectName("ConnectionPanel")
        layout = QVBoxLayout(self.offline)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.addStretch(1)
        title = QLabel("正在连接小鱼之家…")
        title.setStyleSheet("font-size:24px; font-weight:600;")
        layout.addWidget(title)
        self.connection_message = QLabel("界面正在准备。连接成功后会自动打开。")
        self.connection_message.setWordWrap(True)
        layout.addWidget(self.connection_message)
        row = QHBoxLayout()
        self.retry_button = QPushButton("重新连接")
        self.retry_button.clicked.connect(self.reload)
        row.addWidget(self.retry_button)
        self.browser_button = QPushButton("在浏览器打开")
        self.browser_button.clicked.connect(self.open_browser)
        row.addWidget(self.browser_button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(2)
        self.stack.addWidget(self.offline)
        self.stack.setCurrentWidget(self.offline)

        refresh = QAction("刷新", self)
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.reload)
        self.addAction(refresh)
        self.title_bar.options.addAction(refresh)
        browser = QAction("在浏览器打开", self)
        browser.triggered.connect(self.open_browser)
        self.title_bar.options.addAction(browser)

        # Retain an accessible resize grip alongside native edge/corner resizing.
        self.resize_grip = QSizeGrip(self.shell)
        self.resize_grip.setFixedSize(14, 14)
        self.resize_grip.setAccessibleName("调整窗口大小")
        self.resize_grip.setStyleSheet("background:transparent;")

        self.retry_timer = QTimer(self)
        self.retry_timer.setInterval(3000)
        self.retry_timer.timeout.connect(self.reload)
        self.view.loadFinished.connect(self._loaded)
        # Read the shared page's presentation only; no model or business API call.
        self.theme_timer = QTimer(self)
        self.theme_timer.setInterval(1000)
        self.theme_timer.timeout.connect(self._sync_theme)
        self.theme_timer.start()
        self.view.load(self._url)

    def _sync_theme(self):
        if not self._ready or self.isMinimized():
            return
        self.page.runJavaScript("""JSON.stringify((()=>{
            const s=getComputedStyle(document.documentElement);
            return Object.fromEntries(['frame-bg','frame-ink','frame-hover','bg','card','line','text','primary-soft']
                .map(k=>[k,s.getPropertyValue('--'+k).trim()]));
        })())""", self._apply_theme)

    def _apply_theme(self, value):
        try:
            palette = json.loads(value)
            keys = ('frame-bg','frame-ink','frame-hover','bg','card','line','text','primary-soft')
            if not isinstance(palette, dict) or any(not re.fullmatch(r'#[0-9a-fA-F]{6}', palette.get(k, '')) for k in keys):
                return
        except (TypeError, ValueError):
            return
        if palette == self._theme_palette:
            return
        self._theme_palette = palette
        p = palette
        close_hover = '#4c3040' if p['bg'] == '#101c2a' else '#f6e3e8'
        self.setStyleSheet(f"""
            QMainWindow#RoomWindow, QWidget#ConnectionPanel {{ background:{p['bg']}; }}
            QWidget#RoomShell {{ background:{p['bg']}; border:1px solid {p['line']}; }}
            QWidget#RoomTitleBar {{ background:{p['frame-bg']}; }}
            QLabel#RoomWindowCaption {{ color:{p['frame-ink']}; font-size:11px; }}
            QLabel#RoomWindowConnection {{ color:{p['frame-ink']}; font-size:10px; padding-left:6px; }}
            QToolButton#RoomWindowButton, QToolButton#RoomWindowClose {{ background:transparent; border:0; border-radius:6px; padding:0; }}
            QToolButton#RoomWindowButton:hover {{ background:{p['frame-hover']}; }}
            QToolButton#RoomWindowClose:hover {{ background:{close_hover}; }}
            QToolButton#RoomWindowButton:focus, QToolButton#RoomWindowClose:focus {{ border:1px solid {p['frame-ink']}; }}
            QToolButton::menu-indicator {{ image:none; }}
            QMenu {{ background:{p['card']}; color:{p['text']}; border:1px solid {p['line']}; padding:5px; }}
            QMenu::item {{ padding:7px 22px 7px 12px; border-radius:4px; }}
            QMenu::item:selected {{ background:{p['primary-soft']}; }}
            QLabel {{ color:{p['text']}; }}
            QPushButton {{ background:{p['card']}; color:{p['text']}; padding:8px 14px; border:1px solid {p['line']}; border-radius:9px; }}
            QPushButton:hover {{ background:{p['primary-soft']}; }}
        """)
        self.title_bar.apply_icon_color(p['frame-ink'])
        self.page.setBackgroundColor(QColor(p['bg']))

    def reload(self) -> None:
        self.retry_timer.stop()
        # Preserve the current route when refreshing an already loaded page.
        if self._ready:
            self.view.reload()
        else:
            self.view.load(self._url)

    def save_download(self, download) -> None:
        from PySide6.QtWidgets import QFileDialog
        target, _ = QFileDialog.getSaveFileName(self, '保存到本机', str(Path.home() / 'Downloads' / download.suggestedFileName()))
        if target:
            download.setDownloadDirectory(str(Path(target).parent))
            download.setDownloadFileName(Path(target).name)
            download.accept()
        else:
            download.cancel()

    def open_browser(self) -> None:
        current = self.view.url()
        QDesktopServices.openUrl(current if current.isValid() and self._ready else self._url)

    def _loaded(self, success: bool) -> None:
        self._ready = bool(success)
        if success:
            self._sync_theme()
            self.retry_timer.stop()
            self.stack.setCurrentWidget(self.view)
            self.title_bar.connection.setText("· 日常小屋")
            self.title_bar.connection.setToolTip("网页与软件使用同一套界面，已连接本机服务")
            self.page.runJavaScript(
                "JSON.stringify({title:document.title, theme:document.body.dataset.theme, version:document.querySelector('meta[name=\"xiaoyu-version\"]')?.content, routes:Array.from(document.querySelectorAll('#navList .nav-item'),b=>'#/'+b.dataset.id)})",
                lambda info: self._report(True, info),
            )
        else:
            self.title_bar.connection.setText("· 正在连接")
            self.connection_message.setText("暂时连不上本机界面服务，正在自动重试。你也可以点击重新连接。")
            self.stack.setCurrentWidget(self.offline)
            self.retry_timer.start()
            self._report(False)
        self.ready_changed.emit(self._ready)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "resize_grip"):
            self.resize_grip.move(self.shell.width() - 15, self.shell.height() - 15)
            self.resize_grip.raise_()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.sync_state()
            self.resize_grip.setVisible(not self.isMaximized())

    def nativeEvent(self, event_type, message):
        # Frameless windows retain edge and corner resize hit targets on Windows.
        if sys.platform == "win32" and not self.isMaximized() and not self.isFullScreen():
            import ctypes
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                rect = wintypes.RECT()
                user32 = ctypes.WinDLL("user32")
                user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                if user32.GetWindowRect(int(self.winId()), ctypes.byref(rect)):
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    border = max(5, round(6 * self.devicePixelRatioF()))
                    left, right = x < rect.left + border, x >= rect.right - border
                    top, bottom = y < rect.top + border, y >= rect.bottom - border
                    if top:
                        target = 13 if left else 14 if right else 12
                    elif bottom:
                        target = 16 if left else 17 if right else 15
                    else:
                        target = 10 if left else 11 if right else 0
                    if target:
                        return True, target
        return super().nativeEvent(event_type, message)

    def _report(self, ready: bool, document=None) -> None:
        """Record connection diagnostics without page content or user settings."""
        report = self.data_dir / "state/desktop-ui.json"
        try:
            if isinstance(document, str):
                document = json.loads(document)
            report.parent.mkdir(parents=True, exist_ok=True)
            data = {"pid": os.getpid(), "entry": "shared-web", "ready": ready,
                    "url": self._url.toString(), "checked_at": datetime.now().isoformat(timespec="seconds"),
                    "frameless": bool(self.windowFlags() & Qt.FramelessWindowHint),
                    "document": document if isinstance(document, dict) else {}}
            temporary = report.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(report)
        except (OSError, ValueError):
            pass
