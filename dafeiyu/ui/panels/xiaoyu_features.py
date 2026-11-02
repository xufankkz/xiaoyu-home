"""小鱼 · 三个功能分区（语音 / 联系人 / 人设）

背景：AstrBot 那套里做过的能力，迁到「DSH + 桥接」后大多没有界面入口。
这里把有入口价值的三个补齐，和已有的「情绪 / 记忆 / 花费 / 思考」不重复。

  VoicePanel    —— 克隆音色（三语气）试听 + 直接发一条到 QQ
  ContactsPanel —— 称呼表与 QQ 白名单（只读展示，改动走配置）
  PersonaPanel  —— 人设查看/编辑，保存时同步写回两个 agent preset
"""

from __future__ import annotations
import release_paths

import base64
import json
import os
import re
import subprocess
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import hint_label, title_label

VOICE_CFG = Path(release_paths.path('astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json'))
BRIDGE_CFG = Path(release_paths.path('qq-bridge/config.json'))
PERSONA_MD = Path(release_paths.path('dafeiyu/data/config/personality.md'))
PRESETS = [
    Path(release_paths.path('qq-bridge/plugins/qq-agent-presets/presets/qq-chat.patch.yml')),
    Path(release_paths.path('qq-bridge/plugins/qq-agent-presets/presets/qq-chat-v2.patch.yml')),
]
ALIASES = {}
ALIASES_JSON = Path(release_paths.path('astrbot/data/plugins/astrbot_plugin_xiaoyu_alias/aliases.json'))


