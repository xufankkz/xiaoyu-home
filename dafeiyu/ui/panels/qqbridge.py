# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""QQ 接入向导（v12 5.1/5.3）：填账号 + 白名单 → 自动写桥接配置；只读监控 3100。"""

from __future__ import annotations
import release_paths

import json
import urllib.request
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QTextEdit, QVBoxLayout, QWidget)

from core import settings
from ui.widgets import hint_label

CONFIG_FILE = "qq_config.json"


def bridge_dir() -> Path:
    return Path(str((settings.app().get("bridge") or {}).get("dir") or ""))


def bridge_config_path() -> Path:
    return bridge_dir() / "config.json"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_whitelist(text: str) -> dict:
    """一行一个：纯数字=私聊，g:群号=群，p:QQ号=私聊。"""
    groups, private = [], []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("g:") or low.startswith("group:"):
            value = line.split(":", 1)[1].strip()
            if value.isdigit():
                groups.append(int(value))
        elif low.startswith("p:") or low.startswith("private:"):
            value = line.split(":", 1)[1].strip()
            if value.isdigit():
                private.append(int(value))
        elif line.isdigit():
            private.append(int(line))
    return {"groups": sorted(set(groups)), "private": sorted(set(private))}


class QQBridgePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.status = hint_label("")
        layout.addWidget(self.status)
        self.qq_line = hint_label("")
        layout.addWidget(self.qq_line)

        layout.addWidget(QLabel("大肥鱼的 QQ 号"))
        self.owner = QLineEdit()
        self.owner.setPlaceholderText("例如 100000000")
        layout.addWidget(self.owner)

        layout.addWidget(QLabel("允许互动的群 / 私聊（一行一个；纯数字=私聊，g:群号=群）"))
        self.whitelist = QTextEdit()
        self.whitelist.setMinimumHeight(120)
        self.whitelist.setPlaceholderText("100000000\ng:100000002")
        layout.addWidget(self.whitelist)

        # ── 会话轮换（省上下文，默认关）──
        layout.addWidget(QLabel("会话轮换（省上下文；默认关闭）"))
        rot_row = QHBoxLayout()
        self.rotate = QCheckBox("开启")
        rot_row.addWidget(self.rotate)
        rot_row.addWidget(QLabel("最长"))
        self.rot_hours = QSpinBox()
        self.rot_hours.setRange(1, 48)
        self.rot_hours.setValue(6)
        rot_row.addWidget(self.rot_hours)
        rot_row.addWidget(QLabel("小时 /"))
        self.rot_turns = QSpinBox()
        self.rot_turns.setRange(10, 2000)
        self.rot_turns.setValue(120)
        rot_row.addWidget(self.rot_turns)
        rot_row.addWidget(QLabel("轮，空闲"))
        self.rot_idle = QSpinBox()
        self.rot_idle.setRange(1, 120)
        self.rot_idle.setValue(10)
        rot_row.addWidget(self.rot_idle)
        rot_row.addWidget(QLabel("分钟才换"))
        rot_row.addStretch(1)
        layout.addLayout(rot_row)
        layout.addWidget(hint_label(
            "开启后：会话累计超过时长或轮数、且已经空闲够久，才开新会话，避免聊到一半断掉。"
            "换会话会把当前对话归档，长期/瞬时/残留记忆照常兜底；不开就是一直沿用同一个会话。"))

        row = QHBoxLayout()
        self.save = QPushButton("保存并同步到桥接")
        self.save.clicked.connect(self._save)
        row.addWidget(self.save)
        self.reload = QPushButton("刷新状态")
        self.reload.clicked.connect(self.refresh)
        row.addWidget(self.reload)
        self.open_helper = QPushButton("打开部署助手说明")
        self.open_helper.clicked.connect(self._helper)
        row.addWidget(self.open_helper)
        row.addStretch(1)
        layout.addLayout(row)

        self.result = hint_label("")
        layout.addWidget(self.result)
        layout.addWidget(hint_label(
            "只会改白名单与账号，不启动/停止桥接（SnowLuma 的 EULA 不允许打包第三方组件）。"
            "桥接没在跑时，这里只显示状态，不会去拉起它。"))
        self._load()

    # ── 读写 ──
    def _load(self) -> None:
        cfg = _read_json(settings.CONFIG / CONFIG_FILE)
        bridge = _read_json(bridge_config_path())
        owner = str(cfg.get("owner_qq") or bridge.get("ownerQQ") or "")
        self.owner.setText(owner)
        allow = cfg.get("allow") or bridge.get("allow") or {}
        lines = [str(x) for x in (allow.get("private") or [])]
        lines += [f"g:{x}" for x in (allow.get("groups") or [])]
        self.whitelist.setPlainText("\n".join(lines))
        rot = ((bridge.get("socialV2") or {}).get("session") or {}).get("rotate") or {}
        self.rotate.setChecked(bool(rot.get("enabled")))
        self.rot_hours.setValue(int(rot.get("maxHours") or 6))
        self.rot_turns.setValue(int(rot.get("maxTurns") or 120))
        self.rot_idle.setValue(int(rot.get("idleMinutes") or 10))

    def _save(self) -> None:
        owner = self.owner.text().strip()
        if owner and not owner.isdigit():
            self.result.setText("QQ 号只能是数字")
            return
        allow = parse_whitelist(self.whitelist.toPlainText())
        payload = {"owner_qq": owner, "allow": allow,
                   "updated_at": datetime.now().isoformat(timespec="seconds")}
        try:
            _write_json(settings.CONFIG / CONFIG_FILE, payload)
        except Exception as exc:  # noqa: BLE001
            self.result.setText(f"写本地配置失败：{exc}")
            return

        path = bridge_config_path()
        note = ""
        if path.exists():
            try:
                bridge = _read_json(path)
                if owner:
                    bridge["ownerQQ"] = int(owner)
                current = bridge.get("allow") if isinstance(bridge.get("allow"), dict) else {}
                current["groups"] = allow["groups"]
                current["private"] = allow["private"]
                current.setdefault("allowAllWhenEmpty", False)
                bridge["allow"] = current
                social = bridge.get("socialV2") if isinstance(bridge.get("socialV2"), dict) else {}
                session = social.get("session") if isinstance(social.get("session"), dict) else {}
                session["rotate"] = {"enabled": bool(self.rotate.isChecked()),
                                     "maxHours": int(self.rot_hours.value()),
                                     "maxTurns": int(self.rot_turns.value()),
                                     "idleMinutes": int(self.rot_idle.value())}
                social["session"] = session
                bridge["socialV2"] = social
                _write_json(path, bridge)
                note = "，已同步到桥接 config.json（重启桥接生效）"
            except Exception as exc:  # noqa: BLE001
                note = f"，但写桥接配置失败：{exc}"
        else:
            note = "，但没找到桥接目录，只写了本地配置"
        self.result.setText(
            f"已保存：账号 {owner or '（未填）'}｜私聊 {len(allow['private'])} 个｜"
            f"群 {len(allow['groups'])} 个{note}")
        self.refresh()

    def _helper(self) -> None:
        self.result.setText(
            "部署助手：先装 SnowLuma（官方压缩包，解压在任意目录），"
            "再用 start.bat 启动 qq-bridge；桥接起来后这里会自动显示「运行中」。")

    # ── 只读状态 ──
    def _probe(self) -> dict:
        base = release_paths.url('bridge', '')
        token = ""
        try:
            token = (bridge_dir() / "state" / "console-token").read_text(encoding="utf-8").strip()
        except Exception:
            pass
        req = urllib.request.Request(base + "/api/status")
        if token:
            req.add_header("x-console-token", token)
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            return {"_error": str(exc)}

    def refresh(self) -> None:
        data = self._probe()
        if data.get("_error"):
            self.status.setText("QQ 桥接：未运行（127.0.0.1:3100 连不上）——只读检查，不会去启动它")
            self.qq_line.setText(f"上次检查：{datetime.now():%H:%M:%S}")
            return
        snow = data.get("snowluma") or {}
        online = bool(snow.get("accountOnline") or snow.get("connected"))
        mode = data.get("mode") or ""
        owner = data.get("ownerQQ") or ""
        role = data.get("role") or ""
        self.status.setText(
            f"QQ 桥接：运行中｜QQ {'在线' if online else '离线'}｜模式 {mode or '?'}"
            f"｜角色 {role or '默认'}")
        self.qq_line.setText(f"当前登录 QQ：{owner or '（未读到）'}｜上次检查：{datetime.now():%H:%M:%S}")
        # 白名单以桥接实时值为准（界面只读展示 + 可改）
        groups = data.get("allowGroups") or []
        private = data.get("allowPrivate") or []
        if groups or private:
            lines = [str(x) for x in private] + [f"g:{x}" for x in groups]
            if self.whitelist.toPlainText().strip() != "\n".join(lines):
                self.whitelist.setPlainText("\n".join(lines))
