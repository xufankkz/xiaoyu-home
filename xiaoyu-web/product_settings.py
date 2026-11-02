"""Shared product settings. Reads never probe a model or start a learning job."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
from urllib.parse import urlparse
from datetime import datetime
from uuid import uuid4

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from version import VERSION
import release_paths


class SettingsError(ValueError):
    pass


def load(path, default=None):
    if not path.exists():
        return copy.deepcopy(default if default is not None else {})
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except Exception:
        raise SettingsError("配置文件无法读取，已保留原文件，请先检查本地配置。") from None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def numeric(value, lo, hi, *, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise SettingsError("数值超出允许范围，请检查输入。")
    if integer and int(value) != value:
        raise SettingsError("此设置需要填写整数。")
    return int(value) if integer else float(value)


def boolean(value):
    if type(value) is not bool:
        raise SettingsError("开关值无效。")
    return value


def clock(value):
    try:
        datetime.strptime(value, "%H:%M")
        if len(value) != 5:
            raise ValueError()
    except Exception:
        raise SettingsError("时间请填写为 时:分。") from None
    return value


UI_THEMES = ("xiaoyu-room", "sunlit-journal", "forest-cottage", "deepsea-night")
UI_COLOR_MODES = ("light", "dark", "system")
UI_NIGHT_PALETTES = ("charcoal", "sky-blue", "slate-blue")
UI_DEFAULTS = {"theme": "xiaoyu-room", "color_mode": "light", "night_palette": "charcoal", "font_size": "standard", "scale": 100, "density": "comfortable", "animations": True}
PREF_DEFAULTS = {"appearance": UI_DEFAULTS, "budget": {"daily_warning": 0},
                 "backup": {"enabled": False, "time": "04:00"}}
DRAWING_DEFAULTS = {"enabled": True, "endpoint": "", "model": "", "key_mode": "chat",
                    "size": "1024x1024", "daily_limit": 6,
                    "style": "儿童拿蜡笔和彩笔在白纸上随手画的稚拙简笔涂鸦。白纸平面背景，大量留白；线条歪歪扭扭、断续和重描；圆圆的大头、豆豆眼、弯线嘴巴、火柴棍胳膊腿，人物只画几笔。几条蓝线表示小鱼的蓝头发，小蓝团表示鲸鱼发夹。每幅只保留主体和两三个简单物件，大小比例不太准，构图朴素不对称。只用几支彩笔的鲜明平涂色，涂色留有空隙、出界与重复笔痕。保留孩子表达亲近和开心的笨拙感。不要专业插画、动漫立绘、精致五官、细密发丝、柔光、渐变、阴影塑形、景深、立体渲染或摄影效果。",
                    "character_description": "", "companion_description": ""}


def drawing_text(value, limit=1200):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise SettingsError("绘画设置文字过长或格式无效。")
    return value.strip()


def drawing_endpoint(value):
    value = drawing_text(value, 500).rstrip("/")
    if not value:
        return value
    url = urlparse(value)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment
            or (url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"))):
        raise SettingsError("请填写 HTTPS 绘画接口根地址，或本机 HTTP 地址；不要填写 Key 或 images/generations 后缀。")
    if url.path.endswith("/images/generations"):
        raise SettingsError("绘画接口请填写根地址，例如 https://服务地址/v1。")
    return value


class ProductSettings:
    def __init__(self, root, core, bridge, *, snapshotter=None):
        self.root, self.core = Path(root), Path(core)
        self.bridge = bridge
        self.paths = {
            "bridge": self.root / "qq-bridge/config.json",
            "voice": self.root / "astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json",
            "webstudy": self.core / "data/config/webstudy.json",
            "learning": self.core / "data/config/learning.json",
            "thresholds": self.core / "data/config/thresholds.json",
            "preferences": self.root / "xiaoyu-web/data/settings.json",
            "llm": self.core / "data/config/llm.json",
            "credentials": Path(release_paths.path('dsh/.credentials.yaml')),
            "drawing": self.root / "qq-bridge/data/drawing/config.json",
        }
        self.state_path = self.root / "xiaoyu-web/data/backup-state.json"
        self.lock = threading.RLock()
        self.backup_lock = threading.Lock()
        self.job = {"running": False}
        self.snapshotter = snapshotter

    def preferences(self):
        value = load(self.paths["preferences"])
        return {key: {**default, **value.get(key, {})} for key, default in PREF_DEFAULTS.items()}

    def revision(self):
        pairs = [(key, digest(path)) for key, path in sorted(self.paths.items())]
        return hashlib.sha256(json.dumps(pairs).encode()).hexdigest()

    def read(self):
        with self.lock:
            c = load(self.paths["bridge"])
            social = c.get("socialV2", {})
            proactive = social.get("proactive", {})
            daily = proactive.get("dailyContact", {})
            wait = social.get("wait", {})
            voice = load(self.paths["voice"])
            auto = social.get("voice", {}).get("auto", {})
            media = social.get("media", {}).get("understanding", {})
            study = load(self.paths["webstudy"])
            quota = {**study.get("quota", {}), **load(self.paths["learning"]).get("webstudy_quota", {})}
            quiet = daily.get("quietHours", load(self.paths["thresholds"]).get("chitchat", {}).get("quiet_hours", ["23:30", "08:30"]))
            prefs = self.preferences()
            drawing = {**DRAWING_DEFAULTS, **load(self.paths["drawing"])}
            values = {**prefs,
                "chat": {"wait_seconds": wait.get("minQuietAfterNewMs", 10000) / 1000,
                         "memory_enabled": social.get("context", {}).get("memoryEnabled", social.get("tools", {}).get("memory", True)) is not False,
                         "proactive_enabled": daily.get("enabled", True) and proactive.get("enabled", True), "daily_count": daily.get("maxPerDay", 1),
                         "idle_minutes": daily.get("idleMs", 1500000) / 60000,
                         "retry_minutes": daily.get("retryMs", 2700000) / 60000,
                         "start": daily.get("start", "10:00"), "end": daily.get("end", "21:30"),
                         "quiet_start": quiet[0], "quiet_end": quiet[1]},
                "voice": {"auto_enabled": auto.get("enabled", True), "percent": round(auto.get("probability", .3) * 100, 6),
                          "incoming_voice": media.get("voiceEnabled", True), "incoming_video": media.get("videoEnabled", True),
                          "default_tone": voice.get("default_tone", "gentle"),
                          "speeds": {key: tone.get("speed", 1.1) for key, tone in voice.get("tones", {}).items()}},
                "learning": {"enabled": study.get("enabled", False), "auto_run": study.get("auto_run", True),
                             "rounds_per_day": quota.get("rounds_per_day", 6), "items_per_round": quota.get("items_per_round", 6),
                             "min_interval_minutes": quota.get("min_interval_minutes", 120)}}
            values["drawing"] = {key: drawing[key] for key in DRAWING_DEFAULTS}
            return {"revision": self.revision(), "values": values,
                    "backup_status": self.backup_status(), "version": "小鱼之家 · 共用设置 1.0"}

    def validate(self, changes):
        if not isinstance(changes, dict) or not changes:
            raise SettingsError("没有要保存的修改。")
        current = self.read()["values"]
        rules = {
            "appearance": {"theme": UI_THEMES, "color_mode": UI_COLOR_MODES, "night_palette": UI_NIGHT_PALETTES, "font_size": ("standard", "large", "larger"), "scale": (90, 100, 110, 125),
                           "density": ("comfortable", "compact"), "animations": boolean},
            "chat": {"wait_seconds": lambda v: numeric(v, 1, 120, integer=True), "proactive_enabled": boolean,
                     "memory_enabled": boolean,
                     "daily_count": lambda v: numeric(v, 1, 3, integer=True),
                     "idle_minutes": lambda v: numeric(v, 5, 1440, integer=True),
                     "retry_minutes": lambda v: numeric(v, 5, 1440, integer=True),
                     **{key: clock for key in ("start", "end", "quiet_start", "quiet_end")}},
            "voice": {"auto_enabled": boolean, "percent": lambda v: numeric(v, 0, 100),
                      "incoming_voice": boolean, "incoming_video": boolean,
                      "default_tone": tuple(current["voice"]["speeds"]), "speeds": "speeds"},
            "learning": {"enabled": boolean, "auto_run": boolean,
                         "rounds_per_day": lambda v: numeric(v, 0, 60, integer=True),
                         "items_per_round": lambda v: numeric(v, 1, 50, integer=True),
                         "min_interval_minutes": lambda v: numeric(v, 0, 1440, integer=True)},
            "budget": {"daily_warning": lambda v: numeric(v, 0, 10000)},
            "backup": {"enabled": boolean, "time": clock},
            "drawing": {"enabled": boolean, "endpoint": drawing_endpoint,
                        "model": lambda v: drawing_text(v, 160), "key_mode": ("chat", "separate"),
                        "size": ("1024x1024", "1024x1536", "1536x1024"),
                        "daily_limit": lambda v: numeric(v, 1, 100, integer=True),
                        **{key: drawing_text for key in ("style", "character_description", "companion_description")}},
        }
        result = {}
        for section, patch in changes.items():
            if section not in rules or not isinstance(patch, dict) or not patch:
                raise SettingsError("设置分区无效。")
            result[section] = {}
            for key, value in patch.items():
                rule = rules[section].get(key)
                if rule is None:
                    raise SettingsError("包含未知设置，已取消保存。")
                if rule == "speeds":
                    if not isinstance(value, dict) or set(value) - set(current["voice"]["speeds"]):
                        raise SettingsError("请选择已有音色。")
                    checked = {k: numeric(v, .8, 1.5) for k, v in value.items()}
                elif isinstance(rule, tuple):
                    if value not in rule or type(value) is bool:
                        raise SettingsError("选项无效。")
                    checked = value
                else:
                    checked = rule(value)
                result[section][key] = checked
        chat = {**current["chat"], **result.get("chat", {})}
        if chat["start"] >= chat["end"]:
            raise SettingsError("主动联系结束时间应晚于开始时间。")
        return result

    def snapshot(self, paths):
        dest = self.root / "_backup" / ("settings-save-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
        dest.mkdir(parents=True)
        records = []
        for index, path in enumerate(dict.fromkeys(paths)):
            existed = path.exists()
            record = {"source": str(path), "existed": existed}
            if existed:
                raw = path.read_bytes()
                target = dest / (str(index) + "-" + path.name)
                target.write_bytes(raw)
                expected = hashlib.sha256(raw).hexdigest()
                if digest(target) != expected:
                    raise SettingsError("备份校验失败，已取消保存。")
                record.update(path=str(target), sha256=expected)
            records.append(record)
        atomic(dest / "manifest.json", {"complete": True, "files": records})
        return dest

    def save(self, body):
        with self.lock:
            if body.get("revision") != self.revision():
                raise SettingsError("设置已被另一端修改，请重新加载后再保存。")
            changes = self.validate(body.get("changes"))
            plans = {}
            social = {}
            cfg = load(self.paths["bridge"])
            existing = cfg.get("socialV2", {})
            if "drawing" in changes:
                drawing = load(self.paths["drawing"])
                drawing.update(changes["drawing"])
                plans[self.paths["drawing"]] = drawing
            if "chat" in changes:
                patch = changes["chat"]
                if "memory_enabled" in patch:
                    social["context"] = {**existing.get("context", {}), "memoryEnabled": patch["memory_enabled"]}
                if "wait_seconds" in patch:
                    ms = patch["wait_seconds"] * 1000
                    social["wait"] = {**existing.get("wait", {}), "minQuietAfterNewMs": ms, "defaultQuietMs": ms}
                daily = copy.deepcopy(existing.get("proactive", {}).get("dailyContact", {}))
                mapping = {"proactive_enabled": ("enabled", 1), "daily_count": ("maxPerDay", 1),
                           "idle_minutes": ("idleMs", 60000), "retry_minutes": ("retryMs", 60000),
                           "start": ("start", 1), "end": ("end", 1)}
                for source, (target, factor) in mapping.items():
                    if source in patch:
                        daily[target] = patch[source] if factor == 1 else patch[source] * factor
                quiet = self.read()["values"]["chat"]
                if "quiet_start" in patch or "quiet_end" in patch:
                    daily["quietHours"] = [patch.get("quiet_start", quiet["quiet_start"]), patch.get("quiet_end", quiet["quiet_end"])]
                if set(patch) - {"wait_seconds", "memory_enabled"}:
                    social["proactive"] = {**existing.get("proactive", {}), "dailyContact": daily}
                    if "proactive_enabled" in patch:
                        social["proactive"]["enabled"] = patch["proactive_enabled"]
            if "voice" in changes:
                patch = changes["voice"]
                if "auto_enabled" in patch or "percent" in patch:
                    auto = {**existing.get("voice", {}).get("auto", {})}
                    if "auto_enabled" in patch: auto["enabled"] = patch["auto_enabled"]
                    if "percent" in patch: auto["probability"] = patch["percent"] / 100
                    social["voice"] = {**existing.get("voice", {}), "auto": auto}
                if "incoming_voice" in patch or "incoming_video" in patch:
                    media = copy.deepcopy(existing.get("media", {}))
                    understand = media.setdefault("understanding", {})
                    if "incoming_voice" in patch: understand["voiceEnabled"] = patch["incoming_voice"]
                    if "incoming_video" in patch: understand["videoEnabled"] = patch["incoming_video"]
                    social["media"] = media
                if "default_tone" in patch or "speeds" in patch:
                    voice = load(self.paths["voice"])
                    if "default_tone" in patch: voice["default_tone"] = patch["default_tone"]
                    for key, speed in patch.get("speeds", {}).items(): voice["tones"][key]["speed"] = speed
                    plans[self.paths["voice"]] = voice
            if "learning" in changes:
                patch = changes["learning"]
                study = load(self.paths["webstudy"])
                for key in ("enabled", "auto_run"):
                    if key in patch: study[key] = patch[key]
                quota_patch = {k: v for k, v in patch.items() if k not in ("enabled", "auto_run")}
                study.setdefault("quota", {}).update(quota_patch)
                plans[self.paths["webstudy"]] = study
                learning = load(self.paths["learning"])
                overlay = learning.get("webstudy_quota", {})
                # Existing overlay remains the effective source for already overridden keys.
                overlap = {k: v for k, v in quota_patch.items() if k in overlay}
                if overlap:
                    overlay.update(overlap)
                    plans[self.paths["learning"]] = learning
            prefs = load(self.paths["preferences"])
            for section in ("appearance", "budget", "backup"):
                if section in changes:
                    prefs.setdefault(section, {}).update(changes[section])
                    plans[self.paths["preferences"]] = prefs
            affected = list(plans) + ([self.paths["bridge"]] if social else [])
            before = {p: p.read_bytes() if p.exists() else None for p in affected}
            backup = self.snapshot(affected)
            if body["revision"] != self.revision():
                raise SettingsError("备份期间设置发生变化，请重新加载后再保存。")
            written = []
            try:
                # Only one live owner is written, last; its endpoint checks the original file hash.
                for path, value in plans.items():
                    atomic(path, value)
                    written.append(path)
                if social:
                    self.bridge("/api/socialV2/config", {**social, "_expectedFileHash": hashlib.sha256(before[self.paths["bridge"]]).hexdigest()})
            except Exception:
                for path in reversed(written):
                    raw = before[path]
                    if raw is None: path.unlink(missing_ok=True)
                    else:
                        temp = path.with_name(path.name + ".rollback.tmp")
                        temp.write_bytes(raw)
                        os.replace(temp, path)
                raise SettingsError("保存未完成，本地改动已回滚。请检查连接并重新加载；若桥接响应中断，请核对重新加载后的值。") from None
            return {**self.read(), "saved": True, "backup": str(backup)}

    def backup_status(self):
        with self.backup_lock:
            return {**load(self.state_path), **self.job}

    def start_backup(self, scheduled_day=None):
        with self.backup_lock:
            if self.job.get("running"):
                return {**self.job, "already_running": True}
            self.job = {"running": True, "started_at": datetime.now().isoformat(timespec="seconds"), "error": ""}
            result = dict(self.job)
        threading.Thread(target=self._backup_work, args=(scheduled_day,), daemon=True).start()
        return result

    def _backup_work(self, scheduled_day):
        try:
            if self.snapshotter:
                result = self.snapshotter()
            else:
                scripts = str(self.root / "scripts")
                if scripts not in sys.path: sys.path.insert(0, scripts)
                import project_maintenance as maintenance
                dest = self.root / "_backup" / ("settings-manual-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6])
                maintenance.create_snapshot(self.root, self.core, dest)
                verified = maintenance.verify_snapshot(dest)
                if not verified.get("ok"):
                    raise SettingsError("备份校验未通过。")
                result = {"path": str(dest), "verified": True}
            state = load(self.state_path)
            state.update(last_success=datetime.now().isoformat(timespec="seconds"), **result)
            if scheduled_day: state["scheduled_day"] = scheduled_day
            atomic(self.state_path, state)
            update = {"running": False, "error": "", "finished_at": datetime.now().isoformat(timespec="seconds")}
        except Exception:
            update = {"running": False, "error": "备份失败，未覆盖已有备份。请检查磁盘空间和文件读取权限。"}
        with self.backup_lock: self.job.update(update)

    def schedule_tick(self, now=None):
        now = now or datetime.now()
        prefs = self.preferences()["backup"]
        day = now.date().isoformat()
        state = self.backup_status()
        if prefs["enabled"] and now.strftime("%H:%M") >= prefs["time"] and state.get("scheduled_day") != day and not state.get("running"):
            # One attempt per day, including failures; service restart does not create a retry storm.
            previous = load(self.state_path)
            previous["scheduled_day"] = day
            atomic(self.state_path, previous)
            self.start_backup(day)

    def export(self):
        current = self.read()
        # Appearance notes may describe a private person; exclude them from diagnostic exports.
        for key in ("character_description", "companion_description"):
            current["values"]["drawing"].pop(key, None)
        return {"application": "小鱼之家", "exported_at": datetime.now().isoformat(timespec="seconds"),
                "values": current["values"], "note": "不含 Key、联系人、聊天内容及记忆原文。"}

    def model_info(self):
        # The bridge projects only safe fields; provider credentials never cross this boundary.
        return self.bridge("/api/product-settings/model")

    def drawing_info(self):
        return self.bridge("/api/drawing/status")

    def drawing_gallery(self):
        return self.bridge("/api/drawing/gallery?limit=12")

    def replace_drawing_key(self, body):
        key = body.get("key")
        if not isinstance(key, str) or not 16 <= len(key.strip()) <= 512 or any(c.isspace() for c in key.strip()):
            raise SettingsError("请输入有效的绘画 Key。")
        with self.lock:
            if body.get("revision") != self.revision():
                raise SettingsError("设置已变化，请重新加载后再保存绘画 Key。")
            path = self.paths["drawing"]
            value = load(path)
            backup = self.snapshot([path])
            if body["revision"] != self.revision():
                raise SettingsError("备份期间设置发生变化，请重新加载。")
            value["api_key"] = key.strip()
            value["key_mode"] = "separate"
            atomic(path, value)
            return {"saved": True, "backup": str(backup), "revision": self.revision(),
                    "message": "绘画专用 Key 已保存，聊天和语音设置保持原值。"}

    def replace_key(self, body):
        key = body.get("key")
        if not isinstance(key, str) or not 16 <= len(key.strip()) <= 512 or any(c.isspace() for c in key.strip()):
            raise SettingsError("请输入有效的新 Key。")
        with self.lock:
            if body.get("revision") != self.revision():
                raise SettingsError("设置已变化，请重新加载后再更换 Key。")
            # Supported DSH credential API owns its lock, persistence and runtime refresh.
            credential = Path(release_paths.path('dsh/.credentials.yaml'))
            backup = self.snapshot([credential, self.paths["llm"]])
            try:
                result = self.bridge("/api/product-settings/key", {"key": key.strip()})
            except Exception:
                raise SettingsError("Key 更新未确认，请重新加载查看。原配置快照已保留。") from None
            return {"saved": True, "backup": str(backup), **result, "revision": self.revision()}

    def diagnostics(self):
        checks = {}
        for key, path in self.paths.items():
            if key == "credentials":
                checks[key] = {"exists": path.exists()}
                continue
            try: checks[key] = {"exists": path.exists(), "readable": isinstance(load(path), dict)}
            except SettingsError: checks[key] = {"exists": path.exists(), "readable": False}
        return {"application": "小鱼之家", "generated_at": datetime.now().isoformat(timespec="seconds"),
                "version": VERSION, "checks": checks,
                "note": "仅导出文件可读性，不含 Key、账号、消息、日志原文或记忆。"}
