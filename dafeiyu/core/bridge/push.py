# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""把话递到 QQ（碎碎念要用）。

桥接在 reserved2 模式下只认会话级 agentToken，所以这里按「控制台 token → 会话 agentToken」
逐级尝试；全失败就落 state/outbox/ 等下一轮重试，绝不让碎碎念把主流程卡住。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime

from .. import settings

BRIDGE_DIR = r""


def _bridge_dir() -> str:
    return str(settings.app().get("bridge", {}).get("dir") or BRIDGE_DIR)


def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def _port() -> int:
    cfg = _read_json(f"{_bridge_dir()}\\config.json", {}) or {}
    return int(cfg.get("consolePort") or 3100)


def _owner() -> str:
    return str(settings.app().get("bridge", {}).get("owner_qq") or "100000000")


def _agent_token() -> str:
    state = _read_json(f"{_bridge_dir()}\\state\\social-v2.json", {}) or {}
    conv = (state.get("conversations") or {}).get(f"private:{_owner()}") or {}
    return str(conv.get("agentToken") or "")


def _console_token() -> str:
    return _read_text(f"{_bridge_dir()}\\state\\console-token")


def mode() -> str:
    return str((_read_json(f"{_bridge_dir()}\\state\\mode.json", {}) or {}).get("mode") or "")


def _post(path: str, payload: dict, token: str, timeout: float = 8.0,
          console_token: str = "", agent_token: str = "") -> tuple[bool, str]:
    body = dict(payload)
    if token:
        body["token"] = token
    headers = {"Content-Type": "application/json"}
    # 桥接要过两道闸：x-console-token（控制台身份）+ x-agent-token（会话级 AI 身份）
    if console_token:
        headers["x-console-token"] = console_token
    if agent_token:
        headers["x-agent-token"] = agent_token
    req = urllib.request.Request(
        f"http://127.0.0.1:{_port()}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip().startswith("{") else {"ok": True, "raw": raw}
        if data.get("ok") is False:
            return False, str(data.get("error") or "unknown")
        return True, "ok"
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", "")
        except Exception:
            detail = ""
        return False, f"HTTP {exc.code} {detail}".strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def send_private(text: str, user_id: str | None = None) -> dict:
    """发一条私聊。返回 {'ok':bool,'via':..,'error':..,'outbox':..}"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "空消息"}
    if not settings.app().get("bridge", {}).get("enabled", True):
        return _outbox(text, user_id, "bridge disabled")
    uid = str(user_id or _owner())
    console = _console_token()
    agent = _agent_token()
    errors = []
    for label, token in (("agent", agent), ("console", console), ("none", "")):
        ok, err = _post("/api/send/private", {"userId": uid, "message": text}, token,
                        console_token=console, agent_token=agent)
        if ok:
            return {"ok": True, "via": label, "len": len(text)}
        errors.append(f"{label}:{err}")
    return _outbox(text, uid, " | ".join(errors))


def _outbox(text: str, user_id: str | None, error: str) -> dict:
    folder = settings.STATE / "outbox"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"chitchat-{datetime.now():%Y%m%d-%H%M%S}-{abs(hash(text)) % 10000}.json"
    payload = {"text": text, "userId": user_id or _owner(), "t": datetime.now().isoformat(timespec="seconds"),
               "error": error}
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return {"ok": False, "error": error, "outbox": str(path)}


def flush_outbox(limit: int = 5) -> dict:
    """重试外发队列（每次 tick 试一小把，不刷屏）。"""
    folder = settings.STATE / "outbox"
    if not folder.exists():
        return {"sent": 0, "left": 0}
    files = sorted(folder.glob("chitchat-*.json"))[:limit]
    sent = 0
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            path.unlink(missing_ok=True)
            continue
        result = send_private(str(payload.get("text") or ""), payload.get("userId"))
        if result.get("ok"):
            path.unlink(missing_ok=True)
            sent += 1
        else:
            break
    return {"sent": sent, "left": len(list(folder.glob("chitchat-*.json")))}


def status() -> dict:
    return {"mode": mode(), "port": _port(), "owner": _owner(),
            "has_agent_token": bool(_agent_token()), "has_console_token": bool(_console_token()),
            "outbox": len(list((settings.STATE / "outbox").glob("chitchat-*.json")))
            if (settings.STATE / "outbox").exists() else 0}
