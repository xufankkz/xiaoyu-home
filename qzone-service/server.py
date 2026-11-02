"""小鱼 · QQ空间服务（本地 HTTP，127.0.0.1:18888）

作用：给她「自己发空间 / 自己评论 / 看谁评论了我」的能力。
登录态不自己存：每次向 SnowLuma 的 OneBot HTTP API 要 cookies（get_cookies），
用其中的 skey / p_skey / uin 调 QQ空间 Web 接口。

接口（给本地 MCP / 面板用）：
  GET  /whoami                       → {uin}
  GET  /feed?uin=&count=             → 说说列表（默认自己的）
  GET  /detail?host_uin=&tid=        → 单条说说的详情（含评论）
  POST /post   {content}             → 发说说，返回 tid
  POST /comment{host_uin,tid,content}→ 评论
  POST /like   {host_uin,tid}        → 点赞

用法：
  <workspace>/dafeiyu\\.venv\\Scripts\\python.exe <workspace>\\qzone-service\\server.py
"""

from __future__ import annotations
import release_paths
from home_runtime.credentials import Credentials

import json
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = release_paths.port('qzone')
BRIDGE_CFG = Path(release_paths.path('qq-bridge/config.json'))

BASE = "https://user.qzone.qq.com"
EMOTION_URL = f"{BASE}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
LIST_URL = f"{BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
COMMENT_URL = f"{BASE}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
DETAIL_URL = f"{BASE}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6"
LIKE_URL = f"{BASE}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

_ctx_cache: dict = {"at": 0.0, "data": None}


