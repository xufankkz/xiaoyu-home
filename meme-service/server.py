"""小鱼 · 表情包语义检索服务（127.0.0.1:18892）

用途：让「小鱼」按**意思**找表情包，而不是按文件名。
索引来源（都可选）：
  1) 她 QQ 收藏的表情：`<workspace>\\qq-bridge\\state\\stickers.json`（bridge 每次同步会更新）
  2) 本地表情库目录：`<workspace>\\meme-library\\**`（你可以往里丢图，png/jpg/gif/webp）
  3) 手工补充的说明：`<workspace>\\meme-library\\notes.json`（id -> 描述/标签）

能力：
  GET  /meme/list                        索引概况
  GET  /meme/search?q=无语&limit=5        语义检索（中文二元组 + 描述/标签/备注）
  GET  /meme/image?id=<id>               取图（用于预览/给模型看）
  POST /meme/note      {id, note, tags}  写说明
  POST /meme/refresh                     重新扫索引
  POST /meme/send      {target, id}      发到 QQ（target: private:QQ 或 group:群号）
  POST /meme/annotate  {id}              让视觉模型生成描述与标签（可选，失败不影响检索）

启动：
  <workspace>/dafeiyu\\.venv\\Scripts\\python.exe <workspace>\\meme-service\\server.py
"""

from __future__ import annotations
import release_paths
from home_runtime.credentials import Credentials

import base64
import json
import math
import os
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST, PORT = '127.0.0.1', release_paths.port('memes')
STICKERS = Path(release_paths.path('qq-bridge/state/stickers.json'))
BRIDGE_CFG = Path(release_paths.path('qq-bridge/config.json'))
LIB = Path(release_paths.path('meme-library'))
NOTES = LIB / "notes.json"
INDEX_CACHE: dict = {"at": 0.0, "items": []}


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return default if default is not None else {}


def bridge() -> dict:
    c = read_json(BRIDGE_CFG, {})
    s = c.get("snowluma") or {}
    return {"url": str(s.get("httpUrl") or release_paths.url('onebot', '')).rstrip("/"),
            "token": Credentials(Path(release_paths.path("release/credentials.dpapi"))).get("onebot:token")}


def notes() -> dict:
    return read_json(NOTES, {})


def build_index(force: bool = False) -> list[dict]:
    if not force and INDEX_CACHE["items"] and time.time() - INDEX_CACHE["at"] < 30:
        return INDEX_CACHE["items"]
    items: list[dict] = []
    seen = set()

    # 1) QQ 收藏
    for st in (read_json(STICKERS, []) or []):
        if not isinstance(st, dict):
            continue
        sid = str(st.get("id") or st.get("resId") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        items.append({
            "id": sid,
            "kind": "qq_sticker",
            "desc": str(st.get("desc") or ""),
            "note": str(st.get("localNote") or ""),
            "tags": st.get("tags") or [],
            "url": str(st.get("url") or ""),
            "local": "",
            "useCount": int(st.get("useCount") or 0),
        })

    # 2) 本地库
    if LIB.exists():
        for f in LIB.rglob("*"):
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                continue
            sid = "local:" + f.relative_to(LIB).as_posix()
            if sid in seen:
                continue
            seen.add(sid)
            meta = notes().get(sid) or {}
            items.append({
                "id": sid, "kind": "local", "desc": str(meta.get("desc") or f.stem),
                "note": str(meta.get("note") or ""), "tags": meta.get("tags") or [],
                "url": "", "local": str(f), "useCount": 0,
            })

    # 3) 手工说明覆盖（对 QQ 收藏也能补）
    n = notes()
    for it in items:
        m = n.get(it["id"])
        if isinstance(m, dict):
            if m.get("desc"):
                it["desc"] = str(m["desc"])
            if m.get("note"):
                it["note"] = str(m["note"])
            if m.get("tags"):
                it["tags"] = m["tags"]

    INDEX_CACHE.update(at=time.time(), items=items)
    return items


def bigrams(text: str) -> set[str]:
    t = "".join(ch for ch in str(text or "").lower() if ch.strip())
    out = set()
    for i in range(len(t)):
        if i + 1 < len(t):
            out.add(t[i:i + 2])
        out.add(t[i])
    return out


def score(query: str, it: dict) -> float:
    """语义近似打分：优先中文二元组重合，单字只在没有二元组命中时兜底。"""
    hay = " ".join([it.get("desc") or "", it.get("note") or "", " ".join(map(str, it.get("tags") or []))]).lower()
    q = "".join(ch for ch in str(query or "").lower() if ch.strip())
    if not q or not hay:
        return 0.0
    q_bi = {q[i:i + 2] for i in range(len(q) - 1)}
    h_bi = {hay[i:i + 2] for i in range(len(hay) - 1)}
    if q_bi:
        inter = len(q_bi & h_bi)
        if inter:
            return inter / len(q_bi) + min(0.1, it.get("useCount", 0) * 0.01)
        # 没有二元组命中时，用单字兜底（只给很低的分数，避免乱匹配）
        q_ch = set(q)
        hits = sum(1 for ch in q_ch if ch in hay)
        return (hits / max(1, len(q_ch))) * 0.25
    hits = sum(1 for ch in set(q) if ch in hay)
    return (hits / max(1, len(set(q)))) * 0.3


def fetch_bytes(url: str, timeout: int = 20) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def onebot(action: str, payload: dict) -> dict:
    b = bridge()
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{b['url']}/{action}", data=data, method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {b['token']}"} if b["token"] else {})},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def item_bytes(it: dict) -> bytes | None:
    if it.get("local") and Path(it["local"]).exists():
        return Path(it["local"]).read_bytes()
    if it.get("url"):
        return fetch_bytes(it["url"])
    return None