def _aliases() -> dict[str, str]:
    """返回 {QQ: 称呼}。优先读 aliases.json（与桥接 / QQ 里的 /称呼 同一份）。"""
    data = _read_json(ALIASES_JSON)
    if ALIASES_JSON.exists():
        return {
            str(qq).strip(): str(name).strip()
            for qq, name in data.items()
            if str(qq).strip() and str(name).strip()
        }
    return {qq: name for name, qq in ALIASES.items()}


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _post_json(url: str, payload: dict, token: str = "", timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


class VoicePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        outer.addWidget(title_label("语音"))
        outer.addWidget(hint_label("克隆音色（GPT-SoVITS）。选语气 → 试听，或直接发一条语音到她爸的 QQ。"))

        row = QHBoxLayout()
        row.addWidget(QLabel("语气"))
        self.tone = QComboBox()
        row.addWidget(self.tone)
        row.addWidget(QLabel("发给"))
        self.tone_target = QComboBox()
        self.tone_target.addItems(list(_aliases().values()))
        row.addWidget(self.tone_target)
        self.text = QLineEdit("爸爸，我今天状态不错，给你发条语音听听")
        row.addWidget(self.text, 1)
        self.btn_preview = QPushButton("试听")
        self.btn_send = QPushButton("发到 QQ")
        row.addWidget(self.btn_preview)
        row.addWidget(self.btn_send)
        outer.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        outer.addStretch(1)

        self.btn_preview.clicked.connect(self._preview)
        self.btn_send.clicked.connect(self._send)
        self.reload()

    def reload(self) -> None:
        cfg = _read_json(VOICE_CFG)
        self.tone.clear()
        for name in (cfg.get("tones") or {}).keys():
            self.tone.addItem(name)
        idx = self.tone.findText(str(cfg.get("default_tone") or "gentle"))
        if idx >= 0:
            self.tone.setCurrentIndex(idx)

    def _synth(self, text: str, tone: str) -> bytes:
        cfg = _read_json(VOICE_CFG)
        t = (cfg.get("tones") or {}).get(tone) or {}
        api = str(cfg.get("gsv_api") or release_paths.url('tts', '')).rstrip("/")
        payload = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": t.get("ref", ""),
            "prompt_text": t.get("prompt", ""),
            "prompt_lang": "zh",
            "text_split_method": "cut0",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "parallel_infer": True,
            "repetition_penalty": 1.25,
            "temperature": float(t.get("temperature", 0.62)),
            "speed_factor": float(t.get("speed", 0.96)),
        }
        req = urllib.request.Request(
            f"{api}/tts",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.read()

    def _preview(self) -> None:
        text = self.text.text().strip()
        if not text:
            self.status.setText("先写一句要念的话")
            return
        tone = self.tone.currentText()
        self.status.setText("合成中…")

        def work() -> None:
            try:
                wav = self._synth(text, tone)
                out = Path(os.environ.get("TEMP", ".")) / f"xiaoyu-preview-{tone}.wav"
                out.write_bytes(wav)
                self.status.setText(f"已合成 {len(wav)//1024} KB：{out}")
                os.startfile(str(out))  # noqa: S606
            except Exception as e:  # noqa: BLE001
                self.status.setText(f"合成失败：{e}")

        threading.Thread(target=work, daemon=True).start()

    def _send(self) -> None:
        text = self.text.text().strip()
        if not text:
            self.status.setText("先写一句要念的话")
            return
        tone = self.tone.currentText()
        target = self.tone_target.text().strip() if hasattr(self, "tone_target") else "爸爸"
        qq = {name: q for q, name in _aliases().items()}.get(target, target)
        self.status.setText("合成并发送中…")

        def work() -> None:
            try:
                wav = self._synth(text, tone)
                bcfg = _read_json(BRIDGE_CFG)
                http_url = str((bcfg.get("snowluma") or {}).get("httpUrl") or release_paths.url('onebot', '')).rstrip("/")
                token = str((bcfg.get("snowluma") or {}).get("accessToken") or "")
                res = _post_json(
                    f"{http_url}/send_private_msg",
                    {"user_id": int(qq), "message": [{"type": "record", "data": {"file": "base64://" + base64.b64encode(wav).decode()}}]},
                    token=token,
                )
                self.status.setText(f"已发送给 {target}（{qq}）：{json.dumps(res, ensure_ascii=False)[:120]}")
            except Exception as e:  # noqa: BLE001
                self.status.setText(f"发送失败：{e}")

        threading.Thread(target=work, daemon=True).start()


class ContactsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        outer.addWidget(title_label("联系人"))
        outer.addWidget(hint_label("她认谁、能跟谁说话。称呼在 QQ 里发 /称呼 增删（或改 aliases.json）；白名单改 qq-bridge/config.json 的 allow.private / allow.groups。"))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["称呼", "QQ", "权限"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        outer.addWidget(self.table, 1)

        btn = QPushButton("刷新")
        btn.clicked.connect(self.refresh)
        outer.addWidget(btn, 0, Qt.AlignLeft)

        self.refresh()

    def refresh(self) -> None:
        cfg = _read_json(BRIDGE_CFG)
        allow = [str(x) for x in ((cfg.get("allow") or {}).get("private") or [])]
        aliases = _aliases()
        rows = []
        for qq, name in aliases.items():
            rows.append((name, qq, "白名单内" if qq in allow else "未放行"))
        for qq in allow:
            if qq not in aliases:
                rows.append(("（未命名，可在 QQ 里发 /称呼 设置）", qq, "白名单内"))
        groups = [str(x) for x in ((cfg.get("allow") or {}).get("groups") or [])]
        for g in groups:
            rows.append(("群", g, "白名单内"))
        self.table.setRowCount(len(rows))
        for i, (name, qq, perm) in enumerate(rows):
            for j, val in enumerate((name, qq, perm)):
                self.table.setItem(i, j, QTableWidgetItem(val))


class PersonaPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        outer.addWidget(title_label("人设"))
        outer.addWidget(hint_label("编辑后保存：会同时写回 personality.md 和两个 QQ agent preset（她说话就按这份）。"))

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(self._load())
        outer.addWidget(self.editor, 1)

        row = QHBoxLayout()
        self.btn_save = QPushButton("保存并同步到 QQ")
        self.btn_reload = QPushButton("重新读取")
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_reload)
        row.addStretch(1)
        outer.addLayout(row)

        self.status = QLabel("")
        outer.addWidget(self.status)

        self.btn_save.clicked.connect(self._save)
        self.btn_reload.clicked.connect(lambda: self.editor.setPlainText(self._load()))

    def _load(self) -> str:
        try:
            return PERSONA_MD.read_text(encoding="utf-8-sig")
        except Exception as e:  # noqa: BLE001
            return f"（读不到人设文件：{e}）"

    def _save(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            self.status.setText("人设不能为空")
            return
        PERSONA_MD.write_text(text + "\n", encoding="utf-8")

        block_lines = ["                【小鱼·人设·开始（这是她本人，优先于下面所有通用规则）】"]
        block_lines += ["                " + ln.strip() for ln in text.splitlines() if ln.strip()]
        block_lines += ["                【小鱼·人设·结束】"]
        block = "\n".join(block_lines)
        patched = 0
        for p in PRESETS:
            raw = p.read_bytes()
            enc = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
            s = raw.decode(enc)
            new, n = re.subn(
                r"                【小鱼·人设·开始[^\n]*\n.*?\n                【小鱼·人设·结束】",
                block,
                s,
                count=1,
                flags=re.S,
            )
            if n:
                p.write_text(new, encoding=enc)
                patched += 1
        self.status.setText(f"已保存；同步到 {patched}/2 个 preset（重启 DSH Web 后生效）")

QZONE_API = release_paths.url('qzone', '')


def _qz(path: str, payload: dict | None = None, timeout: int = 45) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        QZONE_API + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


class QzonePanel(QWidget):
    """她的空间：最近说说+评论，可以手动替她发一条 / 回一条评论。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        outer.addWidget(title_label("空间"))
        outer.addWidget(hint_label("她自己想发就发；这里能看最近说了什么、谁评论了她，也能手动发一条。"))

        row = QHBoxLayout()
        self.post = QLineEdit()
        self.post.setPlaceholderText("替她发一条说说…")
        row.addWidget(self.post, 1)
        self.btn_post = QPushButton("发布")
        self.btn_refresh = QPushButton("刷新")
        row.addWidget(self.btn_post)
        row.addWidget(self.btn_refresh)
        outer.addLayout(row)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        outer.addWidget(self.view, 1)

        self.status = QLabel("")
        outer.addWidget(self.status)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_post.clicked.connect(self._do_post)
        self.refresh()

    def refresh(self) -> None:
        def work() -> None:
            try:
                data = _qz("/feed?count=5")
                lines = []
                for it in data.get("items", []):
                    lines.append(f"[{it.get('tid')}] {it.get('content', '')}")
                    for c in it.get("comments", []):
                        lines.append(f"    └ {c.get('name') or c.get('uin')}（{c.get('uin')}）：{c.get('content')}")
                    lines.append("")
                text = "\n".join(lines) or "（还没有说说）"
                cnt = data.get("count", 0)
                QTimer.singleShot(0, lambda: (self.view.setPlainText(text), self.status.setText(f"共 {cnt} 条")))
            except Exception as e:  # noqa: BLE001
                msg = f"读不到空间：{e}"
                QTimer.singleShot(0, lambda: self.view.setPlainText(msg))
        threading.Thread(target=work, daemon=True).start()

    def _do_post(self) -> None:
        text = self.post.text().strip()
        if not text:
            self.status.setText("先说点什么")
            return

        def work() -> None:
            try:
                r = _qz("/post", {"content": text})
                msg = f"发布结果：{json.dumps(r, ensure_ascii=False)[:120]}"
                QTimer.singleShot(0, lambda: (self.status.setText(msg), self.post.clear(), self.refresh()))
            except Exception as e:  # noqa: BLE001
                msg = f"发布失败：{e}"
                QTimer.singleShot(0, lambda: self.status.setText(msg))
        threading.Thread(target=work, daemon=True).start()

