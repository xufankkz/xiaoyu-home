#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xiaoyu-web — 小鱼桌面 UI 的网页版后端

只用 Python 标准库。静态文件 + 本地数据聚合 API，监听 127.0.0.1:18891。

运行：
    <workspace>/dafeiyu\\.venv\\Scripts\\python.exe <workspace>\\xiaoyu-web\\server.py

设计要点：
  * 所有读操作（文件 / SQLite / 本地 HTTP）都 try/except，失败降级为"暂无数据"，
    绝不 500，绝不抛到客户端。
  * 统一响应 {"ok": true, "data": ...} / {"ok": false, "error": "..."}
  * 短 TTL 内存缓存（1~15s），扛得住 3 秒轮询，不重复读整个库。
"""
import release_paths

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import zstandard
except Exception:  # pragma: no cover - 语音/思考面板会降级
    zstandard = None

# ──────────────────────────── 配置 ────────────────────────────

HOST = "127.0.0.1"
PORT = release_paths.port('web')
BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
if BASE not in sys.path:
    sys.path.insert(0, BASE)
from thought_translation import ThoughtTranslations, translate_batch
from chat_status import build_chat_status
from product_settings import ProductSettings, SettingsError, UI_THEMES
from persona_settings import PersonaSettings
from room_features import ContactPreferences
from recovery_preview import RecoveryPreview
from douyin_social import DouyinSocial
from version import VERSION, RELEASE

DAFEIYU = release_paths.path('dafeiyu')
DB_PATH = os.path.join(DAFEIYU, r"data\db\dafeiyu.db")
ASSET_DIR = os.path.join(DAFEIYU, r"data\ui\assets")
PERSONALITY = os.path.join(DAFEIYU, r"data\config\personality.md")
WEBSTUDY_JSON = os.path.join(DAFEIYU, r"data\config\webstudy.json")
REPORTS_DIR = os.path.join(DAFEIYU, r"data\learning\reports")

QZONE_BASE = release_paths.url('qzone', '')
DIARY_BASE = release_paths.url('diary', '')
TTS_BASE = release_paths.url('tts', '')
SNOWLUMA_BASE = release_paths.url('onebot', '')
CONSOLE_BASE = release_paths.url('bridge', '')
CORE_BASE = release_paths.url('core', '')

QQBRIDGE_CONFIG = release_paths.path('qq-bridge/config.json')
CONSOLE_TOKEN_FILE = release_paths.path('qq-bridge/state/console-token')
VOICE_CONFIG = release_paths.path('astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json')
ALIASES_JSON = release_paths.path('astrbot/data/plugins/astrbot_plugin_xiaoyu_alias/aliases.json')
QQ_SESSIONS_JSON = release_paths.path('qq-bridge/state/sessions.json')

SESSIONS_ROOT = os.path.expandvars(release_paths.path('dsh/sessions'))
SNOW_TOOL = "mcp__snowluma__"

HTTP_TIMEOUT = 8.0
ASSET_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".svg"}

# 音频小仓库：/api/voice/synth 生成，/api/voice/audio 播放
_AUDIO = {}
_AUDIO_LOCK = threading.Lock()
_AUDIO_TTL = 1800
_AUDIO_MAX = 24


# ──────────────────────────── 日志 ────────────────────────────

_LOG_LOCK = threading.Lock()


def log(msg, level="INFO"):
    line = "[%s] %-5s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, msg)
    with _LOG_LOCK:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:
            pass


# ──────────────────────────── 缓存 ────────────────────────────

_CACHE = {}
_CACHE_LOCK = threading.Lock()


def cached(key, ttl, fn):
    """带 TTL 的内存缓存。fn 抛异常时不写缓存，异常向上抛由调用方 try/except。"""
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    val = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (now + ttl, val)
    return val


# ──────────────────────────── 工具 ────────────────────────────

def http_json(url, data=None, headers=None, timeout=HTTP_TIMEOUT, allow_error_body=False):
    """发本地 HTTP 请求拿 JSON。

    allow_error_body=True 时，4xx/5xx 只要响应体是 JSON 也照常返回（并带 _http_status），
    用于日记 /diary/get 这种"没有就 404"的接口。
    """
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b""
        status = exc.code
        if not allow_error_body:
            raise
        try:
            parsed = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            parsed["_http_status"] = status
            return parsed
        return {"_http_status": status, "ok": False, "error": "HTTP %s" % status}
    if not raw:
        return None
    parsed = json.loads(raw.decode("utf-8", "replace"))
    if isinstance(parsed, dict):
        parsed.setdefault("_http_status", status)
    return parsed


def http_bytes(url, data=None, headers=None, timeout=120.0):
    hdrs = {"Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def read_text(path, default=""):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return raw.decode("utf-8-sig", "replace")
    except Exception:
        return default


def read_json(path, default=None):
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8-sig"))
    except Exception:
        return default


def write_text_atomic(path, text):
    tmp = path + ".xiaoyu-web.tmp"
    with open(tmp, "wb") as fh:
        fh.write(text.encode("utf-8"))
    os.replace(tmp, path)


def db_rows(sql, params=()):
    """只读查库，短超时，绝不阻塞写入方。"""
    uri = "file:%s?mode=ro" % DB_PATH.replace("\\", "/")
    con = sqlite3.connect(uri, uri=True, timeout=3.0)
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        try:
            con.close()
        except Exception:
            pass


def db_one(sql, params=()):
    rows = db_rows(sql, params)
    return rows[0] if rows else None


def jloads(text, default=None):
    """容错解析 JSON 字符串字段（DB 里很多列存的是 JSON 字符串）。"""
    if not text:
        return default
    if isinstance(text, (list, dict)):
        return text
    try:
        return json.loads(text)
    except Exception:
        return default


def today_str():
    return date.today().isoformat()


def fmt_ts(ms):
    """毫秒时间戳 → 本地时间字符串；0/空/负数返回空串（避免 1970-01-01 这种噪音）。"""
    try:
        v = float(ms)
    except Exception:
        return ""
    if v <= 0:
        return ""
    try:
        return datetime.fromtimestamp(v / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def pretty_args(raw, limit=400):
    """把 tool/call 的 arguments 变成人能读的 "键: 值" 多行文本。"""
    if isinstance(raw, (dict, list)):
        obj = raw
    else:
        s = str(raw or "")
        if not s.strip():
            return ""
        try:
            obj = json.loads(s)
        except Exception:
            return truncate(s, limit)
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            lines.append("%s: %s" % (k, v))
        return truncate("\n".join(lines), limit)
    return truncate(json.dumps(obj, ensure_ascii=False), limit)


def truncate(text, limit):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + " …"


def console_token():
    return read_text(CONSOLE_TOKEN_FILE).strip()


def bridge_config():
    return read_json(QQBRIDGE_CONFIG, {}) or {}


def snowluma_token():
    return str(((bridge_config().get("snowluma") or {}).get("accessToken")) or "")


def num(v, default=0.0):
    try:
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except Exception:
        return default


# ──────────────────────────── 思考：DSH 会话日志 ────────────────────────────

_SESSION_CACHE = {"scan_at": 0.0, "files": []}
_SCAN_LOCK = threading.Lock()
_THINKING_LOCK = threading.RLock()
_THINKING_PARSED = {}
_THINKING_OWNERS = {"signature": None, "value": {}}
SCAN_BUDGET = 6 * 1024 * 1024     # 单文件最多解压 6MB
SCAN_MAX_LINES = 6000


def _list_session_files():
    """列出所有 session.v4.jsonl.zstd，按 mtime 倒序。缓存 20s。"""
    now = time.time()
    if _SESSION_CACHE["files"] and now - _SESSION_CACHE["scan_at"] < 20:
        return _SESSION_CACHE["files"]
    found = []
    try:
        for dirpath, _dirnames, filenames in os.walk(SESSIONS_ROOT):
            for fn in filenames:
                if fn != "session.v4.jsonl.zstd":
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except Exception:
                    continue
                found.append({"path": p, "mtime": st.st_mtime, "size": st.st_size})
    except Exception as exc:
        log("扫描会话目录失败: %s" % exc, "WARN")
    found.sort(key=lambda x: x["mtime"], reverse=True)
    _SESSION_CACHE["files"] = found
    _SESSION_CACHE["scan_at"] = now
    return found


def _thinking_session_owners():
    """只用桥接持久化归属，绝不从聊天内容或相同工作目录猜联系人。"""
    state_dir = os.path.dirname(QQ_SESSIONS_JSON)
    sources = [(QQ_SESSIONS_JSON + suffix, "mapping") for suffix in ("", ".bak", ".bak2")]
    sources += [(os.path.join(state_dir, name), field) for name, field in
                (("token-usage.jsonl", "sid"), ("tool-calls.jsonl", "sessionId"))]
    signature = []
    for path, field in sources:
        try:
            stat = os.stat(path)
            signature.append((path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((path, None, None))
    with _THINKING_LOCK:
        if signature == _THINKING_OWNERS["signature"]:
            return _THINKING_OWNERS["value"]
        owners = {}

        def add(sid, peer):
            if (isinstance(sid, str) and re.fullmatch(r"session-[A-Za-z0-9-]+", sid)
                    and isinstance(peer, str) and re.fullmatch(r"private:\d+", peer)):
                owners.setdefault(sid, set()).add(peer)

        for path, field in sources:
            if field == "mapping":
                for peer, sid in ((read_json(path, {}) or {}).get("sessions") or {}).items():
                    add(sid, peer)
                continue
            try:
                with open(path, encoding="utf-8-sig", errors="replace") as stream:
                    for line in stream:
                        try:
                            row = json.loads(line)
                            if isinstance(row, dict):
                                add(row.get(field), row.get("key"))
                        except ValueError:
                            continue  # 正在追加的末行下次重读
            except OSError:
                continue
        # 归属有冲突时不把同一份记录展示给两个联系人。
        result = {sid: next(iter(keys)) for sid, keys in owners.items() if len(keys) == 1}
        _THINKING_OWNERS.update(signature=signature, value=result)
        return result


def _qq_peer_list():
    """当前白名单联系人及其仍保留在磁盘上的全部已确认归属会话。"""
    cfg = read_json(QQBRIDGE_CONFIG, {}) or {}
    allow = [str(x) for x in ((cfg.get("allow") or {}).get("private") or [])]
    owner = str(cfg.get("ownerQQ") or "")
    aliases = read_json(ALIASES_JSON, {}) or {}
    bridge_sessions = (read_json(QQ_SESSIONS_JSON, {}) or {}).get("sessions") or {}
    owners = _thinking_session_owners()
    files_by_peer = {}
    for f in _list_session_files():
        sid = os.path.basename(os.path.dirname(f["path"]))
        peer = owners.get(sid)
        if peer:
            files_by_peer.setdefault(peer, []).append(dict(f, id=sid))

    peers = []
    seen = set()

    def add(key, sid):
        m = re.match(r"^private:(\d+)$", str(key or ""))
        if not m:
            return
        qq = m.group(1)
        if qq in seen:
            return
        seen.add(qq)
        history = files_by_peer.get(key, [])
        f = history[0] if history else None
        current = next((entry for entry in history if entry["id"] == sid), None)
        alias = str(aliases.get(qq) or "").strip()
        peers.append({
            "key": "private:" + qq,
            "kind": "private",
            "qq": qq,
            "alias": alias,
            "label": alias or ("QQ " + qq),
            "session": str(sid or ""),
            "path": current["path"] if current else "",
            "history": history,
            "history_sessions": len(history),
            "has_data": bool(f),
            "mtime": f["mtime"] if f else 0,
            "time": fmt_ts(f["mtime"] * 1000) if f else "",
            "size_kb": round(f["size"] / 1024.0, 1) if f else 0,
        })

    for key, sid in bridge_sessions.items():
        add(key, sid)
    for qq in allow:
        add("private:" + qq, bridge_sessions.get("private:" + qq, ""))

    allow_set = set(allow)
    peers = [p for p in peers if p["qq"] in allow_set]
    peers.sort(key=lambda p: (p["qq"] != owner, -p["mtime"], p["qq"]))
    return peers


def _public_peers(peers):
    return [{k: v for k, v in p.items() if k not in ("path", "history")} for p in peers]


def _iter_events(path):
    """逐行读取至文件结尾；历史记录不再受旧版 6MB / 6000 行截断。"""
    if zstandard is None:
        raise RuntimeError("zstandard 未安装")
    with open(path, "rb") as fh:
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(fh) as reader:
            stream = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                if line[0] != "{":
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if isinstance(ev, dict):
                    yield ev


def _snow_call_name(line):
    """从一条 tool/call 事件行里取出工具名并判断是不是 snowluma 调用。

    只切到 "arguments" 之前再找 "name":"…"，避免把参数里出现的
    mcp__snowluma__ 字样（比如自己写的探测脚本）误判成调用。
    """
    flat = line.replace(b" ", b"")
    if b'"type":"tool/call"' not in flat:
        return False
    head = flat.split(b'"arguments"')[0]
    m = re.search(rb'"name":"([^"]*)"', head)
    return bool(m and SNOW_TOOL.encode("utf-8") in m.group(1))


def _scan_snow(path, budget=SCAN_BUDGET):
    """流式扫描一个会话文件，返回 (是否提到 mcp__snowluma__, 真实工具调用次数)。

    只做字节级匹配、不解析 JSON，代价小：
      * mentioned — 文件里出现过这个前缀就算（工具定义里出现也算，符合"含该工具的会话"）
      * calls     — 真的有一次 tool/call 的工具名带这个前缀
    """
    mentioned = False
    calls = 0
    if zstandard is None:
        return False, 0
    needle = SNOW_TOOL.encode("utf-8")
    try:
        with open(path, "rb") as fh:
            dctx = zstandard.ZstdDecompressor()
            with dctx.stream_reader(fh) as reader:
                used = 0
                buf = b""
                while True:
                    chunk = reader.read(262144)
                    if not chunk:
                        break
                    used += len(chunk)
                    buf += chunk
                    if b"\n" not in buf:
                        if used > budget:
                            break
                        continue
                    parts = buf.split(b"\n")
                    buf = parts.pop()
                    for ln in parts:
                        if needle not in ln:
                            continue
                        mentioned = True
                        if _snow_call_name(ln):
                            calls += 1
                    if used > budget:
                        break
                if buf and needle in buf:
                    mentioned = True
                    if _snow_call_name(buf):
                        calls += 1
    except Exception:
        return mentioned, calls
    return mentioned, calls


def _extract_session(path):
    """把一个会话解析成展示用的条目列表。"""
    meta = {}
    items = []
    has_snow = False
    snow_calls = 0

    for ev in _iter_events(path):
        etype = ev.get("type")
        data = ev.get("data") or {}
        ms = ev.get("time") or 0

        if etype == "session":
            meta.update({key: ev.get(key) for key in ("id", "cwd", "origin", "parentSession")})
        elif etype == "session/title":
            meta["title"] = data.get("title") or data.get("text") or ""
        elif etype == "assistant/message":
            # reasoning 在 stream 的 block-end 里
            for st in (data.get("stream") or []):
                if not isinstance(st, dict):
                    continue
                ch = st.get("chunk")
                if not isinstance(ch, dict):
                    continue
                if ch.get("type") != "block-end":
                    continue
                block = ch.get("block") or {}
                btype = block.get("type")
                if btype == "reasoning":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        items.append({"kind": "reasoning", "text": txt, "ts": ms,
                                      "time": fmt_ts(ms), "turn": data.get("turn"), "step": data.get("step")})
                elif btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        items.append({"kind": "assistant", "text": txt, "ts": ms,
                                      "time": fmt_ts(ms), "turn": data.get("turn"), "step": data.get("step")})
            # 兜底：万一 stream 缺块，从 message.content 取文本
            msg = data.get("message") or {}
            for block in (msg.get("content") or []):
                if isinstance(block, dict) and block.get("type") == "text":
                    txt = (block.get("text") or "").strip()
                    if txt and not any(i["kind"] == "assistant" and i["ts"] == ms and i["text"] == txt for i in items[-4:]):
                        items.append({"kind": "assistant", "text": txt, "ts": ms,
                                      "time": fmt_ts(ms), "turn": data.get("turn"), "step": data.get("step")})

        elif etype == "tool/call":
            name = str(data.get("name") or "")
            raw_args = data.get("arguments")
            if SNOW_TOOL in name:
                has_snow = True
                snow_calls += 1
            items.append({
                "kind": "tool", "name": name, "args": pretty_args(raw_args),
                "args_len": len(raw_args if isinstance(raw_args, str) else json.dumps(raw_args or "")),
                "snow": SNOW_TOOL in name,
                "ts": ms, "time": fmt_ts(ms),
                "turn": data.get("turn"), "step": data.get("step"),
            })

    if not has_snow:
        try:
            mentioned, _calls = _scan_snow(path)
            has_snow = mentioned
        except Exception:
            has_snow = False

    info = {
        "id": meta.get("id") or os.path.basename(os.path.dirname(path)),
        "title": meta.get("title") or "",
        "cwd": meta.get("cwd") or "",
        "origin": meta.get("origin") or "",
        "parent": meta.get("parentSession") or "",
    }
    days = {}
    for index, item in enumerate(items):
        item["session_id"] = info["id"]
        item["id"] = "%s:%s" % (info["id"], index)
        try:
            day = datetime.fromtimestamp(float(item["ts"]) / 1000).date().isoformat()
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        days.setdefault(day, []).append(item)
    return {"items": items, "days": days, "meta": info, "has_snowluma": has_snow,
            "snow_calls": snow_calls, "truncated": False}


def _thinking_parsed(path):
    stat = os.stat(path)
    signature = (stat.st_mtime_ns, stat.st_size)
    with _THINKING_LOCK:
        hit = _THINKING_PARSED.get(path)
        if hit and hit[0] == signature:
            return hit[1], signature
        result = _extract_session(path)
        _THINKING_PARSED.pop(path, None)
        _THINKING_PARSED[path] = (signature, result)
        while len(_THINKING_PARSED) > 24:
            _THINKING_PARSED.pop(next(iter(_THINKING_PARSED)))
        return result, signature


_THOUGHT_TECHNICAL = re.compile(
    r"mcp__|\b(?:qq|dafeiyu|xiaoyu|qzone|meme)_[a-z_]+\b"
    r"|\b(?:preSleepWaitSatisfied|canSleepNow|countsAsObservation|throughSeq|readThroughSeq|sleepMs|anyMessage|wakeConfig)\b"
    r"|\b(?:set[_ ]wake[_ ]config|mark[_ ]read|wait[_ ]for[_ ]messages)\b"
    r"|\b(?:hint|agent token|gsv_ok|snowluma_ok)\b"
    r"|\b(?:message|messages|reply) array\s*:|消息数组\s*[:：]"
    r"|\b(?:call|invoke|calling|use|using)\s+(?:the\s+|a\s+)?(?:tool|function|api)\b"
    r"|(?:调用|执行|调试)(?:[^。\n]{0,16})(?:工具|函数|接口|API)"
    r"|(?:设置|更新|保持|保留)(?:[^。\n]{0,10})唤醒(?:[^。\n]{0,8})配置|标记已读"
    r"|(?:Traceback \(most recent call last\)|\b(?:stdout|stderr|stacktrace)\b)"
    r"|(?:\b[A-Za-z_]\w*\s*[:=]\s*(?:true|false|null|None|\d+)(?!\w))"
    r"|(?:\b[A-Za-z_]\w*\s*=\s*[\"'\[{])"
    r"|(?:\b[A-Za-z]+_[A-Za-z_]+\s+(?:true|false|null|\d+)\b)"
    r"|(?:\b\d{4,}\s*(?:ms|毫秒|等待|观察))|(?:等待|观察)[^。\n]{0,12}\b\d{4,}\b"
    r"|(?:[\"'][\w-]+[\"']\s*:\s*(?:[\"'\[{]|true|false|null|\d))"
    r"|(?:\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\([^\n。]*\))"
    r"|`[^`\n]+`|(?:\b[A-Za-z_]\w*\s*(?:=>|===|!==|:=))",
    re.IGNORECASE,
)
_THOUGHT_CODE_LINE = re.compile(
    r"^\s*(?:[{}\[\]]\s*[,;]?\s*$|(?:def|class|import|from|const|let|var|function|return|await)\s+"
    r"|(?:if|for|while|try|except)\b[^。\n]*(?:[{:])\s*$"
    r"|(?:[A-Za-z_$][\w.$]*\s*=\s*[^=])|(?:[A-Za-z]:\\|PS\s+[^>]+>|\$\s+(?:python|node|npm|pip)\b))",
    re.MULTILINE,
)


def _readable_thought(text):
    """只读展示投影：去掉代码块及运行过程段落，不改写或补写角色思考。"""
    paragraphs, prose, fence = [], [], None
    for line in str(text or "").splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if prose:
                paragraphs.append("\n".join(prose).strip())
                prose = []
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        if line.strip():
            prose.append(line)
        elif prose:
            paragraphs.append("\n".join(prose).strip())
            prose = []
    if prose:
        paragraphs.append("\n".join(prose).strip())
    readable = []
    for paragraph in paragraphs:
        if not paragraph or _THOUGHT_TECHNICAL.search(paragraph) or _THOUGHT_CODE_LINE.search(paragraph):
            continue
        if paragraph.startswith(("{", "[")):
            try:
                if isinstance(json.loads(paragraph), (dict, list)):
                    continue
            except (ValueError, TypeError):
                pass
        readable.append(paragraph)
    return "\n\n".join(readable)


def _thinking_display_item(item):
    if item.get("kind") not in ("reasoning", "assistant"):
        return None
    text = _readable_thought(item.get("text"))
    return dict(item, text=text) if text else None


_THOUGHT_TRANSLATOR = None
_THOUGHT_TRANSLATOR_LOCK = threading.Lock()


def _translate_thinking_page(items):
    global _THOUGHT_TRANSLATOR
    with _THOUGHT_TRANSLATOR_LOCK:
        if _THOUGHT_TRANSLATOR is None:
            _THOUGHT_TRANSLATOR = ThoughtTranslations(
                os.path.join(DAFEIYU, "data", "state", "thought-translations.json"),
                translate_batch)
    translated = _THOUGHT_TRANSLATOR.project(items)
    # Translation is a display projection too; retain the existing code filter.
    for item in translated:
        if item.get("translation") == "ready":
            item["text"] = _readable_thought(item["text"]) or "这段运行记录已隐藏。"
    return translated


def api_thinking(params):
    """按联系人和本地自然日汇总所有已确认归属的历史会话，只读分页。"""
    day = (params.get("date") or date.today().isoformat()).strip()
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError()
        date.fromisoformat(day)
        limit = max(1, min(int(params.get("limit") or 160), 500))
        offset = max(0, int(params.get("offset") or 0))
    except (ValueError, TypeError):
        return {"items": [], "total_items": 0, "date": day, "session": None,
                "peers": [], "sessions": [], "error": "请选择有效日期（YYYY-MM-DD），分页参数须为整数"}
    descending = (params.get("order") or "desc") in ("desc", "d")
    want_id = (params.get("session") or "").strip()
    want_peer = (params.get("peer") or "").strip()
    peers = _qq_peer_list()
    public = _public_peers(peers)
    result = {"items": [], "translation_engine": "argos-local", "translation_uses_api": False,
              "total_items": 0, "date": day, "limit": limit, "offset": offset,
              "has_more": False, "next_offset": offset, "available_dates": [],
              "first_date": "", "last_date": "", "peers": public, "sessions": [],
              "session_count": 0, "session": None, "auto_refresh": 3, "hidden_items": 0}
    if zstandard is None:
        return dict(result, error="zstandard 未安装，无法解压会话日志")
    chosen = None
    if want_peer:
        chosen = next((p for p in peers if p["key"] == want_peer), None)
        if chosen is None:
            return dict(result, error="该联系人不在当前白名单中")
    elif want_id:
        chosen = next((p for p in peers if any(f["id"] == want_id for f in p["history"])), None)
        if chosen is None:
            return dict(result, error="未找到此会话的联系人归属")
    else:
        chosen = next((p for p in peers if p["has_data"]), peers[0] if peers else None)
    if chosen is None:
        return result  # 不退回无关的开发 / 自测会话
    items, available, sessions, revisions, errors = [], set(), [], [], []
    hidden_items, snow_calls = 0, 0
    for entry in chosen["history"]:
        try:
            parsed, signature = _thinking_parsed(entry["path"])
            revisions.append("%s:%s:%s" % (entry["id"], *signature))
            available.update(parsed["days"])
            selected = parsed["days"].get(day, [])
            if selected:
                visible = [shown for item in selected if (shown := _thinking_display_item(item)) is not None]
                hidden_items += len(selected) - len(visible)
                snow_calls += sum(1 for item in selected if item.get("snow"))
                items.extend(visible)
                sessions.append({"id": entry["id"], "items": len(visible)})
        except Exception as exc:
            log("解析历史会话失败 %s: %s" % (entry["id"], exc), "WARN")
            errors.append(entry["id"])
    # 同一时间的多个事件维持日志内原序；倒序与分页在完整自然日合并后进行。
    items.sort(key=lambda item: num(item.get("ts")))
    if descending:
        items.reverse()
    total = len(items)
    dates = sorted(available)
    page_items = _translate_thinking_page(items[offset:offset + limit])
    translation_revision = _THOUGHT_TRANSLATOR.revision if _THOUGHT_TRANSLATOR else 0
    # Derive this part from the returned projection: a worker can finish between
    # project() and the revision read, so its counter alone can miss a ready update.
    display_revision = hashlib.sha256(json.dumps(page_items, ensure_ascii=False,
                                                sort_keys=True).encode("utf-8")).hexdigest()[:16]
    result.update(items=page_items, total_items=total,
                  has_more=offset + limit < total, next_offset=min(offset + limit, total),
                  available_dates=dates, first_date=dates[0] if dates else "",
                  last_date=dates[-1] if dates else "", sessions=sessions, session_count=len(sessions),
                  revision="readable-zh-v2:%s:%s|" % (translation_revision, display_revision) + "|".join(revisions), hidden_items=hidden_items,
                  session={"id": chosen["session"], "time": chosen["time"],
                           "size_kb": chosen["size_kb"], "has_snowluma": snow_calls > 0,
                           "snow_calls": snow_calls, "truncated": bool(errors),
                           "title": chosen["label"], "cwd": "", "origin": "",
                           "peer": chosen["key"], "peer_qq": chosen["qq"],
                           "peer_alias": chosen["alias"]})
    if errors:
        result["error"] = "%s 段历史会话暂时读取失败，其余记录已显示" % len(errors)
    return result


# ──────────────────────────── 首页 ────────────────────────────

def _emotion_latest(character=None):
    if character is None:
        character = cached("character-state", 2.0, lambda: core_view("/api/affective/state"))
    if character.get("emotion"):
        return character["emotion"]
    row = db_one("SELECT * FROM emotion_snapshot ORDER BY t DESC LIMIT 1")
    if not row:
        return None
    dims = ["joy", "happiness", "sadness", "inferiority", "shyness"]
    labels = {"joy": "愉悦", "happiness": "幸福", "sadness": "难过",
              "inferiority": "自卑", "shyness": "害羞"}
    return {
        "t": row.get("t"),
        "dominant": max(dims, key=lambda key: num(row.get(key))),
        "dominant_label": labels[max(dims, key=lambda key: num(row.get(key)))],
        "reason": "历史快照 · 核心暂不可用",
        "historical": True,
        "values": [{"key": k, "label": labels[k], "value": round(num(row.get(k)), 2)} for k in dims],
    }


def _cost_today():
    d = today_str()
    row = db_one(
        "SELECT COUNT(*) AS n, "
        "SUM(COALESCE(NULLIF(actual_cost,0), estimated_cost)) AS c, "
        "SUM(COALESCE(input_tokens,0)) AS it, SUM(COALESCE(output_tokens,0)) AS ot "
        "FROM cost_event WHERE substr(t,1,10)=?", (d,))
    row = row or {}
    tags = db_rows(
        "SELECT feature_tag, COUNT(*) AS n, "
        "SUM(COALESCE(NULLIF(actual_cost,0), estimated_cost)) AS c "
        "FROM cost_event WHERE substr(t,1,10)=? GROUP BY feature_tag ORDER BY c DESC LIMIT 8", (d,))
    return {
        "date": d,
        "total": round(num(row.get("c")), 4),
        "rounds": int(row.get("n") or 0),
        "input_tokens": int(row.get("it") or 0),
        "output_tokens": int(row.get("ot") or 0),
        "breakdown": [{"tag": t.get("feature_tag") or "-",
                       "count": int(t.get("n") or 0),
                       "cost": round(num(t.get("c")), 4)} for t in tags],
    }


def api_chat_status(params):
    # Only the existing local admin status endpoint. No model/QQ send or paid probe.
    def build():
        try:
            status = http_json(CONSOLE_BASE + '/api/status',
                               headers={'x-console-token': console_token()}, timeout=2.0)
            return build_chat_status(status)
        except urllib.error.HTTPError as exc:
            return build_chat_status(bridge_error='auth' if exc.code in (401, 403) else 'unreachable')
        except Exception:
            return build_chat_status(bridge_error='unreachable')
    if params.get('refresh') == '1':
        result = build()
        with _CACHE_LOCK:
            _CACHE['chat_online_status'] = (time.time() + 3.0, result)
        return result
    return cached('chat_online_status', 3.0, build)


def api_home(_params):
    def build():
        mem = db_one("SELECT COUNT(*) AS n FROM memory_item WHERE COALESCE(tombstone,0)=0") or {}
        kn = db_one("SELECT COUNT(*) AS n FROM knowledge_item") or {}
        diary_today = None
        try:
            if port_open_cached(18889):
                d = http_json("%s/diary/get?date=%s" % (DIARY_BASE, today_str()),
                              allow_error_body=True)
                if d and d.get("ok"):
                    diary_today = {"date": d.get("date"), "mood": d.get("mood") or "",
                                   "chars": d.get("chars") or 0,
                                   "summary": truncate(d.get("summary") or "", 80)}
        except Exception:
            pass
        learn = None
        try:
            st = _learning_status()
            if st:
                learn = {"today_rounds": (st.get("today") or {}).get("rounds", 0),
                         "rounds_per_day": st.get("rounds_per_day", 0),
                         "last_run": st.get("last_run") or ""}
        except Exception:
            pass
        qq = None
        try:
            qq = {"conversations": _qq_conversation_count()}
        except Exception:
            pass
        return {
            "emotion": _emotion_latest(),
            "memory": {"count": int(mem.get("n") or 0), "knowledge": int(kn.get("n") or 0)},
            "cost": _cost_today(),
            "diary_today": diary_today,
            "learning": learn,
            "qq": qq,
            "portrait": "/api/asset?name=xiaoyu_portrait.png",
            "generated_at": datetime.now().strftime("%H:%M:%S"),
        }

    return cached("home", 2.0, build)


# ──────────────────────────── 人设 ────────────────────────────

def api_persona(_params):
    def build():
        return PersonaSettings(release_paths.path(), PERSONALITY).read()
    return cached("persona", 2.0, build)


def api_persona_save(body):
    result = PersonaSettings(release_paths.path(), PERSONALITY).save(body.get("text"), body.get("revision"))
    with _CACHE_LOCK:
        _CACHE.pop("persona", None)
    log("人设与QQ预设已同步保存 (%d 字符)" % result['chars'])
    return result


# ──────────────────────────── 语音 ────────────────────────────

def _voice_cfg():
    cfg = read_json(VOICE_CONFIG, {}) or {}
    tones = cfg.get("tones") or {}
    out = []
    for name, t in tones.items():
        if not isinstance(t, dict):
            continue
        ref = str(t.get("ref") or "")
        out.append({
            "name": name,
            "ref": ref,
            "ref_exists": bool(ref) and os.path.exists(ref),
            "prompt": truncate(str(t.get("prompt") or ""), 90),
            "temperature": num(t.get("temperature"), 0.8),
            "speed": num(t.get("speed"), 1.0),
            "pitch": num(t.get("pitch"), 0),
        })
    return {
        "backend": cfg.get("backend") or "auto",
        "default_tone": cfg.get("default_tone") or (out[0]["name"] if out else "gentle"),
        "max_chars": int(num(cfg.get("max_chars"), 44)),
        "api": cfg.get("gsv_api") or release_paths.url("tts"),
        "text_lang": cfg.get("gsv_text_lang") or "zh",
        "prompt_lang": cfg.get("gsv_prompt_lang") or "zh",
        "tones": out,
        "path": VOICE_CONFIG,
    }


def api_voice(_params):
    def build():
        cfg = _voice_cfg()
        parsed = urllib.parse.urlsplit(cfg["api"])
        cfg["tts_online"] = bool(parsed.hostname) and port_open_cached(parsed.port or 80, parsed.hostname)
        bridge = bridge_config()
        social = bridge.get('socialV2') or {}
        cfg['automatic_voice'] = (social.get('voice') or {}).get('auto') or {}
        cfg['daily_contact'] = (social.get('proactive') or {}).get('dailyContact') or {}
        cfg['understanding'] = (social.get('media') or {}).get('understanding') or {}
        cfg['conversation_care'] = http_json(CONSOLE_BASE + '/api/conversation-care',
                                             headers={'x-console-token': console_token()}, timeout=3) or {}
        cfg['media_service'] = http_json(release_paths.url('media', '/health'), timeout=2) or {}
        return cfg
    return cached("voice", 5.0, build)


def _port_open(port, host="127.0.0.1", timeout=0.35):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


_PORT_CACHE = {}


def port_open_cached(port, host="127.0.0.1", ttl=2.0):
    """带缓存的端口探活。服务没起来时用它快速失败，不要每条请求都卡 2 秒等超时。"""
    now = time.time()
    hit = _PORT_CACHE.get(port)
    if hit and hit[0] > now:
        return hit[1]
    ok = _port_open(port, host)
    _PORT_CACHE[port] = (now + ttl, ok)
    return ok


def _synth(text, tone):
    cfg = _voice_cfg()
    tones = {t["name"]: t for t in cfg["tones"]}
    name = (tone or cfg["default_tone"]).lower()
    if name not in tones:
        name = cfg["default_tone"] if cfg["default_tone"] in tones else next(iter(tones), "")
    if not name:
        raise RuntimeError("voice_config.json 里没有可用语气")
    t = tones[name]
    ref = t["ref"]
    if not ref or not os.path.exists(ref):
        raise RuntimeError("参考音频不存在: %s" % (ref or "(空)"))
    parsed = urllib.parse.urlsplit(cfg["api"])
    if not parsed.hostname or not _port_open(parsed.port or 80, parsed.hostname):
        raise RuntimeError("配置的语音服务未连接，请前往接入与组件设置。")

    text = (text or "").strip()
    if not text:
        raise ValueError("文本不能为空")
    if len(text) > 300:
        text = text[:300]

    payload = {
        "text": text,
        "text_lang": cfg["text_lang"],
        "ref_audio_path": ref,
        "prompt_text": t["prompt"],
        "prompt_lang": cfg["prompt_lang"],
        "text_split_method": "cut5",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "parallel_infer": True,
        "repetition_penalty": 1.35,
        "temperature": t["temperature"],
        "speed_factor": t["speed"],
    }
    audio = http_bytes("%s/tts" % cfg["api"], data=payload, timeout=120.0)
    if not audio or len(audio) < 1000:
        raise RuntimeError("GPT-SoVITS 返回空音频 (%d 字节)" % len(audio or b""))
    return name, audio, text


def _audio_put(data, text='', tone=''):
    aid = "%d-%d" % (int(time.time() * 1000), len(data))
    now = time.time()
    with _AUDIO_LOCK:
        # 清理过期 + 超量
        for k in [k for k, v in _AUDIO.items() if v[0] < now]:
            _AUDIO.pop(k, None)
        while len(_AUDIO) >= _AUDIO_MAX:
            _AUDIO.pop(next(iter(_AUDIO)), None)
        _AUDIO[aid] = (now + _AUDIO_TTL, data, {'text': text, 'tone': tone})
    return aid


def api_voice_synth(body):
    tone = body.get("tone") or ""
    text = body.get("text") or ""
    name, audio, used = _synth(text, tone)
    aid = _audio_put(audio, used, name)
    log("语音合成成功 tone=%s chars=%d bytes=%d" % (name, len(used), len(audio)))
    return {"id": aid, "tone": name, "bytes": len(audio), "chars": len(used),
            "url": "/api/voice/audio?id=%s" % aid}


def api_voice_audio(params):
    aid = params.get("id") or ""
    with _AUDIO_LOCK:
        item = _AUDIO.get(aid)
    if not item:
        return None
    return item[1]


def api_voice_send(body):
    """合成并发到 QQ（SnowLuma send_private_msg，record base64）。"""
    uid = str(body.get("user_id") or "").strip()
    if not uid:
        cfg = bridge_config()
        uid = str(cfg.get("ownerQQ") or "").strip()
    if not uid:
        raise ValueError("没指定 QQ 号，且 config.json 里没有 ownerQQ")

    # 可以直接转发已合成的音频，省一次合成
    aid = (body.get("id") or "").strip()
    audio = None
    tone = body.get("tone") or ""
    text = body.get("text") or ""
    if aid:
        with _AUDIO_LOCK:
            item = _AUDIO.get(aid)
        if item:
            audio = item[1]
            if len(item) > 2:
                text = item[2].get('text') or text
                tone = item[2].get('tone') or tone
    if audio is None:
        name, audio, used = _synth(text, tone)
        tone = name
        text = used

    tok = snowluma_token()
    if not tok:
        raise RuntimeError("config.json 里没有 snowluma.accessToken")
    if not _port_open(3000):
        raise RuntimeError("SnowLuma OneBot 服务 (127.0.0.1:3000) 未启动")

    b64 = base64.b64encode(audio).decode("ascii")
    payload = {
        "user_id": int(uid) if uid.isdigit() else uid,
        "message": [{"type": "record", "data": {"file": "base64://" + b64}}],
    }
    res = http_json("%s/send_private_msg" % SNOWLUMA_BASE, data=payload,
                    headers={"Authorization": "Bearer " + tok}, timeout=60.0)
    ok = bool(res) and (res.get("status") == "ok" or res.get("retcode") == 0)
    log("语音发送 QQ uid=%s tone=%s ok=%s" % (uid, tone, ok))
    if not ok:
        raise RuntimeError("发送失败: %s" % json.dumps(res, ensure_ascii=False)[:300])
    memory_payload = {'content': text, 'conversation': 'private:' + uid,
                      'event_id': 'private:%s:sent:%s' % (uid, (res.get('data') or {}).get('message_id') or os.urandom(12).hex()),
                      'is_self': True, 'media_type': 'voice'}
    memory = http_json(CORE_BASE + '/api/chat/record', data=memory_payload, timeout=8)
    if not memory or not memory.get('ok'):
        inbox = os.path.join(DAFEIYU, 'data', 'state', 'chat-inbox')
        os.makedirs(inbox, exist_ok=True)
        filename = os.path.join(inbox, os.urandom(16).hex() + '.json')
        with open(filename + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(memory_payload, f, ensure_ascii=False)
        os.replace(filename + '.tmp', filename)
    return {"sent": True, "user_id": uid, "tone": tone, "bytes": len(audio),
            "text": text, "resp": res.get("data") if isinstance(res, dict) else None}


# ──────────────────────────── 联系人 ────────────────────────────

def api_contacts(_params):
    def build():
        cfg = bridge_config()
        allow = cfg.get("allow") or {}
        owner = str(cfg.get("ownerQQ") or "")
        aliases = read_json(ALIASES_JSON, {}) or {}
        people, groups = [], []

        def nice(uin):
            a = aliases.get(str(uin))
            if a:
                return str(a)
            return "我" if str(uin) == owner else ""

        for uin in (allow.get("private") or []):
            uin = str(uin)
            people.append({"qq": uin, "alias": nice(uin), "is_owner": uin == owner,
                           "kind": "private"})
        for g in (allow.get("groups") or []):
            if isinstance(g, dict):
                gid = str(g.get("id") or g.get("group_id") or "")
                name = str(g.get("name") or "")
            else:
                gid, name = str(g), ""
            groups.append({"qq": gid, "alias": name, "is_owner": False, "kind": "group"})

        # 从 QQ 会话状态补一层（有就更好）
        convs = {}
        try:
            convs = _qq_states_map()
        except Exception:
            pass
        for p in people:
            c = convs.get("private:" + p["qq"])
            if c:
                p["unread"] = c.get("unread", 0)
                p["recent"] = c.get("recent", 0)
                p["last_wake"] = c.get("last_wake", "")
                p["mode"] = c.get("mode", "")
        return {"owner": owner, "private": people, "groups": groups,
                "allow_all_when_empty": bool(cfg.get("allowAllWhenEmpty")),
                "cross_conversation": {
                    "enabled": (cfg.get("socialV2") or {}).get("crossConversation", {}).get("enabled") is True,
                    "scope": "all_whitelisted",
                },
                "aliases_path": ALIASES_JSON}
    return cached("contacts", 5.0, build)


def api_affinity(params):
    day = str(params.get('date') or '')
    if day:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError('日期格式应为 YYYY-MM-DD')
        datetime.strptime(day, '%Y-%m-%d')
    return core_view('/api/affinity' + ('?date=' + day if day else ''))


def _qq_states_raw():
    tok = console_token()
    if not tok:
        raise RuntimeError("读不到 console-token")
    res = http_json("%s/api/socialV2/states" % CONSOLE_BASE,
                    headers={"x-console-token": tok})
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError("控制台返回异常")
    return res


def _qq_states_map():
    res = cached("qq_states_raw", 4.0, _qq_states_raw)
    out = {}
    for c in (res.get("conversations") or []):
        key = c.get("key") or ""
        wc = c.get("wakeConfig") or {}
        out[key] = {
            "key": key,
            "unread": c.get("unreadCount") or 0,
            "recent": c.get("recentCount") or 0,
            "last_wake": fmt_ts(c.get("lastAiReplyAt") or 0),
            "mode": wc.get("mode") or "",
        }
    return out


def _qq_conversation_count():
    try:
        return len(_qq_states_map())
    except Exception:
        return 0


def api_qq(_params):
    def build():
        out = {"states": None, "login": None, "errors": []}
        if port_open_cached(3100):
            try:
                res = _qq_states_raw()
                convs = []
                for c in (res.get("conversations") or []):
                    wc = c.get("wakeConfig") or {}
                    trig = wc.get("triggers") or {}
                    convs.append({
                        "key": c.get("key") or "",
                        "mode": wc.get("mode") or "",
                        "infinite": bool(wc.get("infinite")),
                        "sleep_until": wc.get("sleepUntil") or "",
                        "wake_count": wc.get("wakeCount") or 0,
                        "last_wake_reason": c.get("lastWakeReason") or "",
                        "last_ai_reply": fmt_ts(c.get("lastAiReplyAt") or 0),
                        "unread": c.get("unreadCount") or 0,
                        "recent": c.get("recentCount") or 0,
                        "triggers": {
                            "any_message": bool(trig.get("anyMessage")),
                            "at_mention": bool(trig.get("atMention")),
                            "question": bool(trig.get("question")),
                            "poke": bool(trig.get("poke")),
                            "probability": num(trig.get("probability")),
                            "keywords": (trig.get("keywords") or [])[:8],
                        },
                    })
                out["states"] = convs
            except Exception as exc:
                out["errors"].append("会话状态: %s" % exc)
        else:
            out["errors"].append("QQ 桥接控制台 (127.0.0.1:3100) 未启动")
        if not port_open_cached(3000):
            out["errors"].append("SnowLuma OneBot (127.0.0.1:3000) 未启动")
            out["generated_at"] = datetime.now().strftime("%H:%M:%S")
            return out
        try:
            tok = snowluma_token()
            if not tok:
                raise RuntimeError("config.json 缺 snowluma.accessToken")
            res = http_json("%s/get_login_info" % SNOWLUMA_BASE,
                            headers={"Authorization": "Bearer " + tok})
            if isinstance(res, dict) and res.get("data"):
                out["login"] = {"user_id": res["data"].get("user_id"),
                                "nickname": res["data"].get("nickname")}
            else:
                raise RuntimeError("返回异常")
        except Exception as exc:
            out["errors"].append("登录信息: %s" % exc)
        out["generated_at"] = datetime.now().strftime("%H:%M:%S")
        return out
    return cached("qq", 4.0, build)


# ──────────────────────────── 空间 ────────────────────────────

def api_qzone(_params):
    def build():
        out = {"whoami": None, "items": [], "errors": []}
        if not port_open_cached(18888):
            out["errors"].append("QQ空间服务 (127.0.0.1:18888) 未启动")
            out["offline"] = True
            return out
        try:
            w = http_json("%s/whoami" % QZONE_BASE)
            if w and w.get("ok"):
                out["whoami"] = {"uin": w.get("uin")}
        except Exception as exc:
            out["errors"].append("whoami: %s" % exc)
        try:
            f = http_json("%s/feed" % QZONE_BASE)
            if f and f.get("ok"):
                items = []
                for it in (f.get("items") or []):
                    created = num(it.get("created"))
                    items.append({
                        "tid": str(it.get("tid") or ""),
                        "content": it.get("content") or "",
                        "created": created,
                        "time": fmt_ts(created * 1000) if created else "",
                        "host_uin": str(it.get("host_uin") or ""),
                        "comments": [{
                            "uin": str(c.get("uin") or ""),
                            "name": c.get("name") or "",
                            "content": c.get("content") or "",
                        } for c in (it.get("comments") or [])],
                    })
                out["items"] = items
        except Exception as exc:
            out["errors"].append("feed: %s" % exc)
        return out
    return cached("qzone", 4.0, build)


def api_qzone_post(body):
    content = (body.get("content") or "").strip()
    if not content:
        raise ValueError("内容不能为空")
    res = http_json("%s/post" % QZONE_BASE, data={"content": content}, timeout=20.0)
    if not res or not res.get("ok"):
        raise RuntimeError("发布失败: %s" % json.dumps(res, ensure_ascii=False)[:300])
    with _CACHE_LOCK:
        _CACHE.pop("qzone", None)
    log("发说说成功: %s" % truncate(content, 40))
    return res


# ──────────────────────────── 记忆 ────────────────────────────

MEM_COLS = "*"  # Additive schema fields are optional while the core is being upgraded.


def memory_policy(row):
    ext = jloads(row.get("ext"), {}) or {}
    return {"confidence": num(row.get("confidence"), 1.0),
            "memory_type": row.get("memory_type") or "legacy_record",
            "valid_from": row.get("valid_from") or "", "valid_until": row.get("valid_until") or "",
            "supersedes": row.get("supersedes") or "", "superseded_by": row.get("superseded_by") or "",
            "subject": ext.get("subject") or ext.get("conv") or "",
            "evidence_weight": num(row.get("evidence_weight"), 1.0),
            "emotional_valence": num(row.get("emotional_valence"))}


def core_view(route):
    try:
        return http_json(CORE_BASE + route, timeout=2.0)
    except Exception:
        return {"ok": False, "offline": True, "note": "核心暂不可用，稍后刷新即可"}


def api_retrieval(_params):
    return cached("retrieval", 3.0, lambda: core_view("/api/retrieval/status"))


MEMORY_SECTIONS = (
    ("long1", "1号长期记忆", "layer='long1'"),
    ("long2", "2号长期记忆", "layer='long2'"),
    ("diary", "短期 · 日记本", "layer='short' AND subtype='diary'"),
    ("pending", "短期 · 待总结", "layer='short' AND status='open'"),
    ("instant", "瞬时记忆", "layer='instant'"),
    ("language", "语言记忆", "layer='language'"),
    ("emotion", "情绪记忆", "layer='emotion'"),
    ("all", "全部记忆", "1=1"),
)


def api_memory_sections(_params):
    def build():
        return {"sections": [
            {"id": key, "label": label, "count": int((db_one(
                "SELECT COUNT(*) AS n FROM memory_item WHERE COALESCE(tombstone,0)=0 AND (" + where + ")"
            ) or {}).get("n") or 0)} for key, label, where in MEMORY_SECTIONS]}
    return cached("memory:sections", 3.0, build)


def _memory_windows():
    thresholds = read_json(os.path.join(DAFEIYU, "data", "config", "thresholds.json"), {}) or {}
    instant = thresholds.get("instant") or {}
    return {"hot_hours": num(instant.get("hot_hours"), 6),
            "purge_hours": num(instant.get("purge_hours"), 18)}


def api_memory(params):
    limit = max(1, min(int(params.get("limit") or 100), 400))
    offset = max(0, int(params.get("offset") or 0))
    q = (params.get("q") or "").strip()
    section = params.get("section") or "all"
    section_where = {key: where for key, _label, where in MEMORY_SECTIONS}.get(section)
    if section_where is None:
        raise ValueError("未知记忆分区")
    group = (params.get("group") or "").strip()
    group_column = {"long1": "subtype", "long2": "emotion_tag"}.get(section)

    def build():
        where = "WHERE COALESCE(tombstone,0)=0 AND (" + section_where + ")"
        windows = _memory_windows()
        args = []
        if q:
            where += " AND (summary LIKE ? OR content LIKE ? OR concepts LIKE ?)"
            like = "%" + q + "%"
            args += [like, like, like]
        groups = []
        if group_column:
            groups = db_rows("SELECT COALESCE(" + group_column + ", '') AS name, COUNT(*) AS count "
                             "FROM memory_item " + where + " GROUP BY COALESCE(" + group_column + ", '') "
                             "ORDER BY count DESC, name", tuple(args))
        if group and group_column:
            where += " AND COALESCE(" + group_column + ", '')=?"
            args.append("" if group == "__empty__" else group)
        rows = db_rows("SELECT %s FROM memory_item %s ORDER BY t_created DESC LIMIT ? OFFSET ?"
                       % (MEM_COLS, where), tuple(args + [limit, offset]))
        total = db_one("SELECT COUNT(*) AS n FROM memory_item %s" % where, tuple(args)) or {}
        items = []
        for r in rows:
            concepts = jloads(r.get("concepts"), []) or []
            age = None
            try:
                created = datetime.fromisoformat(str(r.get("t_created") or ""))
                age = max(0.0, (datetime.now(created.tzinfo) - created).total_seconds() / 3600)
            except (ValueError, TypeError):
                pass
            items.append({
                "id": r.get("id"),
                "layer": r.get("layer") or "",
                "subtype": r.get("subtype") or "",
                "summary": r.get("summary") or "",
                "content": truncate(r.get("content") or "", 160),
                "concepts": concepts[:10],
                "emotion_tag": r.get("emotion_tag") or "",
                "salience": round(num(r.get("salience"), 0.5), 3),
                "strength": round(num(r.get("strength")), 3),
                "status": r.get("status") or "",
                "t_created": r.get("t_created") or "",
                "origin": r.get("origin") or "",
                "access_count": r.get("access_count") or 0,
                "age_hours": round(age, 1) if age is not None else None,
                "instant_band": ("hot" if age < windows["hot_hours"] else
                                 "warm" if age < windows["purge_hours"] else "retained") if age is not None else "unknown",
                "day": (jloads(r.get("ext"), {}) or {}).get("date") or (r.get("t_created") or "")[:10],
                **memory_policy(r),
            })
        layers = db_rows(
            "SELECT layer, COUNT(*) AS n FROM memory_item WHERE COALESCE(tombstone,0)=0 "
            "GROUP BY layer ORDER BY n DESC")
        return {"items": items, "total": int(total.get("n") or 0),
                "offset": offset, "limit": limit,
                "section": section, "groups": groups, "instant_windows": windows,
                "layers": [{"layer": l.get("layer") or "", "count": l.get("n") or 0} for l in layers]}
    return cached("memory:%s:%s:%s:%s:%s" % (limit, offset, q, section, group), 3.0, build)


def api_memory_detail(params):
    mid = (params.get("id") or "").strip()
    if not mid:
        raise ValueError("缺少 id")
    row = db_one("SELECT * FROM memory_item WHERE id=?", (mid,))
    if row is None:
        row = db_one("SELECT * FROM knowledge_item WHERE id=?", (mid,))
        if row is None:
            raise ValueError("未找到该记忆")
        return {"kind": "knowledge", "id": row.get("id"), "name": row.get("name") or "",
                "domain": row.get("domain") or "",
                "concepts": jloads(row.get("concepts"), []) or [],
                "content": row.get("content") or "",
                "confidence": num(row.get("confidence")),
                "source": row.get("source") or "",
                "learned_at": row.get("learned_at") or ""}
    return {
        "kind": "memory",
        "id": row.get("id"),
        "layer": row.get("layer") or "",
        "subtype": row.get("subtype") or "",
        "summary": row.get("summary") or "",
        "content": row.get("content") or "",
        "skeleton": row.get("skeleton") or "",
        "concepts": jloads(row.get("concepts"), []) or [],
        "emotion_tag": row.get("emotion_tag") or "",
        "salience": num(row.get("salience"), 0.5),
        "strength": num(row.get("strength")),
        "level": row.get("level"),
        "status": row.get("status") or "",
        "t_created": row.get("t_created") or "",
        "last_access": row.get("last_access") or "",
        "access_count": row.get("access_count") or 0,
        "origin": row.get("origin") or "",
        "refs": jloads(row.get("refs"), []) or [],
        **memory_policy(row),
    }


MAX_NODES = 400
MAX_EDGES = 2000


def _graph():
    mem = db_rows(
        "SELECT id, summary, concepts, salience, emotion_tag, layer, subtype, t_created "
        "FROM memory_item WHERE COALESCE(tombstone,0)=0 AND concepts IS NOT NULL AND concepts NOT IN ('','[]') "
        "ORDER BY COALESCE(salience,0.5) DESC, t_created DESC LIMIT ?", (MAX_NODES,))
    kn = db_rows(
        "SELECT id, name, concepts, confidence, domain, learned_at FROM knowledge_item "
        "WHERE concepts IS NOT NULL AND concepts NOT IN ('','[]') ORDER BY learned_at DESC LIMIT 120")

    nodes, index = [], {}

    def add(nid, kind, label, concepts, weight, extra):
        if nid in index or len(nodes) >= MAX_NODES:
            return
        index[nid] = len(nodes)
        nodes.append({
            "id": nid, "kind": kind, "label": truncate(label or "(无摘要)", 40),
            "weight": round(max(0.15, min(1.0, num(weight, 0.5))), 3),
            "concepts": concepts[:8],
            **extra,
        })

    for r in mem:
        cs = jloads(r.get("concepts"), []) or []
        add(r.get("id"), "memory", r.get("summary") or "", cs,
            num(r.get("salience"), 0.5),
            {"emotion": r.get("emotion_tag") or "", "layer": r.get("layer") or "",
             "subtype": r.get("subtype") or "", "t": r.get("t_created") or ""})
    for r in kn:
        cs = jloads(r.get("concepts"), []) or []
        add(r.get("id"), "knowledge", r.get("name") or "", cs,
            num(r.get("confidence"), 0.7),
            {"domain": r.get("domain") or "", "t": r.get("learned_at") or ""})

    # 倒排：concept -> [node idx]
    inv = {}
    for i, n in enumerate(nodes):
        for c in n["concepts"]:
            c = str(c).strip()
            if not c:
                continue
            inv.setdefault(c, []).append(i)

    pair = {}
    for c, idxs in inv.items():
        if len(idxs) < 2 or len(idxs) > 24:   # 太泛的概念不连线
            continue
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                key = (i, j) if i < j else (j, i)
                pair[key] = pair.get(key, 0) + 1

    edges = []
    for (i, j), w in sorted(pair.items(), key=lambda kv: -kv[1])[:MAX_EDGES]:
        edges.append({"source": nodes[i]["id"], "target": nodes[j]["id"], "weight": min(w, 5)})

    deg = {}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1

    return {
        "nodes": [dict(n, degree=deg.get(n["id"], 0)) for n in nodes],
        "edges": edges,
        "stats": {
            "nodes": len(nodes), "edges": len(edges),
            "memory": sum(1 for n in nodes if n["kind"] == "memory"),
            "knowledge": sum(1 for n in nodes if n["kind"] == "knowledge"),
            "concepts": len(inv),
        },
    }


def api_memory_graph(_params):
    return cached("memory_graph", 10.0, _graph)


# ──────────────────────────── 学习 ────────────────────────────

_LEARN_SUBPROC = {"at": 0.0, "data": None}


def _learning_status():
    """优先同进程 import core.learning.webstudy；失败退回子进程。"""
    py_root = DAFEIYU
    if py_root not in sys.path:
        sys.path.insert(0, py_root)
    try:
        from core.learning import webstudy  # noqa: WPS433
        st = webstudy.status()
        if isinstance(st, dict):
            return st
    except Exception as exc:
        log("in-process webstudy.status 失败: %s" % exc, "WARN")

    now = time.time()
    if _LEARN_SUBPROC["data"] and now - _LEARN_SUBPROC["at"] < 10:
        return _LEARN_SUBPROC["data"]
    code = ("import json;from core.learning import webstudy;"
            "print(json.dumps(webstudy.status(), ensure_ascii=False, default=str))")
    env = dict(os.environ, PYTHONPATH=py_root, PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-c", code], cwd=py_root, env=env,
                         capture_output=True, timeout=45)
    if out.returncode != 0:
        raise RuntimeError("子进程失败: %s" % out.stderr.decode("utf-8", "replace")[-300:])
    data = json.loads(out.stdout.decode("utf-8", "replace"))
    _LEARN_SUBPROC["data"] = data
    _LEARN_SUBPROC["at"] = now
    return data


def _quota_from_json():
    cfg = read_json(WEBSTUDY_JSON, {}) or {}
    q = cfg.get("quota") or {}
    return {
        "enabled": bool(cfg.get("enabled")),
        "auto_run": bool(cfg.get("auto_run")),
        "rounds_per_day": int(num(q.get("rounds_per_day"), 0)),
        "items_per_round": int(num(q.get("items_per_round"), 0)),
        "min_interval_minutes": int(num(q.get("min_interval_minutes"), 0)),
        "pages_per_round": int(num(q.get("pages_per_round"), 0)),
        "points_per_page": int(num(q.get("points_per_page"), 0)),
        "max_attempts_per_round": int(num(q.get("max_attempts_per_round"), 0)),
    }


def api_learning(_params):
    def build():
        out = {"quota_file": _quota_from_json(), "status": None,
               "reports": _reports(6), "errors": [], "path": WEBSTUDY_JSON}
        try:
            st = _learning_status()
            if isinstance(st, dict):
                out["status"] = {
                    "enabled": st.get("enabled"),
                    "auto_run": st.get("auto_run"),
                    "rounds_per_day": st.get("rounds_per_day"),
                    "items_per_round": st.get("items_per_round"),
                    "min_interval_minutes": st.get("min_interval_minutes"),
                    "today": st.get("today") or {},
                    "quota": st.get("quota") or {},
                    "last_run": st.get("last_run") or "",
                    "last_error": st.get("last_error") or "",
                    "blocked_total": st.get("blocked_total") or 0,
                }
        except Exception as exc:
            out["errors"].append("状态: %s" % exc)
        return out
    return cached("learning", 4.0, build)


def api_learning_dashboard(_params):
    # The running core owns learning work. Opening a page only reads its snapshot.
    return core_view("/api/learning/dashboard")


def _learning_command(route, body):
    result = http_json(CORE_BASE + route, data=body, timeout=8.0, allow_error_body=True)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError((result or {}).get("error") or "学习核心暂不可用")
    return result


def api_learning_report(params):
    name = str(params.get("name") or "")
    if not name.endswith(".md") or os.path.basename(name) != name or "/" in name or "\\" in name:
        raise ValueError("请选择一份学习报告")
    path = os.path.join(REPORTS_DIR, name)
    if not os.path.isfile(path):
        raise ValueError("这份学习报告不存在")
    text = read_text(path, "")
    return {"name": name, "title": name[:-3], "content": text, "chars": len(text)}


def _reports(limit=6):
    try:
        names = [n for n in os.listdir(REPORTS_DIR) if n.endswith(".md")]
    except Exception:
        return []
    names.sort(reverse=True)
    out = []
    for n in names[:limit]:
        p = os.path.join(REPORTS_DIR, n)
        try:
            txt = read_text(p, "")
            # 去掉 markdown 噪音，留一段干净的摘要
            clean = re.sub(r"```[a-zA-Z]*", "", txt)
            clean = re.sub(r"^[#>\s]*", "", clean, flags=re.M)
            clean = re.sub(r"^\s*[-*+]\s*", "", clean, flags=re.M)
            clean = re.sub(r"[`*_|]+", "", clean)
            head = re.sub(r"\s+", " ", clean).strip()[:200]
            out.append({"name": n, "title": n[:-3], "head": head,
                        "mtime": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M"),
                        "chars": len(txt)})
        except Exception:
            continue
    return out


def api_learning_quota_save(body):
    cfg = read_json(WEBSTUDY_JSON, None)
    if not isinstance(cfg, dict):
        raise RuntimeError("读不到 %s" % WEBSTUDY_JSON)
    q = cfg.get("quota")
    if not isinstance(q, dict):
        q = {}
        cfg["quota"] = q

    changed = {}
    if "enabled" in body:
        cfg["enabled"] = bool(body["enabled"])
        changed["enabled"] = cfg["enabled"]
    for key, lo, hi in (("rounds_per_day", 0, 60), ("items_per_round", 1, 50),
                        ("min_interval_minutes", 0, 1440)):
        if key in body and body[key] is not None:
            try:
                v = int(float(body[key]))
            except Exception:
                raise ValueError("%s 不是数字" % key)
            v = max(lo, min(hi, v))
            q[key] = v
            changed[key] = v
    if not changed:
        raise ValueError("没有要修改的字段")

    # 保留其它键：只整体回写被改过的 cfg
    bak = WEBSTUDY_JSON + ".bak"
    try:
        with open(WEBSTUDY_JSON, "rb") as fh:
            old = fh.read()
        with open(bak, "wb") as fh:
            fh.write(old)
    except Exception as exc:
        log("webstudy 备份失败: %s" % exc, "WARN")
    write_text_atomic(WEBSTUDY_JSON, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    with _CACHE_LOCK:
        for k in list(_CACHE):
            if k.startswith("learning"):
                _CACHE.pop(k, None)
    _LEARN_SUBPROC["data"] = None
    log("学习配额已更新: %s" % changed)
    return {"saved": True, "changed": changed, "path": WEBSTUDY_JSON, "backup": bak}


# ──────────────────────────── 日记 ────────────────────────────

def api_diary_calendar(params):
    kind = (params.get("kind") or "daily").strip()
    if kind not in ("daily", "weekly", "monthly"):
        kind = "daily"
    year = params.get("year")
    month = params.get("month")

    def build():
        if not port_open_cached(18889):
            return {"kind": kind, "year": None, "month": None, "count": 0,
                    "marks": [], "memory_marks": [], "offline": True,
                    "note": "日记服务 (127.0.0.1:18889) 未启动"}
        q = [("kind", kind)]
        if year:
            q.append(("year", year))
        if month:
            q.append(("month", month))
        url = "%s/diary/calendar?%s" % (DIARY_BASE, urllib.parse.urlencode(q))
        res = http_json(url, allow_error_body=True)
        if not isinstance(res, dict) or not res.get("ok"):
            raise RuntimeError((res or {}).get("error") or "日记服务返回异常")
        days = res.get("days") or {}
        marks = sorted([d for d, v in days.items() if v])

        # 记忆联动：哪几天有 memory_item（失败就当没有，不影响主功能）
        mem_marks = []
        try:
            murl = "%s/diary/memory_days?%s" % (
                DIARY_BASE, urllib.parse.urlencode([("year", res.get("year")), ("month", res.get("month"))]))
            mres = http_json(murl, allow_error_body=True)
            if isinstance(mres, dict) and mres.get("ok"):
                mdays = mres.get("days") or {}
                mem_marks = sorted([d for d, v in mdays.items() if v])
        except Exception:
            pass

        return {"kind": res.get("kind") or kind,
                "year": res.get("year"), "month": res.get("month"),
                "count": res.get("count") or len(marks),
                "marks": marks, "memory_marks": mem_marks}
    return cached("diary_cal:%s:%s:%s" % (kind, year, month), 5.0, build)


def api_diary_get(params):
    kind = (params.get("kind") or "daily").strip()
    if kind not in ("daily", "weekly", "monthly"):
        kind = "daily"
    d = (params.get("date") or today_str()).strip()

    def build():
        if not port_open_cached(18889):
            return {"found": False, "date": d, "kind": kind, "body": "", "title": "",
                    "offline": True, "error": "日记服务 (127.0.0.1:18889) 未启动"}
        url = "%s/diary/get?%s" % (DIARY_BASE, urllib.parse.urlencode({"kind": kind, "date": d}))
        res = http_json(url, allow_error_body=True)
        if not isinstance(res, dict):
            raise RuntimeError("日记服务返回异常")
        if not res.get("ok"):
            # 正常空态：404 = 这天没有日记
            return {"found": False, "date": d, "kind": kind, "body": "", "title": "",
                    "error": "这天还没有日记" if res.get("_http_status") in (404, None)
                    else (res.get("error") or "读取失败")}
        return {"found": True, "date": res.get("date") or d, "kind": res.get("kind") or kind,
                "title": res.get("title") or "", "body": res.get("body") or "",
                "summary": res.get("summary") or "", "mood": res.get("mood") or "",
                "chars": res.get("chars") or len(res.get("body") or ""),
                "source": res.get("source") or "",
                "generated_at": res.get("generated_at") or ""}
    return cached("diary_get:%s:%s" % (kind, d), 5.0, build)


def api_diary_generate(body):
    kind = (body.get("kind") or "daily").strip()
    if kind not in ("daily", "weekly", "monthly"):
        raise ValueError("kind 只能是 daily/weekly/monthly")
    d = (body.get("date") or today_str()).strip()
    force = bool(body.get("force"))
    log("请求补生成日记 kind=%s date=%s force=%s" % (kind, d, force))
    res = http_json("%s/diary/generate" % DIARY_BASE,
                    data={"kind": kind, "date": d, "force": force}, timeout=180.0)
    if not isinstance(res, dict):
        raise RuntimeError("日记服务返回异常")
    with _CACHE_LOCK:
        for k in list(_CACHE):
            if k.startswith("diary"):
                _CACHE.pop(k, None)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "生成失败")
    return res


# ──────────────────────────── 情绪 / 花费 ────────────────────────────

def api_emotion(params):
    limit = max(10, min(int(params.get("limit") or 60), 400))
    selected_day = str(params.get("date") or "")
    if selected_day:
        date.fromisoformat(selected_day)

    def build():
        rows = db_rows("SELECT * FROM emotion_snapshot ORDER BY id DESC LIMIT ?", (limit,))
        rows.reverse()
        dims = ["joy", "happiness", "sadness", "inferiority", "shyness"]
        labels = {"joy": "愉悦", "happiness": "幸福", "sadness": "难过",
                  "inferiority": "自卑", "shyness": "害羞"}
        series = [{k: round(num(r.get(k)), 2) for k in dims} for r in rows]
        times = [r.get("t") or "" for r in rows]
        route = "/api/emotion/dashboard" + ("?date=" + selected_day if selected_day else "")
        bundle = core_view(route)
        character = bundle.get("character") or {"offline": True, "note": bundle.get("note", "角色状态暂不可用")}
        current = _emotion_latest(character)
        daily_rows = db_rows("SELECT substr(t,1,10) AS day, " +
                             ", ".join("ROUND(AVG(%s),2) AS %s" % (key, key) for key in dims) +
                             ", COUNT(*) AS samples FROM emotion_snapshot "
                             "WHERE substr(t,1,10)>=? GROUP BY day ORDER BY day",
                             ((date.today() - timedelta(days=29)).isoformat(),))
        return {"dims": [{"key": k, "label": labels[k]} for k in dims],
                "series": series, "times": times,
                "current": current, "count": len(rows),
                "character": character, "dashboard": bundle.get("dashboard") or {},
                "daily": bundle.get("daily") or {},
                "daily_trend": {"series": [{key: num(r.get(key)) for key in dims} for r in daily_rows],
                                "times": [r["day"] for r in daily_rows], "count": len(daily_rows)}}
    return cached("emotion:%s:%s" % (limit, selected_day), 2.5, build)


def api_cost(params):
    rng = (params.get("range") or "7d").strip()
    if rng not in ("today", "7d", "30d", "all"):
        rng = "7d"
    days = {"today": 1, "7d": 7, "30d": 30, "all": 400}[rng]

    def build():
        today = date.today()
        start = (today - timedelta(days=days - 1)).isoformat() if rng != "all" else "0000-00-00"
        by_day = db_rows(
            "SELECT substr(t,1,10) AS d, COUNT(*) AS n, "
            "SUM(COALESCE(NULLIF(actual_cost,0),estimated_cost)) AS c, "
            "SUM(COALESCE(input_tokens,0)) AS it, SUM(COALESCE(output_tokens,0)) AS ot "
            "FROM cost_event WHERE substr(t,1,10) >= ? GROUP BY d ORDER BY d", (start,))
        by_tag = db_rows(
            "SELECT feature_tag, COUNT(*) AS n, "
            "SUM(COALESCE(NULLIF(actual_cost,0),estimated_cost)) AS c "
            "FROM cost_event WHERE substr(t,1,10) >= ? GROUP BY feature_tag ORDER BY c DESC", (start,))
        tot = db_one(
            "SELECT COUNT(*) AS n, SUM(COALESCE(NULLIF(actual_cost,0),estimated_cost)) AS c "
            "FROM cost_event WHERE substr(t,1,10) >= ?", (start,)) or {}
        return {
            "range": rng,
            "start": "" if rng == "all" else start,
            "total": round(num(tot.get("c")), 4),
            "events": int(tot.get("n") or 0),
            "today": _cost_today(),
            "douyin": douyin_service().usage(start),
            "by_day": [{"day": r.get("d") or "", "events": int(r.get("n") or 0),
                        "cost": round(num(r.get("c")), 4),
                        "in_tokens": int(r.get("it") or 0),
                        "out_tokens": int(r.get("ot") or 0)} for r in by_day],
            "by_tag": [{"tag": r.get("feature_tag") or "-", "events": int(r.get("n") or 0),
                        "cost": round(num(r.get("c")), 4)} for r in by_tag],
        }
    return cached("cost:%s" % rng, 3.0, build)


# ──────────────────────────── 碎碎念 / 拒绝权 / 审计 ────────────────────────────

def api_chitchat(_params):
    def build():
        rows = db_rows("SELECT * FROM chitchat_log ORDER BY t DESC LIMIT 30")
        return {"items": [{"id": r.get("id"), "t": r.get("t") or "",
                           "content": r.get("content") or "",
                           "replied": bool(r.get("replied")),
                           "replied_at": r.get("replied_at") or "",
                           "emotion": r.get("emotion") or ""} for r in rows],
                "count": len(rows)}
    return cached("chitchat", 4.0, build)


def api_refusal(_params):
    def build():
        rows = db_rows("SELECT * FROM refusal_decision ORDER BY t DESC LIMIT 30")
        items = []
        for r in rows:
            signals = jloads(r.get("signals"), {}) or {}
            alts = jloads(r.get("alternatives"), []) or []
            items.append({
                "id": r.get("id"), "t": r.get("t") or "",
                "request": r.get("request_summary") or "",
                "decision": r.get("decision") or "",
                "propensity": round(num(r.get("propensity")), 3),
                "threshold": round(num(r.get("threshold")), 3),
                "reason": r.get("reason_text") or "",
                "style": r.get("style") or "",
                "signals": {k: round(num(v), 3) for k, v in signals.items()} if isinstance(signals, dict) else {},
                "alternatives": alts if isinstance(alts, list) else [],
            })
        return {"items": items, "count": len(items)}
    return cached("refusal", 4.0, build)


def api_audit(_params):
    def build():
        rows = db_rows("SELECT * FROM audit_event ORDER BY t DESC LIMIT 30")
        items = []
        for r in rows:
            detail = jloads(r.get("detail"), None)
            if isinstance(detail, dict):
                act = detail.get("activity") or ""
                steps = detail.get("steps") or []
                notes = detail.get("notes") or []
                detail_txt = ("activity=%s steps=%s" % (act, ",".join(map(str, steps[:6])))) if act else ""
                if notes:
                    detail_txt += (" | " + " ; ".join(map(str, notes[:3])))
            else:
                detail_txt = truncate(r.get("detail") or "", 200)
            items.append({"id": r.get("id"), "t": r.get("t") or "",
                          "entity_kind": r.get("entity_kind") or "",
                          "entity_id": r.get("entity_id") or "",
                          "action": r.get("action") or "",
                          "detail": detail_txt})
        return {"items": items, "count": len(items)}
    return cached("audit", 4.0, build)


# ──────────────────────────── 静态资源 ────────────────────────────

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}


def resolve_asset(name):
    """把 ?name= 限制在 ASSET_DIR 下。"""
    name = os.path.basename((name or "").replace("\\", "/"))
    if not name:
        return None
    ext = os.path.splitext(name)[1].lower()
    if ext not in ASSET_EXTS:
        return None
    p = os.path.join(ASSET_DIR, name)
    if not os.path.isfile(p):
        return None
    return p


def resolve_static(rel):
    rel = urllib.parse.unquote(rel or "").lstrip("/")
    p = os.path.abspath(os.path.join(STATIC, rel))
    if not p.startswith(os.path.abspath(STATIC)):
        return None
    if not os.path.isfile(p):
        return None
    return p


# ──────────────────────────── 路由表 ────────────────────────────

def _settings_bridge(route, body=None):
    try:
        result = http_json(CONSOLE_BASE + route, data=body,
                           headers={"x-console-token": console_token()}, timeout=15)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError()
        return {k: v for k, v in result.items() if k not in ("ok", "_http_status")}
    except Exception:
        raise SettingsError("本地桥接暂时不可用，请确认连接后重试。") from None


PRODUCT_SETTINGS = ProductSettings(release_paths.path(), DAFEIYU, _settings_bridge)
CONTACT_PREFERENCES = ContactPreferences(release_paths.path(), PRODUCT_SETTINGS.snapshot)
RECOVERY_PREVIEW = RecoveryPreview(release_paths.path(), DAFEIYU)
_DOUYIN = None
_DOUYIN_LOCK = threading.Lock()


def douyin_service():
    global _DOUYIN
    with _DOUYIN_LOCK:
        if _DOUYIN is None:
            _DOUYIN = DouyinSocial(release_paths.path(), snapshot=PRODUCT_SETTINGS.snapshot)
        return _DOUYIN


def api_room_sync(_params):
    def build():
        signatures = []
        for folder, _dirs, files in os.walk(STATIC):
            for name in sorted(files):
                if name.endswith(('.js', '.css', '.html')):
                    path = os.path.join(folder, name)
                    signatures.append((os.path.relpath(path, STATIC), hashlib.sha256(open(path, 'rb').read()).hexdigest()))
        return hashlib.sha256(json.dumps(signatures, sort_keys=True).encode()).hexdigest()[:20]
    return {'release': RELEASE, 'build': cached('room-build', 5, build),
            'settings_revision': PRODUCT_SETTINGS.revision(), 'contacts_revision': CONTACT_PREFERENCES.revision()}


def api_memory_editorial_save(body):
    try:
        result = http_json(CORE_BASE + '/api/memory/editorial', data=body, timeout=45, allow_error_body=True)
    except Exception:
        raise SettingsError('记忆核心暂不可用，请稍后重试。') from None
    if not isinstance(result, dict) or result.get('ok') is False or result.get('_http_status', 200) >= 400:
        raise SettingsError((result or {}).get('error') or '记忆操作未完成')
    with _CACHE_LOCK:
        for key in list(_CACHE):
            if key.startswith(('memory', 'graph', 'home', 'room-life')): _CACHE.pop(key, None)
    return result


def api_room_life(_params):
    def build():
        items = []
        for row in db_rows("SELECT id,name,content,learned_at FROM knowledge_item ORDER BY learned_at DESC LIMIT 3"):
            items.append({'kind': '学到一点', 'title': row.get('name') or '新知识',
                          'text': truncate(row.get('content') or '', 120), 'at': row.get('learned_at') or '',
                          'href': '#/memory?view=all&record=' + urllib.parse.quote(row['id'])})
        for row in db_rows("SELECT id,summary,content,t_created FROM memory_item WHERE tombstone=0 "
                           "AND layer IN ('long1','long2') AND status!='superseded' ORDER BY t_created DESC LIMIT 3"):
            items.append({'kind': '认真记住', 'title': row.get('summary') or '一段记忆',
                          'text': truncate(row.get('content') or '', 120), 'at': row.get('t_created') or '',
                          'href': '#/memory?view=all&record=' + urllib.parse.quote(row['id'])})
        diary = api_home({}).get('diary_today')
        if diary:
            items.append({'kind': '写进日记', 'title': diary['date'] + ' 的日记', 'text': diary['summary'],
                          'at': diary['date'], 'href': '#/diary?d=' + diary['date']})
        return {'items': sorted(items, key=lambda r: r['at'], reverse=True)[:5],
                'note': '来自已经保存的记忆、知识和日记，点击可以查看来源。'}
    return cached('room-life', 10, build)


def api_product_settings_save(body):
    result = PRODUCT_SETTINGS.save(body)
    with _CACHE_LOCK:
        for key in list(_CACHE):
            if key.startswith(("learning", "voice")):
                _CACHE.pop(key, None)
    _LEARN_SUBPROC["data"] = None
    return result


def _backup_scheduler():
    while True:
        try:
            PRODUCT_SETTINGS.schedule_tick()
        except Exception:
            log("自动备份检查未完成，请在设置中检查备份状态。", "WARN")
        time.sleep(30)

GET_ROUTES = {
    "/api/douyin": lambda p: douyin_service().read(),
    "/api/room/sync": api_room_sync,
    "/api/room/life": api_room_life,
    "/api/contacts/preferences": lambda p: CONTACT_PREFERENCES.read(),
    "/api/contacts/topics": lambda p: _settings_bridge('/api/product-experience'),
    "/api/settings/recovery": lambda p: RECOVERY_PREVIEW.backups(),
    "/api/memory/editorial": lambda p: core_view('/api/memory/editorial?id=' + urllib.parse.quote(str(p.get('id') or ''))),
    "/api/settings": lambda p: PRODUCT_SETTINGS.read(),
    "/api/settings/appearance": lambda p: PRODUCT_SETTINGS.preferences()["appearance"],
    "/api/settings/model": lambda p: PRODUCT_SETTINGS.model_info(),
    "/api/settings/drawing": lambda p: PRODUCT_SETTINGS.drawing_info(),
    "/api/settings/drawing/gallery": lambda p: PRODUCT_SETTINGS.drawing_gallery(),
    "/api/settings/backup": lambda p: PRODUCT_SETTINGS.backup_status(),
    "/api/settings/export": lambda p: PRODUCT_SETTINGS.export(),
    "/api/settings/diagnostics": lambda p: PRODUCT_SETTINGS.diagnostics(),
    "/api/chat/status": api_chat_status,
    "/api/contacts/affinity": api_affinity,
    "/api/home": api_home,
    "/api/thinking": api_thinking,
    "/api/persona": api_persona,
    "/api/voice": api_voice,
    "/api/contacts": api_contacts,
    "/api/qzone": api_qzone,
    "/api/memory": api_memory,
    "/api/memory/sections": api_memory_sections,
    "/api/memory/detail": api_memory_detail,
    "/api/memory/graph": api_memory_graph,
    "/api/retrieval/status": api_retrieval,
    "/api/memory/residual": lambda p: core_view("/api/thought/residual"),
    "/api/learning": api_learning,
    "/api/learning/dashboard": api_learning_dashboard,
    "/api/learning/job": lambda p: core_view("/api/learning/job"),
    "/api/learning/report": api_learning_report,
    "/api/learning/reports": lambda p: {"items": _reports(int(p.get("limit") or 20))},
    "/api/diary/calendar": api_diary_calendar,
    "/api/diary/get": api_diary_get,
    "/api/emotion": api_emotion,
    "/api/cost": api_cost,
    "/api/chitchat": api_chitchat,
    "/api/refusal": api_refusal,
    "/api/audit": api_audit,
    "/api/qq": api_qq,
}

POST_ROUTES = {
    "/api/douyin/settings": lambda b: douyin_service().save_settings(b),
    "/api/douyin/key": lambda b: douyin_service().replace_key(b),
    "/api/douyin/connect": lambda b: douyin_service().connect(),
    "/api/douyin/start": lambda b: douyin_service().start(b),
    "/api/douyin/stop": lambda b: douyin_service().stop(),
    "/api/douyin/receipt": lambda b: douyin_service().receipt(b),
    "/api/douyin/memory": lambda b: (douyin_service().flush_memory(), douyin_service().read())[1],
    "/api/contacts/preferences": lambda b: CONTACT_PREFERENCES.save(b),
    "/api/contacts/topics": lambda b: _settings_bridge('/api/product-experience', b),
    "/api/settings/recovery": lambda b: RECOVERY_PREVIEW.start(b.get('backup_id'), b.get('rehearse') is True),
    "/api/memory/editorial": api_memory_editorial_save,
    "/api/settings": api_product_settings_save,
    "/api/settings/key": lambda b: PRODUCT_SETTINGS.replace_key(b),
    "/api/settings/drawing-key": lambda b: PRODUCT_SETTINGS.replace_drawing_key(b),
    "/api/settings/backup": lambda b: PRODUCT_SETTINGS.start_backup(),
    "/api/persona": api_persona_save,
    "/api/voice/synth": api_voice_synth,
    "/api/voice/send": api_voice_send,
    "/api/learning/quota": api_learning_quota_save,
    "/api/learning/settings": lambda b: _learning_command("/api/learning/settings", b),
    "/api/learning/run": lambda b: _learning_command("/api/learning/run", b),
    "/api/diary/generate": api_diary_generate,
    "/api/qzone/post": api_qzone_post,
}


# ──────────────────────────── HTTP ────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "xiaoyu-web/" + VERSION
    protocol_version = "HTTP/1.1"

    # ---- 基础输出 ----
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _ok(self, data):
        self._send(200, json.dumps({"ok": True, "data": data}, ensure_ascii=False))

    def _err(self, msg, code=200):
        # 业务错误也回 200，让前端统一处理，不会白屏
        self._send(code, json.dumps({"ok": False, "error": str(msg)[:600]}, ensure_ascii=False))

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        if n <= 0:
            return {}
        if n > 8 * 1024 * 1024:
            raise ValueError("请求体过大")
        raw = self.rfile.read(n)
        if not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            raise ValueError("请求体不是合法 JSON")

    def _parse(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        params = {k: (v[0] if v else "") for k, v in qs.items()}
        return parsed.path, params

    # ---- 静态 ----
    def _serve_file(self, path):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except Exception as exc:
            self._err("读取文件失败: %s" % exc, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        self._send(200, data, MIME.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream")

    def _serve_index(self):
        p = os.path.join(STATIC, "index.html")
        if not os.path.isfile(p):
            self._send(200, "<h1>xiaoyu-web</h1><p>缺少 static/index.html</p>",
                       "text/html; charset=utf-8")
            return
        with open(p, encoding='utf-8') as stream:
            page = stream.read()
        page = page.replace('__XIAOYU_BUILD__', api_room_sync({})['build']).replace('__XIAOYU_VERSION__', VERSION)
        appearance=PRODUCT_SETTINGS.preferences().get('appearance',{})
        theme=appearance.get('theme','xiaoyu-room')
        page=page.replace('__XIAOYU_THEME__',theme if theme in UI_THEMES else 'xiaoyu-room')
        color_mode=appearance.get('color_mode','light')
        if color_mode not in ('light','dark','system'):
            color_mode='light'
        night_palette=appearance.get('night_palette','charcoal')
        if night_palette not in ('charcoal','sky-blue','slate-blue'):
            night_palette='charcoal'
        page=page.replace('__XIAOYU_NIGHT_PALETTE__',night_palette)
        page=page.replace('__XIAOYU_COLOR_MODE__',color_mode).replace('__XIAOYU_SCHEME__','dark' if color_mode=='dark' else 'light')
        self._send(200, page, 'text/html; charset=utf-8')

    def do_GET(self):  # noqa: N802
        t0 = time.time()
        path, params = self._parse()
        try:
            if path in ("/", "/index.html"):
                self._serve_index()
                return
            if path == "/api/ping":
                self._ok({"pong": True, "time": datetime.now().isoformat(timespec="seconds")})
                return
            if path == "/api/douyin/frame":
                image = douyin_service().screenshot()
                if not image:
                    self._err("尚无抖音抽样画面，请先连接或观看。", 404)
                    return
                self._send(200, image, "image/jpeg", {"Cache-Control": "no-store"})
                return
            if path == "/api/asset":
                p = resolve_asset(params.get("name"))
                if not p:
                    self._err("找不到该图片", 404)
                    return
                self._serve_file(p)
                return
            if path == "/api/settings/drawing/image":
                artwork_id = params.get("id", "")
                if not re.fullmatch(r"[0-9a-f]{32}", artwork_id):
                    self._err("作品编号无效", 400)
                    return
                try:
                    req = urllib.request.Request(CONSOLE_BASE + "/api/drawing/image?id=" + artwork_id,
                                                 headers={"x-console-token": console_token()})
                    with urllib.request.urlopen(req, timeout=10) as response:
                        image = response.read(16 * 1024 * 1024 + 1)
                        mime = response.headers.get("Content-Type", "image/png")
                    if len(image) > 16 * 1024 * 1024 or mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
                        raise ValueError()
                    self._send(200, image, mime, {"Cache-Control": "no-store"})
                except Exception:
                    self._err("作品图片暂不可用", 404)
                return
            if path == "/api/voice/audio":
                try:
                    audio = api_voice_audio(params)
                except Exception as exc:
                    self._err("播放失败: %s" % exc, 404)
                    return
                if not audio:
                    self._err("音频已过期，请重新合成", 404)
                    return
                self._send(200, audio, "audio/wav", {"Cache-Control": "max-age=600"})
                return
            if path.startswith("/api/"):
                fn = GET_ROUTES.get(path)
                if fn is None:
                    self._err("未知接口 %s" % path, 404)
                    return
                try:
                    data = fn(params)
                except Exception as exc:
                    log("GET %s 失败: %s" % (path, exc), "ERROR")
                    log(traceback.format_exc(), "ERROR")
                    self._err(exc)
                    return
                self._ok(data)
                self._log_access("GET", path, t0)
                return
            if path.startswith("/static/"):
                p = resolve_static(path[len("/static/"):])
                if not p:
                    self._err("找不到文件", 404)
                    return
                self._serve_file(p)
                return
            p = resolve_static(path)
            if p:
                self._serve_file(p)
                return
            # hash 路由的兜底
            self._serve_index()
        except Exception as exc:
            log("GET 处理异常 %s: %s" % (path, exc), "ERROR")
            try:
                self._err("服务端异常: %s" % exc)
            except Exception:
                pass

    def do_POST(self):  # noqa: N802
        t0 = time.time()
        path, _params = self._parse()
        try:
            if path == "/api/settings" or path.startswith(("/api/settings/", "/api/douyin/")) or path in {
                '/api/contacts/preferences', '/api/contacts/topics', '/api/memory/editorial'}:
                origin = self.headers.get("Origin")
                if self.headers.get("X-Xiaoyu-Settings") != "1" or (origin and origin != "http://" + self.headers.get("Host", "")):
                    self._err("请从小鱼之家设置页面提交修改。", 403)
                    return
            fn = POST_ROUTES.get(path)
            if fn is None:
                self._err("未知接口 %s" % path, 404)
                return
            try:
                body = self._body_json()
            except Exception as exc:
                self._err(exc)
                return
            if not isinstance(body, dict):
                self._err("请求体必须是对象")
                return
            try:
                data = fn(body)
            except SettingsError as exc:
                self._err(str(exc))
                return
            except Exception as exc:
                log("POST %s 失败: %s" % (path, exc), "ERROR")
                log(traceback.format_exc(), "ERROR")
                self._err(exc)
                return
            self._ok(data)
            self._log_access("POST", path, t0)
        except Exception as exc:
            log("POST 处理异常 %s: %s" % (path, exc), "ERROR")
            try:
                self._err("服务端异常: %s" % exc)
            except Exception:
                pass

    def _log_access(self, method, path, t0):
        ms = (time.time() - t0) * 1000.0
        if ms > 1200:
            log("%s %s 慢 %.0fms" % (method, path, ms), "WARN")

    def log_message(self, fmt, *args):
        # 默认访问日志太吵；只在明显错误时记
        msg = fmt % args
        if " 4" in msg or " 5" in msg:
            log("HTTP %s" % msg, "WARN")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not os.path.isdir(STATIC):
        os.makedirs(STATIC, exist_ok=True)
    log("=" * 62)
    log("xiaoyu-web 启动中 …")
    log("  解释器   : %s (python %s)" % (sys.executable, sys.version.split()[0]))
    log("  静态目录 : %s" % STATIC)
    log("  数据库   : %s" % DB_PATH)
    log("  会话日志 : %s" % SESSIONS_ROOT)
    log("  zstandard: %s" % ("已就绪" if zstandard else "缺失（思考面板会降级）"))
    log("  监听     : http://%s:%d/" % (HOST, PORT))
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        log("端口 %d 绑定失败: %s（是不是已经启动了？）" % (PORT, exc), "ERROR")
        return 1
    httpd.daemon_threads = True
    threading.Thread(target=_backup_scheduler, daemon=True).start()
    douyin_service().start_scheduler()
    log("就绪，等待请求 …")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("收到 Ctrl-C，退出")
    finally:
        douyin_service().close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