def send_meme(target: str, it: dict) -> dict:
    raw = item_bytes(it)
    if not raw:
        return {"ok": False, "error": "拿不到这张图（本地文件丢失 / 链接失效）"}
    seg = {"type": "image", "data": {"file": "base64://" + base64.b64encode(raw).decode()}}
    if target.startswith("group:"):
        res = onebot("send_group_msg", {"group_id": int(target.split(":", 1)[1]), "message": [seg]})
    else:
        qq = target.split(":", 1)[1] if ":" in target else target
        res = onebot("send_private_msg", {"user_id": int(qq), "message": [seg]})
    return {"ok": True, "result": res}


def annotate(it: dict) -> dict:
    """让视觉模型给这张图写描述与标签（走她的中转）。失败就返回错误，不抛。"""
    raw = item_bytes(it)
    if not raw:
        return {"ok": False, "error": "拿不到图"}
    b64 = base64.b64encode(raw).decode()
    prompt = ("这张图是一个中文聊天表情包。请输出 JSON：{\"desc\":\"一句话描述画面和它表达的情绪/用途\","
              "\"tags\":[\"3-8个中文标签，如 无语/开心/嘲讽/摸鱼\"]}。只输出 JSON。")
    try:
        from home_runtime.model import ModelClient
        result = ModelClient(Path(release_paths.path())).complete_payload({
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}}
            ]}], 'max_tokens': 512, 'temperature': 0.3
        }, feature='meme_annotation', group='vision')
        data = result['response']
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        text = text.strip().strip("`")
        if text.startswith("json"):
            text = text[4:]
        obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
        n = notes()
        n[it["id"]] = {"desc": obj.get("desc", ""), "tags": obj.get("tags") or []}
        LIB.mkdir(parents=True, exist_ok=True)
        NOTES.write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
        build_index(force=True)
        return {"ok": True, "note": n[it["id"]]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def _find(self, sid: str) -> dict | None:
        for it in build_index():
            if it["id"] == sid:
                return it
        return None

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/meme/list":
                items = build_index()
                return self._json(200, {"ok": True, "data": {"count": len(items),
                                                             "kinds": {"qq": sum(1 for i in items if i["kind"] == "qq_sticker"),
                                                                       "local": sum(1 for i in items if i["kind"] == "local")}}})
            if u.path == "/meme/search":
                query = (q.get("q") or [""])[0]
                limit = int((q.get("limit") or ["5"])[0])
                items = [dict(it, score=round(score(query, it), 3)) for it in build_index()]
                items = [i for i in items if i["score"] > 0]
                items.sort(key=lambda x: -x["score"])
                fallback = False
                if not items:
                    fallback = True
                    items = sorted(build_index(), key=lambda x: -x.get("useCount", 0))[:limit]
                    items = [dict(i, score=0.0) for i in items]
                return self._json(200, {"ok": True, "data": {"query": query, "fallback": fallback, "hits": items[:limit]}})
            if u.path == "/meme/image":
                it = self._find((q.get("id") or [""])[0])
                if not it:
                    return self._json(404, {"ok": False, "error": "没有这个 id"})
                raw = item_bytes(it)
                if not raw:
                    return self._json(404, {"ok": False, "error": "取图失败"})
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if u.path == "/health":
                return self._json(200, {"ok": True, "port": PORT, "count": len(build_index())})
            return self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            body = {}
        try:
            if u.path == "/meme/refresh":
                return self._json(200, {"ok": True, "data": {"count": len(build_index(force=True))}})
            if u.path == "/meme/note":
                sid = str(body.get("id") or "")
                n = notes()
                n[sid] = {"desc": str(body.get("desc") or ""), "note": str(body.get("note") or ""),
                          "tags": body.get("tags") or []}
                LIB.mkdir(parents=True, exist_ok=True)
                NOTES.write_text(json.dumps(n, ensure_ascii=False, indent=2), encoding="utf-8")
                build_index(force=True)
                return self._json(200, {"ok": True})
            if u.path == "/meme/send":
                it = self._find(str(body.get("id") or ""))
                if not it:
                    return self._json(404, {"ok": False, "error": "没有这个 id"})
                target = str(body.get("target") or "")
                return self._json(200, {"ok": True, "data": send_meme(target, it)})
            if u.path == "/meme/annotate":
                it = self._find(str(body.get("id") or ""))
                if not it:
                    return self._json(404, {"ok": False, "error": "没有这个 id"})
                r = annotate(it)
                return self._json(200 if r.get("ok") else 500, {"ok": bool(r.get("ok")), "data": r})
            return self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args) -> None:
        return


if __name__ == "__main__":
    LIB.mkdir(parents=True, exist_ok=True)
    print(f"[meme] http://{HOST}:{PORT}/  index={len(build_index())}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