def _bridge() -> dict:
    try:
        return json.loads(BRIDGE_CFG.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _snowluma() -> tuple[str, str]:
    c = _bridge().get("snowluma") or {}
    return (str(c.get("httpUrl") or release_paths.url('onebot', '')).rstrip("/"),
            Credentials(Path(release_paths.path("release/credentials.dpapi"))).get("onebot:token"))


def onebot(action: str, payload: dict | None = None, timeout: int = 20) -> dict:
    url, token = _snowluma()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{url}/{action}", data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def ctx(force: bool = False) -> dict:
    """拿 QQ空间登录上下文（uin / skey / p_skey / g_tk），10 分钟缓存。"""
    if not force and _ctx_cache["data"] and time.time() - _ctx_cache["at"] < 600:
        return _ctx_cache["data"]
    res = onebot("get_cookies", {"domain": "user.qzone.qq.com"})
    raw = ((res.get("data") or {}).get("cookies") or "")
    jar = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        jar[k.strip()] = v.strip()
    uin = (jar.get("uin") or jar.get("p_uin") or "").lstrip("o")
    skey = jar.get("skey") or ""
    p_skey = jar.get("p_skey") or skey
    hashv = 5381
    for ch in p_skey:
        hashv += (hashv << 5) + ord(ch)
    data = {"uin": uin, "skey": skey, "p_skey": p_skey, "gtk": str(hashv & 0x7FFFFFFF), "cookies": raw}
    _ctx_cache.update(at=time.time(), data=data)
    return data


def qz_request(url: str, params: dict, data: dict | None = None, timeout: int = 30) -> str:
    c = ctx()
    q = urllib.parse.urlencode(params, doseq=True)
    body = urllib.parse.urlencode(data or {}, doseq=True).encode() if data is not None else None
    req = urllib.request.Request(
        f"{url}?{q}", data=body, method="POST" if body is not None else "GET",
        headers={
            "Cookie": c["cookies"],
            "User-Agent": UA,
            "Referer": f"{BASE}/{c['uin']}",
            "Origin": BASE,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def publish(text: str) -> dict:
    c = ctx()
    data = {
        "syn_tweet_verson": "1", "paramstr": "1", "who": "1", "con": text,
        "feedversion": "1", "ver": "1", "ugc_right": "1", "to_sign": "0",
        "hostuin": c["uin"], "code_version": "1", "format": "json",
        "qzreferrer": f"{BASE}/{c['uin']}",
    }
    raw = qz_request(EMOTION_URL, {"g_tk": c["gtk"], "uin": c["uin"]}, data)
    try:
        obj = json.loads(raw)
    except Exception:
        obj = {"raw": raw[:400]}
    tid = None
    for key in ("tid", "feedid", "id"):
        if isinstance(obj, dict) and obj.get(key):
            tid = str(obj[key])
            break
    return {"ok": bool(tid) or obj.get("code") == 0, "tid": tid, "response": obj}


def comment(host_uin: str, tid: str, text: str) -> dict:
    c = ctx()
    data = {
        "topicId": f"{host_uin}_{tid}__1", "uin": c["uin"], "hostUin": host_uin,
        "feedsType": 100, "inCharset": "utf-8", "outCharset": "utf-8",
        "plat": "qzone", "source": "ic", "platformid": 52, "format": "fs",
        "ref": "feeds", "content": text,
        "qzreferrer": f"{BASE}/{c['uin']}/main",
    }
    raw = qz_request(COMMENT_URL, {"g_tk": c["gtk"]}, data)
    return {"ok": '"code":0' in raw or "succ" in raw.lower(), "raw": raw[:600]}


def list_feed(uin: str | None = None, count: int = 10) -> dict:
    c = ctx()
    target = str(uin or c["uin"])
    params = {
        "uin": c["uin"], "hostUin": target, "ftype": 0, "sort": 0,
        "pos": 0, "num": count, "format": "json", "g_tk": c["gtk"],
        "inCharset": "utf-8", "outCharset": "utf-8", "notice": 0, "code_version": 1,
    }
    raw = qz_request(LIST_URL, params)
    try:
        obj = json.loads(raw)
    except Exception:
        return {"ok": False, "raw": raw[:400]}
    items = []
    for m in (obj.get("msglist") or [])[:count]:
        items.append({
            "host_uin": target,
            "tid": str(m.get("tid") or ""),
            "content": (m.get("content") or "")[:500],
            "created": m.get("created_time"),
            "comments": [
                {"uin": str(cm.get("uin") or ""), "name": cm.get("name") or "", "content": (cm.get("content") or "")[:200],
                 "tid": str(cm.get("tid") or ""), "reply_uin": str(cm.get("reply_uin") or "")}
                for cm in (m.get("commentlist") or [])
            ],
        })
    return {"ok": True, "count": len(items), "items": items}


def detail(host_uin: str, tid: str) -> dict:
    c = ctx()
    params = {
        "uin": c["uin"], "hostUin": host_uin, "tid": tid, "format": "json",
        "g_tk": c["gtk"], "inCharset": "utf-8", "outCharset": "utf-8", "code_version": 1,
    }
    raw = qz_request(DETAIL_URL, params)
    try:
        obj = json.loads(raw)
    except Exception:
        return {"ok": False, "raw": raw[:400]}
    cm = obj.get("commentlist") or []
    return {
        "ok": True,
        "tid": tid,
        "host_uin": host_uin,
        "content": (obj.get("content") or "")[:800],
        "comments": [
            {"uin": str(x.get("uin") or ""), "name": x.get("name") or "",
             "content": (x.get("content") or "")[:300], "tid": str(x.get("tid") or "")}
            for x in cm
        ],
    }


def like(host_uin: str, tid: str) -> dict:
    c = ctx()
    key = f"{BASE}/{host_uin}/mood/{tid}"
    data = {
        "qzreferrer": f"{BASE}/{c['uin']}", "opuin": c["uin"], "unikey": key, "curkey": key,
        "appid": 311, "from": 1, "typeid": 0, "abstime": int(time.time()), "fid": tid,
        "active": 0, "format": "json", "fupdate": 1,
    }
    raw = qz_request(LIKE_URL, {"g_tk": c["gtk"]}, data)
    return {"ok": '"code":0' in raw or "succ" in raw.lower(), "raw": raw[:400]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/whoami":
                c = ctx()
                self._send(200, {"ok": True, "uin": c["uin"]})
            elif u.path == "/feed":
                self._send(200, list_feed((q.get("uin") or [None])[0], int((q.get("count") or ["10"])[0])))
            elif u.path == "/detail":
                self._send(200, detail((q.get("host_uin") or [""])[0], (q.get("tid") or [""])[0]))
            elif u.path == "/health":
                self._send(200, {"ok": True, "port": PORT})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            body = {}
        try:
            if u.path == "/post":
                self._send(200, publish(str(body.get("content") or "")))
            elif u.path == "/comment":
                self._send(200, comment(str(body.get("host_uin") or ""), str(body.get("tid") or ""), str(body.get("content") or "")))
            elif u.path == "/like":
                self._send(200, like(str(body.get("host_uin") or ""), str(body.get("tid") or "")))
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args) -> None:
        return


if __name__ == "__main__":
    print(f"[qzone] listening on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
